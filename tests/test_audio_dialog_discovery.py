import sqlite3
import unittest

from audio_dialog_discovery import (
    AUDIO_DIALOG_LOGICAL_ID,
    AudioDialogDiscoveryError,
    classify_language_pck,
    discover_audio_dialog_inputs,
)


class AudioDialogDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(
            """
            CREATE TABLE files (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                block_name TEXT NOT NULL,
                logical_id TEXT NOT NULL,
                source_logical_id TEXT NOT NULL,
                file_name TEXT NOT NULL,
                chunk_path TEXT NOT NULL,
                chunk_exists INTEGER NOT NULL,
                offset INTEGER NOT NULL,
                length INTEGER NOT NULL,
                encrypted INTEGER NOT NULL
            );
            CREATE TABLE entries (
                scope TEXT NOT NULL,
                type TEXT NOT NULL,
                path TEXT NOT NULL,
                file_id INTEGER
            );
            """
        )

    def tearDown(self):
        self.conn.close()

    def add_file(
        self,
        file_id,
        logical_id,
        file_name,
        *,
        source="Persistent",
        block_name="AudioChinese",
        chunk_exists=True,
        effective=True,
    ):
        self.conn.execute(
            """
            INSERT INTO files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                source,
                block_name,
                logical_id,
                f"{source}/{logical_id}",
                file_name,
                f"C:/game/{file_id}.chk",
                int(chunk_exists),
                100,
                200,
                int(file_name.endswith(".bytes")),
            ),
        )
        if effective:
            self.conn.execute(
                "INSERT INTO entries VALUES ('effective', 'file', ?, ?)",
                (logical_id, file_id),
            )

    def test_discovers_tablecfg_and_all_observed_language_package_shapes(self):
        self.add_file(
            1,
            AUDIO_DIALOG_LOGICAL_ID,
            "Data/TableCfg/AudioDialog.bytes",
            block_name="Table",
        )
        package_paths = (
            (2, "Chinese/default_chinese_banks.pck"),
            (3, "Chinese/default_chinese_stream.pck"),
            (4, "English/default_english_banks.pck"),
            (5, "English/default_english_stream.pck"),
            (6, "Japanese/default_japanese_banks.pck"),
            (7, "Japanese/default_japanese_stream_0.pck"),
            (8, "Japanese/default_japanese_stream_1.pck"),
            (9, "Korean/default_korean_banks.pck"),
            (10, "Korean/default_korean_stream_0.pck"),
            (11, "Korean/default_korean_stream_1.pck"),
            (12, "Hotfix/hotfix_chinese.pck"),
        )
        for file_id, suffix in package_paths:
            file_name = f"Data/Audio/PCK/Windows/{suffix}"
            self.add_file(file_id, f"Audio/{file_name}", file_name)

        result = discover_audio_dialog_inputs(self.conn)

        self.assertEqual(1, result.preferred_tablecfg.file_id)
        self.assertEqual(
            ["banks", "stream", "hotfix"],
            [candidate.role for candidate in result.packages["chinese"]],
        )
        self.assertEqual(3, len(result.packages["japanese"]))
        self.assertEqual(3, len(result.packages["korean"]))
        self.assertEqual(0, len(result.unclassified_audio_pcks))

    def test_prefers_effective_tablecfg_without_hiding_other_sources(self):
        self.add_file(
            20,
            AUDIO_DIALOG_LOGICAL_ID,
            "Data/TableCfg/AudioDialog.bytes",
            source="StreamingAssets",
            block_name="Table",
            effective=False,
        )
        self.add_file(
            21,
            AUDIO_DIALOG_LOGICAL_ID,
            "Data/TableCfg/AudioDialog.bytes",
            source="Persistent",
            block_name="Table",
            effective=True,
        )

        result = discover_audio_dialog_inputs(self.conn)

        self.assertEqual([21, 20], [item.file_id for item in result.tablecfg_candidates])

    def test_does_not_guess_unobserved_or_inconsistent_package_names(self):
        names = (
            "Data/Audio/PCK/Windows/Chinese/voice_chinese_stream.pck",
            "Data/Audio/PCK/Windows/Chinese/default_english_stream.pck",
            "Data/Audio/PCK/Windows/Main/default_stream_0.pck",
        )
        for file_id, file_name in enumerate(names, 30):
            self.add_file(file_id, f"Audio/{file_name}", file_name)

        result = discover_audio_dialog_inputs(self.conn)

        self.assertTrue(all(not items for items in result.packages.values()))
        self.assertEqual(list(names), [item.file_name for item in result.unclassified_audio_pcks])

    def test_reports_missing_required_schema(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY)")

        with self.assertRaisesRegex(AudioDialogDiscoveryError, "missing columns"):
            discover_audio_dialog_inputs(conn)


class AudioPackageClassificationTests(unittest.TestCase):
    def test_classifies_strict_observed_paths(self):
        cases = {
            "Data/Audio/PCK/Windows/Chinese/default_chinese_banks.pck": (
                "chinese", "banks"
            ),
            "Data/Audio/PCK/Windows/Japanese/default_japanese_stream_2.pck": (
                "japanese", "stream"
            ),
            "Data/Audio/PCK/Windows/Hotfix/hotfix_korean.pck": (
                "korean", "hotfix"
            ),
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(expected, classify_language_pck(path))

    def test_rejects_case_or_language_mismatch(self):
        paths = (
            "Data/Audio/PCK/Windows/chinese/default_chinese_banks.pck",
            "Data/Audio/PCK/Windows/Chinese/default_english_banks.pck",
            "Data/Audio/PCK/Windows/Hotfix/hotfix_main.pck",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertIsNone(classify_language_pck(path))


if __name__ == "__main__":
    unittest.main()
