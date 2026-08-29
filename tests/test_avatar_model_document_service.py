import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from avatar_model_document_service import AvatarModelDocumentService


class AvatarModelDocumentServiceTests(unittest.TestCase):
    def test_loads_resources_binds_textures_and_builds_document(self):
        selections = [{"sourceFile": "CAB-a", "pathId": 7, "name": "Body_D"}]
        with (
            patch("avatar_model_document_service.load_exported_objects", return_value=({"mesh": 1}, {"material": 2}, "avatar")),
            patch("avatar_model_document_service.material_texture_selections", return_value=selections),
            patch("avatar_model_document_service.build_static_avatar_mesh_document", return_value=({"nodes": []}, b"geometry")) as build,
            tempfile.TemporaryDirectory() as directory,
        ):
            service = AvatarModelDocumentService()
            assembly = service.load(Path(directory), {"plan": True})
            service.attach_exported_textures(
                assembly,
                {"artifacts": [{"sourceFile": "cab-A", "pathId": 7, "relativePath": "body.png"}]},
                lambda relative: f"/textures/{relative}",
            )
            result = service.build({}, assembly, lod=0, buffer_uri="/geometry.bin")

        self.assertEqual(({"nodes": []}, b"geometry"), result)
        self.assertEqual({"Body_D": "/textures/body.png"}, assembly.texture_uris)
        self.assertEqual(assembly.texture_uris, build.call_args.kwargs["texture_uris"])

    def test_rejects_duplicate_texture_name(self):
        with (
            patch("avatar_model_document_service.load_exported_objects", return_value=({}, {}, None)),
            patch("avatar_model_document_service.material_texture_selections", return_value=[
                {"sourceFile": "CAB-a", "pathId": 7, "name": "Body_D"},
                {"sourceFile": "CAB-b", "pathId": 8, "name": "Body_D"},
            ]),
            tempfile.TemporaryDirectory() as directory,
        ):
            service = AvatarModelDocumentService()
            assembly = service.load(Path(directory), {})
            with self.assertRaisesRegex(RuntimeError, "duplicate Texture2D name"):
                service.attach_exported_textures(
                    assembly,
                    {"artifacts": [
                        {"sourceFile": "CAB-a", "pathId": 7, "relativePath": "a.png"},
                        {"sourceFile": "CAB-b", "pathId": 8, "relativePath": "b.png"},
                    ]},
                    lambda value: value,
                )


if __name__ == "__main__":
    unittest.main()
