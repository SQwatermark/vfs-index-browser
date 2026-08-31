import unittest

from akedb_compatible_route import (
    AkedbCompatibleRouteError,
    resolve_akedb_compatible_route,
)


class AkedbCompatibleRouteTests(unittest.TestCase):
    def test_resolves_all_supported_resource_shapes(self):
        cases = {
            "TableCfg-1.0@1/SampleTable.json": (
                "handle_akedb_compatible_table",
                ("SampleTable",),
            ),
            "SkillData/manifest.json": (
                "handle_akedb_compatible_collection_manifest",
                ("SkillData",),
            ),
            "BuffData/sample.json": (
                "handle_akedb_compatible_collection_file",
                ("BuffData", "sample.json"),
            ),
            "ProjectileData/manifest.json": (
                "handle_akedb_compatible_projectile_manifest",
                (),
            ),
            "ProjectileData/projectile_sample.json": (
                "handle_akedb_compatible_projectile_file",
                ("projectile_sample",),
            ),
            "AbilityEntityData/abilityentity_sample.json": (
                "handle_akedb_compatible_ability_entity_file",
                ("abilityentity_sample",),
            ),
        }
        prefix = "/api/akedb-compatible/"
        for suffix, expected in cases.items():
            with self.subTest(suffix=suffix):
                route = resolve_akedb_compatible_route(prefix + suffix)
                self.assertEqual(expected, (route.handler_name, route.arguments))

    def test_rejects_decoded_path_traversal_as_not_found(self):
        with self.assertRaises(AkedbCompatibleRouteError) as raised:
            resolve_akedb_compatible_route(
                "/api/akedb-compatible/SkillData/%2E%2E%2Fsecret.json"
            )
        self.assertEqual(404, raised.exception.status)

    def test_invalid_known_resource_name_is_bad_request(self):
        with self.assertRaises(AkedbCompatibleRouteError) as raised:
            resolve_akedb_compatible_route(
                "/api/akedb-compatible/TableCfg-1.0@1/not-safe!.json"
            )
        self.assertEqual(400, raised.exception.status)

    def test_unknown_collection_is_not_found(self):
        with self.assertRaises(AkedbCompatibleRouteError) as raised:
            resolve_akedb_compatible_route(
                "/api/akedb-compatible/UnknownData/sample.json"
            )
        self.assertEqual(404, raised.exception.status)


if __name__ == "__main__":
    unittest.main()
