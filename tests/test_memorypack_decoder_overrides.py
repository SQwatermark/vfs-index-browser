import json
import struct
import tempfile
import unittest
from pathlib import Path

from tools.decode_memorypack_json import DecodeError, Decoder, MemoryPackReader, SchemaIndex, main
from memorypack_value_decoder import memorypack_json_value


class MemoryPackDecoderOverrideTests(unittest.TestCase):
    def test_clip_async_info_preserves_padding_and_following_field(self):
        decoder = Decoder(SchemaIndex({"classes": []}))
        reader = MemoryPackReader(struct.pack(
            "<qffffff??2s", -0x123456789ABCDEF, 1.5, 30.0,
            1.0, 2.0, 3.0, 0.25, True, False, b"\xaa\xbb",
        ) + b"\xbe")
        value = decoder.read_value(reader, "Beyond.Montage.AnimationClipAsyncInfo", "$.clip")
        self.assertEqual(-0x123456789ABCDEF, value["montagePathHash"])
        self.assertEqual({"x": 1.0, "y": 2.0, "z": 3.0}, value["averageSpeed"])
        self.assertEqual(30.0, value["framerate"])
        self.assertTrue(value["isHumanoid"])
        self.assertFalse(value["isLooping"])
        self.assertEqual(0xbe, reader.read_u8())

    def test_resource_path_hash_preserves_all_64_bits_and_next_field(self):
        decoder = Decoder(SchemaIndex({"classes": []}), enum_names=True)
        for value in (0, 0x123456789ABCDEF, -0x123456789ABCDEF):
            with self.subTest(value=value):
                reader = MemoryPackReader(struct.pack("<q", value) + b"\xbe")
                self.assertEqual(value, decoder.read_value(
                    reader, "Beyond.Resource.StringPathHash", "$.controllerPath",
                ))
                self.assertEqual(0xbe, reader.read_u8())

    def test_fkeyframe_array_uses_raw_structs_and_preserves_following_field(self):
        decoder = Decoder(SchemaIndex({"classes": []}))
        keys = [(0.0, 0.25, -2.0, 3.0, 34, 1, 0.25, 0.5),
                (1.0, 2.0, 4.0, -5.0, 0, 2, 0.5, 0.75)]
        reader = MemoryPackReader(struct.pack("<i", 2) + b"".join(
            struct.pack("<ffffiiff", *key) for key in keys) + b"\xbe")
        result = decoder.read_value(reader, "Beyond.FKeyframe[]", "$.keys")
        self.assertEqual(34, result[0]["tangentMode"])
        self.assertEqual(-5.0, result[1]["outTangent"])
        self.assertEqual(0.75, result[1]["outWeight"])
        self.assertEqual(0xbe, reader.read_u8())
        for count, expected in ((-1, None), (0, [])):
            reader = MemoryPackReader(struct.pack("<i", count) + b"\xbe")
            self.assertEqual(expected, decoder.read_value(reader, "Beyond.FKeyframe[]", "$.keys"))
            self.assertEqual(0xbe, reader.read_u8())

    def test_skmorph_inline_fields_do_not_consume_wrapper_headers(self):
        decoder = Decoder(SchemaIndex({"classes": []}))
        reader = MemoryPackReader(struct.pack("<ifi", -12345, 0.25, 2) + b"\xbe")
        self.assertEqual({"tagId": -12345}, decoder.read_value(
            reader, "Beyond.Gameplay.Core.GameplayTag", "$.motionTag",
            "Beyond.Gameplay.Core.SkMorphPlaySetting", "motionTag",
        ))
        self.assertEqual({"fixedTransitionDuration": 0.25, "fixedTransitionType": 2},
                         decoder.read_value(
                             reader, "Beyond.Gameplay.Core.FMorphEmotionTransitionInfo", "$.transitionInfo",
                             "Beyond.Gameplay.Core.SkMorphPlaySetting", "transitionInfo",
                         ))
        self.assertEqual(0xbe, reader.read_u8())

    def test_nonfinite_curve_values_are_standard_json_strings(self):
        raw = {"keys": [{"inTangent": float("inf"), "outTangent": float("-inf"), "value": float("nan")}]}
        encoded = json.dumps(memorypack_json_value(raw), allow_nan=False)
        self.assertEqual({"keys": [{"inTangent": "Infinity", "outTangent": "-Infinity", "value": "NaN"}]}, json.loads(encoded))
        self.assertEqual(float("inf"), raw["keys"][0]["inTangent"])

    def test_color_exports_color_channels_without_changing_vector_fields(self):
        decoder = Decoder(SchemaIndex({"classes": []}))
        for type_name, fields in [("UnityEngine.Color", "rgba"), ("UnityEngine.Vector4", "xyzw"), ("UnityEngine.Quaternion", "xyzw")]:
            with self.subTest(type_name=type_name):
                reader = MemoryPackReader(struct.pack("<ffff", 0.25, 0.5, 0.75, 1.0) + b"\xbe")
                self.assertEqual(dict(zip(fields, [0.25, 0.5, 0.75, 1.0])), decoder.read_value(reader, type_name, "$.value"))
                self.assertEqual(0xbe, reader.read_u8())

    def test_cli_reports_incomplete_payload_and_returns_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "payload.bin"
            output = root / "result.json"
            schema = root / "schema.json"
            schema.write_text(json.dumps({'classes': [{'class': 'Beyond.Gameplay.Core.BuffData', 'members': []}]}), encoding='utf-8')
            args = ["--binary", str(binary), "--schema", str(schema), "--class", "Beyond.Gameplay.Core.BuffData", "--output", str(output)]
            for payload, success in [(b"\xff", True), (b"\xff\x00", False), (b"\xfa", False)]:
                with self.subTest(payload=payload):
                    binary.write_bytes(payload)
                    self.assertEqual(0 if success else 1, main(args))
                    result = json.loads(output.read_text(encoding="utf-8"))
                    self.assertEqual(success, result["__meta"]["complete"])
                    if not success:
                        self.assertIsNotNone(result["__meta"]["error"])

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

    def test_buff_tag_arrays_are_raw_signed_int32_and_preserve_next_field(self):
        decoder = Decoder(SchemaIndex({"classes": []}))
        for field in ("applyTags", "tagsAfterTriggerExtendBuffAction"):
            for tags in ([], [0], [-1480463572, 226, 2147483647, -2147483648]):
                with self.subTest(field=field, tags=tags):
                    reader = MemoryPackReader(struct.pack("<i", len(tags)) + b"".join(
                        struct.pack("<i", tag) for tag in tags
                    ) + b"\xbe")
                    self.assertEqual([{"tagId": tag} for tag in tags], decoder.read_value(
                        reader, "Beyond.Gameplay.Core.GameplayTag[]", f"$.{field}",
                        "Beyond.Gameplay.Core.BuffData", field,
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
