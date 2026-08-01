import sqlite3
import tempfile
import unittest
from pathlib import Path

from manifest_index import ManifestIndex
import server


class ManifestAnimationSearchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "manifest.sqlite"
        conn = sqlite3.connect(self.database)
        try:
            conn.executescript("""
                CREATE TABLE bundles (bundle_index INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE assets (
                    asset_index INTEGER PRIMARY KEY, path TEXT, parent TEXT,
                    name TEXT, bundle_index INTEGER, size INTEGER, path_hash TEXT
                );
            """)
            conn.execute("INSERT INTO bundles VALUES (1, 'actor.ab')")
            conn.executemany(
                "INSERT INTO assets VALUES (?, ?, '', ?, 1, 1, '')",
                [
                    (1, "assets/actor/animations/a_actor_01.anim", "a_actor_01.anim"),
                    (2, "assets/actor/animations/a_actor_01.fbx##idle_loop", "idle_loop"),
                    (3, "assets/actor/models/a_actor_01.fbx##body_lod0", "body_lod0"),
                    (4, "assets/actor/animations/aXactorX01.anim", "aXactorX01.anim"),
                ],
            )
            conn.commit()
        finally:
            conn.close()
        self.index = ManifestIndex(self.database)

    def tearDown(self):
        self.temporary.cleanup()

    def test_finds_only_explicit_animation_shapes(self):
        result = self.index.search_animation_assets("actor_01")
        self.assertEqual([1, 2], [item["assetIndex"] for item in result["files"]])

    def test_treats_underscores_as_literal_characters(self):
        result = self.index.search_animation_assets("actor_01")
        self.assertNotIn(4, [item["assetIndex"] for item in result["files"]])

    def test_rejects_invalid_paging_even_for_an_empty_query(self):
        with self.assertRaisesRegex(ValueError, "invalid animation search page"):
            self.index.search_animation_assets("", page_size=0)

    def test_pages_animation_search_results(self):
        first = self.index.search_animation_assets("actor_01", page=1, page_size=1)
        second = self.index.search_animation_assets("actor_01", page=2, page_size=1)

        self.assertEqual((1, 2, 2), (first["page"], first["pages"], first["total"]))
        self.assertEqual([1], [item["assetIndex"] for item in first["files"]])
        self.assertEqual([2], [item["assetIndex"] for item in second["files"]])

    def test_candidate_handler_builds_preview_and_blender_urls(self):
        handler = object.__new__(server.BrowserHandler)
        payloads = []
        model_asset = {
            "asset_index": 9,
            "path": "assets/actors/chr_0004_pelica_postmodel.prefab",
        }
        handler.resolve_manifest_asset_source = lambda _query: (
            self.index,
            model_asset,
            {},
            Path("source.chk"),
        )
        handler.send_json = lambda value, **_kwargs: payloads.append(value)
        handler.send_error_json = lambda status, message: self.fail(
            f"unexpected HTTP {status}: {message}"
        )

        handler.handle_manifest_asset_model_animations(
            {"manifestId": ["12"], "assetIndex": ["9"], "q": ["actor_01"]}
        )

        self.assertEqual("pelica", payloads[0]["defaultQuery"])
        self.assertEqual(2, payloads[0]["total"])
        self.assertIn("animationAssetIndex=1", payloads[0]["files"][0]["previewUrl"])
        self.assertIn("animationAssetIndex=1", payloads[0]["files"][0]["blendUrl"])

    def test_candidate_handler_uses_production_animation_urls(self):
        handler = object.__new__(server.BrowserHandler)
        payloads = []
        model_asset = {
            "asset_index": 9,
            "path": "assets/actors/chr_0004_pelica_postmodel.prefab",
        }
        handler.resolve_manifest_asset_source = lambda _query: (
            self.index,
            model_asset,
            {},
            Path("source.chk"),
        )
        handler.send_json = lambda value, **_kwargs: payloads.append(value)
        handler.send_error_json = lambda status, message: self.fail(
            f"unexpected HTTP {status}: {message}"
        )

        handler.handle_manifest_asset_model_animations(
            {
                "manifestId": ["12"],
                "assetIndex": ["9"],
                "q": ["actor_01"],
            }
        )

        self.assertNotIn("humanoid", payloads[0]["files"][0]["previewUrl"])
        self.assertIn(
            f"v={server.MODEL_ANIMATION_CACHE_REVISION}",
            payloads[0]["files"][0]["previewUrl"],
        )


if __name__ == "__main__":
    unittest.main()
