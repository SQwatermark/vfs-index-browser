import json
import struct
import unittest
from pathlib import Path

from tools.decode_memorypack_json import DecodeError, Decoder, MemoryPackReader, SchemaIndex


class MemoryPackDecoderOverrideTests(unittest.TestCase):
    def test_animation_curve_keyframe_field_order_matches_unity_layout(self):
        decoder = Decoder(SchemaIndex({"classes": []}))
        keys = [
            (0.0, 0.0, 0.0, 0.0, 0, 0.0, 1.0 / 3.0),
            (1.0, 0.0, 0.0, 0.0, 0, 1.0 / 3.0, 0.0),
        ]
        payload = (
            b"\x03"
            + struct.pack("<ii", 8, 8)
            + struct.pack("<i", len(keys))
            + b"".join(struct.pack("<ffffiff", *key) for key in keys)
            + b"\xbe"
        )
        reader = MemoryPackReader(payload)

        value = decoder.read_value(reader, "UnityEngine.AnimationCurve", "$.curve")

        self.assertEqual(0, value["keys"][0]["weightedMode"])
        self.assertAlmostEqual(0.0, value["keys"][0]["inWeight"])
        self.assertAlmostEqual(1.0 / 3.0, value["keys"][0]["outWeight"])
        self.assertEqual(0, value["keys"][1]["weightedMode"])
        self.assertAlmostEqual(1.0 / 3.0, value["keys"][1]["inWeight"])
        self.assertAlmostEqual(0.0, value["keys"][1]["outWeight"])
        self.assertEqual(0xBE, reader.read_u8())

    def test_buff_apply_tags_are_raw_signed_int32_and_preserve_next_field(self):
        decoder = Decoder(SchemaIndex({"classes": []}))
        for tags in ([], [0], [-1480463572, 226, 2147483647, -2147483648]):
            with self.subTest(tags=tags):
                reader = MemoryPackReader(struct.pack("<i", len(tags)) + b"".join(
                    struct.pack("<i", tag) for tag in tags
                ) + b"\xbe")
                self.assertEqual([{"tagId": tag} for tag in tags], decoder.read_value(
                    reader, "Beyond.Gameplay.Core.GameplayTag[]", "$.applyTags",
                    "Beyond.Gameplay.Core.BuffData", "applyTags",
                ))
                self.assertEqual(0xBE, reader.read_u8())

    def test_buff_apply_tags_null_is_not_empty(self):
        decoder = Decoder(SchemaIndex({"classes": []}))
        reader = MemoryPackReader(struct.pack("<i", -1) + b"\xbe")
        self.assertIsNone(decoder.read_value(
            reader, "Beyond.Gameplay.Core.GameplayTag[]", "$.applyTags",
            "Beyond.Gameplay.Core.BuffData", "applyTags",
        ))
        self.assertEqual(0xBE, reader.read_u8())

    def test_buff_apply_tags_truncated_payload_fails(self):
        decoder = Decoder(SchemaIndex({"classes": []}))
        with self.assertRaises(DecodeError):
            decoder.read_value(MemoryPackReader(struct.pack("<ii", 2, 42)),
                "Beyond.Gameplay.Core.GameplayTag[]", "$.applyTags",
                "Beyond.Gameplay.Core.BuffData", "applyTags")

    def test_super_armor_blackboard_value_is_int32(self):
        decoder = Decoder(SchemaIndex({"classes": []}))
        reader = MemoryPackReader(struct.pack("<i", 280))

        value = decoder.read_value(
            reader,
            "TSerializeValue",
            "$.value",
            "Beyond.Gameplay.Core.BlackboardSuperArmorValue",
            "value",
        )

        self.assertEqual(280, value)
        self.assertEqual(4, reader.tell())

    def test_buff_id_blackboard_value_is_string(self):
        decoder = Decoder(SchemaIndex({"classes": []}))
        reader = MemoryPackReader(struct.pack("<i", 4) + b"buff")

        value = decoder.read_value(
            reader,
            "TSerializeValue",
            "$.value",
            "Beyond.Gameplay.Core.Conditions.BlackboardBuffId",
            "value",
        )

        self.assertEqual("buff", value)
        self.assertEqual(8, reader.tell())

    def test_plain_check_buff_id_list_keeps_plain_buff_id_schema(self):
        plain_buff_id = "Beyond.Gameplay.Core.BuffId"
        plain_check = "Beyond.Gameplay.Core.Conditions.CheckBuffIdInContext.Data"
        schema = SchemaIndex({"classes": [
            {
                "class": plain_buff_id,
                "memberDetails": [{"name": "buffId", "type": "System.String"}],
            },
        ]})
        decoder = Decoder(schema)
        encoded_string = b"buff_common_affixes_enhance_pulse"
        reader = MemoryPackReader(
            struct.pack("<iB", 1, 1)
            + struct.pack("<i", len(encoded_string))
            + encoded_string
        )

        value = decoder.read_value(
            reader,
            f"System.Collections.Generic.List<{plain_buff_id}>",
            "$.buffIdList",
            plain_check,
            "buffIdList",
        )

        self.assertEqual([{"buffId": encoded_string.decode("ascii")}], value)
        self.assertEqual(len(reader.data), reader.tell())

    def test_obtain_cost_recovery_tag_is_inline_int32(self):
        decoder = Decoder(SchemaIndex({"classes": []}))
        reader = MemoryPackReader(struct.pack("<i", 1234))

        value = decoder.read_value(
            reader,
            "Beyond.Gameplay.Core.GameplayTag",
            "$.uspRecoverTag",
            "Beyond.Gameplay.Core.ObtainCostAction.Data",
            "uspRecoverTag",
        )

        self.assertEqual({"tagId": 1234}, value)
        self.assertEqual(4, reader.tell())

    def test_zero_recovery_tag_does_not_consume_next_union_header(self):
        decoder = Decoder(SchemaIndex({"classes": []}))
        reader = MemoryPackReader(struct.pack("<i", 0) + b"\xbe")
        self.assertEqual({"tagId": 0}, decoder.read_value(
            reader, "Beyond.Gameplay.Core.GameplayTag", "$.uspRecoverTag",
            "Beyond.Gameplay.Core.ObtainCostAction.Data", "uspRecoverTag",
        ))
        self.assertEqual(0xBE, reader.read_u8())

    def test_enemy_ai_marker_info_uses_full_unmanaged_layout(self):
        decoder = Decoder(SchemaIndex({"classes": []}))
        reader = MemoryPackReader(struct.pack("<?3xi", True, 4321))

        value = decoder.read_value(
            reader,
            "Beyond.Gameplay.AI.EnemyCheckAIMarker.EnemyCheckAIMarkerInfo",
            "$.markerInfo",
        )

        self.assertEqual(True, value["invert"])
        self.assertEqual({"tagId": 4321}, value["marker"])
        self.assertEqual(8, reader.tell())

    def test_union_tag_zero_is_a_concrete_variant(self):
        base_type = "Example.Base"
        derived_type = "Example.Derived"
        schema = SchemaIndex({"classes": [{"class": derived_type, "memberDetails": []}]})
        decoder = Decoder(schema, {base_type: {0: derived_type}})
        reader = MemoryPackReader(b"\x00\x00")

        value = decoder.read_value(reader, base_type, "$.value")

        self.assertEqual(derived_type, value["$type"])
        self.assertNotIn("$tag", value)
        self.assertEqual(2, reader.tell())

    def test_generated_schema_covers_every_known_union_variant(self):
        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / "schemas/memorypack-known-schema.json").read_text(encoding="utf-8"))
        unions = json.loads((root / "schemas/memorypack-known-unions.json").read_text(encoding="utf-8"))
        classes = {item["class"] for item in schema["classes"]}
        variants = {variant for entries in unions.values() for variant in entries.values()}

        self.assertEqual(set(), variants - classes)


if __name__ == "__main__":
    unittest.main()
