import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from manifest_index import ManifestIndex
from tests.test_inspect_npc_avatar_mesh import SAMPLE


def string_path_hash(entries):
    strings = bytearray()
    mappings = []
    for path_hash, path in entries:
        encoded = path.encode("utf-16-le")
        offset = len(strings)
        strings.extend(struct.pack("<i", len(encoded)))
        strings.extend(encoded)
        mappings.append(struct.pack("<qii", path_hash, offset, 0))
    count = len(entries)
    string_base = 8 + count * 8 + count * 16
    return (
        struct.pack("<II", string_base, count)
        + b"\0" * (count * 8)
        + b"".join(mappings)
        + strings
    )


class ServerAvatarResourceTests(unittest.TestCase):
    def test_recognizes_only_concrete_npc_avatar_mesh_assets(self):
        self.assertTrue(server.is_avatar_mesh_asset_path(
            "Assets/Beyond/DynamicAssets/Gameplay/NPC/AvatarMesh/Actor/"
            "data_npc_avatarmesh_andrew.asset"
        ))
        self.assertFalse(server.is_avatar_mesh_asset_path(
            "Assets/Beyond/DynamicAssets/GameData/GameplayConfig/GameplayTagConfig/"
            "data_tag_npc_avatarmesh.asset"
        ))

    def test_recognizes_supported_model_entry_paths(self):
        self.assertTrue(server.is_model_entry_path("Assets/Character/sample.prefab"))
        self.assertTrue(server.is_model_entry_path(
            "Assets/Beyond/DynamicAssets/Gameplay/NPC/AvatarMesh/Actor/"
            "data_npc_avatarmesh_andrew.asset"
        ))
        self.assertFalse(server.is_model_entry_path("Assets/Effects/sample.asset"))

    def test_materializes_effective_string_path_hash_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_chunk = root / "old.chk"
            current_chunk = root / "current.chk"
            old_chunk.write_bytes(b"old")
            current_chunk.write_bytes(b"current")
            database = root / "vfs.sqlite"
            conn = sqlite3.connect(database)
            try:
                conn.executescript("""
                    CREATE TABLE files (
                        id INTEGER PRIMARY KEY, source TEXT, logical_id TEXT,
                        chunk_path TEXT, chunk_exists INTEGER, offset INTEGER,
                        length INTEGER, encrypted INTEGER, file_data_md5 TEXT
                    );
                    CREATE TABLE entries (
                        scope TEXT, type TEXT, path TEXT, file_id INTEGER
                    );
                """)
                conn.executemany(
                    "INSERT INTO files VALUES (?, ?, ?, ?, 1, 0, ?, 0, '')",
                    [
                        (1, "StreamingAssets", server.STRING_PATH_HASH_LOGICAL_ID, str(old_chunk), 3),
                        (2, "Persistent", server.STRING_PATH_HASH_LOGICAL_ID, str(current_chunk), 7),
                    ],
                )
                conn.execute(
                    "INSERT INTO entries VALUES ('effective', 'file', ?, 2)",
                    (server.STRING_PATH_HASH_LOGICAL_ID,),
                )
                conn.commit()
            finally:
                conn.close()

            handler = object.__new__(server.BrowserHandler)
            handler.db_path = database
            with patch.object(server, "INTERNAL_CACHE_DIR", root / "cache"):
                first, first_meta = handler.ensure_string_path_hash_file()
                second, second_meta = handler.ensure_string_path_hash_file()

            self.assertEqual(b"current", first.read_bytes())
            self.assertEqual(first, second)
            self.assertEqual(2, first_meta["source"]["recordId"])
            self.assertEqual(first_meta, second_meta)

    def test_avatar_plan_handler_connects_dump_hash_and_manifest_layers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "manifest.sqlite"
            conn = sqlite3.connect(manifest_path)
            try:
                conn.executescript("""
                    CREATE TABLE bundles (bundle_index INTEGER PRIMARY KEY, name TEXT);
                    CREATE TABLE assets (
                        asset_index INTEGER PRIMARY KEY, path TEXT, parent TEXT,
                        name TEXT, bundle_index INTEGER, size INTEGER, path_hash TEXT
                    );
                """)
                conn.executemany(
                    "INSERT INTO bundles VALUES (?, ?)",
                    [(1, "character.ab"), (2, "material.ab")],
                )
                conn.executemany(
                    "INSERT INTO assets VALUES (?, ?, '', ?, ?, 1, '')",
                    [
                        (10, "Assets/Npc/Character.fbx##Body", "Body", 1),
                        (11, "Assets/Npc/Character.fbx##CharacterAvatar", "Avatar", 1),
                        (12, "Assets/Npc/Body.mat", "Body.mat", 2),
                    ],
                )
                conn.commit()
            finally:
                conn.close()
            dump_path = root / "avatar.txt"
            dump_path.write_text(SAMPLE, encoding="utf-8")
            hash_path = root / "StringPathHash.bin"
            hash_path.write_bytes(string_path_hash([
                (100, "Assets/Npc/sample.prefab"),
                (101, "Assets/Npc/Character.fbx##Body"),
                (201, "Assets/Npc/Body.mat"),
            ]))
            asset = {
                "asset_index": 7,
                "path": (
                    "assets/beyond/dynamicassets/gameplay/npc/avatarmesh/actor/"
                    "data_npc_avatarmesh_sample.asset"
                ),
            }
            handler = object.__new__(server.BrowserHandler)
            payloads = []
            handler.resolve_manifest_asset_source = lambda _query: (
                ManifestIndex(manifest_path), asset, {"id": 4}, root / "bundle.chk"
            )
            handler.ensure_manifest_monobehaviour_dump = lambda *_args: (
                dump_path, {"builtAtEpoch": 1, "source": {}}
            )
            handler.ensure_string_path_hash_file = lambda: (
                hash_path, {"builtAtEpoch": 2}
            )
            handler.send_json = lambda value, **_kwargs: payloads.append(value)
            handler.send_error_json = lambda status, message: self.fail(
                f"unexpected HTTP {status}: {message}"
            )

            handler.handle_manifest_asset_avatar_plan({})

            self.assertEqual(1, len(payloads))
            self.assertEqual("avatarMeshResourcePlan", payloads[0]["kind"])
            self.assertEqual(10, payloads[0]["plan"]["parts"][0]["meshAsset"]["assetIndex"])
            self.assertEqual(11, payloads[0]["plan"]["avatarAsset"]["assetIndex"])


if __name__ == "__main__":
    unittest.main()
