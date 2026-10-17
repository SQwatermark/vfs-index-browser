import unittest

from native_enum_catalog import NativeEnumCatalog


def catalog(*, flags: bool) -> NativeEnumCatalog:
    return NativeEnumCatalog(
        {
            "format": "VfsNativeEnumCatalog",
            "version": 1,
            "enums": [
                {
                    "type": "Example.WeaponTypeMask",
                    "underlyingType": "System.Int32",
                    "members": [
                        {"name": "None", "value": 0},
                        {"name": "All", "value": -1},
                        {"name": "Sword", "value": 2},
                        {"name": "Claymores", "value": 8},
                        {"name": "Lance", "value": 32},
                    ],
                    **({"isFlags": True} if flags else {}),
                }
            ],
        }
    )


class NativeEnumCatalogTests(unittest.TestCase):
    def test_formats_known_flag_bits_in_declaration_order(self):
        self.assertEqual(
            "Sword, Claymores, Lance",
            catalog(flags=True).json_value("Example.WeaponTypeMask", 42),
        )

    def test_preserves_exact_all_and_none_names(self):
        self.assertEqual("All", catalog(flags=True).json_value("Example.WeaponTypeMask", -1))
        self.assertEqual("None", catalog(flags=True).json_value("Example.WeaponTypeMask", 0))

    def test_preserves_unnamed_values_without_guessing_flag_names(self):
        self.assertEqual(1, catalog(flags=True).json_value("Example.WeaponTypeMask", 1))
        self.assertEqual(-4, catalog(flags=True).json_value("Example.WeaponTypeMask", -4))
        self.assertEqual(42, catalog(flags=False).json_value("Example.WeaponTypeMask", 42))
        with self.assertRaisesRegex(ValueError, "missing enum metadata"):
            catalog(flags=True).json_value("Example.Missing", 0)


if __name__ == "__main__":
    unittest.main()
