"""Local photo library backed by SQLite and files on disk."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .faces import FaceEngine, similarity


IMAGE_EXTENSIONS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
IMAGE_MIME_TYPES = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


class LocalStore:
    def __init__(self, data_dir: Path, engine: FaceEngine):
        self.data_dir = data_dir
        self.original_dir = data_dir / "originals"
        self.preview_dir = data_dir / "previews"
        self.original_dir.mkdir(parents=True, exist_ok=True)
        self.preview_dir.mkdir(parents=True, exist_ok=True)
        self.database = data_dir / "local.sqlite3"
        self.engine = engine
        self.lock = threading.RLock()
        with self._connect() as connection:
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS photos (
                    id INTEGER PRIMARY KEY,
                    sha256 TEXT NOT NULL UNIQUE,
                    original_name TEXT NOT NULL,
                    stored_name TEXT NOT NULL,
                    preview_name TEXT NOT NULL,
                    captured_at TEXT,
                    uploaded_at TEXT NOT NULL,
                    face_count INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS faces (
                    id INTEGER PRIMARY KEY,
                    photo_id INTEGER NOT NULL REFERENCES photos(id),
                    cluster_id INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    thumbnail BLOB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS faces_photo ON faces(photo_id);
                CREATE INDEX IF NOT EXISTS faces_cluster ON faces(cluster_id);
            """)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _public_photo(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "name": row["original_name"],
            "captured_at": row["captured_at"],
            "uploaded_at": row["uploaded_at"],
            "face_count": row["face_count"],
        }

    @staticmethod
    def _prepare_image(image_bytes: bytes) -> tuple[str, bytes, str | None]:
        with Image.open(BytesIO(image_bytes)) as original:
            extension = IMAGE_EXTENSIONS.get(original.format or "")
            if extension is None:
                raise ValueError("Use a JPEG, PNG, or WebP image.")
            exif = original.getexif()
            raw_date = exif.get(36867) or exif.get(306)
            captured_at = None
            if isinstance(raw_date, str):
                try:
                    captured_at = datetime.strptime(raw_date, "%Y:%m:%d %H:%M:%S").isoformat()
                except ValueError:
                    pass
            preview = ImageOps.exif_transpose(original).convert("RGB")
            preview.thumbnail((1200, 1200))
            output = BytesIO()
            preview.save(output, format="JPEG", quality=84, optimize=True)
        return extension, output.getvalue(), captured_at

    def add_photo(self, image_bytes: bytes, original_name: str) -> dict:
        if not image_bytes:
            raise ValueError("Choose an image to upload.")
        digest = hashlib.sha256(image_bytes).hexdigest()
        with self.lock:
            with self._connect() as connection:
                existing = connection.execute("SELECT * FROM photos WHERE sha256=?", (digest,)).fetchone()
                if existing:
                    return {**self._public_photo(existing), "duplicate": True}

            extension, preview_bytes, captured_at = self._prepare_image(image_bytes)
            faces = self.engine.extract(image_bytes)
            stored_name = digest + extension
            preview_name = digest + ".jpg"
            self.original_dir.joinpath(stored_name).write_bytes(image_bytes)
            self.preview_dir.joinpath(preview_name).write_bytes(preview_bytes)
            uploaded_at = datetime.now().astimezone().isoformat(timespec="seconds")

            with self._connect() as connection:
                cursor = connection.execute("""INSERT INTO photos
                    (sha256, original_name, stored_name, preview_name, captured_at, uploaded_at, face_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (digest, Path(original_name).name[:200] or "Untitled image", stored_name,
                     preview_name, captured_at, uploaded_at, len(faces)))
                photo_id = cursor.lastrowid
                representatives = [
                    (row["id"], np.frombuffer(row["vector"], dtype=np.float32))
                    for row in connection.execute("SELECT id, vector FROM faces WHERE id=cluster_id")
                ]
                for face in faces:
                    cluster_id = None
                    best_score = 0.55
                    for candidate_id, candidate_vector in representatives:
                        score = similarity(face.vector, candidate_vector)
                        if score > best_score:
                            cluster_id, best_score = candidate_id, score
                    face_cursor = connection.execute("""INSERT INTO faces
                        (photo_id, cluster_id, vector, thumbnail) VALUES (?, 0, ?, ?)""",
                        (photo_id, face.vector.tobytes(), face.thumbnail))
                    face_id = face_cursor.lastrowid
                    connection.execute("UPDATE faces SET cluster_id=? WHERE id=?",
                                       (cluster_id or face_id, face_id))
                    if cluster_id is None:
                        representatives.append((face_id, face.vector))
                row = connection.execute("SELECT * FROM photos WHERE id=?", (photo_id,)).fetchone()
            return {**self._public_photo(row), "duplicate": False}

    def stats(self) -> dict:
        with self._connect() as connection:
            photo_count = connection.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
            face_count = connection.execute("SELECT COUNT(*) FROM faces").fetchone()[0]
            group_count = connection.execute("SELECT COUNT(*) FROM faces WHERE id=cluster_id").fetchone()[0]
        return {"photos": photo_count, "faces": face_count, "groups": group_count}

    def recent_photos(self, limit: int = 12) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM photos ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._public_photo(row) for row in rows]

    def photo_file(self, photo_id: int, preview: bool = True) -> tuple[Path, str] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT stored_name, preview_name FROM photos WHERE id=?",
                                     (photo_id,)).fetchone()
        if row is None:
            return None
        if preview:
            return self.preview_dir / row["preview_name"], "image/jpeg"
        original = self.original_dir / row["stored_name"]
        return original, IMAGE_MIME_TYPES[original.suffix]

    def face_thumbnail(self, face_id: int) -> bytes | None:
        with self._connect() as connection:
            row = connection.execute("SELECT thumbnail FROM faces WHERE id=?", (face_id,)).fetchone()
        return row[0] if row else None

    def face_vector(self, face_id: int) -> np.ndarray | None:
        with self._connect() as connection:
            row = connection.execute("SELECT vector FROM faces WHERE id=?", (face_id,)).fetchone()
        return np.frombuffer(row[0], dtype=np.float32) if row else None

    def face_page(self, page: int, all_faces: bool = False, page_size: int = 24) -> dict:
        if page < 0:
            raise ValueError("Page must be zero or greater.")
        filter_sql = "" if all_faces else "WHERE id=cluster_id"
        with self._connect() as connection:
            total = connection.execute(f"SELECT COUNT(*) FROM faces {filter_sql}").fetchone()[0]
            rows = connection.execute(f"""SELECT id FROM faces {filter_sql}
                ORDER BY id LIMIT ? OFFSET ?""", (page_size, page * page_size)).fetchall()
        return {"faces": [{"id": row["id"]} for row in rows], "total": total, "page": page}

    def search(self, vector: np.ndarray, threshold: float = 0.45) -> list[dict]:
        best: dict[int, tuple[float, dict]] = {}
        with self._connect() as connection:
            rows = connection.execute("""SELECT faces.photo_id, faces.vector, photos.*
                FROM faces JOIN photos ON photos.id=faces.photo_id""")
            for row in rows:
                score = similarity(vector, np.frombuffer(row["vector"], dtype=np.float32))
                if score >= threshold and (row["photo_id"] not in best or score > best[row["photo_id"]][0]):
                    best[row["photo_id"]] = (score, self._public_photo(row))
        return [photo for score, photo in sorted(best.values(), key=lambda pair: pair[0], reverse=True)]
