import json
import tempfile
import unittest
from pathlib import Path

from avatar_mesh_snapshot import (
    load_exported_objects,
    material_texture_selections,
    selected_container_paths,
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
    def test_builds_deduplicated_object_and_container_selection(self):
        names = selected_object_names(plan())
        self.assertEqual(["Body", "Eyes"], names["Mesh"])
        self.assertEqual(["Body", "Eyes"], names["Material"])
        self.assertEqual(["CharacterAvatar"], names["Avatar"])
        self.assertEqual(
            [
                "assets/npc/character.fbx",
                "assets/npc/body.asset",
                "assets/npc/body.mat",
                "assets/npc/eyes.mat",
            ],
            selected_container_paths(plan()),
        )

    def test_loads_exact_mesh_material_and_avatar_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for kind in ("Mesh", "Material", "Avatar"):
                (root / kind).mkdir()
            for path_id, name in enumerate(("Body", "Eyes"), start=1):
                mesh_container = (
                    "assets/npc/body.asset"
                    if name == "Body"
                    else "assets/npc/character.fbx"
                )
                material_container = f"assets/npc/{name.casefold()}.mat"
                (root / "Mesh" / f"{name}.json").write_text(
                    json.dumps({
                        "$animestudio": {
                            "contract": "AnimeStudioObjectSnapshot",
                            "version": "1.0.0",
                            "sourceFile": "CAB-test",
                            "pathId": path_id,
                            "type": "Mesh",
                            "container": mesh_container,
                        },
                        "m_Name": name,
                    }),
                    encoding="utf-8",
                )
                (root / "Material" / f"{name}.json").write_text(
                    json.dumps({
                        "$animestudio": {
                            "contract": "AnimeStudioObjectSnapshot",
                            "version": "1.0.0",
                            "sourceFile": "CAB-test",
                            "pathId": path_id + 10,
                            "type": "Material",
                            "container": material_container,
                        },
                        "m_Name": name,
                        "m_SavedProperties": {},
                    }),
                    encoding="utf-8",
                )
            (root / "Avatar" / "CharacterAvatar.json").write_text(
                json.dumps({
                    "$animestudio": {
                        "contract": "AnimeStudioObjectSnapshot",
                        "version": "1.0.0",
                        "sourceFile": "CAB-test",
                        "pathId": 99,
                        "type": "Avatar",
                        "container": "assets/npc/character.fbx",
                    },
                    "m_Name": "CharacterAvatar",
                    "m_Avatar": {},
                }),
                encoding="utf-8",
            )

            meshes, materials, avatar = load_exported_objects(root, plan())

            self.assertEqual({"body", "eyes"}, set(meshes))
            self.assertEqual({"body", "eyes"}, set(materials))
            self.assertEqual("CharacterAvatar", avatar["m_Name"])

    def test_collects_exact_texture_identities_from_snapshot_references(self):
        materials = {
            "body": {
                "$animestudio": {
                    "pptrReferences": [
                        {
                            "path": "$.m_SavedProperties.m_TexEnvs._BaseMap.m_Texture",
                            "targetType": "Texture2D",
                            "targetSourceFile": "CAB-a",
                            "targetPathId": -17,
                            "targetName": "Body_D",
                        },
                        {
                            "path": "$.m_SavedProperties.m_TexEnvs._BaseMap.m_Texture",
                            "targetType": "Texture2D",
                            "targetSourceFile": "cab-A",
                            "targetPathId": -17,
                            "targetName": "Body_D",
                        },
                    ]
                }
            }
        }
        self.assertEqual(
            [{"sourceFile": "CAB-a", "pathId": -17, "name": "Body_D"}],
            material_texture_selections(materials),
        )


if __name__ == "__main__":
    unittest.main()
