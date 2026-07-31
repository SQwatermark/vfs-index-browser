import json
import tempfile
import unittest
from pathlib import Path

from avatar_mesh_snapshot import (
    build_object_export_command,
    load_exported_objects,
    material_texture_names,
    selected_object_names,
)


def plan():
    return {
        "avatarAsset": {"path": "Assets/Npc/Character.fbx##CharacterAvatar"},
        "parts": [
            {
                "meshAsset": {"path": "Assets/Npc/Body.asset"},
                "materialAssets": [
                    {"path": "Assets/Npc/Body.mat"},
                    {"path": "Assets/Npc/Body.mat"},
                ],
            },
            {
                "meshAsset": {"path": "Assets/Npc/Character.fbx##Eyes"},
                "materialAssets": [{"path": "Assets/Npc/Eyes.mat"}],
            },
        ],
    }


class AvatarMeshSnapshotTests(unittest.TestCase):
    def test_builds_deduplicated_object_selection_and_escaped_filter(self):
        names = selected_object_names(plan())
        self.assertEqual(["Body", "Eyes"], names["Mesh"])
        self.assertEqual(["Body", "Eyes"], names["Material"])
        self.assertEqual(["CharacterAvatar"], names["Avatar"])
        command = build_object_export_command(
            Path("cli.exe"), Path("inputs"), Path("objects"), "sample", plan()
        )
        self.assertEqual(["Mesh", "Material", "Avatar"], command[command.index("--types") + 1:command.index("--names")])
        self.assertIn("CharacterAvatar", command[command.index("--names") + 1])

    def test_loads_exact_mesh_material_and_avatar_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for kind in ("Mesh", "Material", "Avatar"):
                (root / kind).mkdir()
            for name in ("Body", "Eyes"):
                (root / "Mesh" / f"{name}.json").write_text(
                    json.dumps({"m_Name": name}), encoding="utf-8"
                )
                (root / "Material" / f"{name}.json").write_text(
                    json.dumps({"m_Name": name, "m_SavedProperties": {}}),
                    encoding="utf-8",
                )
            (root / "Avatar" / "CharacterAvatar.json").write_text(
                json.dumps({"m_Name": "CharacterAvatar", "m_Avatar": {}}),
                encoding="utf-8",
            )

            meshes, materials, avatar = load_exported_objects(root, plan())

            self.assertEqual({"body", "eyes"}, set(meshes))
            self.assertEqual({"body", "eyes"}, set(materials))
            self.assertEqual("CharacterAvatar", avatar["m_Name"])

    def test_collects_unique_non_null_texture_names(self):
        materials = {
            "body": {
                "m_SavedProperties": {
                    "m_TexEnvs": {
                        "_BaseMap": {"m_Texture": {"IsNull": False, "Name": "Body_D"}},
                        "_Mask": {"m_Texture": {"IsNull": False, "Name": "Body_M"}},
                    }
                }
            },
            "face": {
                "m_SavedProperties": {
                    "m_TexEnvs": {
                        "_BaseMap": {"m_Texture": {"IsNull": False, "Name": "Body_D"}},
                        "_Empty": {"m_Texture": {"IsNull": True, "Name": "Ignored"}},
                    }
                }
            },
        }
        self.assertEqual(["Body_D", "Body_M"], material_texture_names(materials))


if __name__ == "__main__":
    unittest.main()
