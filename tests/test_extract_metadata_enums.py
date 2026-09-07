import unittest

from tools.extract_metadata_enums import referenced_schema_types
from tools.metadata_enum_catalog import is_strict_mask_enum


class ReferencedSchemaTypesTests(unittest.TestCase):
    def test_collects_direct_nested_and_generic_type_references(self):
        schema = {
            "classes": [
                {
                    "class": "Example.Container",
                    "memberDetails": [
                        {
                            "name": "priority",
                            "type": "Beyond.Gameplay.Core.AbilityAction.AbilityActionData.Priority",
                            "typeInfo": {
                                "baseType": "Beyond.Gameplay.Core.AbilityAction.AbilityActionData.Priority",
                                "elementTypes": [],
                            },
                        },
                        {
                            "name": "states",
                            "type": "System.Collections.Generic.List<Beyond.Gameplay.AI.CharacterFollowBehavior`1.GuardState>",
                            "typeInfo": {
                                "baseType": "System.Collections.Generic.List",
                                "elementTypes": [
                                    "Beyond.Gameplay.AI.CharacterFollowBehavior`1+GuardState"
                                ],
                            },
                        },
                    ],
                }
            ]
        }

        self.assertEqual(
            referenced_schema_types(schema),
            {
                "Beyond.Gameplay.Core.AbilityAction.AbilityActionData.Priority",
                "System.Collections.Generic.List",
                "Beyond.Gameplay.AI.CharacterFollowBehavior`1.GuardState",
            },
        )

    def test_ignores_non_type_strings_and_existing_catalog(self):
        schema = {
            "classes": [{"class": "Example.Root", "members": ["not.a.type.field"]}],
            "enumCatalog": {
                "enums": [{"type": "Unrelated.Namespace.Enum", "members": []}]
            },
        }

        self.assertEqual(referenced_schema_types(schema), set())

    def test_only_recognizes_the_strict_native_mask_shape(self):
        members = [
            {"name": "None", "value": 0},
            {"name": "All", "value": -1},
            {"name": "Sword", "value": 2},
            {"name": "Claymores", "value": 8},
            {"name": "Lance", "value": 32},
        ]

        self.assertTrue(is_strict_mask_enum("Example.WeaponTypeMask", members))
        self.assertFalse(is_strict_mask_enum("Example.WeaponType", members))
        self.assertFalse(
            is_strict_mask_enum(
                "Example.WeaponTypeMask",
                [*members, {"name": "Combined", "value": 10}],
            )
        )


if __name__ == "__main__":
    unittest.main()
