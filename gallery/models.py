"""Download the two model files from the OpenCV Zoo project."""

import hashlib
from pathlib import Path
import os
import requests


BASE = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models"
FILES = {
    "face_detection_yunet_2023mar.onnx": (
        "face_detection_yunet", "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "face_recognition_sface_2021dec.onnx": (
        "face_recognition_sface", "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
}


def main() -> None:
    directory = Path(os.getenv("MODEL_DIR", "models"))
    directory.mkdir(parents=True, exist_ok=True)
    for filename, (group, expected_sha256) in FILES.items():
        target = directory / filename
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == expected_sha256:
            print(f"Exists: {target}")
            continue
        response = requests.get(f"{BASE}/{group}/{filename}", timeout=120, stream=True)
        response.raise_for_status()
        temporary = target.with_suffix(".download")
        digest = hashlib.sha256()
        with temporary.open("wb") as output:
            for chunk in response.iter_content(256 * 1024):
                output.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != expected_sha256:
            temporary.unlink()
            raise RuntimeError(f"Model checksum mismatch: {filename}")
        temporary.replace(target)
        print(f"Downloaded: {target}")


if __name__ == "__main__":
    main()
