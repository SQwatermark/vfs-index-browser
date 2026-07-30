import struct
import unittest
import zlib

from animestudio_animation import attach_animation_clip
from model_document import create_model_document, validate_model_document


SOURCE = {"logicalPath": "animations/idle", "bundle": "animation.ab"}


class AnimeStudioAnimationTests(unittest.TestCase):
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
            "version": "1.0.0",
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
            "version": "1.0.0",
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
            "version": "1.0.0",
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
