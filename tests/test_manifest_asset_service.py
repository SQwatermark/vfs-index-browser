import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from manifest_asset_service import ManifestAssetResolutionError, ManifestAssetService


class FakeIndex:
    def __init__(self, assets):
        self.assets = assets

    def asset(self, asset_index):
        return self.assets.get(asset_index)

    def assets_by_name(self, name):
        return self.assets.get(name, [])


class ManifestAssetServiceTests(unittest.TestCase):
    def test_resolves_manifest_fallback_and_readable_bundle(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "index.sqlite"
            manifest = root / "manifest.chk"
            bundle = root / "bundle.chk"
            manifest.write_bytes(b"manifest")
            bundle.write_bytes(b"bundle")
            self._create_database(database, [
                (1, "manifest", str(root / "missing.chk"), "Persistent", 0, "manifest.hgmmap"),
                (2, "manifest", str(manifest), "StreamingAssets", 1, "manifest.hgmmap"),
                (3, "bundle", str(bundle), "Persistent", 1, "Data/Bundles/Windows/main/model.ab"),
            ])
            observed = []
            index = FakeIndex({11: {"asset_index": 11, "bundle_name": "main/model.ab"}})
            service = ManifestAssetService(
                database,
                lambda record, path: (observed.append((record["id"], path)), index)[1],
                lambda source, exists: (0 if exists else 1, source),
            )

            resolved_index, asset, record, chunk = service.resolve(1, 11)

        self.assertIs(index, resolved_index)
        self.assertEqual(11, asset["asset_index"])
        self.assertEqual(3, record["id"])
        self.assertEqual(bundle, chunk)
        self.assertEqual([(2, manifest)], observed)

    def test_resolve_many_deduplicates_and_sorts_asset_indexes(self):
        service = object.__new__(ManifestAssetService)
        observed = []
        service.resolve = lambda manifest_id, asset_index: (
            observed.append((manifest_id, asset_index)),
            asset_index,
        )[1]

        result = service.resolve_many(3, [22, "11", 22])

        self.assertEqual([11, 22], result)
        self.assertEqual([(3, 11), (3, 22)], observed)

    def test_resolve_model_rejects_non_model_asset(self):
        service = object.__new__(ManifestAssetService)
        service.resolve = lambda *_args: (
            object(),
            {"path": "assets/sample.animation"},
            {},
            Path("bundle.chk"),
        )

        with self.assertRaises(ManifestAssetResolutionError) as raised:
            service.resolve_model(3, 11)

        self.assertEqual(400, raised.exception.status)
        self.assertEqual("resource is not a supported model entry", str(raised.exception))

    def test_reports_missing_asset_and_bundle_without_http_dependency(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "index.sqlite"
            manifest = root / "manifest.chk"
            manifest.write_bytes(b"manifest")
            self._create_database(database, [
                (1, "manifest", str(manifest), "Persistent", 1, "manifest.hgmmap"),
            ])
            index = FakeIndex({11: {"asset_index": 11, "bundle_name": "missing.ab"}})
            service = ManifestAssetService(database, lambda *_args: index, lambda *_args: (0,))

            with self.assertRaises(ManifestAssetResolutionError) as missing_asset:
                service.resolve(1, 99)
            with self.assertRaises(ManifestAssetResolutionError) as missing_bundle:
                service.resolve(1, 11)

        self.assertEqual(404, missing_asset.exception.status)
        self.assertEqual("Manifest 中不存在该资源", str(missing_asset.exception))
        self.assertEqual(404, missing_bundle.exception.status)
        self.assertIn("missing.ab", str(missing_bundle.exception))

    def test_maps_manifest_parser_failure_to_stable_client_error(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "index.sqlite"
            manifest = root / "ordinary.chk"
            manifest.write_bytes(b"not a manifest")
            self._create_database(database, [
                (1, "ordinary", str(manifest), "Persistent", 1, "ordinary.bin"),
            ])
            service = ManifestAssetService(
                database,
                lambda *_args: (_ for _ in ()).throw(
                    ValueError("invalid Brotli-compressed HGM manifest")
                ),
                lambda *_args: (0,),
            )

            with self.assertRaises(ManifestAssetResolutionError) as raised:
                service.resolve(1, 11)

        self.assertEqual(400, raised.exception.status)
        self.assertEqual(
            "invalid Brotli-compressed HGM manifest", str(raised.exception)
        )

    def test_resolves_installed_manifest_and_builds_all_name_candidates(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "index.sqlite"
            manifest = root / "manifest.chk"
            manifest.write_bytes(b"manifest")
            self._create_database(database, [
                (7, "manifest", str(manifest), "Persistent", 1, "manifest.hgmmap"),
            ])
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "INSERT INTO entries VALUES ('effective', 'file', 'manifest', 7)"
                )
                connection.commit()
            finally:
                connection.close()
            index = FakeIndex({
                "icon.png": [
                    {"assetIndex": 11, "path": "assets/a/icon.png"},
                    {"assetIndex": 22, "path": "assets/b/icon.png"},
                ]
            })
            service = ManifestAssetService(
                database,
                lambda *_args: index,
                lambda *_args: (0,),
            )

            resolved_index, record, path = service.resolve_installed("manifest")
            document = service.candidates_by_name("manifest", " icon.png ")

        self.assertIs(index, resolved_index)
        self.assertEqual(7, record["id"])
        self.assertEqual(manifest, path)
        self.assertEqual("icon.png", document["name"])
        self.assertEqual([11, 22], [x["assetIndex"] for x in document["candidates"]])
        self.assertIn("manifestId=7&assetIndex=11", document["candidates"][0]["rawUrl"])

    def test_name_query_rejects_path_and_maps_missing_manifest(self):
        service = object.__new__(ManifestAssetService)
        with self.assertRaises(ManifestAssetResolutionError) as invalid:
            service.candidates_by_name("manifest", "../icon.png")
        self.assertEqual(400, invalid.exception.status)

        service.resolve_installed = lambda _logical_id: (_ for _ in ()).throw(
            FileNotFoundError("manifest missing")
        )
        with self.assertRaises(ManifestAssetResolutionError) as missing:
            service.candidates_by_name("manifest", "icon.png")
        self.assertEqual(503, missing.exception.status)

    def test_directory_asset_count_has_no_error_side_effect(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "index.sqlite"
            manifest = root / "manifest.chk"
            manifest.write_bytes(b"manifest")
            self._create_database(database, [
                (7, "manifest", str(manifest), "Persistent", 1, "manifest.hgmmap"),
            ])

            class SummaryIndex:
                @staticmethod
                def summary():
                    return {"assetCount": 23}

            service = ManifestAssetService(
                database,
                lambda *_args: SummaryIndex(),
                lambda *_args: (0,),
            )
            connection = sqlite3.connect(database)
            connection.row_factory = sqlite3.Row
            try:
                self.assertEqual(23, service.asset_count(connection, 7))
                self.assertEqual(0, service.asset_count(connection, 99))
            finally:
                connection.close()

    @staticmethod
    def _create_database(path: Path, rows) -> None:
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                """
                CREATE TABLE files (
                    id INTEGER PRIMARY KEY,
                    logical_id TEXT NOT NULL,
                    chunk_path TEXT NOT NULL,
                    source TEXT NOT NULL,
                    chunk_exists INTEGER NOT NULL,
                    file_name TEXT NOT NULL
                )
                ;
                CREATE TABLE entries (
                    scope TEXT, type TEXT, path TEXT, file_id INTEGER
                )
                """
            )
            connection.executemany(
                "INSERT INTO files VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
            connection.commit()
        finally:
            connection.close()
