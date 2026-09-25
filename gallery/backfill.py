"""One-time backfill from a Telegram Desktop single-channel JSON export."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

from .faces import FaceEngine
from .store import Store


MAX_EXPORTED_IMAGE = 100 * 1024 * 1024
MAX_ALBUM_DOCUMENT = 50 * 1024 * 1024
MAX_ALBUM_PHOTO = 10 * 1024 * 1024


def exported_posts(export_file: Path, channel_id: int):
    """Return (message ID, Unix time, image path, media type) for this channel."""
    root = export_file.resolve().parent
    with export_file.open(encoding="utf-8") as handle:
        export = json.load(handle)
    if not isinstance(export, dict) or export.get("type") not in {"private_channel", "public_channel"}:
        raise ValueError("Expected a single Telegram Desktop channel export (result.json)")
    bare_id = -channel_id - 10**12
    if channel_id >= 0 or export.get("id") != bare_id:
        raise ValueError("Export channel ID does not match CHANNEL_ID in .env")
    messages = export.get("messages")
    if not isinstance(messages, list):
        raise ValueError("Export has no messages array")
    posts = []
    for message in messages:
        if not isinstance(message, dict) or message.get("type") != "message":
            continue
        photo = message.get("photo")
        document = message.get("file")
        mime_type = message.get("mime_type", "")
        relative = photo if isinstance(photo, str) else (
            document if isinstance(document, str) and isinstance(mime_type, str)
            and mime_type.startswith("image/") else None)
        if not relative:
            continue
        message_id = message.get("id")
        try:
            posted_at = int(message["date_unixtime"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"Image post {message_id} has no valid date_unixtime") from None
        if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id <= 0:
            raise ValueError("Image post has no valid message ID")
        relative_path = Path(relative)
        if relative_path.is_absolute():
            raise ValueError(f"Image post {message_id} has an absolute media path")
        path = (root / relative_path).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"Image post {message_id} points outside the export folder")
        posts.append((message_id, posted_at, path, "photo" if isinstance(photo, str) else "document"))
    return posts


def retain_album_media(path: Path, data_dir: Path, channel_id: int,
                       message_id: int, media_type: str) -> tuple[str, str] | None:
    """Keep a private copy so an old post can be uploaded in a search-result album."""
    size = path.stat().st_size
    if size > MAX_ALBUM_DOCUMENT:
        return None  # The original channel post can still be copied individually.
    if media_type == "photo" and size > MAX_ALBUM_PHOTO:
        media_type = "document"
    destination_dir = data_dir / "album-media"
    destination_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = f"{abs(channel_id)}_{message_id}{path.suffix.lower()}"
    destination = destination_dir / name
    if not destination.is_file() or destination.stat().st_size != size:
        with tempfile.NamedTemporaryFile(dir=destination_dir, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            try:
                with path.open("rb") as source:
                    shutil.copyfileobj(source, temporary)
                temporary.flush()
                os.fsync(temporary.fileno())
                os.replace(temporary_path, destination)
            finally:
                temporary_path.unlink(missing_ok=True)
    return name, media_type


def backfill(export_file: Path, channel_id: int, data_dir: Path, model_dir: Path,
             dry_run: bool = False) -> tuple[int, int, int]:
    posts = exported_posts(export_file, channel_id)
    store = Store(data_dir / "gallery.sqlite3")
    try:
        todo = [(message_id, posted_at, path, media_type)
                for message_id, posted_at, path, media_type in posts
                if not store.has_ready_asset(channel_id, message_id)]
        if dry_run:
            print(f"Export contains {len(posts)} image posts; {len(todo)} are not indexed yet.")
            print(f"Media files present: {sum(path.is_file() for _, _, path, _ in posts)}/{len(posts)}")
            return 0, len(posts) - len(todo), 0
        engine = FaceEngine(model_dir) if todo else None
        imported = 0
        failed = 0
        retained_count = 0
        for message_id, posted_at, path, media_type in posts:
            try:
                if not path.is_file():
                    raise FileNotFoundError("media not downloaded in export")
                if path.stat().st_size > MAX_EXPORTED_IMAGE:
                    raise ValueError("exported image exceeds 100 MB")
                if not store.has_ready_asset(channel_id, message_id):
                    faces = engine.extract(path.read_bytes())
                    if store.import_exported_asset(channel_id, message_id, posted_at, faces):
                        imported += 1
                        print(f"Indexed post {message_id}: {len(faces)} faces")
                retained = retain_album_media(path, data_dir, channel_id, message_id, media_type)
                if retained:
                    store.attach_album_media(channel_id, message_id, *retained)
                    retained_count += 1
            except Exception as error:
                failed += 1
                print(f"Could not prepare post {message_id}: {error}")
        print(f"Done: {imported} indexed, {len(posts) - len(todo)} already indexed, "
              f"{retained_count} album media ready, {failed} failed.")
        return imported, len(posts) - len(todo), failed
    finally:
        store.db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path, help="result.json from Telegram Desktop's Export chat history")
    parser.add_argument("--dry-run", action="store_true", help="check channel and media files without indexing")
    args = parser.parse_args()
    _, _, failed = backfill(args.export, int(os.environ["CHANNEL_ID"]),
                            Path(os.getenv("DATA_DIR", "data")),
                            Path(os.getenv("MODEL_DIR", "models")), args.dry_run)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
