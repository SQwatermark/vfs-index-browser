import unittest
from pathlib import Path

from audio_dialog_rebuild import AudioDialogRebuildError
from secondary_audio_startup import ensure_secondary_audio_indexes


class SecondaryAudioStartupTests(unittest.TestCase):
    def setUp(self):
        self.paths = tuple(Path(name) for name in ("vfs", "audio", "wwise", "root"))
        self.events = []

    @staticmethod
    def report(audio="current", wwise="current"):
        status = "current" if audio == wwise == "current" else "stale"
        return {
            "status": status,
            "audioDialog": {"status": audio},
            "wwise": {"status": wwise},
        }

    def coordinate(self, reports, **options):
        queue = iter(reports)
        return ensure_secondary_audio_indexes(
            *self.paths,
            object(),
            lambda data, _seed: data,
            inspect=lambda *_args: next(queue),
            emit=lambda level, event, payload: self.events.append(
                (level, event, payload)
            ),
            **options,
        )

    def test_current_indexes_do_not_rebuild(self):
        result = self.coordinate(
            [self.report()],
            rebuild_audio=lambda *_args: self.fail("unexpected AudioDialog rebuild"),
            rebuild_wwise=lambda *_args: self.fail("unexpected Wwise rebuild"),
        )

        self.assertEqual("notNeeded", result.rebuild_report["status"])
        self.assertEqual([], self.events)

    def test_rebuilds_both_and_reaudits_after_each_publication(self):
        calls = []
        result = self.coordinate(
            [
                self.report("stale", "stale"),
                self.report("current", "stale"),
                self.report(),
            ],
            rebuild_audio=lambda *_args: (
                calls.append("audio"),
                {"status": "rebuilt"},
            )[1],
            rebuild_wwise=lambda *_args: (
                calls.append("wwise"),
                {"status": "rebuilt"},
            )[1],
        )

        self.assertEqual(["audio", "wwise"], calls)
        self.assertEqual("current", result.index_report["status"])
        self.assertEqual("rebuilt", result.rebuild_report["status"])
        self.assertEqual(
            ["audio_dialog_index_rebuild_started", "wwise_index_rebuild_started"],
            [event for _level, event, _payload in self.events],
        )

    def test_one_failure_does_not_prevent_other_rebuild(self):
        result = self.coordinate(
            [self.report("stale", "stale"), self.report("stale", "current")],
            rebuild_audio=lambda *_args: (_ for _ in ()).throw(
                AudioDialogRebuildError("table failed")
            ),
            rebuild_wwise=lambda *_args: {"status": "rebuilt"},
        )

        self.assertEqual("failed", result.rebuild_report["status"])
        self.assertEqual("table failed", result.rebuild_report["audioDialog"]["message"])
        self.assertEqual("rebuilt", result.rebuild_report["wwise"]["status"])

    def test_disabled_auto_rebuild_only_returns_audit(self):
        result = self.coordinate(
            [self.report("stale", "stale")],
            auto_rebuild=False,
            rebuild_audio=lambda *_args: self.fail("unexpected rebuild"),
            rebuild_wwise=lambda *_args: self.fail("unexpected rebuild"),
        )

        self.assertEqual("stale", result.index_report["status"])
        self.assertEqual("notNeeded", result.rebuild_report["status"])


if __name__ == "__main__":
    unittest.main()
