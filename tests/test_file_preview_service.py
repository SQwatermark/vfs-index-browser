import unittest

from file_preview_service import FilePreviewService


class FilePreviewServiceTests(unittest.TestCase):
    def test_media_preview_does_not_read_payload(self):
        result = FilePreviewService.build(
            {"rawUrl": "/raw"},
            "image.png",
            100,
            lambda _limit: self.fail("media classification must not read payload"),
        )

        self.assertEqual("image", result["kind"])
        self.assertEqual("image/png", result["contentType"])

    def test_text_preview_reports_encoding_and_truncation(self):
        result = FilePreviewService.build(
            {},
            "notes.txt",
            100,
            lambda _limit: "测试".encode("utf-8"),
        )

        self.assertEqual("text", result["kind"])
        self.assertEqual("utf-8-sig", result["encoding"])
        self.assertTrue(result["truncated"])

    def test_binary_preview_is_bounded_hex(self):
        result = FilePreviewService.build(
            {},
            "payload.bin",
            3,
            lambda limit: b"\x00\xffA"[:limit],
        )

        self.assertEqual("hex", result["kind"])
        self.assertIn("00 ff 41", result["hex"])
        self.assertFalse(result["truncated"])


if __name__ == "__main__":
    unittest.main()
