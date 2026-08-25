from __future__ import annotations

import struct
import unittest

from sparkbuffer import SparkType
from tools.analyze_sparkbuffer_schema_ownership import (
    SchemaRootInput,
    analyze_schema_ownership,
)


class SparkBufferSchemaOwnershipTests(unittest.TestCase):
    def test_uses_type_identity_and_root_reachability(self) -> None:
        report = analyze_schema_ownership(
            [
                SchemaRootInput("operator", "CharacterTable", schema(10, "Character", 100, "AttributeType")),
                SchemaRootInput("equipment", "EquipTable", schema(20, "Equip", 100, "AttributeType")),
                SchemaRootInput("weapon", "WeaponTable", schema(30, "Weapon", 200, "AttributeType")),
            ]
        )

        by_hash = {item["typeHash"]: item for item in report["types"]}
        self.assertEqual(by_hash[100]["ownership"], "shared")
        self.assertEqual(by_hash[100]["owners"], ["equipment", "operator"])
        self.assertEqual(by_hash[100]["definition"]["values"], [{"value": 0, "name": "Value"}])
        self.assertEqual(by_hash[200]["ownership"], "private")
        self.assertEqual(by_hash[200]["owners"], ["weapon"])
        self.assertEqual(report["summary"], {
            "typeCount": 5,
            "sharedTypeCount": 1,
            "privateTypeCount": 4,
        })

    def test_rejects_same_hash_with_different_nominal_definition(self) -> None:
        with self.assertRaisesRegex(ValueError, "type hash 0x00000064 conflicts"):
            analyze_schema_ownership(
                [
                    SchemaRootInput("operator", "CharacterTable", schema(10, "Character", 100, "AttributeType")),
                    SchemaRootInput("equipment", "EquipTable", schema(20, "Equip", 100, "OtherType")),
                ]
            )


def schema(root_hash: int, root_name: str, enum_hash: int, enum_name: str) -> bytes:
    writer = Writer()
    writer.bytes(b"\x00" * 12)
    type_offset = writer.position
    writer.int32(2)

    writer.byte(SparkType.BEAN)
    writer.align(4)
    writer.int32(root_hash)
    writer.string(root_name)
    writer.align(4)
    writer.int32(1)
    writer.string("attribute")
    writer.byte(SparkType.ENUM)
    writer.align(4)
    writer.int32(enum_hash)

    writer.byte(SparkType.ENUM)
    writer.align(4)
    writer.int32(enum_hash)
    writer.string(enum_name)
    writer.align(4)
    writer.int32(1)
    writer.string("Value")
    writer.align(4)
    writer.int32(0)

    root_offset = writer.position
    writer.byte(SparkType.MAP)
    writer.string(f"{root_name}Table")
    writer.byte(SparkType.STRING)
    writer.byte(SparkType.BEAN)
    writer.align(4)
    writer.int32(root_hash)
    data_offset = writer.position
    writer.patch_int32(0, type_offset)
    writer.patch_int32(4, root_offset)
    writer.patch_int32(8, data_offset)
    return bytes(writer.data)


class Writer:
    def __init__(self) -> None:
        self.data = bytearray()

    @property
    def position(self) -> int:
        return len(self.data)

    def bytes(self, value: bytes) -> None:
        self.data.extend(value)

    def byte(self, value: int) -> None:
        self.data.append(int(value))

    def int32(self, value: int) -> None:
        self.data.extend(struct.pack("<i", value))

    def string(self, value: str) -> None:
        self.data.extend(value.encode("utf-8") + b"\x00")

    def align(self, alignment: int) -> None:
        position_minus_one = self.position - 1
        target = position_minus_one + (alignment - (position_minus_one % alignment))
        self.data.extend(b"\x00" * (target - self.position))

    def patch_int32(self, offset: int, value: int) -> None:
        struct.pack_into("<i", self.data, offset, value)


if __name__ == "__main__":
    unittest.main()
