import struct
import unittest

from ability_entity_data import (
    ROOT_TYPE,
    AbilityEntityDecodeError,
    AbilityEntityNotFoundError,
    ability_entity_asset_path,
    list_ability_entity_ids,
    parse_ability_entity_template,
    select_ability_entity_asset,
)


def align4(offset):
    return (offset + 3) & ~3


def encoded_string(value, offset):
    encoded = value.encode()
    raw = struct.pack("<i", len(encoded)) + encoded
    return raw + bytes(align4(offset + len(raw)) - offset - len(raw))


def fixture():
    data = bytearray(bytes(12) + b"\x01" + bytes(3) + bytes(12))
    data += encoded_string("fixture", len(data))
    root_rid = 0x123456789
    data += struct.pack("<qii", root_rid, 2, 3)
    data += struct.pack("<q", 8) + encoded_string("", len(data) + 8) * 3
    data += struct.pack("<q", root_rid)
    for value in ROOT_TYPE:
        data += encoded_string(value, len(data))
    data += encoded_string("abilityentity_fixture", len(data))
    data += encoded_string("abilityentity_fixture", len(data))
    data += struct.pack("<ii", 2, 2)
    data += struct.pack("<ii", 10, -20)
    data += struct.pack("<ffB", 0.25, 0.5, 1)
    data += bytes(align4(len(data)) - len(data))
    data += b"\x00"
    data += bytes(align4(len(data)) - len(data))
    data += struct.pack("<fiqq", 1.0, 2, 11, 12)
    data += struct.pack("<i", 3)
    data += b"\x01" + bytes(3) + struct.pack("<i", 7)
    data += encoded_string("stack", len(data))
    data += struct.pack("<if", 0, 12.5)
    data += b"\x00" + bytes(3) + struct.pack("<f", 9.5)
    data += encoded_string("", len(data))
    data += struct.pack("<f", 30.0)
    return bytes(data)


class FakeIndex:
    def __init__(self, assets):
        self.assets = assets

    def assets_by_path(self, _path):
        return self.assets

    def assets_in_directory(self, _path):
        return self.assets


class AbilityEntityDataTests(unittest.TestCase):
    def test_uses_exact_canonical_asset_path(self):
        self.assertEqual(
            "assets/beyond/dynamicassets/gamedata/abilityentity/"
            "data_abilityentity_fixture.asset",
            ability_entity_asset_path("abilityentity_fixture"),
        )
        selected = {"assetIndex": 1}
        self.assertIs(
            selected,
            select_ability_entity_asset(FakeIndex([selected]), "abilityentity_fixture"),
        )
        with self.assertRaises(AbilityEntityNotFoundError):
            select_ability_entity_asset(FakeIndex([]), "abilityentity_missing")

    def test_lists_only_canonical_ability_entity_assets(self):
        self.assertEqual(
            ["abilityentity_a", "abilityentity_b"],
            list_ability_entity_ids(
                FakeIndex(
                    [
                        {"name": "data_abilityentity_b.asset"},
                        {"name": "readme.txt"},
                        {"name": "data_abilityentity_a.asset"},
                    ]
                )
            ),
        )

    def test_parses_only_the_proven_template_prefix(self):
        value = parse_ability_entity_template(fixture(), "abilityentity_fixture")
        self.assertEqual([10, -20], value["bornTagIds"])
        self.assertEqual(0, value["lifeTypeNativeValue"])
        self.assertEqual(12.5, value["durationSeconds"])
        self.assertEqual(3, value["maxStackingCount"])
        self.assertEqual(2, value["componentCount"])

    def test_rejects_identity_mismatch(self):
        with self.assertRaisesRegex(AbilityEntityDecodeError, "identity mismatch"):
            parse_ability_entity_template(fixture(), "other")


if __name__ == "__main__":
    unittest.main()
