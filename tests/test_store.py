import tempfile
import unittest
import sqlite3
from pathlib import Path

import numpy as np

from gallery.faces import Face
from gallery.store import Store


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name) / "gallery.sqlite3")

    def tearDown(self):
        self.store.db.close()
        self.temporary.cleanup()

    def test_duplicate_post_and_matching_asset(self):
        self.store.queue_asset(-100, 1, "first", "photo", 100)
        self.store.queue_asset(-100, 1, "replacement", "photo", 100)
        asset = self.store.pending_asset()
        self.assertEqual(asset["file_id"], "first")
        self.store.save_faces(asset["id"], [
            Face(np.array([1, 0], dtype=np.float32), b"thumb"),
            Face(np.array([0, 1], dtype=np.float32), b"thumb"),
        ])
        self.assertEqual(self.store.matching_assets(np.array([1, 0], dtype=np.float32), 0.45),
                         [asset["id"]])
        self.assertEqual(self.store.matching_assets(np.array([-1, 0], dtype=np.float32), 0.45), [])
        self.assertEqual(self.store.face_page(0)[0], 2)
        self.assertEqual(self.store.face_page(0, groups_only=False)[0], 2)

    def test_result_pagination_and_offset(self):
        self.store.save_results(42, list(range(23)))
        self.assertEqual(self.store.result_page(42), (list(range(10)), 13))
        self.store.advance_results(42, 10)
        self.assertEqual(self.store.result_page(42), (list(range(10, 20)), 3))
        self.store.advance_results(42, 10)
        self.assertEqual(self.store.result_page(42), (list(range(20, 23)), 0))
        self.store.advance_results(42, 3)
        self.assertEqual(self.store.result_page(42), ([], 0))
        self.store.set_offset(123)
        self.assertEqual(self.store.get_offset(), 123)

    def test_existing_database_gains_album_columns(self):
        path = Path(self.temporary.name) / "old.sqlite3"
        with sqlite3.connect(path) as db:
            db.execute("""CREATE TABLE assets (
                id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL, file_id TEXT NOT NULL,
                media_type TEXT NOT NULL, posted_at INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', error TEXT,
                UNIQUE(channel_id, message_id))""")
            db.execute("""CREATE TABLE sessions (
                user_id INTEGER PRIMARY KEY,
                results TEXT NOT NULL DEFAULT '[]',
                next_result INTEGER NOT NULL DEFAULT 0)""")
        migrated = Store(path)
        migrated.queue_asset(-100, 7, "file-id", "photo", 100)
        asset = migrated.pending_asset()
        self.assertIsNone(asset["album_name"])
        self.assertIsNone(asset["album_type"])
        self.assertIsNone(asset["album_file_id"])
        self.assertIsNone(migrated.query_vector(42))
        migrated.save_results(42, [asset["id"]], np.array([1, 0], dtype=np.float32))
        np.testing.assert_array_equal(migrated.query_vector(42), [1, 0])
        migrated.db.close()

    def test_query_and_sent_history_survive_reopen_and_reset(self):
        self.store.save_results(42, [1, 2], np.array([1, 0], dtype=np.float32))
        self.store.record_sent(42, [1], advance=True)
        self.assertEqual(self.store.result_page(42), ([2], 0))
        path = Path(self.temporary.name) / "gallery.sqlite3"
        reopened = Store(path)
        self.assertEqual(reopened.sent_asset_ids(42), {1})
        self.assertEqual(reopened.known_results(42), {1, 2})
        np.testing.assert_array_equal(reopened.query_vector(42), [1, 0])
        reopened.reset_history(42)
        self.assertEqual(reopened.sent_asset_ids(42), set())
        self.assertEqual(reopened.known_results(42), set())
        np.testing.assert_array_equal(reopened.query_vector(42), [1, 0])
        reopened.reset_history(42, clear_query=True)
        self.assertIsNone(reopened.query_vector(42))
        reopened.db.close()


if __name__ == "__main__":
    unittest.main()
