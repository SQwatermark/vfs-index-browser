import hashlib
import json
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from manifest_index import ManifestIndex, _ref_int_array


class ManifestDependencyTests(unittest.TestCase):
    @staticmethod
    def write_valid_cache(path: Path, fingerprint: str) -> None:
        conn = sqlite3.connect(path)
        try:
            conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.executemany(
                "INSERT INTO meta VALUES (?, ?)",
                [
                    ("schemaVersion", "3"),
                    ("fingerprint", fingerprint),
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def test_reuses_verified_cache_by_stable_source_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            fingerprint = "a" * 64
            cache_path = cache_dir / f"manifest-{fingerprint}.sqlite"
            self.write_valid_cache(cache_path, fingerprint)
            identity = "vfs-md5:abcd:length:42"
            alias_path = ManifestIndex._source_alias_path(cache_dir, identity)
            alias_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "sourceIdentity": identity,
                        "fingerprint": fingerprint,
                    }
                ),
                encoding="utf-8",
            )

            index = ManifestIndex.ensure_for_source(
                lambda: self.fail("payload should not be read"),
                cache_dir,
                identity,
            )

            self.assertEqual(cache_path, index.cache_path)

    def test_invalid_alias_falls_back_and_is_republished_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            identity = "vfs-md5:abcd:length:42"
            alias_path = ManifestIndex._source_alias_path(cache_dir, identity)
            alias_path.write_text("not json", encoding="utf-8")
            payload = b"compressed manifest"
            rebuilt = ManifestIndex(cache_dir / "rebuilt.sqlite")

            with patch.object(ManifestIndex, "ensure", return_value=rebuilt) as ensure:
                index = ManifestIndex.ensure_for_source(
                    lambda: payload,
                    cache_dir,
                    identity,
                )

            self.assertIs(rebuilt, index)
            ensure.assert_called_once_with(payload, cache_dir)
            alias = json.loads(alias_path.read_text(encoding="utf-8"))
            self.assertEqual(hashlib.sha256(payload).hexdigest(), alias["fingerprint"])
            self.assertEqual([], list(cache_dir.glob(".*.tmp")))

    def test_reports_invalid_compressed_payload_as_manifest_input_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                ValueError, "invalid Brotli-compressed HGM manifest"
            ):
                ManifestIndex.ensure(b"not a Brotli manifest", Path(directory))

            self.assertEqual([], list(Path(directory).glob("manifest-*.sqlite")))

    def test_reads_reference_integer_array(self):
        data = b"head" + struct.pack("<i3i", 3, 1, 4, 2)
        self.assertEqual([1, 4, 2], _ref_int_array(data, 4, 0, 5))

    def test_rejects_out_of_range_dependency(self):
        data = struct.pack("<i2i", 2, 1, 5)
        with self.assertRaises(ValueError):
            _ref_int_array(data, 0, 0, 5)

    def test_queries_direct_and_transitive_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.sqlite"
            conn = sqlite3.connect(path)
            try:
                conn.executescript("""
                    CREATE TABLE bundles (bundle_index INTEGER PRIMARY KEY, name TEXT NOT NULL);
                    CREATE TABLE bundle_dependencies (
                        bundle_index INTEGER NOT NULL,
                        dependency_index INTEGER NOT NULL,
                        kind TEXT NOT NULL,
                        PRIMARY KEY (bundle_index, dependency_index, kind)
                    );
                """)
                conn.executemany(
                    "INSERT INTO bundles VALUES (?, ?)",
                    [(0, "root.ab"), (1, "body.ab"), (2, "shared.ab"), (3, "unused.ab")],
                )
                conn.executemany(
                    "INSERT INTO bundle_dependencies VALUES (?, ?, 'direct')",
                    [(0, 1), (1, 2)],
                )
                conn.commit()
            finally:
                conn.close()
            index = ManifestIndex(path)
            self.assertEqual(["body.ab"], [row["name"] for row in index.bundle_dependencies(0, transitive=False)])
            self.assertEqual(["body.ab", "shared.ab"], [row["name"] for row in index.bundle_dependencies(0)])

    def test_queries_all_exact_asset_filenames_case_insensitively(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.sqlite"
            conn = sqlite3.connect(path)
            try:
                conn.executescript("""
                    CREATE TABLE bundles (bundle_index INTEGER PRIMARY KEY, name TEXT NOT NULL);
                    CREATE TABLE assets (
                        asset_index INTEGER PRIMARY KEY, path TEXT NOT NULL, parent TEXT NOT NULL,
                        name TEXT NOT NULL, bundle_index INTEGER NOT NULL, size INTEGER NOT NULL,
                        path_hash TEXT NOT NULL
                    );
                """)
                conn.execute("INSERT INTO bundles VALUES (7, 'icons.ab')")
                conn.executemany(
                    "INSERT INTO assets VALUES (?, ?, ?, ?, 7, 42, 'hash')",
                    [
                        (1, "assets/bufficon/icon_test.png", "assets/bufficon", "icon_test.png"),
                        (2, "assets/termicon/icon_test.png", "assets/termicon", "icon_test.png"),
                        (3, "assets/bufficon/icon_other.png", "assets/bufficon", "icon_other.png"),
                    ],
                )
                conn.commit()
            finally:
                conn.close()

            rows = ManifestIndex(path).assets_by_name("ICON_TEST.PNG")

            self.assertEqual([1, 2], [row["assetIndex"] for row in rows])
            self.assertEqual(["icons.ab", "icons.ab"], [row["bundleName"] for row in rows])


if __name__ == "__main__":
    unittest.main()
