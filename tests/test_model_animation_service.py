import hashlib
import tempfile
import unittest
from pathlib import Path

from model_animation_service import ModelAnimationService


class FakeGlbService:
    def __init__(self, exporter_path):
        self.exporter_mtime_ns = exporter_path.stat().st_mtime_ns
        self.animated_calls = []

    @staticmethod
    def animation_selection_key(indexes):
        return hashlib.sha256(",".join(map(str, indexes)).encode("ascii")).hexdigest()[:16]

    def load_inputs(self, *_args, **_kwargs):
        return {"animations": []}, b"geometry", {}

    def ensure_animated(
        self, document, geometry, _images, model_path, clip_paths, indexes, **_kwargs
    ):
        self.animated_calls.append((document, geometry, clip_paths, indexes))
        target = (
            model_path.parent
            / "animation-sets"
            / self.animation_selection_key(indexes)
            / "model.glb"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"animated")
        return target


class ModelAnimationServiceTests(unittest.TestCase):
    def sources(self, root, indexes):
        result = []
        for index in indexes:
            chunk = root / f"{index}.chk"
            chunk.write_bytes(b"chunk")
            result.append((
                object(),
                {
                    "asset_index": index,
                    "path": f"assets/{index}.animation",
                    "bundle_name": f"{index}.ab",
                },
                {"id": index + 100, "length": 5, "file_data_md5": str(index)},
                chunk,
            ))
        return result

    def service(self, root, *, export_failure=None, bind_empty=None):
        model_path = root / "run" / "model.json"
        model_path.parent.mkdir()
        model_path.write_text("{}", encoding="utf-8")
        base_glb = model_path.with_name("model.glb")
        base_glb.write_bytes(b"base")
        glb = FakeGlbService(Path(__file__))
        exports = []

        def export(_record, _chunk, asset, **_kwargs):
            index = int(asset["asset_index"])
            exports.append(index)
            if index == export_failure:
                raise ValueError("bad clip")
            clip_path = root / f"clip-{index}.json"
            clip_path.write_text("{}", encoding="utf-8")
            return {"index": index}, clip_path, {}

        def bind(document, geometry, clip, **_kwargs):
            if clip["index"] != bind_empty:
                document.setdefault("animations", []).append({"id": clip["index"]})
            return geometry + bytes([clip["index"]])

        service = ModelAnimationService(
            glb,
            lambda *_args, **_kwargs: (
                {"asset_index": 1, "path": "assets/model.prefab"},
                model_path,
                base_glb,
            ),
            glb.load_inputs,
            export,
            bind,
            glb_version=4,
            clip_export_version=5,
            binding_path=Path(__file__),
        )
        return service, glb, exports, model_path, base_glb

    def test_sorts_binds_publishes_and_reuses_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, glb, exports, model_path, _ = self.service(root)
            sources = self.sources(root, [22, 11])
            progress = []

            first = service.ensure(
                (object(), {}, {"id": 7}, Path(__file__)),
                sources,
                lod=0,
                progress=progress.append,
            )
            second = service.ensure(
                (object(), {}, {"id": 7}, Path(__file__)),
                sources,
                lod=0,
                progress=progress.append,
            )

            self.assertEqual([11, 22], exports)
            self.assertEqual([11, 22], [item["asset_index"] for item in first.animations])
            self.assertEqual(first, second)
            self.assertEqual([11, 22], glb.animated_calls[0][3])
            self.assertEqual(b"geometry\x0b\x16", glb.animated_calls[0][1])
            self.assertEqual("animationCache", progress[-1]["stage"])
            request_root = model_path.parent / "animation-requests"
            self.assertEqual(1, len(list(request_root.glob("*/result.json"))))
            self.assertEqual([], list(request_root.rglob(".*.tmp")))

    def test_skip_incompatible_records_export_and_binding_issues(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, glb, exports, _, base_glb = self.service(
                root, export_failure=11, bind_empty=22
            )
            result = service.ensure(
                (object(), {}, {"id": 7}, Path(__file__)),
                self.sources(root, [11, 22]),
                lod=0,
                skip_incompatible=True,
            )

            self.assertEqual([11, 22], exports)
            self.assertEqual([], result.animations)
            self.assertEqual(base_glb, result.glb_path)
            self.assertEqual(["clipExport", "modelBinding"], [x.stage for x in result.issues])
            self.assertEqual([], glb.animated_calls)


if __name__ == "__main__":
    unittest.main()
