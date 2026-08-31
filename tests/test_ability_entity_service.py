import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ability_entity_data import AbilityEntityUnavailableError
from ability_entity_service import AbilityEntityService


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


if __name__ == "__main__":
    unittest.main()
