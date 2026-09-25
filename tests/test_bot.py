import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np

from gallery.bot import GalleryBot, TransientTelegramError, parse_extra_allowed_user_ids
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
    @staticmethod
    def matching_photo(bot, message_id: int, face: Face) -> int:
        bot.store.queue_asset(-100, message_id, f"photo-{message_id}", "photo", 100)
        asset = bot.store.pending_asset()
        bot.store.save_faces(asset["id"], [face])
        return asset["id"]

    @staticmethod
    def private_message(user_id: int, **fields):
        return {"chat": {"type": "private"}, "from": {"id": user_id}, **fields}

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

    def test_extra_allowed_user_bypasses_membership_for_messages_and_buttons(self):
        with tempfile.TemporaryDirectory() as directory, patch("gallery.bot.FaceEngine"):
            bot = GalleryBot("unused", -100, Path(directory), Path(directory), 0.45,
                             frozenset({42}))
            checked_users = []
            messages = []
            def fake_call(method, data=None, files=None):
                if method == "getChatMember":
                    checked_users.append(data["user_id"])
                    return {"status": "member" if data["user_id"] == 43 else "left"}
                if method == "answerCallbackQuery":
                    return True
                raise AssertionError(method)
            bot.tg.call = fake_call
            bot.tg.text = lambda _user_id, message: messages.append(message)
            shown = []
            bot.show_faces = lambda user_id, page: shown.append((user_id, page))
            bot.handle_message(self.private_message(42, text="/start"))
            bot.handle_callback({"id": "callback", "from": {"id": 42}, "data": "f:0"})
            bot.handle_message(self.private_message(43, text="/start"))
            bot.handle_message(self.private_message(44, text="/start"))
            self.assertEqual(shown, [(42, 0)])
            self.assertEqual(checked_users, [43, 44])
            self.assertIn("Send one clear selfie", messages[0])
            self.assertIn("Join the private event channel", messages[-1])
            bot.store.db.close()

    def test_extra_allowed_nonmember_can_receive_matching_photo(self):
        face = Face(np.array([1, 0], dtype=np.float32), b"thumb")
        with tempfile.TemporaryDirectory() as directory, patch("gallery.bot.FaceEngine") as engine_class:
            engine_class.return_value.extract.return_value = [face]
            bot = GalleryBot("unused", -100, Path(directory), Path(directory), 0.45,
                             frozenset({42}))
            bot.tg = FakeTelegram()
            bot.tg.call = lambda *_: (_ for _ in ()).throw(AssertionError("Membership must not be checked"))
            self.matching_photo(bot, 1, face)
            with patch("gallery.bot.time.sleep"):
                bot.handle_message(self.private_message(42, photo=[{"file_id": "selfie"}]))
            self.assertEqual(bot.tg.sent_photos, [(42, "photo-1")])
            bot.store.db.close()

    def test_extra_allowed_user_ids_must_be_positive_numeric_ids(self):
        self.assertEqual(parse_extra_allowed_user_ids(""), frozenset())
        self.assertEqual(parse_extra_allowed_user_ids(" 42, 43,42 "), frozenset({42, 43}))
        for invalid in ("0", "-42", "alice", "42,,43", "42,", "۴۲"):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                parse_extra_allowed_user_ids(invalid)

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
            def fake_call(method, data):
                calls.append((method, data))
                return [{} for _ in json.loads(data["media"])]
            bot.tg.call = fake_call
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
            def fake_call(method, data):
                calls.append((method, data))
                return [{} for _ in json.loads(data["media"])]
            bot.tg.call = fake_call
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

    def test_update_sends_only_new_matches_not_old_more_page(self):
        face = Face(np.array([1, 0], dtype=np.float32), b"thumb")
        with tempfile.TemporaryDirectory() as directory, patch("gallery.bot.FaceEngine") as engine_class:
            engine_class.return_value.extract.return_value = [face]
            bot = GalleryBot("unused", -100, Path(directory), Path(directory), 0.45)
            bot.tg = FakeTelegram()
            sent = []
            bot.send_album = lambda _user_id, assets: sent.extend(asset["id"] for asset in assets)
            bot.send_one_result = lambda _user_id, asset: sent.append(asset["id"])
            original = [self.matching_photo(bot, number, face) for number in range(1, 13)]
            with patch("gallery.bot.time.sleep"):
                bot.handle_message(self.private_message(42, photo=[{"file_id": "selfie"}]))
                self.assertEqual(sent, original[:10])
                new_id = self.matching_photo(bot, 13, face)
                bot.handle_message(self.private_message(42, text="/update"))
                self.assertEqual(sent, original[:10] + [new_id])
                bot.handle_message(self.private_message(42, text="/update"))
                self.assertEqual(sent, original[:10] + [new_id])
                bot.handle_message(self.private_message(42, text="/more"))
            self.assertEqual(sent, original[:10] + [new_id] + original[10:])
            self.assertEqual(bot.store.sent_asset_ids(42), set(original + [new_id]))
            self.assertIn("/update", bot.tg.sent_text[-1])
            bot.store.db.close()

    def test_new_selfie_and_reset_history_allow_resending(self):
        face = Face(np.array([1, 0], dtype=np.float32), b"thumb")
        with tempfile.TemporaryDirectory() as directory, patch("gallery.bot.FaceEngine") as engine_class:
            engine_class.return_value.extract.return_value = [face]
            bot = GalleryBot("unused", -100, Path(directory), Path(directory), 0.45)
            bot.tg = FakeTelegram()
            asset_id = self.matching_photo(bot, 1, face)
            with patch("gallery.bot.time.sleep"):
                bot.handle_message(self.private_message(42, photo=[{"file_id": "selfie"}]))
                bot.handle_message(self.private_message(42, text="/update"))
                self.assertEqual(len(bot.tg.sent_photos), 1)
                bot.handle_message(self.private_message(42, text="/reset-history"))
                self.assertEqual(bot.store.sent_asset_ids(42), set())
                bot.handle_message(self.private_message(42, text="/update"))
                self.assertEqual(len(bot.tg.sent_photos), 2)
                bot.handle_message(self.private_message(42, photo=[{"file_id": "new-selfie"}]))
            self.assertEqual(len(bot.tg.sent_photos), 3)
            self.assertEqual(bot.store.sent_asset_ids(42), {asset_id})
            bot.store.db.close()

    def test_invalid_new_selfie_clears_old_query_and_history(self):
        face = Face(np.array([1, 0], dtype=np.float32), b"thumb")
        with tempfile.TemporaryDirectory() as directory, patch("gallery.bot.FaceEngine") as engine_class:
            engine_class.return_value.extract.return_value = [face]
            bot = GalleryBot("unused", -100, Path(directory), Path(directory), 0.45)
            bot.tg = FakeTelegram()
            self.matching_photo(bot, 1, face)
            with patch("gallery.bot.time.sleep"):
                bot.handle_message(self.private_message(42, photo=[{"file_id": "selfie"}]))
            engine_class.return_value.extract.return_value = []
            bot.show_faces = lambda *_: None
            bot.handle_message(self.private_message(42, photo=[{"file_id": "invalid"}]))
            self.assertEqual(bot.store.sent_asset_ids(42), set())
            self.assertIsNone(bot.store.query_vector(42))
            bot.handle_message(self.private_message(42, text="/update"))
            self.assertIn("Send a selfie", bot.tg.sent_text[-1])
            bot.store.db.close()

    def test_sent_history_is_per_user(self):
        face = Face(np.array([1, 0], dtype=np.float32), b"thumb")
        with tempfile.TemporaryDirectory() as directory, patch("gallery.bot.FaceEngine") as engine_class:
            engine_class.return_value.extract.return_value = [face]
            bot = GalleryBot("unused", -100, Path(directory), Path(directory), 0.45)
            bot.tg = FakeTelegram()
            asset_id = self.matching_photo(bot, 1, face)
            with patch("gallery.bot.time.sleep"):
                bot.handle_message(self.private_message(42, photo=[{"file_id": "selfie-42"}]))
                bot.handle_message(self.private_message(43, photo=[{"file_id": "selfie-43"}]))
            self.assertEqual(bot.tg.sent_photos, [(42, "photo-1"), (43, "photo-1")])
            self.assertEqual(bot.store.sent_asset_ids(42), {asset_id})
            self.assertEqual(bot.store.sent_asset_ids(43), {asset_id})
            bot.store.reset_history(42)
            self.assertEqual(bot.store.sent_asset_ids(43), {asset_id})
            bot.store.db.close()

    def test_update_after_no_initial_matches_pages_new_photos(self):
        face = Face(np.array([1, 0], dtype=np.float32), b"thumb")
        with tempfile.TemporaryDirectory() as directory, patch("gallery.bot.FaceEngine") as engine_class:
            engine_class.return_value.extract.return_value = [face]
            bot = GalleryBot("unused", -100, Path(directory), Path(directory), 0.45)
            bot.tg = FakeTelegram()
            bot.show_faces = lambda *_: None
            sent = []
            bot.send_album = lambda _user_id, assets: sent.extend(asset["id"] for asset in assets)
            bot.send_one_result = lambda _user_id, asset: sent.append(asset["id"])
            bot.handle_message(self.private_message(42, photo=[{"file_id": "selfie"}]))
            ids = [self.matching_photo(bot, number, face) for number in range(1, 13)]
            with patch("gallery.bot.time.sleep"):
                bot.handle_message(self.private_message(42, text="/update"))
                self.assertEqual(sent, ids[:10])
                self.assertIn("2 new matching photos remain", bot.tg.sent_text[-1])
                bot.handle_message(self.private_message(42, text="/update"))
            self.assertEqual(sent, ids)
            self.assertEqual(bot.store.sent_asset_ids(42), set(ids))
            bot.store.db.close()

    def test_failed_update_does_not_add_to_sent_history(self):
        face = Face(np.array([1, 0], dtype=np.float32), b"thumb")
        with tempfile.TemporaryDirectory() as directory, patch("gallery.bot.FaceEngine") as engine_class:
            engine_class.return_value.extract.return_value = [face]
            bot = GalleryBot("unused", -100, Path(directory), Path(directory), 0.45)
            bot.tg = FakeTelegram()
            bot.show_faces = lambda *_: None
            bot.handle_message(self.private_message(42, photo=[{"file_id": "selfie"}]))
            asset_id = self.matching_photo(bot, 1, face)
            bot.send_one_result = lambda *_: (_ for _ in ()).throw(TransientTelegramError("offline"))
            with self.assertRaises(TransientTelegramError):
                bot.handle_message(self.private_message(42, text="/update"))
            self.assertEqual(bot.store.sent_asset_ids(42), set())
            bot.send_one_result = lambda *_: None
            with patch("gallery.bot.time.sleep"):
                bot.handle_message(self.private_message(42, text="/update"))
            self.assertEqual(bot.store.sent_asset_ids(42), {asset_id})
            bot.store.db.close()


if __name__ == "__main__":
    unittest.main()
