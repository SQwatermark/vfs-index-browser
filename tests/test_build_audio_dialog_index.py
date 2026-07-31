import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from contextlib import redirect_stdout
from pathlib import Path

from audio_dialog_index import build_audio_dialog_records
from tools.build_audio_dialog_index import main


class BuildAudioDialogIndexTests(unittest.TestCase):
    def test_builds_sqlite_from_existing_package_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dialog_path = root / "AudioDialog.json"
            package_path = root / "audio_meta.json"
            output_path = root / "audio.sqlite"
            dialog = {"1": {"path": "story/line.wav"}}
            media_id = build_audio_dialog_records(dialog, "chinese")[0].media_id
            dialog_path.write_text(json.dumps(dialog), encoding="utf-8")
            package_path.write_text(json.dumps({
                "version": 1,
                "entryCount": 1,
                "entries": [{
                    "id": media_id,
                    "offset": 10,
                    "size": 20,
                    "source": "sound",
                    "language": "Chinese",
                }],
            }), encoding="utf-8")

            with redirect_stdout(io.StringIO()):
                result = main([
                    str(dialog_path),
                    str(output_path),
                    "--language", "chinese",
                    "--package", f"42={package_path}",
                ])

            self.assertEqual(0, result)
            with closing(sqlite3.connect(output_path)) as conn:
                row = conn.execute(
                    "SELECT match_status, media_match_count FROM audio_dialog"
                ).fetchone()
                physical = conn.execute(
                    "SELECT pck_file_id, offset, size FROM audio_media"
                ).fetchone()
            self.assertEqual(("matched", 1), row)
            self.assertEqual((42, 10, 20), physical)


if __name__ == "__main__":
    unittest.main()
