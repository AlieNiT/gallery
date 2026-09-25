import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np

from gallery.bot import GalleryBot, TransientTelegramError
from gallery.faces import Face


class FakeTelegram:
    def __init__(self):
        self.sent_photos = []
        self.sent_text = []

    def call(self, method, data=None, files=None):
        if method == "getChatMember":
            return {"status": "member"}
        raise AssertionError(f"Unexpected Telegram call: {method}")

    def download(self, file_id):
        return b"image bytes"

    def text(self, chat_id, message, markup=None):
        self.sent_text.append(message)

    def photo(self, chat_id, file_id, caption=""):
        self.sent_photos.append((chat_id, file_id))


class BotFlowTest(unittest.TestCase):
    def test_new_channel_photo_becomes_selfie_result(self):
        face = Face(np.array([1, 0], dtype=np.float32), b"face thumbnail")
        with tempfile.TemporaryDirectory() as directory, patch("gallery.bot.FaceEngine") as engine_class:
            engine_class.return_value.extract.return_value = [face]
            bot = GalleryBot("unused", -100, Path(directory), Path(directory), 0.45)
            bot.tg = FakeTelegram()
            bot.handle_channel_post({"chat": {"id": -100}, "message_id": 7,
                                     "date": 123, "photo": [{"file_id": "small"}, {"file_id": "large"}]})
            self.assertTrue(bot.index_one())
            with patch("gallery.bot.time.sleep"):
                bot.handle_message({"chat": {"type": "private"}, "from": {"id": 42},
                                    "photo": [{"file_id": "selfie"}]})
            self.assertEqual(bot.tg.sent_photos, [(42, "large")])
            self.assertEqual(bot.store.stats(), (1, 1, 0))
            bot.store.db.close()

    def test_temporary_download_failure_keeps_photo_queued(self):
        with tempfile.TemporaryDirectory() as directory, patch("gallery.bot.FaceEngine"):
            bot = GalleryBot("unused", -100, Path(directory), Path(directory), 0.45)
            bot.store.queue_asset(-100, 7, "photo", "photo", 123)
            bot.tg.download = lambda _: (_ for _ in ()).throw(TransientTelegramError("offline"))
            with patch("gallery.bot.time.sleep"):
                bot.index_one()
            self.assertEqual(bot.store.pending_asset()["message_id"], 7)
            self.assertEqual(bot.store.stats(), (1, 0, 0))
            bot.store.db.close()

    def test_matching_photos_are_paged_in_albums(self):
        with tempfile.TemporaryDirectory() as directory, patch("gallery.bot.FaceEngine"):
            bot = GalleryBot("unused", -100, Path(directory), Path(directory), 0.45)
            calls = []
            bot.tg.call = lambda method, data: calls.append((method, data))
            bot.tg.text = lambda *_: None
            for number in range(12):
                bot.store.queue_asset(-100, number + 1, f"photo-{number}", "photo", 100)
                bot.store.save_faces(bot.store.pending_asset()["id"], [])
            ids = [row[0] for row in bot.store.db.execute("SELECT id FROM assets ORDER BY id")]
            bot.store.save_results(42, ids)
            with patch("gallery.bot.time.sleep"):
                bot.send_results(42)
                self.assertEqual(bot.store.result_page(42), (ids[10:], 0))
                bot.send_results(42)
            self.assertEqual([name for name, _ in calls], ["sendMediaGroup", "sendMediaGroup"])
            self.assertEqual([len(json.loads(data["media"])) for _, data in calls], [10, 2])
            bot.store.db.close()

    def test_failed_album_does_not_advance_results(self):
        with tempfile.TemporaryDirectory() as directory, patch("gallery.bot.FaceEngine"):
            bot = GalleryBot("unused", -100, Path(directory), Path(directory), 0.45)
            for number in range(2):
                bot.store.queue_asset(-100, number + 1, f"photo-{number}", "photo", 100)
                bot.store.save_faces(bot.store.pending_asset()["id"], [])
            ids = [row[0] for row in bot.store.db.execute("SELECT id FROM assets ORDER BY id")]
            bot.store.save_results(42, ids)
            bot.tg.call = lambda *_: (_ for _ in ()).throw(TransientTelegramError("offline"))
            with self.assertRaises(TransientTelegramError):
                bot.send_results(42)
            self.assertEqual(bot.store.result_page(42), (ids, 0))
            bot.store.db.close()

    def test_document_albums_are_separate_from_photos(self):
        with tempfile.TemporaryDirectory() as directory, patch("gallery.bot.FaceEngine"):
            bot = GalleryBot("unused", -100, Path(directory), Path(directory), 0.45)
            calls = []
            bot.tg.call = lambda method, data: calls.append((method, data))
            bot.tg.photo = lambda chat_id, file_id: calls.append(("sendPhoto", file_id))
            bot.tg.text = lambda *_: None
            for number, kind in enumerate(["photo", "document", "document", "photo"]):
                bot.store.queue_asset(-100, number + 1, f"file-{number}", kind, 100)
                bot.store.save_faces(bot.store.pending_asset()["id"], [])
            ids = [row[0] for row in bot.store.db.execute("SELECT id FROM assets ORDER BY id")]
            bot.store.save_results(42, ids)
            with patch("gallery.bot.time.sleep"):
                bot.send_results(42)
            self.assertEqual([name for name, _ in calls],
                             ["sendPhoto", "sendMediaGroup", "sendPhoto"])
            self.assertEqual([item["type"] for item in json.loads(calls[1][1]["media"])],
                             ["document", "document"])
            bot.store.db.close()


if __name__ == "__main__":
    unittest.main()
