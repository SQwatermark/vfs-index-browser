import json
import unittest
from pathlib import Path

from tools.probe_runtime_rvas import (
    MEM_COMMIT,
    PAGE_GUARD,
    format_protection,
    is_readable_region,
    iter_aligned_qwords,
)


class RuntimeRvaProbeTests(unittest.TestCase):
    def test_formats_memory_protection_flags(self):
        self.assertEqual("EXECUTE_READ", format_protection(0x20))
        self.assertEqual("READWRITE|GUARD", format_protection(0x04 | 0x100))

    def test_iterates_only_complete_aligned_qwords(self):
        data = bytes.fromhex("01000000000000000200000000000000ff")

        self.assertEqual([(0, 1), (8, 2)], list(iter_aligned_qwords(data)))

    def test_readable_region_requires_committed_unguarded_memory(self):
        self.assertTrue(is_readable_region({"state": MEM_COMMIT, "protect": 0x20}))
        self.assertFalse(is_readable_region({"state": 0x10000, "protect": 0x20}))
        self.assertFalse(
            is_readable_region({"state": MEM_COMMIT, "protect": 0x20 | PAGE_GUARD})
        )
        self.assertFalse(is_readable_region({"state": MEM_COMMIT, "protect": 0x01}))

    def test_committed_probe_rvas_are_hex_strings(self):
        path = Path(__file__).parents[1] / "docs/research/combat-runtime-probes.json"
        probe_set = json.loads(path.read_text(encoding="utf-8"))

        rvas = [probe["rva"] for probe in probe_set["probes"]]
        self.assertTrue(all(rva.startswith("0x") for rva in rvas))
        self.assertEqual(0x06CA72BC, int(rvas[3], 0))


if __name__ == "__main__":
    unittest.main()
