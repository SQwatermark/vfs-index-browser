import sqlite3
import tempfile
import unittest
from pathlib import Path

from manifest_index import ManifestIndex
from npc_avatar_resources import build_avatar_mesh_resource_plan


class AvatarMeshResourcePlanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        path = Path(self.temporary.name) / "manifest.sqlite"
        conn = sqlite3.connect(path)
        try:
            conn.executescript("""
                CREATE TABLE bundles (bundle_index INTEGER PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE assets (
                    asset_index INTEGER PRIMARY KEY, path TEXT NOT NULL,
                    parent TEXT NOT NULL, name TEXT NOT NULL,
                    bundle_index INTEGER NOT NULL, size INTEGER NOT NULL,
                    path_hash TEXT NOT NULL
                );
            """)
            conn.executemany(
                "INSERT INTO bundles VALUES (?, ?)",
                [(1, "body.ab"), (2, "character.ab"), (3, "materials.ab")],
            )
            conn.executemany(
                "INSERT INTO assets VALUES (?, ?, '', ?, ?, 1, '')",
                [
                    (10, "Assets/Npc/Body.asset", "Body.asset", 1),
                    (11, "Assets/Npc/Character.fbx##Eyes", "Eyes", 2),
                    (12, "Assets/Npc/Character.fbx##CharacterAvatar", "CharacterAvatar", 2),
                    (20, "Assets/Npc/Body.mat", "Body.mat", 3),
                    (21, "Assets/Npc/Eyes.mat", "Eyes.mat", 3),
                ],
            )
            conn.commit()
        finally:
            conn.close()
        self.index = ManifestIndex(path)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def avatar_mesh(mesh_paths=None):
        return {
            "name": "sample",
            "mainPrefabPath": "Assets/Npc/sample.prefab",
            "slots": [
                {
                    "name": "body",
                    "partType": 2,
                    "lods": {
                        "0": [
                            {
                                "meshName": "Body",
                                "meshPaths": mesh_paths or ["assets/npc/body.asset"],
                                "materialPaths": [
                                    {"paths": ["Assets/Npc/Body.mat"]},
                                    {"paths": ["Assets/Npc/Body.mat"]},
                                ],
                                "rootBoneName": "Pelvis",
                            },
                            {
                                "meshName": "Eyes",
                                "meshPaths": ["assets/npc/character.fbx##eyes"],
                                "materialPaths": [{"paths": ["Assets/Npc/Eyes.mat"]}],
                                "isRendererDisabled": True,
                            },
                        ]
                    },
                }
            ],
        }

    def test_resolves_ordered_resources_and_infers_avatar(self):
        plan = build_avatar_mesh_resource_plan(self.index, self.avatar_mesh())

        self.assertEqual("avatarMeshResources", plan["kind"])
        self.assertEqual(12, plan["avatarAsset"]["assetIndex"])
        self.assertEqual([20, 20], [
            asset["assetIndex"] for asset in plan["parts"][0]["materialAssets"]
        ])
        self.assertFalse(plan["parts"][1]["active"])
        self.assertEqual([1, 2, 3], [bundle["bundleIndex"] for bundle in plan["bundles"]])

    def test_uses_only_manifest_candidate_from_hash_collision(self):
        plan = build_avatar_mesh_resource_plan(
            self.index,
            self.avatar_mesh(["Assets/Unknown.asset", "Assets/Npc/Body.asset"]),
        )
        self.assertEqual(10, plan["parts"][0]["meshAsset"]["assetIndex"])

    def test_rejects_missing_resource(self):
        with self.assertRaisesRegex(ValueError, "absent from manifest"):
            build_avatar_mesh_resource_plan(
                self.index,
                self.avatar_mesh(["Assets/Npc/Missing.asset"]),
            )

    def test_rejects_ambiguous_resource(self):
        with self.index._connect() as conn:
            conn.execute(
                "INSERT INTO assets VALUES (13, 'assets/npc/body.asset', '', 'body', 2, 1, '')"
            )
            conn.commit()
        with self.assertRaisesRegex(ValueError, "ambiguous in manifest"):
            build_avatar_mesh_resource_plan(self.index, self.avatar_mesh())

    def test_rejects_multiple_avatar_sources(self):
        value = self.avatar_mesh()
        value["slots"][0]["lods"]["0"][0]["meshPaths"] = [
            "Assets/Npc/Other.fbx##Body"
        ]
        with self.index._connect() as conn:
            conn.execute(
                "INSERT INTO assets VALUES (14, 'Assets/Npc/Other.fbx##Body', '', 'body', 2, 1, '')"
            )
            conn.commit()
        with self.assertRaisesRegex(ValueError, "multiple FBX sources"):
            build_avatar_mesh_resource_plan(self.index, value)


if __name__ == "__main__":
    unittest.main()
