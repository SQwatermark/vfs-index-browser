import unittest
from pathlib import Path
from unittest.mock import Mock

from manifest_model_glb_service import ManifestModelGlbService


class IndexStub:
    def bundle_dependencies(self, bundle_index):
        return [{"bundleIndex": bundle_index + 1}]


class ManifestModelGlbServiceTests(unittest.TestCase):
    def build_service(self):
        self.avatar = Mock()
        self.ordinary = Mock()
        self.resolve_sources = Mock()
        self.runs = Mock()
        self.glb = Mock()
        return ManifestModelGlbService(
            self.avatar,
            self.ordinary,
            self.resolve_sources,
            self.runs,
            self.glb,
        )

    def test_avatar_uses_builder_model_path_directly(self):
        service = self.build_service()
        model_path = Path("avatar/model.json")
        glb_path = Path("avatar/model.glb")
        self.avatar.return_value = ({}, {"selectedRun": "avatar"}, model_path)
        self.glb.ensure.return_value = glb_path
        asset = {
            "path": (
                "Assets/Beyond/DynamicAssets/Gameplay/NPC/AvatarMesh/hero/"
                "data_npc_avatarmesh_test.asset"
            ),
            "asset_index": 4,
        }
        record = {"id": 7}

        result = service.ensure((IndexStub(), asset, record, Path("bundle.ab")), lod=2)

        self.assertEqual((asset, model_path, glb_path), result)
        self.avatar.assert_called_once()
        self.ordinary.assert_not_called()
        self.runs.resolve_model_path.assert_not_called()
        self.glb.ensure.assert_called_once_with(
            asset, record, model_path, lod=2, cancel_event=None
        )

    def test_ordinary_resolves_published_run_through_store(self):
        service = self.build_service()
        dependencies = [{"bundleIndex": 6}]
        sources = [({"id": 8}, Path("dependency.ab"))]
        missing = [{"bundleIndex": 9}]
        self.resolve_sources.return_value = (sources, missing)
        self.ordinary.return_value = ({}, {"selectedRun": "run-1"})
        model_path = Path("ordinary/model.json")
        glb_path = Path("ordinary/model.glb")
        self.runs.resolve_model_path.return_value = model_path
        self.glb.ensure.return_value = glb_path
        asset = {
            "path": "models/test.prefab",
            "asset_index": 4,
            "bundle_index": 5,
        }
        record = {"id": 7}
        index = IndexStub()

        result = service.ensure((index, asset, record, Path("bundle.ab")))

        self.assertEqual((asset, model_path, glb_path), result)
        self.resolve_sources.assert_called_once_with(dependencies)
        self.ordinary.assert_called_once_with(
            record,
            Path("bundle.ab"),
            asset,
            dependencies,
            sources,
            missing,
            cancel_event=None,
        )
        self.runs.resolve_model_path.assert_called_once_with(7, 4, "run-1")

    def test_ordinary_rejects_unpublished_run(self):
        service = self.build_service()
        self.resolve_sources.return_value = ([], [])
        self.ordinary.return_value = ({}, {"selectedRun": "incomplete"})
        self.runs.resolve_model_path.return_value = None
        asset = {
            "path": "models/test.prefab",
            "asset_index": 4,
            "bundle_index": 5,
        }

        with self.assertRaisesRegex(RuntimeError, "published model run is unavailable"):
            service.ensure((IndexStub(), asset, {"id": 7}, Path("bundle.ab")))

        self.glb.ensure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
