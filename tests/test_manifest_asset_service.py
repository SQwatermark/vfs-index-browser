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

    @staticmethod
    def _create_database(path: Path, rows) -> None:
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                """
                CREATE TABLE files (
                    id INTEGER PRIMARY KEY,
                    logical_id TEXT NOT NULL,
                    chunk_path TEXT NOT NULL,
                    source TEXT NOT NULL,
                    chunk_exists INTEGER NOT NULL,
                    file_name TEXT NOT NULL
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
