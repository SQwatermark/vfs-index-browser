import unittest

from projectile_data import ProjectileUnavailableError
from projectile_service import ProjectileService


class ProjectileServiceTests(unittest.TestCase):
    def service(self, *, resolve_manifest=lambda _logical_id: None):
        return ProjectileService(
            resolve_manifest,
            lambda *_args: None,
            lambda *_args: None,
            lambda *_args, **_kwargs: None,
            lambda _error: False,
            manifest_logical_id="manifest.hgmmap",
            api_version=1,
        )

    def test_reports_missing_local_manifest_as_unavailable(self):
        with self.assertRaisesRegex(
            ProjectileUnavailableError,
            "manifest.hgmmap",
        ):
            self.service().build("projectile_sample")

    def test_decode_status_distinguishes_partial_and_unparsed(self):
        self.assertEqual(
            "unparsed",
            ProjectileService._decode_status({"$unparsed": True, "$partial": True}),
        )
        self.assertEqual(
            "partial",
            ProjectileService._decode_status({"$partial": True}),
        )
        self.assertEqual("decoded", ProjectileService._decode_status({}))


if __name__ == "__main__":
    unittest.main()
