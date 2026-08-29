import unittest

from manifest_virtual_directory_service import ManifestVirtualDirectoryService


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


if __name__ == "__main__":
    unittest.main()
