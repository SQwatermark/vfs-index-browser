import hashlib
import tempfile
import unittest
from pathlib import Path

from model_worker_service import ModelBundleInput, ModelWorkerService


def artifact(output: Path, relative: str, payload: bytes) -> dict:
    target = output / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return {
        "artifactCount": 1,
        "artifacts": [{
            "relativePath": relative,
            "byteCount": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }],
    }


class FakeWorker:
    def __init__(self):
        self.calls = []

    def build_cab_map(self, **options):
        self.calls.append(("cab", options))
        return artifact(options["output_directory"], "cab-map.json", b"cab")

    def export_object_snapshots(self, **options):
        self.calls.append(("objects", options))
        return artifact(options["output_directory"], "GameObject/object.json", b"object")

    def export_identified_textures(self, **options):
        self.calls.append(("textures", options))
        return artifact(options["output_directory"], "Texture2D/texture.png", b"texture")


class ModelWorkerServiceTests(unittest.TestCase):
    def test_stages_inputs_and_validates_all_worker_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chunks = [root / "a.chk", root / "b.chk"]
            for index, chunk in enumerate(chunks):
                chunk.write_bytes(f"bundle-{index}".encode())

            def write(record, chunk, target):
                target.write_bytes(chunk.read_bytes()[record["offset"]:])

            worker = FakeWorker()
            service = ModelWorkerService(worker, write)
            run = root / "run"
            staged = service.stage_inputs(run, [
                ModelBundleInput("primary", {"offset": 0}, chunks[0], "entry.ab"),
                ModelBundleInput("dependency", {"offset": 0}, chunks[1], "dependency.ab"),
            ])
            cab = service.build_cab_map(staged, run / "cab", "cab-request")
            objects = service.export_objects(
                staged,
                run / "cab/cab-map.json",
                run / "objects",
                "object-request",
                primary_input_id="primary",
                selection_input_ids=["primary", "dependency"],
                included_types=["GameObject"],
                containers=["assets/model.prefab"],
            )
            textures = service.export_textures(
                staged,
                run / "cab/cab-map.json",
                run / "textures",
                "texture-request",
                primary_input_id="primary",
                selections=[{"sourceFile": "CAB-a", "pathId": 7}],
            )

            self.assertEqual(["cab", "objects", "textures"], [call[0] for call in worker.calls])
            self.assertEqual(b"bundle-0", Path(staged[0]["inputPath"]).read_bytes())
            self.assertEqual(1, cab["artifactCount"])
            self.assertEqual(1, objects["artifactCount"])
            self.assertEqual(1, textures["artifactCount"])
            self.assertEqual(["GameObject"], worker.calls[1][1]["included_types"])

    def test_rejects_duplicate_or_escaping_stage_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chunk = root / "source.chk"
            chunk.write_bytes(b"bundle")
            service = ModelWorkerService(object(), lambda *_args: None)
            with self.assertRaisesRegex(ValueError, "identities are inconsistent"):
                service.stage_inputs(root / "run", [
                    ModelBundleInput("same", {}, chunk, "a.ab"),
                    ModelBundleInput("same", {}, chunk, "../outside.ab"),
                ])

    def test_rejects_inconsistent_worker_artifact_identity(self):
        class BrokenWorker:
            def build_cab_map(self, **options):
                result = artifact(options["output_directory"], "cab-map.json", b"cab")
                result["artifacts"][0]["sha256"] = "0" * 64
                return result

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = ModelWorkerService(BrokenWorker(), lambda *_args: None)
            with self.assertRaisesRegex(RuntimeError, "identity is inconsistent"):
                service.build_cab_map(
                    [{"inputId": "primary", "inputPath": "entry.ab"}],
                    root / "cab",
                    "request",
                )

    def test_texture_export_refuses_stale_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "textures"
            output.mkdir()
            (output / "stale.png").write_bytes(b"stale")
            service = ModelWorkerService(FakeWorker(), lambda *_args: None)
            with self.assertRaisesRegex(RuntimeError, "is not empty"):
                service.export_textures(
                    [{"inputId": "primary", "inputPath": "entry.ab"}],
                    root / "cab-map.json",
                    output,
                    "request",
                    primary_input_id="primary",
                    selections=[],
                )
            self.assertEqual(b"stale", (output / "stale.png").read_bytes())


if __name__ == "__main__":
    unittest.main()
