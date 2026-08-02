import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from animestudio_animation import (
    attach_animation_clip,
    bind_animation_clip,
    load_unique_animation_clip,
)
from model_document import create_model_document, validate_model_document


SOURCE = {"logicalPath": "animations/idle", "bundle": "animation.ab"}


class AnimationExportSelectionTests(unittest.TestCase):
    def test_selects_clip_by_case_insensitive_exported_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "AnimationClip" / "first.animation.json"
            second = root / "AnimationClip" / "second.animation.json"
            first.parent.mkdir(parents=True)
            first.write_text(json.dumps({"name": "Idle_Loop"}), encoding="utf-8")
            second.write_text(json.dumps({"name": "Run_Loop"}), encoding="utf-8")

            clip, path = load_unique_animation_clip(root, "idle_loop")

        self.assertEqual("Idle_Loop", clip["name"])
        self.assertEqual(first, path)

    def test_rejects_missing_or_ambiguous_clip_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(2):
                path = root / f"idle-{index}.animation.json"
                path.write_text(json.dumps({"name": "idle"}), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "2 match 'idle'"):
                load_unique_animation_clip(root, "idle")


class AnimeStudioAnimationTests(unittest.TestCase):
    def test_compensates_root_level_accessories_when_humanoid_root_motion_is_removed(self):
        document = create_model_document(
            "asset:model",
            "Model",
            {"logicalPath": "model.prefab"},
            root_node_ids=["node:model"],
        )
        document["nodes"] = [
            {
                "id": "node:model",
                "name": "Model",
                "children": ["node:root"],
                "transform": {},
            },
            {
                "id": "node:root",
                "name": "Root",
                "parentId": "node:model",
                "children": ["node:hips", "node:accessory"],
                "transform": {},
            },
            {
                "id": "node:hips",
                "name": "Bip001",
                "parentId": "node:root",
                "children": [],
                "transform": {},
                "extras": {"humanoid": {"humanBone": "Hips"}},
            },
            {
                "id": "node:accessory",
                "name": "FloatingMetal",
                "parentId": "node:root",
                "children": [],
                "transform": {},
            },
        ]
        document["skeletons"] = [
            {
                "bones": [
                    {
                        "id": "node:hips",
                        "extras": {"humanoid": {"humanBone": "Hips"}},
                    }
                ]
            }
        ]
        timelines = [[0, 1]]
        curves = [
            {
                "pathHash": zlib.crc32(b"Root/FloatingMetal") & 0xFFFFFFFF,
                "property": "translation",
                "timeline": 0,
                "values": [[0, 0, 0.25], [0, 0, -1.75]],
            },
            {
                "pathHash": zlib.crc32(b"Root/FloatingMetal") & 0xFFFFFFFF,
                "property": "rotation",
                "timeline": 0,
                "values": [[0, 0, 0, 1], [0, 0, 0, 1]],
            },
        ]
        motion = {
            "MotionT": [[0, 0, 0], [0, 0, 2]],
            "MotionQ": [[0, 0, 0, 1], [0, 0, 0, 1]],
            "RootT": [[0, 0, 0], [0, 0, 2]],
            "RootQ": [[0, 0, 0, 1], [0, 0, 0, 1]],
        }
        for prefix, axes in (
            ("MotionT", "xyz"),
            ("MotionQ", "xyzw"),
            ("RootT", "xyz"),
            ("RootQ", "xyzw"),
        ):
            for component, axis in enumerate(axes):
                curves.append(
                    {
                        "property": "float",
                        "propertyName": f"{prefix}.{axis}",
                        "timeline": 0,
                        "values": [row[component] for row in motion[prefix]],
                    }
                )
        clip = {
            "format": "AnimeStudioAnimationClip",
            "version": "1.1.0",
            "name": "Walk",
            "sampleRate": 30,
            "duration": 1,
            "timelines": timelines,
            "curves": curves,
        }

        animation = bind_animation_clip(
            document,
            clip,
            animation_id="animation:walk",
            source=SOURCE,
            bake_humanoid=True,
        )

        accessory_translation = next(
            track
            for track in animation["tracks"]
            if track["targetId"] == "node:accessory"
            and track["property"] == "translation"
        )
        self.assertEqual([[0.0, 0.0, 0.25], [0.0, 0.0, 0.25]], accessory_translation["values"])
        self.assertIn(
            "ANIMATION_ACCESSORY_ROOT_MOTION_COMPENSATED",
            {item["code"] for item in animation["diagnostics"]},
        )

    def test_preserves_humanoid_body_motion_on_root_level_accessories(self):
        document = create_model_document(
            "asset:model",
            "Model",
            {"logicalPath": "model.prefab"},
            root_node_ids=["node:model"],
        )
        document["nodes"] = [
            {"id": "node:model", "name": "Model", "children": ["node:root"], "transform": {}},
            {
                "id": "node:root",
                "name": "Root",
                "parentId": "node:model",
                "children": ["node:hips", "node:accessory"],
                "transform": {},
            },
            {
                "id": "node:hips",
                "name": "Bip001",
                "parentId": "node:root",
                "children": [],
                "transform": {},
                "extras": {"humanoid": {"humanBone": "Hips"}},
            },
            {
                "id": "node:accessory",
                "name": "FloatingMetal",
                "parentId": "node:root",
                "children": [],
                "transform": {},
            },
        ]
        document["skeletons"] = [
            {
                "bones": [
                    {
                        "id": "node:hips",
                        "extras": {"humanoid": {"humanBone": "Hips"}},
                    }
                ]
            }
        ]
        curves = [
            {
                "pathHash": zlib.crc32(b"Root/FloatingMetal") & 0xFFFFFFFF,
                "property": "translation",
                "timeline": 0,
                "values": [[0, 0, 0.25], [0, 0, -1.75]],
            }
        ]
        values = {
            "MotionT": [[0, 0, 0], [0, 0, 2]],
            "MotionQ": [[0, 0, 0, 1], [0, 0, 0, 1]],
            "RootT": [[0, 1, 0], [0, 1.1, 2]],
            "RootQ": [[0, 0, 0, 1], [0, 0, 0, 1]],
        }
        for prefix, axes in (
            ("MotionT", "xyz"),
            ("MotionQ", "xyzw"),
            ("RootT", "xyz"),
            ("RootQ", "xyzw"),
        ):
            for component, axis in enumerate(axes):
                curves.append(
                    {
                        "property": "float",
                        "propertyName": f"{prefix}.{axis}",
                        "timeline": 0,
                        "values": [row[component] for row in values[prefix]],
                    }
                )
        clip = {
            "format": "AnimeStudioAnimationClip",
            "version": "1.1.0",
            "name": "Walk",
            "sampleRate": 30,
            "duration": 1,
            "timelines": [[0, 1]],
            "curves": curves,
        }

        animation = bind_animation_clip(
            document,
            clip,
            animation_id="animation:walk",
            source=SOURCE,
            bake_humanoid=True,
        )

        accessory_translation = next(
            track
            for track in animation["tracks"]
            if track["targetId"] == "node:accessory"
            and track["property"] == "translation"
        )
        self.assertEqual([0.0, 0.0, 0.25], accessory_translation["values"][0])
        self.assertAlmostEqual(0.1, accessory_translation["values"][1][1])
        self.assertEqual(0.25, accessory_translation["values"][1][2])

    def test_attaches_transform_curves_with_a_shared_timeline(self):
        document = create_model_document(
            "asset:model",
            "Model",
            {"logicalPath": "model.prefab", "bundle": "model.ab"},
            root_node_ids=["node:root"],
        )
        document["nodes"] = [
            {
                "id": "node:root",
                "name": "Model",
                "active": True,
                "children": ["node:armature"],
                "transform": {},
            },
            {
                "id": "node:armature",
                "name": "Armature",
                "active": True,
                "parentId": "node:root",
                "children": ["node:bone"],
                "transform": {},
            },
            {
                "id": "node:bone",
                "name": "Bone",
                "active": True,
                "parentId": "node:armature",
                "children": [],
                "transform": {},
            },
        ]
        document["buffers"] = [
            {
                "id": "buffer:geometry",
                "uri": "/geometry.bin",
                "byteLength": 4,
                "sha256": "",
            }
        ]
        path_hash = zlib.crc32(b"Armature/Bone") & 0xFFFFFFFF
        clip = {
            "format": "AnimeStudioAnimationClip",
            "version": "1.1.0",
            "name": "Idle",
            "sampleRate": 60,
            "duration": 1,
            "timelines": [[0, 1]],
            "curves": [
                {
                    "path": "path",
                    "pathHash": path_hash,
                    "property": "translation",
                    "timeline": 0,
                    "values": [[0, 0, 0], [0, 1, 0]],
                },
                {
                    "path": "path",
                    "pathHash": path_hash,
                    "property": "rotation",
                    "timeline": 0,
                    "values": [[0, 0, 0, 1], [0, 0, 0.5, 0.8660254]],
                },
                {
                    "path": "",
                    "pathHash": 0,
                    "property": "float",
                    "propertyName": "Example",
                    "classId": 95,
                    "timeline": 0,
                    "values": [0, 1],
                },
            ],
        }

        animation = bind_animation_clip(
            document,
            clip,
            animation_id="animation:idle",
            source=SOURCE,
        )
        self.assertEqual("EndfieldModelAnimation", animation["format"])
        self.assertEqual("1.0.0", animation["version"])
        self.assertEqual("Idle", animation["name"])
        self.assertEqual([[0.0, 1.0]], animation["timelines"])
        self.assertEqual(
            ["translation", "rotation"],
            [item["property"] for item in animation["tracks"]],
        )
        self.assertEqual(
            {"node:bone"},
            {item["targetId"] for item in animation["tracks"]},
        )
        self.assertEqual(
            "ANIMATION_FLOAT_CURVES_UNSUPPORTED",
            animation["diagnostics"][0]["code"],
        )

        geometry = attach_animation_clip(
            document,
            b"mesh",
            clip,
            animation_id="animation:idle",
            source=SOURCE,
        )

        self.assertEqual([], validate_model_document(document))
        self.assertEqual(1, len(document["animations"]))
        channels = document["animations"][0]["channels"]
        self.assertEqual(["translation", "rotation"], [item["property"] for item in channels])
        self.assertEqual({"node:bone"}, {item["targetId"] for item in channels})
        self.assertEqual(channels[0]["inputAccessorId"], channels[1]["inputAccessorId"])
        self.assertEqual(3, len(document["accessors"]))
        self.assertEqual(1, len(document["diagnostics"]))
        self.assertEqual("ANIMATION_FLOAT_CURVES_UNSUPPORTED", document["diagnostics"][0]["code"])
        self.assertEqual(len(geometry), document["buffers"][0]["byteLength"])
        timeline_view = document["bufferViews"][0]
        self.assertEqual(
            (0.0, 1.0),
            struct.unpack_from("<2f", geometry, timeline_view["byteOffset"]),
        )

    def test_rejects_mismatched_curve_lengths(self):
        document = create_model_document(
            "asset:model",
            "Model",
            {"logicalPath": "model.prefab"},
            root_node_ids=["node:root"],
        )
        document["nodes"] = [
            {
                "id": "node:root",
                "name": "Model",
                "active": True,
                "children": [],
                "transform": {},
            }
        ]
        clip = {
            "format": "AnimeStudioAnimationClip",
            "version": "1.1.0",
            "name": "Broken",
            "duration": 1,
            "timelines": [[0, 1]],
            "curves": [
                {
                    "path": "",
                    "pathHash": 0,
                    "property": "translation",
                    "timeline": 0,
                    "values": [[0, 0, 0]],
                }
            ],
        }

        with self.assertRaisesRegex(ValueError, "value count"):
            attach_animation_clip(
                document,
                b"",
                clip,
                animation_id="animation:broken",
                source=SOURCE,
                buffer_uri="/geometry.bin",
            )

    def test_collapses_exactly_constant_track_to_one_keyframe(self):
        document = create_model_document(
            "asset:model",
            "Model",
            {"logicalPath": "model.prefab"},
            root_node_ids=["node:root"],
        )
        document["nodes"] = [
            {
                "id": "node:root",
                "name": "Root",
                "active": True,
                "children": [],
                "transform": {},
            }
        ]
        document["buffers"] = [
            {
                "id": "buffer:geometry",
                "uri": "/geometry.bin",
                "byteLength": 0,
                "sha256": "",
            }
        ]
        clip = {
            "format": "AnimeStudioAnimationClip",
            "version": "1.1.0",
            "name": "Static",
            "sampleRate": 60,
            "duration": 2,
            "timelines": [[0, 1, 2]],
            "curves": [
                {
                    "pathHash": 0,
                    "property": "translation",
                    "timeline": 0,
                    "values": [[1, 2, 3], [1, 2, 3], [1, 2, 3]],
                }
            ],
        }

        attach_animation_clip(
            document,
            b"",
            clip,
            animation_id="animation:static",
            source=SOURCE,
        )

        channel = document["animations"][0]["channels"][0]
        accessors = {item["id"]: item for item in document["accessors"]}
        self.assertEqual(1, accessors[channel["inputAccessorId"]]["count"])
        self.assertEqual(1, accessors[channel["outputAccessorId"]]["count"])

    def test_matches_dotnet_ascii_hash_for_non_ascii_node_names(self):
        document = create_model_document(
            "asset:model",
            "Model",
            {"logicalPath": "model.prefab"},
            root_node_ids=["node:root"],
        )
        document["nodes"] = [
            {
                "id": "node:root",
                "name": "Model",
                "active": True,
                "children": ["node:helper"],
                "transform": {},
            },
            {
                "id": "node:helper",
                "name": "辅助",
                "active": True,
                "parentId": "node:root",
                "children": [],
                "transform": {},
            },
        ]
        path_hash = zlib.crc32(b"??") & 0xFFFFFFFF
        clip = {
            "format": "AnimeStudioAnimationClip",
            "version": "1.1.0",
            "name": "Idle",
            "duration": 0,
            "timelines": [[0]],
            "curves": [
                {
                    "path": "path",
                    "pathHash": path_hash,
                    "property": "translation",
                    "timeline": 0,
                    "values": [[0, 0, 0]],
                }
            ],
        }

        attach_animation_clip(
            document,
            b"",
            clip,
            animation_id="animation:idle",
            source=SOURCE,
            buffer_uri="/geometry.bin",
        )

        self.assertEqual("node:helper", document["animations"][0]["channels"][0]["targetId"])


if __name__ == "__main__":
    unittest.main()
