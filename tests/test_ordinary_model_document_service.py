import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from ordinary_model_document_service import OrdinaryModelAssembly, OrdinaryModelDocumentService


def snapshot(source, path_id, type_name, name, payload, references):
    return {
        "$animestudio": {
            "contract": "AnimeStudioObjectSnapshot",
            "version": "1.0.0",
            "sourceFile": source,
            "pathId": path_id,
            "classId": 1 if type_name == "GameObject" else 4,
            "type": type_name,
            "name": name,
            "container": "assets/sample.prefab",
            "pptrReferences": references,
        },
        **payload,
    }


class OrdinaryModelDocumentServiceTests(unittest.TestCase):
    def test_assembles_and_finalizes_minimal_hierarchy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game_object = root / "GameObject" / "root.json"
            transform = root / "Transform" / "root.json"
            game_object.parent.mkdir(parents=True)
            transform.parent.mkdir(parents=True)
            game_object.write_text(json.dumps(snapshot(
                "CAB-sample", 1, "GameObject", "Root", {"m_Name": "Root"},
                [{"path": "$.m_Components[0]", "targetType": "Transform",
                  "targetSourceFile": "CAB-sample", "targetPathId": 2}],
            )), encoding="utf-8")
            transform.write_text(json.dumps(snapshot(
                "CAB-sample", 2, "Transform", "Root",
                {"m_LocalPosition": {"x": 0, "y": 0, "z": 0},
                 "m_LocalRotation": {"x": 0, "y": 0, "z": 0, "w": 1},
                 "m_LocalScale": {"x": 1, "y": 1, "z": 1},
                 "m_Father": {"m_FileID": 0, "m_PathID": 0}},
                [{"path": "$.m_GameObject", "targetType": "GameObject",
                  "targetSourceFile": "CAB-sample", "targetPathId": 1}],
            )), encoding="utf-8")

            service = OrdinaryModelDocumentService()
            assembly = service.assemble(
                root,
                logical_path="assets/sample.prefab",
                bundle="sample.ab",
                buffer_uri="/geometry.bin",
            )
            service.finalize(assembly, [{"name": "missing.ab"}])

            self.assertEqual(1, len(assembly.document["nodes"]))
            self.assertEqual(b"", assembly.geometry)
            self.assertEqual([], assembly.textures)
            self.assertEqual(
                "DEPENDENCY_BUNDLES_MISSING",
                assembly.document["diagnostics"][-1]["code"],
            )

    def test_rejects_bare_snapshot_without_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "GameObject" / "root.json"
            target.parent.mkdir(parents=True)
            target.write_text('{"m_Name":"Root"}', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "without required.*identity"):
                OrdinaryModelDocumentService().assemble(
                    root,
                    logical_path="assets/sample.prefab",
                    bundle="sample.ab",
                    buffer_uri="/geometry.bin",
                )

    def test_binds_texture_by_source_and_path_id_and_reports_missing(self):
        @dataclass(frozen=True)
        class Identity:
            source_file: str
            path_id: int
            document_id: str

        matched = Identity("CAB-a", 7, "texture:7")
        missing = Identity("CAB-b", 8, "texture:8")
        assembly = OrdinaryModelAssembly(
            {"diagnostics": []},
            b"",
            [matched, missing],
        )
        service = OrdinaryModelDocumentService()
        with patch("ordinary_model_document_service.attach_texture_images") as attach:
            service.attach_exported_textures(
                assembly,
                {"artifacts": [{
                    "sourceFile": "cab-A",
                    "pathId": 7,
                    "relativePath": "Texture2D/a.png",
                }]},
                lambda relative: f"/preview/{relative}",
            )

        image_uris = attach.call_args.args[2]
        self.assertEqual("/preview/Texture2D/a.png", image_uris[matched])
        self.assertNotIn(missing, image_uris)
        self.assertEqual("MODEL_TEXTURES_MISSING", assembly.document["diagnostics"][0]["code"])
        self.assertEqual(["texture:8"], assembly.document["diagnostics"][0]["details"]["textureIds"])


if __name__ == "__main__":
    unittest.main()
