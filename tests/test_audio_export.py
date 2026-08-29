import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audio_export import VgmstreamConversionService


class VgmstreamConversionServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.executable = self.root / "vgmstream-cli.exe"
        self.executable.write_bytes(b"tool")
        self.wem = self.root / "sample.wem"
        self.wem.write_bytes(b"wem")
        self.wav = self.root / "nested" / "sample.wav"
        self.service = VgmstreamConversionService(self.executable)

    def tearDown(self):
        self.temporary.cleanup()

    def test_publishes_successful_conversion_atomically(self):
        def run(command, **options):
            self.assertEqual(str(self.root), options["cwd"])
            Path(command[2]).write_bytes(b"wav")
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        with patch("audio_export.subprocess.run", side_effect=run):
            result = self.service.ensure_wav(self.wem, self.wav)

        self.assertEqual(self.wav, result)
        self.assertEqual(b"wav", result.read_bytes())
        self.assertEqual([], list(self.wav.parent.glob("*.tmp")))

    def test_reuses_nonempty_cached_wav_without_converter(self):
        self.wav.parent.mkdir(parents=True)
        self.wav.write_bytes(b"cached")

        with patch("audio_export.subprocess.run") as run:
            result = self.service.ensure_wav(self.wem, self.wav)

        self.assertEqual(self.wav, result)
        run.assert_not_called()

    def test_reports_missing_converter(self):
        self.executable.unlink()

        with self.assertRaisesRegex(FileNotFoundError, "vgmstream-cli.exe not found"):
            self.service.ensure_wav(self.wem, self.wav)

    def test_failure_does_not_publish_or_leave_temporary_file(self):
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="bad input")
        with patch("audio_export.subprocess.run", return_value=failed):
            with self.assertRaisesRegex(RuntimeError, "bad input"):
                self.service.ensure_wav(self.wem, self.wav)

        self.assertFalse(self.wav.exists())
        self.assertEqual([], list(self.wav.parent.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
