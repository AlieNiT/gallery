import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from gallery.backfill import backfill, exported_posts
from gallery.bot import GalleryBot
from gallery.faces import Face
from gallery.store import Store


CHANNEL_ID = -1001234567890


class BackfillTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "photos").mkdir()
        (self.root / "photos/one.jpg").write_bytes(b"one")
        (self.root / "photos/two.jpg").write_bytes(b"two")
        self.export = self.root / "result.json"
        self.write_export([
            {"id": 1, "type": "message", "date_unixtime": "100", "photo": "photos/one.jpg"},
            {"id": 2, "type": "message", "date_unixtime": "101", "photo": "photos/two.jpg"},
            {"id": 3, "type": "message", "date_unixtime": "102", "text": "hello"},
        ])

    def tearDown(self):
        self.temporary.cleanup()

    def write_export(self, messages, chat_id=1234567890):
        self.export.write_text(json.dumps({"type": "private_channel", "id": chat_id,
                                           "messages": messages}), encoding="utf-8")

    def test_import_is_idempotent_and_results_form_album(self):
        face = Face(np.array([1, 0], dtype=np.float32), b"thumb")
        data_dir = self.root / "data"
        with patch("gallery.backfill.FaceEngine") as engine_class:
            engine_class.return_value.extract.return_value = [face]
            self.assertEqual(backfill(self.export, CHANNEL_ID, data_dir, self.root), (2, 0, 0))
            self.assertEqual(backfill(self.export, CHANNEL_ID, data_dir, self.root), (0, 2, 0))
            self.assertEqual(engine_class.return_value.extract.call_count, 2)
        self.assertTrue((data_dir / "album-media" / "1001234567890_1.jpg").is_file())
        with patch("gallery.bot.FaceEngine"):
            bot = GalleryBot("unused", CHANNEL_ID, data_dir, self.root, 0.45)
            bot.store.save_results(42, bot.store.matching_assets(face.vector, 0.45))
            calls = []
            def fake_call(method, data, files=None):
                calls.append((method, data, files))
                return [{"photo": [{"file_id": "cached-one"}]},
                        {"photo": [{"file_id": "cached-two"}]}]
            bot.tg.call = fake_call
            bot.tg.text = lambda *_: None
            with patch("gallery.bot.time.sleep"):
                bot.send_results(42)
                bot.store.save_results(42, bot.store.matching_assets(face.vector, 0.45))
                bot.send_results(42)
            self.assertEqual([method for method, _, _ in calls], ["sendMediaGroup"] * 2)
            self.assertEqual(len(calls[0][2]), 2)
            self.assertEqual([item["type"] for item in json.loads(calls[0][1]["media"])],
                             ["photo", "photo"])
            self.assertIsNone(calls[1][2])
            self.assertEqual([item["media"] for item in json.loads(calls[1][1]["media"])],
                             ["cached-one", "cached-two"])
            self.assertEqual(bot.store.result_page(42), ([], 0))
            bot.store.db.close()

    def test_album_media_is_prepared_for_existing_ready_posts(self):
        data_dir = self.root / "data"
        store = Store(data_dir / "gallery.sqlite3")
        store.import_exported_asset(CHANNEL_ID, 1, 100, [])
        store.db.close()
        with patch("gallery.backfill.FaceEngine") as engine_class:
            engine_class.return_value.extract.return_value = []
            self.assertEqual(backfill(self.export, CHANNEL_ID, data_dir, self.root), (1, 1, 0))
            self.assertEqual(engine_class.return_value.extract.call_count, 1)
        store = Store(data_dir / "gallery.sqlite3")
        asset = store.db.execute("SELECT * FROM assets WHERE message_id=1").fetchone()
        self.assertEqual((asset["media_type"], asset["album_type"]), ("copy", "photo"))
        self.assertTrue((data_dir / "album-media" / asset["album_name"]).is_file())
        store.db.close()

    def test_single_backfilled_result_uses_retained_image(self):
        data_dir = self.root / "data"
        store = Store(data_dir / "gallery.sqlite3")
        store.import_exported_asset(CHANNEL_ID, 1, 100, [])
        (data_dir / "album-media").mkdir()
        (data_dir / "album-media" / "one.jpg").write_bytes(b"one")
        store.attach_album_media(CHANNEL_ID, 1, "one.jpg", "photo")
        asset_id = store.db.execute("SELECT id FROM assets WHERE message_id=1").fetchone()[0]
        store.save_results(42, [asset_id])
        store.db.close()
        with patch("gallery.bot.FaceEngine"):
            bot = GalleryBot("unused", CHANNEL_ID, data_dir, self.root, 0.45)
            calls = []
            def fake_call(method, data, files=None):
                calls.append((method, data, files))
                return {"photo": [{"file_id": "cached"}]}
            bot.tg.call = fake_call
            bot.tg.text = lambda *_: None
            with patch("gallery.bot.time.sleep"):
                bot.send_results(42)
                bot.store.save_results(42, [asset_id])
                bot.send_results(42)
            self.assertEqual([method for method, _, _ in calls], ["sendPhoto", "sendPhoto"])
            self.assertIsNotNone(calls[0][2])
            self.assertIsNone(calls[1][2])
            self.assertEqual(calls[1][1]["photo"], "cached")
            bot.store.db.close()

    def test_dry_run_does_not_index(self):
        self.assertEqual(backfill(self.export, CHANNEL_ID, self.root / "data", self.root,
                                  dry_run=True), (0, 0, 0))
        store = Store(self.root / "data/gallery.sqlite3")
        self.assertEqual(store.stats(), (0, 0, 0))
        store.db.close()

    def test_import_repairs_a_failed_live_post(self):
        data_dir = self.root / "data"
        store = Store(data_dir / "gallery.sqlite3")
        store.queue_asset(CHANNEL_ID, 1, "old-file-id", "photo", 100)
        store.mark_failed(store.pending_asset()["id"], "download failed")
        store.db.close()
        with patch("gallery.backfill.FaceEngine") as engine_class:
            engine_class.return_value.extract.return_value = []
            self.assertEqual(backfill(self.export, CHANNEL_ID, data_dir, self.root), (2, 0, 0))
        store = Store(data_dir / "gallery.sqlite3")
        asset = store.db.execute("SELECT * FROM assets WHERE message_id=1").fetchone()
        self.assertEqual((asset["media_type"], asset["status"], asset["error"]),
                         ("copy", "ready", None))
        store.db.close()

    def test_rejects_wrong_channel_and_unsafe_path(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            exported_posts(self.export, CHANNEL_ID - 1)
        self.write_export([{"id": 1, "type": "message", "date_unixtime": "100",
                            "photo": "../secret.jpg"}])
        with self.assertRaisesRegex(ValueError, "outside"):
            exported_posts(self.export, CHANNEL_ID)


if __name__ == "__main__":
    unittest.main()
