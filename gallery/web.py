"""Browser interface for uploading and searching a local event photo library."""

from __future__ import annotations

import hmac
import os
import secrets
from io import BytesIO
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file
from PIL import UnidentifiedImageError
from werkzeug.exceptions import RequestEntityTooLarge

from .faces import FaceEngine
from .local_store import LocalStore


MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def create_app(data_dir: Path | None = None, model_dir: Path | None = None,
               engine: FaceEngine | None = None) -> Flask:
    data_dir = data_dir or Path(os.getenv("DATA_DIR", "data")) / "local"
    model_dir = model_dir or Path(os.getenv("MODEL_DIR", "models"))
    engine = engine or FaceEngine(model_dir)
    store = LocalStore(data_dir, engine)
    api_key = secrets.token_urlsafe(32)

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    app.config["TRUSTED_HOSTS"] = ["localhost", "127.0.0.1"]
    app.extensions["gallery_store"] = store
    app.extensions["gallery_api_key"] = api_key

    @app.before_request
    def require_local_key():
        if request.method == "POST" and request.path.startswith("/api/"):
            supplied = request.headers.get("X-Gallery-Key", "")
            if not hmac.compare_digest(supplied, api_key):
                abort(403)

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob: data:; "
            "script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'"
        )
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def upload_too_large(_error):
        return jsonify(error="This image is over 50 MB. Choose a smaller file."), 413

    @app.get("/")
    def index():
        return render_template("index.html", api_key=api_key)

    @app.get("/api/library")
    def library():
        return jsonify(stats=store.stats(), recent=store.recent_photos())

    @app.post("/api/upload")
    def upload():
        incoming = request.files.get("photo")
        if incoming is None or not incoming.filename:
            return jsonify(error="Choose a photo to upload."), 400
        try:
            photo = store.add_photo(incoming.read(), incoming.filename)
        except (ValueError, UnidentifiedImageError, OSError):
            return jsonify(error="I couldn't read that file. Use a JPEG, PNG, or WebP image under 50 MB."), 400
        return jsonify(photo=photo), 200 if photo["duplicate"] else 201

    @app.post("/api/search")
    def search_selfie():
        incoming = request.files.get("selfie")
        if incoming is None:
            return jsonify(error="Choose one clear selfie first."), 400
        try:
            faces = engine.extract(incoming.read())
        except (ValueError, UnidentifiedImageError, OSError):
            return jsonify(error="I couldn't read that selfie. Try a JPEG, PNG, or WebP photo."), 400
        if len(faces) != 1:
            return jsonify(error="Use a selfie with exactly one clear face, or browse detected faces below.",
                           face_count=len(faces)), 422
        threshold = float(os.getenv("MATCH_THRESHOLD", "0.45"))
        return jsonify(photos=store.search(faces[0].vector, threshold))

    @app.post("/api/search/face/<int:face_id>")
    def search_face(face_id: int):
        vector = store.face_vector(face_id)
        if vector is None:
            abort(404)
        threshold = float(os.getenv("MATCH_THRESHOLD", "0.45"))
        return jsonify(photos=store.search(vector, threshold))

    @app.get("/api/faces")
    def faces():
        try:
            page = int(request.args.get("page", "0"))
        except ValueError:
            return jsonify(error="Page must be a number."), 400
        if page < 0:
            return jsonify(error="Page must be zero or greater."), 400
        return jsonify(store.face_page(page, all_faces=request.args.get("all") == "1"))

    @app.get("/api/faces/<int:face_id>/thumbnail")
    def face_thumbnail(face_id: int):
        thumbnail = store.face_thumbnail(face_id)
        if thumbnail is None:
            abort(404)
        return send_file(BytesIO(thumbnail), mimetype="image/jpeg")

    @app.get("/api/photos/<int:photo_id>/<kind>")
    def photo_file(photo_id: int, kind: str):
        if kind not in {"preview", "original"}:
            abort(404)
        result = store.photo_file(photo_id, preview=kind == "preview")
        if result is None:
            abort(404)
        path, mime_type = result
        return send_file(path, mimetype=mime_type, conditional=True)

    return app


def main() -> None:
    app = create_app()
    port = int(os.getenv("PORT", "8765"))
    print(f"Open http://127.0.0.1:{port} in your browser")
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
