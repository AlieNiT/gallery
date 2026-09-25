import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gallery.bot import GalleryBot, Telegram, TransientTelegramError, main as bot_main
from gallery.check import main as check_main


class TelegramTransportTest(unittest.TestCase):
    def test_server_error_is_transient_and_does_not_reveal_token(self):
        telegram = Telegram("sensitive-token")
        telegram.session.post = Mock(return_value=SimpleNamespace(status_code=503))
        with self.assertRaises(TransientTelegramError) as caught:
            telegram.call("getFile", {"file_id": "example"})
        self.assertNotIn("sensitive-token", str(caught.exception))

    def test_invalid_json_is_transient(self):
        telegram = Telegram("sensitive-token")
        response = SimpleNamespace(status_code=200, json=Mock(side_effect=ValueError("bad JSON")))
        telegram.session.post = Mock(return_value=response)
        with self.assertRaises(TransientTelegramError):
            telegram.call("getUpdates")

    def test_multipart_retry_rewinds_files(self):
        telegram = Telegram("sensitive-token")
        uploaded = []
        def post(_url, data, files, timeout):
            uploaded.append(files["image"][1].read())
            if len(uploaded) == 1:
                return SimpleNamespace(status_code=429,
                                       json=lambda: {"parameters": {"retry_after": 1}})
            return SimpleNamespace(status_code=200, json=lambda: {"ok": True, "result": []})
        telegram.session.post = post
        with io.BytesIO(b"complete-image") as image, patch("gallery.bot.time.sleep"):
            telegram.call("sendMediaGroup", {"chat_id": 42}, {"image": ("image.jpg", image)})
        self.assertEqual(uploaded, [b"complete-image", b"complete-image"])


class DeploymentCheckTest(unittest.TestCase):
    @patch("gallery.bot.GalleryBot")
    def test_bot_reads_extra_allowed_user_ids_from_environment(self, bot_class):
        with patch.dict(os.environ, {"BOT_TOKEN": "secret", "CHANNEL_ID": "-100123",
                                  "EXTRA_ALLOWED_USER_IDS": "42,43"}):
            bot_main()
        self.assertEqual(bot_class.call_args.args[-1], frozenset({42, 43}))
        bot_class.return_value.run.assert_called_once_with()

    @patch("gallery.check.Telegram")
    def test_check_preserves_pending_updates_when_removing_webhook(self, telegram_class):
        telegram = telegram_class.return_value
        telegram.call.side_effect = [
            {"url": "https://old-webhook.invalid"},
            True,
            {"id": 42, "username": "eventbot"},
            {"id": -100123, "type": "channel", "title": "Our event"},
            {"status": "administrator"},
        ]
        output = io.StringIO()
        with patch.dict(os.environ, {"BOT_TOKEN": "secret", "CHANNEL_ID": "-100123"}), \
                patch("sys.argv", ["gallery.check", "--remove-webhook"]), \
                contextlib.redirect_stdout(output):
            check_main()
        self.assertEqual(telegram.call.call_args_list[1].args,
                         ("deleteWebhook", {"drop_pending_updates": "false"}))
        self.assertIn("Ready:", output.getvalue())
        self.assertNotIn("secret", output.getvalue())

    @patch("gallery.check.Telegram")
    def test_webhook_only_does_not_require_channel_id(self, telegram_class):
        telegram_class.return_value.call.side_effect = [
            {"url": "https://old-webhook.invalid"}, True,
        ]
        with patch.dict(os.environ, {"BOT_TOKEN": "secret", "CHANNEL_ID": "0"}), \
                patch("sys.argv", ["gallery.check", "--webhook-only", "--remove-webhook"]), \
                contextlib.redirect_stdout(io.StringIO()):
            check_main()
        self.assertEqual(telegram_class.return_value.call.call_count, 2)

    def test_existing_webhook_blocks_bot_start(self):
        with tempfile.TemporaryDirectory() as directory, patch("gallery.bot.FaceEngine"):
            bot = GalleryBot("secret", -100123, Path(directory), Path(directory), 0.45)
            bot.tg.call = Mock(return_value={"url": "https://old-webhook.invalid"})
            with self.assertRaisesRegex(RuntimeError, "webhook"):
                bot.run()
            bot.store.db.close()


if __name__ == "__main__":
    unittest.main()
