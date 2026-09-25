"""Persistent Telegram update queue and face index."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np

from .faces import Face, similarity


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS assets (
                id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                file_id TEXT NOT NULL,
                media_type TEXT NOT NULL,
                posted_at INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT,
                album_name TEXT,
                album_type TEXT,
                album_file_id TEXT,
                UNIQUE(channel_id, message_id)
            );
            CREATE TABLE IF NOT EXISTS faces (
                id INTEGER PRIMARY KEY,
                asset_id INTEGER NOT NULL REFERENCES assets(id),
                cluster_id INTEGER,
                vector BLOB NOT NULL,
                thumbnail BLOB NOT NULL
            );
            CREATE INDEX IF NOT EXISTS faces_asset ON faces(asset_id);
            CREATE INDEX IF NOT EXISTS faces_cluster ON faces(cluster_id);
            CREATE TABLE IF NOT EXISTS sessions (
                user_id INTEGER PRIMARY KEY,
                results TEXT NOT NULL DEFAULT '[]',
                next_result INTEGER NOT NULL DEFAULT 0,
                query_vector BLOB
            );
            CREATE TABLE IF NOT EXISTS sent_assets (
                user_id INTEGER NOT NULL,
                asset_id INTEGER NOT NULL,
                PRIMARY KEY (user_id, asset_id)
            );
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(assets)")}
        for name in ("album_name", "album_type", "album_file_id"):
            if name not in columns:
                self.db.execute(f"ALTER TABLE assets ADD COLUMN {name} TEXT")
        session_columns = {row[1] for row in self.db.execute("PRAGMA table_info(sessions)")}
        if "query_vector" not in session_columns:
            self.db.execute("ALTER TABLE sessions ADD COLUMN query_vector BLOB")

    def get_offset(self) -> int:
        row = self.db.execute("SELECT value FROM settings WHERE key='update_offset'").fetchone()
        return int(row[0]) if row else 0

    def set_offset(self, offset: int) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES ('update_offset', ?)", (str(offset),))

    def queue_asset(self, channel_id: int, message_id: int, file_id: str,
                    media_type: str, posted_at: int) -> None:
        with self.db:
            self.db.execute("""INSERT INTO assets
                (channel_id, message_id, file_id, media_type, posted_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(channel_id, message_id) DO NOTHING""",
                (channel_id, message_id, file_id, media_type, posted_at))

    def pending_asset(self):
        return self.db.execute("SELECT * FROM assets WHERE status='pending' ORDER BY id LIMIT 1").fetchone()

    def mark_failed(self, asset_id: int, error: str) -> None:
        with self.db:
            self.db.execute("UPDATE assets SET status='failed', error=? WHERE id=?", (error[:500], asset_id))

    def save_faces(self, asset_id: int, faces: list[Face], cluster_threshold: float = 0.55) -> None:
        with self.db:
            self._insert_faces(asset_id, faces, cluster_threshold)
            self.db.execute("UPDATE assets SET status='ready', error=NULL WHERE id=?", (asset_id,))

    def import_exported_asset(self, channel_id: int, message_id: int, posted_at: int,
                              faces: list[Face], cluster_threshold: float = 0.55) -> bool:
        """Atomically add an exported post or repair a failed/pending one."""
        with self.db:
            cursor = self.db.execute("""INSERT INTO assets
                (channel_id, message_id, file_id, media_type, posted_at, status)
                VALUES (?, ?, '', 'copy', ?, 'ready')
                ON CONFLICT(channel_id, message_id) DO UPDATE SET
                file_id='', media_type='copy', posted_at=excluded.posted_at,
                status='ready', error=NULL WHERE assets.status!='ready'""",
                (channel_id, message_id, posted_at))
            if not cursor.rowcount:
                return False
            asset_id = self.db.execute("SELECT id FROM assets WHERE channel_id=? AND message_id=?",
                                       (channel_id, message_id)).fetchone()[0]
            self._insert_faces(asset_id, faces, cluster_threshold)
        return True

    def has_ready_asset(self, channel_id: int, message_id: int) -> bool:
        return self.db.execute("""SELECT 1 FROM assets WHERE channel_id=? AND message_id=?
            AND status='ready'""",
                               (channel_id, message_id)).fetchone() is not None

    def attach_album_media(self, channel_id: int, message_id: int,
                           name: str, media_type: str) -> None:
        if Path(name).name != name or media_type not in {"photo", "document"}:
            raise ValueError("Invalid album media")
        with self.db:
            self.db.execute("""UPDATE assets SET
                album_file_id=CASE WHEN album_name=? AND album_type=? THEN album_file_id ELSE NULL END,
                album_name=?, album_type=?
                WHERE channel_id=? AND message_id=? AND status='ready'""",
                            (name, media_type, name, media_type, channel_id, message_id))

    def cache_album_file_id(self, asset_id: int, file_id: str) -> None:
        with self.db:
            self.db.execute("UPDATE assets SET album_file_id=? WHERE id=?", (file_id, asset_id))

    def _insert_faces(self, asset_id: int, faces: list[Face], cluster_threshold: float) -> None:
        representatives = self.cluster_representatives()
        for face in faces:
            best_id = None
            best_score = cluster_threshold
            for representative in representatives:
                score = similarity(face.vector, representative[1])
                if score > best_score:
                    best_id, best_score = representative[0], score
            cursor = self.db.execute("INSERT INTO faces (asset_id, vector, thumbnail) VALUES (?, ?, ?)",
                                     (asset_id, face.vector.tobytes(), face.thumbnail))
            face_id = cursor.lastrowid
            cluster_id = best_id or face_id
            self.db.execute("UPDATE faces SET cluster_id=? WHERE id=?", (cluster_id, face_id))
            if best_id is None:
                representatives.append((face_id, face.vector))

    def cluster_representatives(self) -> list[tuple[int, np.ndarray]]:
        rows = self.db.execute("SELECT id, vector FROM faces WHERE id=cluster_id ORDER BY id").fetchall()
        return [(row[0], np.frombuffer(row[1], dtype=np.float32)) for row in rows]

    def face_page(self, page: int, groups_only: bool = True, page_size: int = 20):
        condition = "WHERE id=cluster_id" if groups_only else ""
        count = self.db.execute(f"SELECT COUNT(*) FROM faces {condition}").fetchone()[0]
        rows = self.db.execute(f"""SELECT id, thumbnail FROM faces {condition}
            ORDER BY id LIMIT ? OFFSET ?""", (page_size, page * page_size)).fetchall()
        return count, rows

    def face_vector(self, face_id: int):
        row = self.db.execute("SELECT vector FROM faces WHERE id=?", (face_id,)).fetchone()
        return np.frombuffer(row[0], dtype=np.float32) if row else None

    def matching_assets(self, query: np.ndarray, threshold: float) -> list[int]:
        # An asset needs one matching face; keep its best score and return all matches.
        ranked: dict[int, float] = {}
        rows = self.db.execute("""SELECT faces.asset_id, faces.vector FROM faces
            JOIN assets ON assets.id=faces.asset_id WHERE assets.status='ready'""")
        for row in rows:
            score = similarity(query, np.frombuffer(row[1], dtype=np.float32))
            if score >= threshold:
                ranked[row[0]] = max(score, ranked.get(row[0], -1.0))
        return sorted(ranked, key=lambda asset_id: ranked[asset_id], reverse=True)

    def asset(self, asset_id: int):
        return self.db.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()

    def save_results(self, user_id: int, asset_ids: list[int],
                     query: np.ndarray | None = None) -> None:
        vector = query.astype(np.float32).tobytes() if query is not None else None
        with self.db:
            self.db.execute("""INSERT INTO sessions (user_id, results, next_result, query_vector)
                VALUES (?, ?, 0, ?) ON CONFLICT(user_id) DO UPDATE SET
                results=excluded.results, next_result=0,
                query_vector=COALESCE(excluded.query_vector, sessions.query_vector)""",
                            (user_id, json.dumps(asset_ids), vector))

    def query_vector(self, user_id: int) -> np.ndarray | None:
        row = self.db.execute("SELECT query_vector FROM sessions WHERE user_id=?", (user_id,)).fetchone()
        if not row or row[0] is None:
            return None
        return np.frombuffer(row[0], dtype=np.float32)

    def known_results(self, user_id: int) -> set[int]:
        row = self.db.execute("SELECT results FROM sessions WHERE user_id=?", (user_id,)).fetchone()
        return set(json.loads(row[0])) if row else set()

    def sent_asset_ids(self, user_id: int) -> set[int]:
        rows = self.db.execute("SELECT asset_id FROM sent_assets WHERE user_id=?", (user_id,))
        return {row[0] for row in rows}

    def reset_history(self, user_id: int, clear_query: bool = False) -> None:
        with self.db:
            self.db.execute("DELETE FROM sent_assets WHERE user_id=?", (user_id,))
            self.db.execute("""INSERT INTO sessions (user_id, results, next_result, query_vector)
                VALUES (?, '[]', 0, NULL) ON CONFLICT(user_id) DO UPDATE SET
                results='[]', next_result=0,
                query_vector=CASE WHEN ? THEN NULL ELSE sessions.query_vector END""",
                            (user_id, clear_query))

    def result_page(self, user_id: int, page_size: int = 10) -> tuple[list[int], int]:
        row = self.db.execute("SELECT results, next_result FROM sessions WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return [], 0
        results = json.loads(row[0])
        start = row[1]
        return results[start:start + page_size], max(0, len(results) - start - page_size)

    def advance_results(self, user_id: int, count: int) -> None:
        with self.db:
            self.db.execute("UPDATE sessions SET next_result=next_result+? WHERE user_id=?",
                            (count, user_id))

    def record_sent(self, user_id: int, asset_ids: list[int], advance: bool = False) -> None:
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO sent_assets (user_id, asset_id) VALUES (?, ?)",
                                ((user_id, asset_id) for asset_id in asset_ids))
            if advance:
                self.db.execute("UPDATE sessions SET next_result=next_result+? WHERE user_id=?",
                                (len(asset_ids), user_id))

    def stats(self) -> tuple[int, int, int]:
        row = self.db.execute("""SELECT COUNT(*), SUM(status='ready'), SUM(status='failed')
            FROM assets""").fetchone()
        return row[0], row[1] or 0, row[2] or 0
