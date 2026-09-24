import tempfile
import unittest
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

from gallery.faces import Face
from gallery.web import create_app


def image_bytes(color):
    output = BytesIO()
    Image.new("RGB", (24, 24), color).save(output, format="JPEG")
    return output.getvalue()


class FakeEngine:
    def extract(self, image):
        with Image.open(BytesIO(image)) as photo:
            red, green, blue = photo.getpixel((12, 12))
        if max(red, green, blue) < 20:
            return []
        vector = np.array([1, 0] if red > blue else [0, 1], dtype=np.float32)
        return [Face(vector, image_bytes((red, green, blue)))]


class WebAppTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.app = create_app(data_dir=Path(self.temporary.name), engine=FakeEngine())
        self.client = self.app.test_client()
        self.headers = {"X-Gallery-Key": self.app.extensions["gallery_api_key"]}

    def tearDown(self):
        self.temporary.cleanup()

    def upload(self, data, name="event.jpg"):
        return self.client.post("/api/upload", headers=self.headers,
                                data={"photo": (BytesIO(data), name)})

    def test_upload_search_and_fallback(self):
        red = image_bytes((240, 20, 20))
        blue = image_bytes((20, 20, 240))
        first = self.upload(red, "red.jpg")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json["photo"]["face_count"], 1)
        self.assertEqual(self.upload(blue, "blue.jpg").status_code, 201)
        self.assertEqual(self.upload(red).status_code, 200)

        library = self.client.get("/api/library").json
        self.assertEqual(library["stats"], {"photos": 2, "faces": 2, "groups": 2})
        self.assertEqual(len(library["recent"]), 2)
        photo_id = first.json["photo"]["id"]
        with self.client.get(f"/api/photos/{photo_id}/original") as original:
            self.assertEqual(original.data, red)
        with self.client.get(f"/api/photos/{photo_id}/preview") as preview:
            self.assertEqual(preview.mimetype, "image/jpeg")

        matches = self.client.post("/api/search", headers=self.headers,
                                   data={"selfie": (BytesIO(red), "selfie.jpg")}).json["photos"]
        self.assertEqual([photo["id"] for photo in matches], [photo_id])
        self.assertEqual(self.client.get("/api/library").json["stats"]["photos"], 2)

        faces = self.client.get("/api/faces").json
        self.assertEqual(faces["total"], 2)
        face_id = faces["faces"][0]["id"]
        self.assertEqual(self.client.get(f"/api/faces/{face_id}/thumbnail").mimetype, "image/jpeg")
        selected = self.client.post(f"/api/search/face/{face_id}", headers=self.headers).json
        self.assertEqual([photo["id"] for photo in selected["photos"]], [photo_id])

    def test_bad_upload_no_face_and_post_protection(self):
        self.assertEqual(self.upload(b"not an image").status_code, 400)
        self.assertEqual(self.client.post("/api/upload", data={"photo": (BytesIO(b"x"), "x.jpg")}).status_code, 403)
        empty = self.client.post("/api/search", headers=self.headers,
                                 data={"selfie": (BytesIO(image_bytes((0, 0, 0))), "dark.jpg")})
        self.assertEqual(empty.status_code, 422)
        self.assertEqual(empty.json["face_count"], 0)
        self.assertEqual(self.client.get("/api/faces?page=-1").status_code, 400)
        self.assertEqual(self.client.get("/api/photos/999/original").status_code, 404)


if __name__ == "__main__":
    unittest.main()
