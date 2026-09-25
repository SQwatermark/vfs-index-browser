import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ability_entity_data import AbilityEntityDecodeError, AbilityEntityUnavailableError
from ability_entity_service import AbilityEntityService
from unity_worker import UnityWorkerError


class FakeIndex:
    def assets_by_path(self, _path):
        return [{"assetIndex": 7}]


class AbilityEntityServiceTests(unittest.TestCase):
    def service(self, *, resolve_manifest=lambda _logical_id: None, **callbacks):
        return AbilityEntityService(
            resolve_manifest,
            callbacks.get("load_index", lambda *_args: FakeIndex()),
            callbacks.get("resolve_bundle", lambda *_args: None),
            callbacks.get("export_raw", lambda *_args: None),
            lambda _error: False,
            manifest_logical_id="manifest.hgmmap",
            decode_template=callbacks.get("decode_template"),
        )

    def test_reports_missing_manifest_as_unavailable(self):
        with self.assertRaisesRegex(
            AbilityEntityUnavailableError,
            "manifest.hgmmap",
        ):
            self.service().build("abilityentity_sample")

    def test_builds_source_identity_around_parser_result(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory) / "entity.raw"
            raw.write_bytes(b"raw")
            asset = {
                "asset_index": 7,
                "path": "assets/data_abilityentity_sample.asset",
                "bundle_name": "main/sample.ab",
            }
            service = self.service(
                resolve_manifest=lambda _logical_id: ({"id": 1}, Path("manifest.chk")),
                resolve_bundle=lambda *_args: (asset, {"id": 2}, Path("bundle.chk")),
                export_raw=lambda *_args: (raw, {"exportedFile": "entity.raw"}),
            )
            with patch(
                "ability_entity_service.parse_ability_entity_template",
                return_value={"id": "abilityentity_sample"},
            ):
                result = service.build("abilityentity_sample")

        self.assertEqual(7, result["source"]["assetIndex"])
        self.assertEqual("entity.raw", result["source"]["rawExport"])
        self.assertEqual(
            "abilityentity_sample",
            result["abilityEntityTemplateData"]["id"],
        )

    def build_with_decoder(self, decoder):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory) / "entity.raw"
            raw.write_bytes(b"raw")
            service = self.service(
                resolve_manifest=lambda _: ({"id": 1}, Path("manifest.chk")),
                resolve_bundle=lambda *_: (
                    {"asset_index": 7, "path": "entity.asset", "bundle_name": "entity.ab"},
                    {"id": 2}, Path("bundle.chk"),
                ),
                export_raw=lambda *_: (raw, {}),
                decode_template=decoder,
            )
            with patch("ability_entity_service.parse_ability_entity_template", return_value={"gameId": "sample"}):
                return service.build("sample")["abilityEntityTemplateData"]

    def test_preserves_full_native_data(self):
        def decode(**arguments):
            self.assertEqual("sample", arguments["expected_id"])
            self.assertTrue(arguments["input_path"].is_file())
            return {"id": "sample", "template": {"componentList": ["3010137844548109171"]}, "components": {}}
        result = self.build_with_decoder(decode)
        self.assertEqual("complete", result["nativeData"]["decodeStatus"])
        self.assertEqual(["3010137844548109171"], result["nativeData"]["template"]["componentList"])

    def test_unsupported_layout_preserves_partial_prefix(self):
        def decode(**_):
            raise UnityWorkerError("ability_entity_template_decode_failed", "unknown component")
        result = self.build_with_decoder(decode)
        self.assertEqual("sample", result["gameId"])
        self.assertEqual({"decodeStatus": "partial", "reason": "unknown component"}, result["nativeData"])

    def test_worker_failure_is_not_a_partial_decode(self):
        def decode(**_):
            raise UnityWorkerError("internal_error", "worker failed")
        with self.assertRaisesRegex(AbilityEntityDecodeError, "worker failed"):
            self.build_with_decoder(decode)

    def test_partial_template_can_preserve_independently_decoded_skill_registration(self):
        bundle = {"allActiveSkillId": ["skill_a"], "normalSkillId": "skill_a"}

        def decode(**arguments):
            if arguments.get("skill_registration_only"):
                return {"id": "sample", "skillDataBundle": bundle}
            raise UnityWorkerError("ability_entity_template_decode_failed", "unknown mount points")

        result = self.build_with_decoder(decode)
        self.assertEqual("partial", result["nativeData"]["decodeStatus"])
        self.assertEqual(bundle, result["skillRegistration"])

    def test_rejects_wrong_identity(self):
        with self.assertRaisesRegex(AbilityEntityDecodeError, "ID"):
            self.build_with_decoder(lambda **_: {"id": "another"})


if __name__ == "__main__":
    unittest.main()
