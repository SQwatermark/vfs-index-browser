import sqlite3
import unittest
from pathlib import Path
from types import SimpleNamespace

from application_startup_service import ApplicationStartupService


class RebuildFailure(RuntimeError):
    pass


class ApplicationStartupServiceTests(unittest.TestCase):
    def service(
        self,
        *,
        inspections=None,
        rebuild=None,
        secondary_status="current",
        manifest=None,
    ):
        queue = list(inspections or [{"status": "current"}])
        events = []
        service = ApplicationStartupService(
            lambda _path: queue.pop(0),
            rebuild or (lambda _path: {"status": "rebuilt"}),
            lambda _path, auto: SimpleNamespace(
                index_report={"status": secondary_status, "auto": auto},
                rebuild_report={"status": "notNeeded"},
            ),
            manifest or (lambda _path: {"assetCount": 12}),
            lambda level, event, payload: events.append((level, event, payload)),
            rebuild_error_types=(RebuildFailure,),
        )
        return service, events

    def test_current_startup_skips_rebuild_and_prewarms_dependencies(self):
        service, events = self.service()

        reports = service.run(Path("index.sqlite"), auto_rebuild=True)

        self.assertEqual("current", reports.index_freshness["status"])
        self.assertEqual("notNeeded", reports.index_rebuild["status"])
        self.assertEqual("current", reports.secondary_audio_indexes["status"])
        self.assertEqual("ready", reports.manifest_index["status"])
        self.assertEqual(12, reports.manifest_index["assetCount"])
        self.assertEqual([], events)

    def test_stale_index_rebuilds_and_reaudits(self):
        service, events = self.service(
            inspections=[{"status": "stale"}, {"status": "current"}],
        )

        reports = service.run(Path("index.sqlite"), auto_rebuild=True)

        self.assertEqual("rebuilt", reports.index_rebuild["status"])
        self.assertEqual("current", reports.index_freshness["status"])
        self.assertEqual("index_rebuild_started", events[0][1])

    def test_disabled_auto_rebuild_preserves_unverified_report(self):
        service, _events = self.service(inspections=[{"status": "unverified"}])

        reports = service.run(Path("index.sqlite"), auto_rebuild=False)

        self.assertEqual("unverified", reports.index_freshness["status"])
        self.assertEqual("notNeeded", reports.index_rebuild["status"])
        self.assertFalse(reports.secondary_audio_indexes["auto"])

    def test_reports_independent_rebuild_audio_and_manifest_failures(self):
        def fail_rebuild(_path):
            raise RebuildFailure("cannot rebuild")

        def fail_manifest(_path):
            raise sqlite3.DatabaseError("manifest corrupt")

        service, events = self.service(
            inspections=[{"status": "stale"}],
            rebuild=fail_rebuild,
            secondary_status="stale",
            manifest=fail_manifest,
        )

        reports = service.run(Path("index.sqlite"), auto_rebuild=True)

        self.assertEqual("failed", reports.index_rebuild["status"])
        self.assertEqual("stale", reports.secondary_audio_indexes["status"])
        self.assertEqual("unavailable", reports.manifest_index["status"])
        self.assertEqual(
            [
                "index_rebuild_started",
                "index_rebuild_failed",
                "secondary_audio_index_audit_failed",
                "manifest_prewarm_failed",
            ],
            [event for _level, event, _payload in events],
        )


if __name__ == "__main__":
    unittest.main()
