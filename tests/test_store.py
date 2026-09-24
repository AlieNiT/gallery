import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
