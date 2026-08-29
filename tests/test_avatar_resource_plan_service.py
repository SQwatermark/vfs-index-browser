import unittest

from avatar_resource_plan_service import AvatarResourcePlanService


class AvatarResourcePlanServiceTests(unittest.TestCase):
    def test_builds_summary_and_reduces_dump_run_to_public_identity(self):
        asset = {"asset_index": 7, "path": "assets/avatar.asset"}
        avatar_mesh = {
            "name": "hero",
            "mainPrefabPath": "assets/hero.prefab",
            "mainPrefabResolvedPaths": [],
            "slots": [{
                "name": "body",
                "lods": {
                    "0": [{
                        "meshPaths": ["assets/body.mesh"],
                        "materialPaths": [],
                        "backupMaterialPaths": [],
                    }],
                    "1": [],
                    "2": [],
                    "3": [],
                },
            }],
        }
        plan = {"kind": "avatarModelResourcePlan", "lod": 0, "parts": []}
        hash_meta = {"version": 1, "source": "vfs"}
        plan_meta = {
            "dump": {
                "builtAtEpoch": 123,
                "source": {"toolArtifacts": [{"path": "worker", "size": 9}]},
                "private": "not exposed",
            },
            "stringPathHash": hash_meta,
        }

        result = AvatarResourcePlanService().build(
            asset, avatar_mesh, plan, plan_meta
        )

        self.assertEqual("avatarMeshResourcePlan", result["kind"])
        self.assertIs(asset, result["asset"])
        self.assertIs(avatar_mesh, result["avatarMesh"])
        self.assertIs(plan, result["plan"])
        self.assertEqual(1, result["summary"]["slotCount"])
        self.assertEqual(1, result["summary"]["meshCountByLod"]["0"])
        self.assertEqual(123, result["run"]["dump"]["builtAtEpoch"])
        self.assertEqual(
            [{"path": "worker", "size": 9}],
            result["run"]["dump"]["toolArtifacts"],
        )
        self.assertNotIn("private", result["run"]["dump"])
        self.assertIs(hash_meta, result["run"]["stringPathHash"])

    def test_missing_optional_dump_fields_use_stable_empty_values(self):
        avatar_mesh = {"slots": [], "mainPrefabResolvedPaths": []}

        result = AvatarResourcePlanService().build(
            {},
            avatar_mesh,
            {},
            {"dump": {}, "stringPathHash": {}},
        )

        self.assertIsNone(result["run"]["dump"]["builtAtEpoch"])
        self.assertEqual([], result["run"]["dump"]["toolArtifacts"])


if __name__ == "__main__":
    unittest.main()
