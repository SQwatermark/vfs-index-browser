import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import urlopen

import server
from projectile_data import (
    ProjectileDecodeError,
    ProjectileNotFoundError,
    load_projectile_export,
    list_projectile_ids,
    normalize_projectile_id,
    projectile_asset_path,
    select_projectile_asset,
)


class FakeManifestIndex:
    def __init__(self, matches):
        self.matches = matches
        self.paths = []

    def assets_by_path(self, path):
        self.paths.append(path)
        return self.matches

    def assets_in_directory(self, path):
        self.paths.append(path)
        return self.matches


class ProjectileDataTests(unittest.TestCase):
    def test_builds_exact_manifest_path_from_projectile_id(self):
        projectile_id = normalize_projectile_id(
            "  projectile_chr_0030_zhuangfy_attack_sword_1  "
        )

        self.assertEqual(
            "assets/beyond/dynamicassets/gamedata/projectile/"
            "data_projectile_chr_0030_zhuangfy_attack_sword_1.asset",
            projectile_asset_path(projectile_id),
        )

    def test_rejects_path_syntax_in_projectile_id(self):
        for value in ("", "../projectile_x", "projectile-x", "投射物"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_projectile_id(value)

    def test_selects_one_exact_manifest_asset(self):
        expected = {"assetIndex": 149277}
        index = FakeManifestIndex([expected])

        selected = select_projectile_asset(
            index,
            "projectile_chr_0030_zhuangfy_attack_sword_1",
        )

        self.assertIs(expected, selected)
        self.assertEqual(1, len(index.paths))
        self.assertTrue(index.paths[0].endswith("_sword_1.asset"))

    def test_reports_missing_and_duplicate_manifest_assets(self):
        with self.assertRaises(ProjectileNotFoundError):
            select_projectile_asset(FakeManifestIndex([]), "projectile_missing")
        with self.assertRaises(ProjectileDecodeError):
            select_projectile_asset(
                FakeManifestIndex([{"assetIndex": 1}, {"assetIndex": 2}]),
                "projectile_duplicate",
            )

    def test_lists_only_canonical_projectile_assets(self):
        index = FakeManifestIndex(
            [
                {"name": "data_projectile_b.asset"},
                {"name": "README.txt"},
                {"name": "data_projectile_a.asset"},
            ]
        )

        self.assertEqual(
            ["projectile_a", "projectile_b"],
            list_projectile_ids(index),
        )

    def test_loads_nested_component_and_owning_unity_object(self):
        projectile_id = "projectile_chr_0030_zhuangfy_attack_sword_1"
        unity_object = {
            "$animestudio": {"pathId": 41},
            "references": {
                "RefIds": [
                    {
                        "rid": 7,
                        "data": {
                            "$decoded": True,
                            "$partial": True,
                            "layout": "Beyond.Gameplay.Core.ProjectileComponentData",
                            "id": projectile_id,
                            "finishOnReach": True,
                        },
                    }
                ]
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "MonoBehaviour" / "projectile.json"
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps(unity_object), encoding="utf-8")

            parsed = load_projectile_export(
                root,
                ["MonoBehaviour/projectile.json"],
                projectile_id,
            )

        self.assertTrue(parsed["idMatchesRequest"])
        self.assertEqual("/references/RefIds/0/data", parsed["componentPointer"])
        self.assertEqual(41, parsed["unityObject"]["$animestudio"]["pathId"])
        self.assertTrue(parsed["component"]["finishOnReach"])

    def test_rejects_ambiguous_component_exports(self):
        component = {
            "layout": "Beyond.Gameplay.Core.ProjectileComponentData",
            "id": "projectile_duplicate",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "duplicate.json"
            target.write_text(json.dumps([component, component]), encoding="utf-8")
            with self.assertRaises(ProjectileDecodeError):
                load_projectile_export(
                    root,
                    ["duplicate.json"],
                    "projectile_duplicate",
                )


class ProjectileServerTests(unittest.TestCase):
    def test_build_document_uses_manifest_bundle_and_json_export(self):
        projectile_id = "projectile_chr_0030_zhuangfy_attack_sword_1"
        manifest_record = {
            "id": 451359,
            "source": "Persistent",
            "logical_id": server.MANIFEST_LOGICAL_ID,
        }
        bundle_record = {
            "id": 123,
            "source": "StreamingAssets",
            "logical_id": "Bundle/Data/Bundles/Windows/main/example.ab",
        }
        asset = {
            "asset_index": 149277,
            "path": projectile_asset_path(projectile_id),
            "path_hash": "0c163ed620c34550",
            "size": 521,
            "bundle_index": 27293,
            "bundle_name": "main/1c6d472666ce218a2b47735d.ab",
        }
        index = FakeManifestIndex([{"assetIndex": 149277}])
        handler = object.__new__(server.BrowserHandler)
        handler.resolve_logical_file_source = lambda logical_id: (
            manifest_record,
            Path("manifest.chk"),
        )
        handler.manifest_index = lambda _record, _path: index
        handler.resolve_index_asset_bundle = lambda _index, _asset_index: (
            asset,
            bundle_record,
            Path("bundle.chk"),
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            export_root = root / "exported"
            target = export_root / "MonoBehaviour" / "projectile.json"
            target.parent.mkdir(parents=True)
            target.write_text(
                json.dumps(
                    {
                        "references": {
                            "data": {
                                "$decoded": True,
                                "$partial": True,
                                "layout": "Beyond.Gameplay.Core.ProjectileComponentData",
                                "id": projectile_id,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            dump_path = root / "dump.txt"
            export_options = []

            def ensure_export(_record, _chunk, _asset, **options):
                export_options.append(options)
                return (
                    dump_path,
                    {"exportedFiles": ["MonoBehaviour/projectile.json"]},
                )

            handler.ensure_manifest_monobehaviour_dump = ensure_export

            payload = handler.build_projectile_document(projectile_id)

        self.assertEqual(1, payload["apiVersion"])
        self.assertEqual(149277, payload["source"]["asset"]["assetIndex"])
        self.assertEqual("partial", payload["decode"]["status"])
        self.assertEqual(
            projectile_id,
            payload["projectileComponentData"]["id"],
        )
        self.assertEqual(
            [{"export_type": "JSON", "filter_container": False}],
            export_options,
        )

    def test_handler_maps_validation_and_lookup_errors(self):
        handler = object.__new__(server.BrowserHandler)
        responses = []
        handler.send_error_json = lambda status, message: responses.append((status, message))

        handler.handle_projectile({"projectileId": ["../bad"]})
        self.assertEqual(400, responses[-1][0])

        def missing(_projectile_id):
            raise ProjectileNotFoundError("missing")

        handler.build_projectile_document = missing
        handler.handle_projectile({"projectileId": ["projectile_missing"]})
        self.assertEqual((404, "missing"), responses[-1])

    def test_http_route_returns_projectile_document(self):
        projectile_id = "projectile_chr_0030_zhuangfy_attack_sword_1"

        class StubProjectileHandler(server.BrowserHandler):
            def log_message(self, _format, *_args):
                pass

            def build_projectile_document(self, requested_id):
                return {
                    "apiVersion": 1,
                    "projectileId": requested_id,
                    "projectileComponentData": {"id": requested_id},
                }

        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), StubProjectileHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(
                f"http://127.0.0.1:{httpd.server_port}/api/projectile"
                f"?projectileId={projectile_id}",
                timeout=5,
            ) as response:
                payload = json.load(response)
            self.assertEqual(200, response.status)
            self.assertEqual(projectile_id, payload["projectileId"])
            self.assertEqual(
                projectile_id,
                payload["projectileComponentData"]["id"],
            )
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
