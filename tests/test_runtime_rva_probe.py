import json
import unittest
from pathlib import Path

from tools.probe_runtime_rvas import format_protection


class RuntimeRvaProbeTests(unittest.TestCase):
    def test_formats_memory_protection_flags(self):
        self.assertEqual("EXECUTE_READ", format_protection(0x20))
        self.assertEqual("READWRITE|GUARD", format_protection(0x04 | 0x100))

    def test_committed_probe_rvas_are_hex_strings(self):
        path = Path(__file__).parents[1] / "docs/research/combat-runtime-probes.json"
        probe_set = json.loads(path.read_text(encoding="utf-8"))

        rvas = [probe["rva"] for probe in probe_set["probes"]]
        self.assertTrue(all(rva.startswith("0x") for rva in rvas))
        self.assertEqual(0x06CA72BC, int(rvas[3], 0))


if __name__ == "__main__":
    unittest.main()
