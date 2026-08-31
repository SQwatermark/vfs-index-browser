import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from server import BrowserHandler


class AkedbCompatibleApiTests(unittest.TestCase):
    def make_handler(self):
        handler = object.__new__(BrowserHandler)
        handler.db_path = Path("unused.sqlite")
        responses = []
        handler.send_json = lambda payload, status=200, **kwargs: responses.append(
            (status, payload, kwargs)
        )
        handler.send_error_json = lambda status, message: responses.append(
            (status, {"error": message}, {})
        )
        return handler, responses

    def test_table_route_resolves_exact_vfs_logical_id(self):
        handler, responses = self.make_handler()
        resolved = ({"id": 7}, Path("chunk.bin"))
        requested = []
        handler.resolve_logical_file_source = lambda logical_id: (
            requested.append(logical_id) or resolved
        )
        handler.parse_tablecfg_file = lambda record, chunk: (
            {"name": "SampleTable", "data": {"rows": [1, 2]}},
            b"unused",
        )

        handler.handle_akedb_compatible("/api/akedb-compatible/TableCfg-1.0@1/SampleTable.json")

        self.assertEqual(["Table/Data/TableCfg/SampleTable.bytes"], requested)
        self.assertEqual(200, responses[0][0])
        self.assertEqual({"rows": [1, 2]}, responses[0][1])
        self.assertEqual(
            "vfs-index-browser",
            responses[0][2]["extra_headers"]["X-Endaxis-Source"],
        )

    def test_collection_manifest_exposes_only_exact_effective_children(self):
        handler, responses = self.make_handler()
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "index.sqlite"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "CREATE TABLE entries(scope TEXT, parent TEXT, type TEXT, name TEXT)"
                )
                connection.executemany(
                    "INSERT INTO entries VALUES (?, ?, ?, ?)",
                    [
                        ("effective", "JsonData/Data/Json/SkillData", "file", "b.json"),
                        ("effective", "JsonData/Data/Json/SkillData", "file", "a.json"),
                        ("Persistent", "JsonData/Data/Json/SkillData", "file", "old.json"),
                        ("effective", "JsonData/Data/Json/BuffData", "file", "buff.json"),
                        ("effective", "JsonData/Data/Json/SkillData", "dir", "nested"),
                    ],
                )
                connection.commit()
            handler.db_path = database

            handler.handle_akedb_compatible(
                "/api/akedb-compatible/SkillData/manifest.json"
            )

        self.assertEqual(
            [
                {"contentFile": "/api/akedb-compatible/SkillData/a.json"},
                {"contentFile": "/api/akedb-compatible/SkillData/b.json"},
            ],
            responses[0][1],
        )

    def test_rejects_path_traversal_in_compatibility_route(self):
        handler, responses = self.make_handler()

        handler.handle_akedb_compatible(
            "/api/akedb-compatible/SkillData/%2E%2E%2Fsecret.json"
        )

        self.assertEqual(404, responses[0][0])

    def test_projectile_manifest_and_file_use_exact_component_shape(self):
        handler, responses = self.make_handler()
        handler.resolve_installed_manifest_index = lambda: type(
            "Index",
            (),
            {
                "assets_in_directory": lambda _self, _path: [
                    {"name": "data_projectile_sample.asset"},
                ],
            },
        )()
        handler.build_projectile_document = lambda projectile_id: {
            "projectileComponentData": {"id": projectile_id, "speed": 12},
        }

        handler.handle_akedb_compatible(
            "/api/akedb-compatible/ProjectileData/manifest.json"
        )
        handler.handle_akedb_compatible(
            "/api/akedb-compatible/ProjectileData/projectile_sample.json"
        )

        self.assertEqual(
            [
                {
                    "contentFile": (
                        "/api/akedb-compatible/ProjectileData/projectile_sample.json"
                    ),
                }
            ],
            responses[0][1],
        )
        self.assertEqual(
            {"id": "projectile_sample", "speed": 12},
            responses[1][1],
        )

    def test_ability_entity_manifest_and_file_use_template_shape(self):
        handler, responses = self.make_handler()
        handler.resolve_installed_manifest_index = lambda: type(
            "Index",
            (),
            {
                "assets_in_directory": lambda _self, _path: [
                    {"name": "data_abilityentity_sample.asset"},
                ],
            },
        )()
        handler.build_ability_entity_document = lambda entity_id: {
            "abilityEntityTemplateData": {"gameId": entity_id, "durationSeconds": 3},
        }

        handler.handle_akedb_compatible(
            "/api/akedb-compatible/AbilityEntityData/manifest.json"
        )
        handler.handle_akedb_compatible(
            "/api/akedb-compatible/AbilityEntityData/abilityentity_sample.json"
        )

        self.assertEqual(
            [
                {
                    "contentFile": (
                        "/api/akedb-compatible/AbilityEntityData/"
                        "abilityentity_sample.json"
                    ),
                }
            ],
            responses[0][1],
        )
        self.assertEqual(
            {"gameId": "abilityentity_sample", "durationSeconds": 3},
            responses[1][1],
        )


if __name__ == "__main__":
    unittest.main()
