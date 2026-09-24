"""OpenCV YuNet detection and SFace descriptors."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps


@dataclass
class Face:
    vector: np.ndarray
    thumbnail: bytes


def similarity(left: np.ndarray, right: np.ndarray) -> float:
    """Cosine similarity for normalized or unnormalized SFace vectors."""
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    return float(np.dot(left, right) / denominator) if denominator else 0.0


class FaceEngine:
    def __init__(self, model_dir: Path):
        detector_path = model_dir / "face_detection_yunet_2023mar.onnx"
        recognizer_path = model_dir / "face_recognition_sface_2021dec.onnx"
        for path in (detector_path, recognizer_path):
            if not path.is_file():
                raise FileNotFoundError(f"Missing {path}; run python -m gallery.models first")
        self.detector = cv2.FaceDetectorYN.create(str(detector_path), "", (320, 320), score_threshold=0.8)
        self.recognizer = cv2.FaceRecognizerSF.create(str(recognizer_path), "")

    def extract(self, image_bytes: bytes) -> list[Face]:
        # Pillow applies EXIF orientation before OpenCV sees the image.
        with Image.open(BytesIO(image_bytes)) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((2000, 2000))
            rgb = np.asarray(image)
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        height, width = bgr.shape[:2]
        self.detector.setInputSize((width, height))
        _, detections = self.detector.detect(bgr)
        if detections is None:
            return []
        faces = []
        for detection in detections:
            aligned = self.recognizer.alignCrop(bgr, detection)
            vector = self.recognizer.feature(aligned).flatten().astype(np.float32)
            x, y, w, h = (int(value) for value in detection[:4])
            padding = int(max(w, h) * 0.25)
            crop = image.crop((max(0, x - padding), max(0, y - padding),
                               min(width, x + w + padding), min(height, y + h + padding)))
            crop.thumbnail((160, 160))
            output = BytesIO()
            crop.save(output, format="JPEG", quality=80)
            faces.append(Face(vector=vector, thumbnail=output.getvalue()))
        return faces
