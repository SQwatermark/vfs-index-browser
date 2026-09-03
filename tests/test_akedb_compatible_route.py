import unittest

from endaxis_data_route import (
    EndaxisDataRouteError,
    resolve_endaxis_data_route,
)


class EndaxisDataRouteTests(unittest.TestCase):
    def test_resolves_all_supported_resource_shapes(self):
        cases = {
            "TableCfg-current/SampleTable.json": (
                "handle_endaxis_data_table",
                ("SampleTable",),
            ),
            "SkillData/manifest.json": (
                "handle_endaxis_data_collection_manifest",
                ("SkillData",),
            ),
            "BuffData/sample.json": (
                "handle_endaxis_data_collection_file",
                ("BuffData", "sample.json"),
            ),
            "ProjectileData/manifest.json": (
                "handle_endaxis_data_projectile_manifest",
                (),
            ),
            "ProjectileData/projectile_sample.json": (
                "handle_endaxis_data_projectile_file",
                ("projectile_sample",),
            ),
            "AbilityEntityData/abilityentity_sample.json": (
                "handle_endaxis_data_ability_entity_file",
                ("abilityentity_sample",),
            ),
            "CharacterData/manifest.json": ("handle_endaxis_data_character_manifest", ()),
            "CharacterData/chr_0004_pelica.runtime-template.json": (
                "handle_endaxis_data_character_file", ("chr_0004_pelica",),
            ),
        }
        prefix = "/api/endaxis-data/"
        for suffix, expected in cases.items():
            with self.subTest(suffix=suffix):
                route = resolve_endaxis_data_route(prefix + suffix)
                self.assertEqual(expected, (route.handler_name, route.arguments))

    def test_rejects_decoded_path_traversal_as_not_found(self):
        with self.assertRaises(EndaxisDataRouteError) as raised:
            resolve_endaxis_data_route(
                "/api/endaxis-data/SkillData/%2E%2E%2Fsecret.json"
            )
        self.assertEqual(404, raised.exception.status)

    def test_invalid_known_resource_name_is_bad_request(self):
        with self.assertRaises(EndaxisDataRouteError) as raised:
            resolve_endaxis_data_route(
                "/api/endaxis-data/TableCfg-current/not-safe!.json"
            )
        self.assertEqual(400, raised.exception.status)

    def test_unknown_collection_is_not_found(self):
        with self.assertRaises(EndaxisDataRouteError) as raised:
            resolve_endaxis_data_route(
                "/api/endaxis-data/UnknownData/sample.json"
            )
        self.assertEqual(404, raised.exception.status)


if __name__ == "__main__":
    unittest.main()
