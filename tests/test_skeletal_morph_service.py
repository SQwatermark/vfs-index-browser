import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skeletal_morph_service import SkeletalMorphService


class FakeIndex:
    def __init__(self, sidecars, avatars):
        self.sidecars = sidecars
        self.avatars = avatars
        self.path_calls = []
        self.name_calls = []

    def assets_by_path(self, path):
        self.path_calls.append(path)
        return self.sidecars

    def assets_by_name(self, name):
        self.name_calls.append(name)
        return self.avatars.get(name, [])


class SkeletalMorphServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.resolve_calls = []
        self.export_calls = []

    def service(self):
        def resolve(_index, asset_index):
            self.resolve_calls.append(asset_index)
            asset = {
                "asset_index": asset_index,
                "path": f"assets/resolved/{asset_index}.asset",
            }
            return asset, {"id": asset_index}, self.root / f"{asset_index}.chk"

        def export(record, chunk, asset):
            self.export_calls.append((record["id"], chunk.name, asset["asset_index"]))
            target = self.root / f"{asset['asset_index']}.raw"
            target.write_bytes(str(asset["asset_index"]).encode())
            return target, {}

        return SkeletalMorphService(resolve, export)

    def test_resolves_sidecar_and_avatar_then_bakes_stable_source(self):
        index = FakeIndex(
            [{"assetIndex": 10}],
            {
                "avatar-main.asset": [{
                    "assetIndex": 20,
                    "path": "assets/skeletalmorph/skeletalmorphcfg/avatar-main.asset",
                }],
                "avatar-face.asset": [],
            },
        )
        baked = {"kind": "animation"}

        with (
            patch(
                "skeletal_morph_service.morph_clip_asset_path",
                return_value="assets/clip-sidecar.asset",
            ),
            patch(
                "skeletal_morph_service.morph_avatar_asset_names",
                return_value=("avatar-main.asset", "avatar-face.asset"),
            ),
            patch("skeletal_morph_service.parse_morph_clip", return_value="clip"),
            patch("skeletal_morph_service.parse_morph_avatar", return_value="avatar"),
            patch("skeletal_morph_service.merge_morph_avatars", return_value="merged"),
            patch("skeletal_morph_service.bake_morph_animation", return_value=baked) as bake,
        ):
            result = self.service().build(
                index,
                {"path": "assets/model.prefab"},
                {
                    "asset_index": 30,
                    "path": "assets/dialog.anim",
                    "bundle_name": "main/dialog.ab",
                },
                {"nodes": []},
            )

        self.assertIs(baked, result)
        self.assertEqual([10, 20], self.resolve_calls)
        self.assertEqual(["avatar-main.asset", "avatar-face.asset"], index.name_calls)
        bake.assert_called_once_with(
            {"nodes": []},
            "clip",
            "merged",
            animation_id="animation:30",
            source={
                "logicalPath": "assets/dialog.anim",
                "bundle": "main/dialog.ab",
                "morphClipPath": "assets/resolved/10.asset",
                "morphAvatarPaths": ["assets/resolved/20.asset"],
            },
        )

    def test_rejects_ambiguous_sidecar_before_export(self):
        index = FakeIndex([{"assetIndex": 1}, {"assetIndex": 2}], {})
        with (
            patch(
                "skeletal_morph_service.morph_clip_asset_path",
                return_value="assets/clip-sidecar.asset",
            ),
            self.assertRaisesRegex(RuntimeError, "found 2"),
        ):
            self.service().build(
                index,
                {"path": "assets/model.prefab"},
                {"asset_index": 3, "path": "assets/a.anim", "bundle_name": "a"},
                {},
            )
        self.assertEqual([], self.resolve_calls)

    def test_primary_avatar_is_required_and_secondary_is_optional(self):
        index = FakeIndex([{"assetIndex": 1}], {})
        with (
            patch(
                "skeletal_morph_service.morph_clip_asset_path",
                return_value="assets/clip-sidecar.asset",
            ),
            patch(
                "skeletal_morph_service.morph_avatar_asset_names",
                return_value=("required.asset", "optional.asset"),
            ),
            self.assertRaisesRegex(RuntimeError, "expected one.*required.asset"),
        ):
            self.service().build(
                index,
                {"path": "assets/model.prefab"},
                {"asset_index": 3, "path": "assets/a.anim", "bundle_name": "a"},
                {},
            )
        self.assertEqual([], self.resolve_calls)


if __name__ == "__main__":
    unittest.main()
