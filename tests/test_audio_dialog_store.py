import sqlite3
import unittest

from audio_dialog_index import AudioMediaEntry, build_audio_dialog_index
from audio_dialog_store import (
    create_audio_dialog_schema,
    get_audio_dialog_entry,
    list_audio_dialog_directory,
    replace_audio_dialog_language,
)


class AudioDialogStoreTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_replaces_language_and_builds_directory_summary(self):
        payload = {
            "1": {"path": "story/chapter1/line_001.wav"},
            "2": {"path": "story/chapter1/line_002.wav"},
            "3": {"path": "archive/line_003.wav"},
        }
        preliminary = build_audio_dialog_index(payload, "cn", [])
        media = [
            AudioMediaEntry(
                preliminary[0].record.media_id,
                pck_file_id=7,
                offset=100,
                size=20,
                source="sound",
                language="chinese",
            )
        ]
        matches = build_audio_dialog_index(payload, "chinese", media)

        replace_audio_dialog_language(self.conn, matches)

        root = list_audio_dialog_directory(self.conn, "cn")
        self.assertEqual(3, root["summary"]["file_count"])
        self.assertEqual(1, root["summary"]["matched_count"])
        self.assertEqual(2, root["summary"]["missing_count"])
        self.assertEqual(["archive", "story"], [
            item["name"] for item in root["directories"]
        ])
        chapter = list_audio_dialog_directory(
            self.conn,
            "chinese",
            "story/chapter1",
            limit=1,
        )
        self.assertEqual(2, chapter["page"]["total"])
        self.assertEqual(["line_001.wav"], [
            item["name"] for item in chapter["files"]
        ])

    def test_preserves_duplicate_logical_paths_and_physical_candidates(self):
        payload = {
            "10": {"path": "shared/line.wav"},
            "11": {"path": "shared/line.wav"},
        }
        records = build_audio_dialog_index(payload, "en", [])
        media = [
            AudioMediaEntry(
                records[0].record.media_id,
                pck_file_id=1,
                offset=50,
                size=10,
                source="sound",
                language="english",
            )
        ]
        replace_audio_dialog_language(
            self.conn,
            build_audio_dialog_index(payload, "english", media),
        )

        entries = get_audio_dialog_entry(self.conn, "en", "shared/line.wav")

        self.assertEqual([10, 11], [entry["dialog_key"] for entry in entries])
        self.assertTrue(all(len(entry["media"]) == 1 for entry in entries))
        self.assertEqual(1, self.conn.execute(
            "SELECT COUNT(*) FROM audio_media"
        ).fetchone()[0])

    def test_replacing_one_language_keeps_another_language(self):
        for language in ("chinese", "japanese"):
            payload = {"1": {"path": f"{language}/line.wav"}}
            replace_audio_dialog_language(
                self.conn,
                build_audio_dialog_index(payload, language, []),
            )

        replacement = {"2": {"path": "new/line.wav"}}
        replace_audio_dialog_language(
            self.conn,
            build_audio_dialog_index(replacement, "cn", []),
        )

        languages = self.conn.execute(
            "SELECT language, COUNT(*) FROM audio_dialog GROUP BY language ORDER BY language"
        ).fetchall()
        self.assertEqual([("chinese", 1), ("japanese", 1)], languages)

    def test_rejects_unknown_schema_version(self):
        self.conn.executescript(
            """
            CREATE TABLE audio_index_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO audio_index_meta VALUES ('schema_version', '99');
            """
        )

        with self.assertRaisesRegex(RuntimeError, "rebuild with schema 1"):
            create_audio_dialog_schema(self.conn)


if __name__ == "__main__":
    unittest.main()
