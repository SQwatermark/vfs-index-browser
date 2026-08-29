import unittest

from cache_versions import CACHE_VERSIONS, CacheVersionRegistry


class CacheVersionRegistryTests(unittest.TestCase):
    def test_exposes_stable_named_versions(self):
        self.assertEqual(33, CACHE_VERSIONS.version("model-snapshot"))
        self.assertEqual(2, CACHE_VERSIONS.version("audio-package"))
        self.assertEqual(2, CACHE_VERSIONS.version("usm-video"))
        self.assertEqual(
            sorted(CACHE_VERSIONS.diagnostics()),
            list(CACHE_VERSIONS.diagnostics()),
        )

    def test_rejects_invalid_versions(self):
        for invalid in (0, -1, True, "1"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    CacheVersionRegistry({"artifact": invalid})

    def test_unknown_artifact_is_an_explicit_programming_error(self):
        with self.assertRaisesRegex(KeyError, "not registered"):
            CACHE_VERSIONS.version("missing")


if __name__ == "__main__":
    unittest.main()
