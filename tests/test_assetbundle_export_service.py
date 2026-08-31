import unittest
from pathlib import Path

from assetbundle_export_service import AssetBundleExportError, AssetBundleExportService


class FakeWorkerService:
    def __init__(self):
        self.calls = []

    def ensure_map(self, record, chunk_path, *, cancel_event=None):
        self.calls.append(("map", record, chunk_path, cancel_event))
        return {"assetEntries": [], "selectedRun": "map-1"}

    def ensure_preview_export(
        self,
        record,
        chunk_path,
        map_meta,
        *,
        cancel_event=None,
    ):
        self.calls.append(
            ("preview", record, chunk_path, map_meta, cancel_event)
        )
        return Path("export"), {"selectedRun": "preview-1"}


class AssetBundleExportServiceTests(unittest.TestCase):
    def test_coordinates_map_and_preview_without_changing_results(self):
        worker = FakeWorkerService()
        service = AssetBundleExportService(worker)
        record = {"id": 7}
        chunk = Path("bundle.chk")
        cancel = object()

        map_meta = service.ensure_map(record, chunk, cancel_event=cancel)
        preview = service.ensure_preview(
            record,
            chunk,
            map_meta,
            cancel_event=cancel,
        )

        self.assertEqual("map-1", map_meta["selectedRun"])
        self.assertEqual(Path("export"), preview[0])
        self.assertEqual(
            [
                ("map", record, chunk, cancel),
                ("preview", record, chunk, map_meta, cancel),
            ],
            worker.calls,
        )

    def test_maps_map_failure_to_stable_document(self):
        class FailingWorker:
            @staticmethod
            def ensure_map(*_args, **_kwargs):
                raise RuntimeError("invalid AssetMap")

        with self.assertRaises(AssetBundleExportError) as raised:
            AssetBundleExportService(FailingWorker()).ensure_map({}, Path("x"))

        self.assertEqual("map", raised.exception.stage)
        self.assertEqual(
            {
                "kind": "assetBundle",
                "status": "mapFailed",
                "message": "invalid AssetMap",
            },
            raised.exception.document(),
        )

    def test_maps_preview_failure_separately(self):
        class FailingWorker:
            @staticmethod
            def ensure_preview_export(*_args, **_kwargs):
                raise OSError("preview cache unavailable")

        with self.assertRaises(AssetBundleExportError) as raised:
            AssetBundleExportService(FailingWorker()).ensure_preview(
                {}, Path("x"), {}
            )

        self.assertEqual("preview", raised.exception.stage)
        self.assertEqual("exportFailed", raised.exception.status)

    def test_rejects_unknown_failure_stage(self):
        with self.assertRaises(ValueError):
            AssetBundleExportError("unknown", "failure")


if __name__ == "__main__":
    unittest.main()
