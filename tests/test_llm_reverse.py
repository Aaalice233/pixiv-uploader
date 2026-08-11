from __future__ import annotations

import base64
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

from pixiv_uploader.pixiv import llm_reverse


class LlmImagePreviewTests(unittest.TestCase):
    def test_image_is_resized_and_encoded_as_jpeg_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "large-transparent.png"
            Image.new("RGBA", (3072, 1024), (10, 20, 30, 128)).save(source)

            data_url = llm_reverse._image_to_data_url(source)

        prefix, encoded = data_url.split(",", 1)
        self.assertEqual(prefix, "data:image/jpeg;base64")
        preview_bytes = base64.b64decode(encoded)
        self.assertLess(len(preview_bytes), 2 * 1024 * 1024)
        with Image.open(BytesIO(preview_bytes)) as preview:
            self.assertEqual(preview.format, "JPEG")
            self.assertEqual(preview.mode, "RGB")
            self.assertEqual(preview.size, (1536, 512))

    def test_file_extension_does_not_control_preview_mime_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "misnamed.png"
            Image.new("RGB", (64, 64), "white").save(source, format="JPEG")

            data_url = llm_reverse._image_to_data_url(source)

        self.assertTrue(data_url.startswith("data:image/jpeg;base64,"))


if __name__ == "__main__":
    unittest.main()
