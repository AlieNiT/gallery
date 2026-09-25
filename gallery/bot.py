"""Long-polling Telegram bot for a private event channel."""

from __future__ import annotations

import logging
import json
import mimetypes
import os
import time
from contextlib import ExitStack
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageDraw

from .faces import FaceEngine
from .store import Store


LOG = logging.getLogger(__name__)
MAX_DOWNLOAD = 20 * 1024 * 1024


class TransientTelegramError(RuntimeError):
    """An API or network failure that should be retried without dropping a photo."""


class Telegram:
    def __init__(self, token: str):
        self.root = f"https://api.telegram.org/bot{token}"
        self.file_root = f"https://api.telegram.org/file/bot{token}"
        self.session = requests.Session()

    def call(self, method: str, data=None, files=None):
        for _ in range(3):
            # A rate-limited multipart request must resend the complete files.
            for value in (files or {}).values():
                stream = value[1] if isinstance(value, tuple) else value
                if hasattr(stream, "seek"):
                    stream.seek(0)
            try:
                response = self.session.post(f"{self.root}/{method}", data=data, files=files,
                                             timeout=120 if files else 45)
            except requests.RequestException:
                # Requests exceptions include the token-bearing URL. Never log it.
                raise TransientTelegramError(f"Telegram {method} request failed") from None
            if response.status_code == 429:
                try:
                    retry_after = int(response.json().get("parameters", {}).get("retry_after", 2))
                except (ValueError, TypeError):
                    retry_after = 2
                time.sleep(max(1, min(retry_after, 30)))
                continue
            if response.status_code >= 500:
                raise TransientTelegramError(f"Telegram {method} returned HTTP {response.status_code}")
            if response.status_code >= 400:
                raise RuntimeError(f"Telegram {method} returned HTTP {response.status_code}")
            try:
                payload = response.json()
            except ValueError:
                raise TransientTelegramError(f"Telegram {method} returned invalid JSON") from None
            if not payload.get("ok"):
                raise RuntimeError(f"Telegram {method}: {payload.get('description')}")
            return payload["result"]
        raise TransientTelegramError(f"Telegram {method}: rate limited")

    def download(self, file_id: str) -> bytes:
        info = self.call("getFile", {"file_id": file_id})
        if info.get("file_size", 0) > MAX_DOWNLOAD:
            raise ValueError("Telegram cannot download this file (>20 MB)")
        try:
            with self.session.get(f"{self.file_root}/{info['file_path']}", stream=True, timeout=60) as response:
                response.raise_for_status()
                chunks = []
                total = 0
                for chunk in response.iter_content(256 * 1024):
                    total += len(chunk)
                    if total > MAX_DOWNLOAD:
                        raise ValueError("Telegram download exceeded 20 MB")
                    chunks.append(chunk)
        except requests.RequestException:
            raise TransientTelegramError("Telegram file download failed") from None
        return b"".join(chunks)

    def text(self, chat_id: int, message: str, markup=None):
        data = {"chat_id": chat_id, "text": message}
        if markup:
            import json
            data["reply_markup"] = json.dumps(markup)
        return self.call("sendMessage", data)

    def photo(self, chat_id: int, file_id: str, caption: str = ""):
        return self.call("sendPhoto", {"chat_id": chat_id, "photo": file_id, "caption": caption})


class GalleryBot:
    def __init__(self, token: str, channel_id: int, data_dir: Path,
                 model_dir: Path, threshold: float):
        self.tg = Telegram(token)
        self.channel_id = channel_id
        self.data_dir = data_dir
        self.store = Store(data_dir / "gallery.sqlite3")
        self.engine = FaceEngine(model_dir)
        self.threshold = threshold

    def is_member(self, user_id: int) -> bool:
        try:
            member = self.tg.call("getChatMember", {"chat_id": self.channel_id, "user_id": user_id})
            return member["status"] in {"creator", "administrator", "member"} or (
                member["status"] == "restricted" and member.get("is_member", False))
        except (requests.RequestException, RuntimeError) as error:
            LOG.warning("Membership check failed for %s: %s", user_id, error)
            return False

    def handle_channel_post(self, post: dict):
        if post["chat"]["id"] != self.channel_id:
            return
        if post.get("photo"):
            file_id, media_type = post["photo"][-1]["file_id"], "photo"
        elif post.get("document", {}).get("mime_type", "").startswith("image/"):
            file_id, media_type = post["document"]["file_id"], "document"
        else:
            return
        self.store.queue_asset(self.channel_id, post["message_id"], file_id,
                               media_type, post["date"])

    def index_one(self) -> bool:
        asset = self.store.pending_asset()
        if asset is None:
            return False
        try:
            image = self.tg.download(asset["file_id"])
            faces = self.engine.extract(image)
            self.store.save_faces(asset["id"], faces)
            LOG.info("Indexed post %s: %s faces", asset["message_id"], len(faces))
        except TransientTelegramError:
            LOG.warning("Temporary Telegram failure indexing post %s; will retry", asset["message_id"])
            time.sleep(5)
        except Exception as error:
            LOG.exception("Could not index post %s", asset["message_id"])
            self.store.mark_failed(asset["id"], str(error))
        return True

    def album_type(self, asset) -> str | None:
        if asset["media_type"] in {"photo", "document"}:
            return asset["media_type"]
        if asset["media_type"] == "copy" and asset["album_name"] and asset["album_type"]:
            path = self.data_dir / "album-media" / asset["album_name"]
            if asset["album_file_id"] or path.is_file():
                return asset["album_type"]
        return None

    def send_album(self, user_id: int, assets: list) -> None:
        media = []
        files = {}
        with ExitStack() as stack:
            for index, asset in enumerate(assets):
                kind = self.album_type(asset)
                if asset["media_type"] == "copy":
                    if asset["album_file_id"]:
                        media.append({"type": kind, "media": asset["album_file_id"]})
                    else:
                        name = asset["album_name"]
                        path = self.data_dir / "album-media" / name
                        attachment = f"image{index}"
                        files[attachment] = (name, stack.enter_context(path.open("rb")),
                                             mimetypes.guess_type(name)[0] or "application/octet-stream")
                        media.append({"type": kind, "media": f"attach://{attachment}"})
                else:
                    media.append({"type": kind, "media": asset["file_id"]})
            data = {"chat_id": user_id, "media": json.dumps(media)}
            if files:
                sent = self.tg.call("sendMediaGroup", data, files)
            else:
                sent = self.tg.call("sendMediaGroup", data)
            if not isinstance(sent, list) or len(sent) != len(assets):
                raise RuntimeError("Telegram returned an incomplete album")
            for asset, message in zip(assets, sent):
                if asset["media_type"] == "copy" and not asset["album_file_id"]:
                    item = message.get("photo", [{}])[-1] if asset["album_type"] == "photo" else message.get("document", {})
                    if item.get("file_id"):
                        self.store.cache_album_file_id(asset["id"], item["file_id"])

    def send_one_result(self, user_id: int, asset) -> None:
        if asset["media_type"] == "copy":
            kind = self.album_type(asset)
            if kind:
                method = "sendPhoto" if kind == "photo" else "sendDocument"
                field = "photo" if kind == "photo" else "document"
                if asset["album_file_id"]:
                    self.tg.call(method, {"chat_id": user_id, field: asset["album_file_id"]})
                else:
                    name = asset["album_name"]
                    path = self.data_dir / "album-media" / name
                    with path.open("rb") as stream:
                        sent = self.tg.call(method, {"chat_id": user_id},
                                            {field: (name, stream,
                                                     mimetypes.guess_type(name)[0] or "application/octet-stream")})
                    item = sent.get("photo", [{}])[-1] if kind == "photo" else sent.get("document", {})
                    if item.get("file_id"):
                        self.store.cache_album_file_id(asset["id"], item["file_id"])
            else:
                self.tg.call("copyMessage", {"chat_id": user_id, "from_chat_id": asset["channel_id"],
                                             "message_id": asset["message_id"]})
        elif asset["media_type"] == "photo":
            self.tg.photo(user_id, asset["file_id"])
        else:
            self.tg.call("sendDocument", {"chat_id": user_id, "document": asset["file_id"]})

    def send_assets(self, user_id: int, ids: list[int], advance: bool = False) -> None:
        assets = [self.store.asset(asset_id) for asset_id in ids]
        position = 0
        while position < len(assets):
            kind = self.album_type(assets[position])
            end = position + 1
            if kind:
                while end < len(assets) and self.album_type(assets[end]) == kind:
                    end += 1
            if end - position >= 2:
                self.send_album(user_id, assets[position:end])
            else:
                self.send_one_result(user_id, assets[position])
            self.store.record_sent(user_id, ids[position:end], advance=advance)
            position = end
            time.sleep(1.05)

    def send_results(self, user_id: int):
        ids, remaining = self.store.result_page(user_id)
        if not ids:
            self.tg.text(user_id, "No more matching photos. Use /update to check for newly indexed photos, or send another selfie.")
            return
        self.send_assets(user_id, ids, advance=True)
        self.tg.text(user_id, f"{remaining} more photos. Send /more to continue." if remaining else
                     "That's all the matching photos. Use /update later to check for newly indexed photos.")

    def send_updates(self, user_id: int) -> None:
        vector = self.store.query_vector(user_id)
        if vector is None:
            self.tg.text(user_id, "Send a selfie or choose a face with /faces first, then use /update later.")
            return
        known = self.store.known_results(user_id)
        sent = self.store.sent_asset_ids(user_id)
        new_ids = [asset_id for asset_id in self.store.matching_assets(vector, self.threshold)
                   if asset_id not in known and asset_id not in sent]
        if not new_ids:
            self.tg.text(user_id, "No newly indexed matching photos yet. Try /update again later.")
            return
        page = new_ids[:10]
        self.send_assets(user_id, page)
        remaining = len(new_ids) - len(page)
        self.tg.text(user_id, f"{remaining} new matching photos remain. Send /update again." if remaining else
                     "That's all the newly indexed matching photos. Use /update later to check again.")

    def search(self, user_id: int, vector):
        all_ids = self.store.matching_assets(vector, self.threshold)
        sent = self.store.sent_asset_ids(user_id)
        ids = [asset_id for asset_id in all_ids if asset_id not in sent]
        self.store.save_results(user_id, ids, vector)
        if not all_ids:
            self.tg.text(user_id, "No confident matches yet. Try another clear selfie, choose a face with /faces, or use /update later.")
            self.show_faces(user_id, 0)
            return
        if not ids:
            self.tg.text(user_id, "You've already received the matching photos. Use /update later or /reset-history to see them again.")
            return
        self.tg.text(user_id, f"Found {len(ids)} possible photos. Face matching can make mistakes.")
        self.send_results(user_id)

    def show_faces(self, user_id: int, page: int, all_faces: bool = False):
        count, faces = self.store.face_page(page, groups_only=not all_faces)
        if not faces:
            self.tg.text(user_id, "No face groups on this page yet. New channel photos may still be indexing.")
            return
        sheet = Image.new("RGB", (5 * 164, 4 * 190), "white")
        draw = ImageDraw.Draw(sheet)
        buttons = []
        for index, face in enumerate(faces):
            x, y = (index % 5) * 164, (index // 5) * 190
            with Image.open(BytesIO(face["thumbnail"])) as photo:
                sheet.paste(photo.convert("RGB"), (x + 2, y + 2))
            draw.text((x + 4, y + 162), str(index + 1), fill="black")
            if index % 5 == 0:
                buttons.append([])
            buttons[-1].append({"text": str(index + 1), "callback_data": f"s:{face['id']}"})
        nav = []
        prefix = "a" if all_faces else "f"
        if page:
            nav.append({"text": "◀ Previous", "callback_data": f"{prefix}:{page - 1}"})
        if (page + 1) * 20 < count:
            nav.append({"text": "Next ▶", "callback_data": f"{prefix}:{page + 1}"})
        if nav:
            buttons.append(nav)
        buttons.append([{"text": "Face groups" if all_faces else "Every detected face",
                         "callback_data": "f:0" if all_faces else "a:0"}])
        output = BytesIO()
        sheet.save(output, format="JPEG", quality=82)
        import json
        self.tg.call("sendPhoto", {"chat_id": user_id,
                                  "caption": f"{'Faces' if all_faces else 'Face groups'} {page * 20 + 1}–{page * 20 + len(faces)} of {count}. Tap a number.",
                                  "reply_markup": json.dumps({"inline_keyboard": buttons})},
                     {"photo": ("faces.jpg", output.getvalue(), "image/jpeg")})

    def handle_message(self, message: dict):
        if message["chat"]["type"] != "private":
            return
        user_id = message["from"]["id"]
        if not self.is_member(user_id):
            self.tg.text(user_id, "Join the private event channel first, then try again.")
            return
        words = message.get("text", "").split(maxsplit=1)
        command = words[0].split("@", 1)[0] if words else ""
        if command in {"/start", "/help"}:
            self.tg.text(user_id, "Send one clear selfie to find your photos. If that misses, use /faces to choose your face. Use /more for additional results, /update for newly indexed photos, or /reset-history to see sent photos again.")
        elif command == "/faces":
            self.show_faces(user_id, 0)
        elif command == "/allfaces":
            self.show_faces(user_id, 0, all_faces=True)
        elif command == "/more":
            self.send_results(user_id)
        elif command == "/update":
            self.send_updates(user_id)
        elif command == "/reset-history":
            self.store.reset_history(user_id)
            self.tg.text(user_id, "History cleared. Send /update to receive matching photos again, or send a new selfie.")
        elif command == "/status":
            total, ready, failed = self.store.stats()
            self.tg.text(user_id, f"Channel photos: {total}; indexed: {ready}; failed: {failed}.")
        elif message.get("photo") or message.get("document", {}).get("mime_type", "").startswith("image/"):
            self.store.reset_history(user_id, clear_query=True)
            file_id = (message["photo"][-1]["file_id"] if message.get("photo")
                       else message["document"]["file_id"])
            try:
                faces = self.engine.extract(self.tg.download(file_id))
            except Exception:
                LOG.exception("Could not process selfie")
                self.tg.text(user_id, "I couldn't read that image. Try a clear photo under 20 MB, or use /faces.")
                return
            if len(faces) != 1:
                self.tg.text(user_id, "Please send a selfie with exactly one visible face, or use /faces.")
                self.show_faces(user_id, 0)
                return
            self.search(user_id, faces[0].vector)
        else:
            self.tg.text(user_id, "Send a selfie, or use /faces to browse face groups.")

    def handle_callback(self, callback: dict):
        user_id = callback["from"]["id"]
        self.tg.call("answerCallbackQuery", {"callback_query_id": callback["id"]})
        if not self.is_member(user_id):
            self.tg.text(user_id, "Join the private event channel first, then try again.")
            return
        data = callback.get("data", "")
        if data.startswith("f:") and data[2:].isdigit():
            self.show_faces(user_id, int(data[2:]))
        elif data.startswith("a:") and data[2:].isdigit():
            self.show_faces(user_id, int(data[2:]), all_faces=True)
        elif data.startswith("s:") and data[2:].isdigit():
            vector = self.store.face_vector(int(data[2:]))
            if vector is not None:
                self.search(user_id, vector)

    def run(self):
        if self.tg.call("getWebhookInfo").get("url"):
            raise RuntimeError("A Telegram webhook is configured; remove it before using long polling")
        LOG.info("Starting bot for channel %s", self.channel_id)
        while True:
            try:
                # Queue updates durably before doing slower image processing.
                poll_timeout = 1 if self.store.pending_asset() else 20
                updates = self.tg.call("getUpdates", {
                    "offset": self.store.get_offset(), "timeout": poll_timeout, "limit": 100,
                    "allowed_updates": '["message","channel_post","callback_query"]',
                })
                for update in updates:
                    try:
                        if "channel_post" in update:
                            self.handle_channel_post(update["channel_post"])
                        elif "message" in update:
                            self.handle_message(update["message"])
                        elif "callback_query" in update:
                            self.handle_callback(update["callback_query"])
                    except Exception:
                        LOG.exception("Update %s failed", update["update_id"])
                        if "channel_post" in update:
                            break
                    self.store.set_offset(update["update_id"] + 1)
                self.index_one()
            except (requests.RequestException, RuntimeError):
                LOG.exception("Telegram connection failed; retrying")
                time.sleep(5)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.environ["BOT_TOKEN"]
    channel_id = int(os.environ["CHANNEL_ID"])
    data_dir = Path(os.getenv("DATA_DIR", "data"))
    model_dir = Path(os.getenv("MODEL_DIR", "models"))
    threshold = float(os.getenv("MATCH_THRESHOLD", "0.45"))
    if not 0 < threshold < 1:
        raise ValueError("MATCH_THRESHOLD must be between 0 and 1")
    GalleryBot(token, channel_id, data_dir, model_dir, threshold).run()


if __name__ == "__main__":
    main()
