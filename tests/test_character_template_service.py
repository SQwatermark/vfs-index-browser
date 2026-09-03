import unittest
from pathlib import Path
from unittest.mock import Mock

from character_template_service import CharacterTemplateError, CharacterTemplateService, CHARACTER_ASSET_ROOT
from unity_worker import UnityWorkerError


class CharacterTemplateServiceTests(unittest.TestCase):
    def setUp(self):
        self.index = Mock()
        self.index.assets_by_path.return_value = [{"assetIndex": 7}]
        self.document = {
            "format": "character-template-prefix-v1",
            "decodeStatus": "partial",
            "data": {"id": "chr_0004_pelica"},
            "abilitySystemTailBase64": "AA==",
            "conditionReferences": {"123": {"decodeStatus": "raw", "rawBase64": "AQ=="}},
        }
        self.resolve_index = Mock(return_value=self.index)
        self.resolve_bundle = Mock(return_value=({"path": "asset"}, {"id": 1}, Path("chunk")))
        self.raw = Mock(return_value=(Path("character.dat"), {}))
        self.decode = Mock(return_value=self.document)
        self.service = CharacterTemplateService(
            self.resolve_index, self.resolve_bundle, self.raw, self.decode,
            lambda error: error.code == "worker_not_found",
        )

    def test_exact_asset_and_existing_decoder_preserve_partial_document(self):
        self.assertIs(self.document, self.service.build("chr_0004_pelica"))
        self.index.assets_by_path.assert_called_once_with(f"{CHARACTER_ASSET_ROOT}/data_chr_0004_pelica.asset")
        self.resolve_bundle.assert_called_once_with(self.index, 7)
        self.decode.assert_called_once_with(
            input_path=Path("character.dat"), expected_id="chr_0004_pelica",
            request_id="character-template:chr_0004_pelica",
        )

    def test_manifest_is_sorted_and_rejects_duplicates(self):
        self.index.assets_in_directory.return_value = [
            {"name": "data_chr_0004_pelica.asset"},
            {"name": "data_chr_0001_example.asset"},
            {"name": "data_npc_example.asset"},
        ]
        self.assertEqual([
            {"contentFile": f"/api/endaxis-data/CharacterData/{identity}.runtime-template.json"}
            for identity in ["chr_0001_example", "chr_0004_pelica"]
        ], self.service.manifest())
        self.index.assets_in_directory.assert_called_once_with(CHARACTER_ASSET_ROOT)
        self.index.assets_in_directory.return_value *= 2
        with self.assertRaises(CharacterTemplateError) as caught:
            self.service.manifest()
        self.assertEqual(422, caught.exception.status)

    def test_missing_ambiguous_or_unsafe_identity_never_decodes(self):
        for candidates, identity, status in [
            ([], "chr_0004_pelica", 404),
            ([{"assetIndex": 1}, {"assetIndex": 2}], "chr_0004_pelica", 422),
            ([], "../secret", 400),
        ]:
            with self.subTest(status=status):
                self.index.assets_by_path.return_value = candidates
                with self.assertRaises(CharacterTemplateError) as caught:
                    self.service.build(identity)
                self.assertEqual(status, caught.exception.status)
        self.raw.assert_not_called()
        self.decode.assert_not_called()

    def test_worker_availability_is_not_a_successful_partial_decode(self):
        for code, status in [("worker_not_found", 503), ("unknown_operation", 503),
                             ("character_template_decode_failed", 422)]:
            with self.subTest(code=code):
                self.decode.side_effect = UnityWorkerError(code, "failure")
                with self.assertRaises(CharacterTemplateError) as caught:
                    self.service.build("chr_0004_pelica")
                self.assertEqual(status, caught.exception.status)

    def test_missing_manifest_reports_unavailable(self):
        self.raw.side_effect = RuntimeError("expected one raw MonoBehaviour artifact, found 2")
        with self.assertRaises(CharacterTemplateError) as caught:
            self.service.build("chr_0004_pelica")
        self.assertEqual(422, caught.exception.status)
        self.decode.assert_not_called()
        self.resolve_index.side_effect = FileNotFoundError("manifest missing")
        for operation in [self.service.manifest, lambda: self.service.build("chr_0004_pelica")]:
            with self.assertRaises(CharacterTemplateError) as caught:
                operation()
            self.assertEqual(503, caught.exception.status)
