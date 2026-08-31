import unittest
from unittest.mock import Mock, patch

from avatar_resource_plan_service import (
    AvatarResourcePlanError,
    AvatarResourcePlanService,
)


class AvatarResourcePlanServiceTests(unittest.TestCase):
    def test_builds_sorted_bundle_dependency_closure(self):
        class Index:
            def bundle_dependencies(self, bundle_index):
                return {
                    3: [
                        {"bundleIndex": 1, "name": "shared.ab"},
                        {"bundleIndex": 5, "name": "late.ab"},
                    ],
                    4: [{"bundleIndex": 1, "name": "shared.ab"}],
                }[bundle_index]

        result = AvatarResourcePlanService.bundle_closure(
            Index(),
            {
                "bundles": [
                    {"bundleIndex": 4, "bundleName": "body.ab"},
                    {"bundleIndex": 3, "bundleName": "avatar.ab"},
                ]
            },
        )

        self.assertEqual([1, 3, 4, 5], [item["bundleIndex"] for item in result])
        self.assertEqual("avatar.ab", result[1]["name"])
        self.assertEqual("body.ab", result[2]["name"])

    @patch("avatar_resource_plan_service.build_avatar_mesh_resource_plan")
    @patch("avatar_resource_plan_service.attach_resolved_paths")
    @patch("avatar_resource_plan_service.StringPathHashIndex")
    @patch("avatar_resource_plan_service.parse_avatar_mesh")
    def test_loads_dump_resolves_paths_and_builds_plan(
        self,
        parse_avatar_mesh,
        string_path_hash_index,
        attach_resolved_paths,
        build_plan,
    ):
        index = object()
        asset = {"path": "avatar.asset"}
        bundle_record = {"id": 7}
        bundle_chunk = object()
        cancel_event = object()
        dump_path = Mock()
        dump_path.read_text.return_value = "dump text"
        dump_meta = {"builtAtEpoch": 1}
        path_hash_file = object()
        path_hash_meta = {"builtAtEpoch": 2}
        avatar_mesh = {"name": "hero"}
        plan = {"lod": 3}
        parse_avatar_mesh.return_value = avatar_mesh
        path_index = string_path_hash_index.return_value
        build_plan.return_value = plan
        dump_loader = Mock(return_value=(dump_path, dump_meta))
        path_hash_provider = Mock(return_value=(path_hash_file, path_hash_meta))

        result = AvatarResourcePlanService(
            dump_loader,
            path_hash_provider,
        ).load(
            index,
            asset,
            bundle_record,
            bundle_chunk,
            3,
            cancel_event=cancel_event,
        )

        dump_loader.assert_called_once_with(
            bundle_record,
            bundle_chunk,
            asset,
            cancel_event=cancel_event,
        )
        dump_path.read_text.assert_called_once_with(
            encoding="utf-8",
            errors="replace",
        )
        parse_avatar_mesh.assert_called_once_with("dump text")
        string_path_hash_index.assert_called_once_with(path_hash_file)
        attach_resolved_paths.assert_called_once_with(avatar_mesh, path_index)
        build_plan.assert_called_once_with(index, avatar_mesh, lod=3)
        self.assertEqual(
            (
                avatar_mesh,
                plan,
                {"dump": dump_meta, "stringPathHash": path_hash_meta},
            ),
            result,
        )

    def test_builds_document_from_resolved_source(self):
        index = object()
        asset = {
            "path": (
                "assets/beyond/dynamicassets/gameplay/npc/avatarmesh/actor/"
                "data_npc_avatarmesh_hero.asset"
            )
        }
        bundle_record = {"id": 7}
        bundle_chunk = object()
        avatar_mesh = {"slots": [], "mainPrefabResolvedPaths": []}
        plan = {"parts": []}
        plan_meta = {"dump": {}, "stringPathHash": {}}
        calls = []

        def load(*args):
            calls.append(args)
            return avatar_mesh, plan, plan_meta

        result = AvatarResourcePlanService(plan_loader=load).build_from_resolved(
            (index, asset, bundle_record, bundle_chunk),
            2,
        )

        self.assertEqual(
            [(index, asset, bundle_record, bundle_chunk, 2)],
            calls,
        )
        self.assertEqual("avatarMeshResourcePlan", result["kind"])
        self.assertIs(plan, result["plan"])

    def test_rejects_non_avatar_asset_before_loading(self):
        calls = []
        service = AvatarResourcePlanService(
            plan_loader=lambda *args: calls.append(args)
        )

        with self.assertRaisesRegex(AvatarResourcePlanError, "not an NPC AvatarMesh"):
            service.build_from_resolved(
                (object(), {"path": "assets/hero.prefab"}, {}, object()),
                0,
            )

        self.assertEqual([], calls)

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
