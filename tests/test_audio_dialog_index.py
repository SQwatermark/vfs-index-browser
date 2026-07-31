import unittest

from audio_dialog_index import (
    AudioDialogFormatError,
    AudioDialogRecord,
    AudioMediaEntry,
    build_audio_dialog_index,
    build_audio_dialog_index_from_packages,
    build_audio_dialog_records,
    endfield_fnv1_64,
    hash_audio_dialog_path,
    map_audio_dialog_records,
    media_entries_from_audio_package_meta,
    parse_audio_dialog,
)


class AudioDialogParsingTests(unittest.TestCase):
    def test_parses_numeric_keys_and_normalizes_path_separators(self):
        entries = parse_audio_dialog({
            "20": {"path": r"v1d0\story\line_020.wav", "speakerChannel": "npc"},
            "-3": {"path": "v1d0/story/line_003.wav"},
        })

        self.assertEqual([-3, 20], [entry.dialog_key for entry in entries])
        self.assertEqual("v1d0/story/line_003.wav", entries[0].logical_path)
        self.assertEqual("v1d0/story/line_020.wav", entries[1].logical_path)

    def test_rejects_unknown_top_level_or_row_shapes(self):
        invalid_payloads = [
            [],
            {"1": "v1d0/story/line.wav"},
            {"not-an-integer": {"path": "v1d0/story/line.wav"}},
            {"1": {}},
            {"1": {"path": ""}},
            {"1": {"path": "../line.wav"}},
            {"1": {"path": "v1d0//line.wav"}},
            {"1": {"path": "C:/voice/line.wav"}},
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(AudioDialogFormatError):
                    parse_audio_dialog(payload)

    def test_rejects_duplicate_numeric_keys_with_different_spelling(self):
        with self.assertRaisesRegex(AudioDialogFormatError, "duplicate numeric key"):
            parse_audio_dialog({
                "1": {"path": "v1d0/story/one.wav"},
                "01": {"path": "v1d0/story/two.wav"},
            })


class EndfieldAudioHashTests(unittest.TestCase):
    def test_matches_known_endfield_fnv1_vectors(self):
        vectors = {
            b"": 0xCBF29CE484222325,
            b"voice/chinese/v1d0/test.wav": 0x01960C886262D6DA,
            b"voice/english/v2d10/story/line_001.wav": 0x3B12354B33C325A1,
        }

        for payload, expected in vectors.items():
            with self.subTest(payload=payload):
                self.assertEqual(expected, endfield_fnv1_64(payload))

    def test_builds_lowercase_language_qualified_hash_input(self):
        hash_input, media_id = hash_audio_dialog_path(r"V1D0\Story\Line.WAV", "CN")

        self.assertEqual("voice/chinese/v1d0/story/line.wav", hash_input)
        self.assertEqual(endfield_fnv1_64(hash_input.encode("utf-8")), media_id)

    def test_rejects_unknown_languages(self):
        with self.assertRaisesRegex(AudioDialogFormatError, "unsupported audio language"):
            hash_audio_dialog_path("v1d0/story/line.wav", "unknown")


class AudioDialogMappingTests(unittest.TestCase):
    PAYLOAD = {
        "100": {"path": "v1d0/story/line_100.wav"},
        "200": {"path": "v1d0/story/line_200.wav"},
    }

    def test_reports_matched_and_missing_entries(self):
        records = build_audio_dialog_records(self.PAYLOAD, "chinese")
        media = [
            AudioMediaEntry(
                media_id=records[0].media_id,
                pck_file_id=7,
                offset=128,
                size=4096,
                source="sound",
                language="cn",
            )
        ]

        matches = build_audio_dialog_index(self.PAYLOAD, "CN", media)

        self.assertEqual(["matched", "missing"], [match.status for match in matches])
        self.assertEqual((7, 128, 4096), (
            matches[0].media_entries[0].pck_file_id,
            matches[0].media_entries[0].offset,
            matches[0].media_entries[0].size,
        ))
        self.assertEqual(
            {
                "dialog_key": 100,
                "language": "chinese",
                "logical_path": "v1d0/story/line_100.wav",
                "normalized_hash_input": records[0].normalized_hash_input,
                "media_id": records[0].media_id_hex,
                "match_status": "matched",
                "media_match_count": 1,
            },
            matches[0].sqlite_record(),
        )
        self.assertEqual(f"{records[0].media_id:016x}", media[0].sqlite_record()["media_id"])

    def test_reports_ambiguous_physical_media_without_discarding_candidates(self):
        record = build_audio_dialog_records({"1": {"path": "v1d0/story/line.wav"}}, "jp")[0]
        media = [
            AudioMediaEntry(record.media_id, 9, 200, 80, "sound", "japanese"),
            AudioMediaEntry(record.media_id, 4, 100, 90, "bank", "jp", bank_id=55),
        ]

        match = map_audio_dialog_records([record], media)[0]

        self.assertEqual("ambiguous", match.status)
        self.assertEqual([4, 9], [entry.pck_file_id for entry in match.media_entries])

    def test_ignores_same_media_id_from_another_language(self):
        record = build_audio_dialog_records({"1": {"path": "v1d0/story/line.wav"}}, "kr")[0]
        media = [
            AudioMediaEntry(record.media_id, 1, 0, 10, "sound", "chinese"),
            AudioMediaEntry(record.media_id, 2, 0, 10, "sound", "korean"),
        ]

        match = map_audio_dialog_records([record], media)[0]

        self.assertEqual("matched", match.status)
        self.assertEqual(2, match.media_entries[0].pck_file_id)

    def test_reports_hash_collision_before_media_cardinality(self):
        records = [
            AudioDialogRecord(1, "chinese", "one.wav", "voice/chinese/one.wav", 99),
            AudioDialogRecord(2, "chinese", "two.wav", "voice/chinese/two.wav", 99),
        ]
        media = [AudioMediaEntry(99, 1, 0, 10, "sound", "chinese")]

        matches = map_audio_dialog_records(records, media)

        self.assertEqual(["collision", "collision"], [match.status for match in matches])
        self.assertTrue(all(match.media_match_count == 1 for match in matches))

    def test_duplicate_dialog_references_are_not_hash_collisions(self):
        records = build_audio_dialog_records({
            "1": {"path": "v1d0/story/shared.wav"},
            "2": {"path": "v1d0/story/shared.wav"},
        }, "english")
        media = [AudioMediaEntry(records[0].media_id, 1, 0, 10, "sound", "en")]

        matches = map_audio_dialog_records(records, media)

        self.assertEqual(["matched", "matched"], [match.status for match in matches])

    def test_rejects_zero_length_media(self):
        with self.assertRaisesRegex(
            AudioDialogFormatError,
            "size must be a positive signed 64-bit integer",
        ):
            AudioMediaEntry(1, 1, 0, 0, "sound")

    def test_serializes_unsigned_media_id_as_sqlite_safe_hex_text(self):
        media = AudioMediaEntry(0xFEDCBA9876543210, 1, 0, 10, "sound")

        self.assertEqual("fedcba9876543210", media.sqlite_record()["media_id"])

    def test_adapts_existing_audio_package_metadata(self):
        record = build_audio_dialog_records(
            {"1": {"path": "v1d0/story/line.wav"}},
            "chinese",
        )[0]
        package = {
            "version": 1,
            "entryCount": 1,
            "entries": [{
                "id": record.media_id,
                "offset": 128,
                "size": 64,
                "source": "bank",
                "language": "Chinese",
                "bankId": 10,
                "bankOffset": 100,
                "bankSize": 200,
                "bankWemOffset": 28,
                "bankEncrypted": True,
            }],
        }

        entries = media_entries_from_audio_package_meta(45, package)
        matches = build_audio_dialog_index_from_packages(
            {"1": {"path": "v1d0/story/line.wav"}},
            "cn",
            [(45, package)],
        )

        self.assertEqual("chinese", entries[0].language)
        self.assertEqual(45, entries[0].pck_file_id)
        self.assertEqual("matched", matches[0].status)

    def test_rejects_inconsistent_audio_package_metadata(self):
        with self.assertRaisesRegex(AudioDialogFormatError, "entryCount"):
            media_entries_from_audio_package_meta(
                1,
                {"version": 1, "entryCount": 2, "entries": []},
            )

    def test_sfx_package_entries_can_match_dialogue_by_package_context(self):
        record = build_audio_dialog_records(
            {"1": {"path": "v1d0/story/line.wav"}},
            "chinese",
        )[0]
        package = {
            "version": 1,
            "entryCount": 1,
            "entries": [
                {
                    "id": record.media_id,
                    "offset": 10,
                    "size": 20,
                    "source": "sound",
                    "language": "sfx",
                },
            ],
        }

        entries = media_entries_from_audio_package_meta(45, package)
        matches = build_audio_dialog_index_from_packages(
            {"1": {"path": "v1d0/story/line.wav"}},
            "chinese",
            [(45, package)],
        )

        self.assertEqual(["sfx"], [entry.language for entry in entries])
        self.assertEqual("matched", matches[0].status)
        self.assertEqual("sfx", matches[0].media_entries[0].language)


if __name__ == "__main__":
    unittest.main()
