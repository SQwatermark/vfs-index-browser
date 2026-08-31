import struct
import unittest

from skeletal_morph import (
    bake_morph_animation,
    is_dialog_morph_animation_path,
    morph_avatar_asset_name,
    morph_clip_asset_path,
    parse_morph_avatar,
    parse_morph_clip,
)


class _Writer:
    def __init__(self):
        self.data = bytearray()

    def pack(self, fmt, *values):
        self.data.extend(struct.pack(fmt, *values))

    def int32(self, value):
        self.pack("<i", value)

    def int64(self, value):
        self.pack("<q", value)

    def float32(self, value):
        self.pack("<f", value)

    def align4(self):
        while len(self.data) % 4:
            self.data.append(0)

    def boolean(self, value):
        self.pack("<B", int(value))
        self.align4()

    def string(self, value):
        encoded = value.encode("utf-8")
        self.int32(len(encoded))
        self.data.extend(encoded)
        self.align4()

    def array(self, values, write):
        self.int32(len(values))
        for value in values:
            write(value)

    def header(self, name):
        self.int32(0)
        self.int64(0)
        self.boolean(True)
        self.int32(1)
        self.int64(2)
        self.string(name)

    def bone(self, value):
        name_hash, bone_id, position, rotation, scale = value
        self.int32(name_hash)
        self.int32(bone_id)
        for component in (*position, *rotation, *scale):
            self.float32(component)


def avatar_payload():
    writer = _Writer()
    writer.header("data_facemorph_avatar_test")
    writer.int32(3)
    writer.int64(4)
    writer.int32(0)
    writer.int64(0)
    writer.int32(123)
    writer.int32(0)
    writer.boolean(False)
    writer.boolean(False)
    base = (111, 56, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    target = (111, 56, (1.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    writer.array([base], writer.bone)
    writer.array([], writer.int64)
    writer.array([1001], writer.int64)
    writer.array(["faceJoint"], writer.string)
    writer.array([], writer.string)
    writer.array(["eye_test_ctrl"], writer.string)
    writer.array([], writer.int32)
    writer.array([], writer.int32)
    writer.int32(2)
    writer.int32(1)
    writer.int64(1001)
    writer.string("SkeletalMorphMappingData")
    writer.string("Beyond.Gameplay.Core")
    writer.string("Gameplay.Beyond")
    writer.int32(0)
    writer.int32(222)
    writer.int32(333)
    writer.int32(1)
    writer.array([target], writer.bone)
    return bytes(writer.data)


def clip_payload():
    writer = _Writer()
    writer.header("face_test")
    writer.int32(0)
    writer.float32(1.0)
    writer.boolean(False)
    writer.boolean(True)
    writer.boolean(False)

    def curve(_):
        writer.string("eye_test_ctrl")

        def key(value):
            time, amount = value
            writer.float32(time)
            writer.float32(amount)
            writer.float32(0.0)
            writer.float32(0.0)
            writer.int32(0)
            writer.float32(0.0)
            writer.float32(0.0)

        writer.array([(0.0, 0.0), (1.0, 1.0)], key)
        writer.int32(2)
        writer.int32(2)
        writer.int32(4)

    writer.array([None], curve)
    for _ in range(7):
        writer.array([], curve)
    return bytes(writer.data)


class SkeletalMorphTests(unittest.TestCase):
    def test_resource_names_follow_dialog_and_model_conventions(self):
        animation = "assets/dialog/morphanim/example.anim"
        self.assertTrue(is_dialog_morph_animation_path(animation))
        self.assertEqual(
            morph_clip_asset_path(animation),
            "assets/dialog/morphanimso/example.asset",
        )
        self.assertEqual(
            morph_avatar_asset_name(
                "assets/gameplay/actors/postmodels/characters/chr_0036_jsspsi_postmodel.prefab"
            ),
            "data_facemorph_avatar_jsspsi.asset",
        )

    def test_raw_assets_are_parsed_and_baked_to_model_tracks(self):
        avatar = parse_morph_avatar(avatar_payload())
        clip = parse_morph_clip(clip_payload())
        animation = bake_morph_animation(
            {
                "nodes": [
                    {
                        "id": "node:face",
                        "name": "faceJoint",
                        "transform": {
                            "translation": [0.0, 0.0, 0.0],
                            "rotation": [0.0, 0.0, 0.0, 1.0],
                            "scale": [1.0, 1.0, 1.0],
                        },
                    }
                ]
            },
            clip,
            avatar,
            animation_id="animation:test",
            source={"logicalPath": "example.anim"},
            sample_rate=1.0,
        )

        self.assertEqual(animation["timelines"], [[0.0, 1.0]])
        tracks = {track["property"]: track for track in animation["tracks"]}
        self.assertEqual({"translation", "scale"}, set(tracks))
        self.assertEqual(tracks["translation"]["targetId"], "node:face")
        self.assertEqual(
            tracks["translation"]["values"],
            [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
        )
        self.assertEqual(
            tracks["scale"]["values"],
            [[1.0, 1.0, 1.0], [2.0, 2.0, 2.0]],
        )


if __name__ == "__main__":
    unittest.main()
