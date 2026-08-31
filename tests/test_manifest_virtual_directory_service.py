import unittest
import sqlite3
from pathlib import Path
from unittest.mock import Mock

from manifest_virtual_directory_service import (
    ManifestVirtualDirectoryError,
    ManifestVirtualDirectoryService,
)


class FakeManifestIndex:
    def __init__(self, listing):
        self.listing = listing
        self.calls = []

    def list(self, path, page, page_size):
        self.calls.append((path, page, page_size))
        return self.listing


class ManifestVirtualDirectoryServiceTests(unittest.TestCase):
    def test_assembles_nested_directory_and_asset_links(self):
        index = FakeManifestIndex({
            "directory": {
                "name": "models",
                "file_count": 2,
                "total_bytes": 30,
            },
            "dirs": [{
                "path": "assets/models/child",
                "name": "child",
                "fileCount": 1,
                "totalBytes": 10,
            }],
            "files": [
                {
                    "assetIndex": 7,
                    "path": "assets/models/a.prefab",
                    "name": "a.prefab",
                    "size": 12,
                    "bundleName": "bundle-a",
                },
                {
                    "assetIndex": 8,
                    "path": "assets/models/data.bin",
                    "name": "data.bin",
                    "size": 18,
                    "bundleName": "bundle-b",
                },
            ],
            "filePage": {"page": 2, "pageSize": 10, "pages": 3, "total": 22},
            "meta": {"bundleCount": "4", "assetCount": "22"},
        })

        result = ManifestVirtualDirectoryService().list_directory(
            index,
            scope="effective",
            base_path="BundleManifest/Windows",
            inner_path="assets/models",
            manifest_id=42,
            page=2,
            page_size=10,
        )

        self.assertEqual([("assets/models", 2, 10)], index.calls)
        self.assertEqual(
            "BundleManifest/Windows/__manifest_assets__/assets/models",
            result["path"],
        )
        self.assertEqual("models", result["directory"]["name"])
        self.assertEqual(
            "BundleManifest/Windows/__manifest_assets__/assets/models/child",
            result["dirs"][0]["path"],
        )
        self.assertEqual(
            "/api/manifest-asset/preview?manifestId=42&assetIndex=7",
            result["files"][0]["previewUrl"],
        )
        self.assertEqual(
            "/api/manifest-asset/model?manifestId=42&assetIndex=7",
            result["files"][0]["modelUrl"],
        )
        self.assertNotIn("modelUrl", result["files"][1])
        self.assertEqual(42, result["virtual"]["manifestId"])
        self.assertEqual(22, result["virtual"]["assetCount"])

    def test_root_uses_virtual_directory_name_and_avatar_links(self):
        avatar_path = (
            "assets/beyond/dynamicassets/gameplay/npc/avatarmesh/hero/"
            "data_npc_avatarmesh_hero.asset"
        )
        index = FakeManifestIndex({
            "directory": {"name": "", "file_count": 1, "total_bytes": 9},
            "dirs": [],
            "files": [{
                "assetIndex": 3,
                "path": avatar_path,
                "name": "data_npc_avatarmesh_hero.asset",
                "size": 9,
                "bundleName": "avatar-bundle",
            }],
            "filePage": {"page": 1, "pageSize": 10, "pages": 1, "total": 1},
            "meta": {"bundleCount": "1", "assetCount": "1"},
        })

        result = ManifestVirtualDirectoryService().list_directory(
            index,
            scope="raw",
            base_path="Manifest",
            inner_path="",
            manifest_id=9,
            page=1,
            page_size=10,
        )

        self.assertEqual("Manifest 资源", result["directory"]["name"])
        self.assertEqual(
            "/api/manifest-asset/avatar-plan?manifestId=9&assetIndex=3",
            result["files"][0]["avatarPlanUrl"],
        )
        self.assertEqual(
            "/api/manifest-asset/model?manifestId=9&assetIndex=3&lod=0",
            result["files"][0]["modelUrl"],
        )

    def test_resolves_manifest_entry_and_source_before_listing(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        try:
            connection.execute(
                "CREATE TABLE entries(scope TEXT, parent TEXT, type TEXT, name TEXT, file_id INTEGER)"
            )
            connection.execute(
                "INSERT INTO entries VALUES ('effective', 'bundles', 'file', 'manifest.hgmmap', 7)"
            )
            source = Mock()
            original = {"id": 7, "logical_id": "manifest", "chunk_path": "old"}
            record = {"id": 8}
            chunk = Path("manifest.chk")
            source.find_record.return_value = original
            source.resolve_record.return_value = (record, chunk)
            index = FakeManifestIndex({
                "directory": {"name": "", "file_count": 0, "total_bytes": 0},
                "dirs": [],
                "files": [],
                "filePage": {"page": 1, "pageSize": 10, "pages": 0, "total": 0},
                "meta": {"bundleCount": 2, "assetCount": 3},
            })
            service = ManifestVirtualDirectoryService(source, lambda *_args: index)

            result = service.list_from_vfs(
                connection,
                scope="effective",
                base_path="bundles",
                inner_path="",
                page=1,
                page_size=10,
            )
        finally:
            connection.close()

        self.assertEqual(7, result["virtual"]["manifestId"])
        source.find_record.assert_called_once_with(7, connection=connection)
        source.resolve_record.assert_called_once_with(original, connection=connection)

    def test_missing_manifest_entry_is_domain_error(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        try:
            connection.execute(
                "CREATE TABLE entries(scope TEXT, parent TEXT, type TEXT, name TEXT, file_id INTEGER)"
            )
            service = ManifestVirtualDirectoryService(Mock(), Mock())
            with self.assertRaises(ManifestVirtualDirectoryError) as raised:
                service.list_from_vfs(
                    connection,
                    scope="effective",
                    base_path="bundles",
                    inner_path="",
                    page=1,
                    page_size=10,
                )
        finally:
            connection.close()
        self.assertEqual(404, raised.exception.status)


if __name__ == "__main__":
    unittest.main()
