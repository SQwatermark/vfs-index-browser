import unittest
from pathlib import Path
from types import SimpleNamespace

from model_single_animation_service import ModelSingleAnimationService


class ModelSingleAnimationServiceTests(unittest.TestCase):
    def test_builds_and_binds_regular_clip_with_progress(self):
        observed = []
        service = ModelSingleAnimationService(
            lambda *_args, **_kwargs: self.fail("Avatar path is unexpected"),
            lambda *_args, **_kwargs: ({"nodes": []}, {}),
            lambda _dependencies: ([], []),
            lambda _path: False,
            lambda *_args: self.fail("Morph path is unexpected"),
            lambda _record, _chunk, asset, **_kwargs: (
                {"clip": asset["asset_index"]}, Path("clip.json"), {}
            ),
            lambda document, clip, **options: (
                observed.append((document, clip, options)),
                {"tracks": [clip["clip"]]},
            )[1],
        )
        index = SimpleNamespace(bundle_dependencies=lambda _index: [])
        model = (
            index,
            {"path": "assets/hero.prefab", "bundle_index": 3},
            {"id": 7},
            Path("model.chk"),
        )
        animation = (
            object(),
            {
                "path": "assets/idle.animation",
                "asset_index": 22,
                "bundle_name": "idle.ab",
            },
            {"id": 8},
            Path("idle.chk"),
        )
        progress = []

        result = service.build(model, animation, 0, progress=progress.append)

        self.assertEqual({"tracks": [22]}, result)
        self.assertEqual("animation:22", observed[0][2]["animation_id"])
        self.assertEqual(
            ["model", "animation", "binding", "ready"],
            [entry["stage"] for entry in progress],
        )

    def test_builds_dialog_morph_without_exporting_clip(self):
        service = ModelSingleAnimationService(
            lambda *_args, **_kwargs: ({"nodes": []}, {}, Path("model.json")),
            lambda *_args, **_kwargs: self.fail("ordinary path is unexpected"),
            lambda _dependencies: ([], []),
            lambda path: path.endswith(".morph"),
            lambda _index, _model, animation, _document: {
                "morph": animation["asset_index"]
            },
            lambda *_args, **_kwargs: self.fail("clip export is unexpected"),
            lambda *_args, **_kwargs: self.fail("clip binding is unexpected"),
        )
        model = (
            object(),
            {
                "path": (
                    "assets/beyond/dynamicassets/gameplay/npc/avatarmesh/actor/"
                    "data_npc_avatarmesh_sample.asset"
                )
            },
            {"id": 7},
            Path("model.chk"),
        )
        animation = (
            object(),
            {"path": "assets/dialog.morph", "asset_index": 33},
            {"id": 8},
            Path("dialog.chk"),
        )
        progress = []

        result = service.build(model, animation, 2, progress=progress.append)

        self.assertEqual({"morph": 33}, result)
        self.assertEqual(
            ["model", "animation", "ready"],
            [entry["stage"] for entry in progress],
        )


if __name__ == "__main__":
    unittest.main()
