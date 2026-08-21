import mmap
import tempfile
import unittest
from pathlib import Path

from tools.analyze_runtime_snapshot import (
    analyze_snapshot_control_flow,
    analyze_snapshot_methods,
    find_region,
    runtime_regions,
    select_methods,
)


class AnalyzeRuntimeSnapshotTests(unittest.TestCase):
    def test_select_methods_matches_type_and_signature(self):
        index = {
            "types": [
                {
                    "qualifiedName": "Beyond.Gameplay.Core.Skill",
                    "methods": [
                        {"signature": "System.Boolean IsAvailable()", "rva": 16},
                        {"signature": "System.Void OnTick()", "rva": 32},
                    ],
                    "properties": [
                        {"signature": "isCasting  get=0x00000030"},
                    ],
                }
            ]
        }
        selected = select_methods(index, r"Skill::.*IsAvailable")
        self.assertEqual(
            [(item["signature"], item["rva"]) for item in selected],
            [("System.Boolean IsAvailable()", 16)],
        )

    def test_select_methods_includes_property_accessors(self):
        index = {
            "types": [
                {
                    "qualifiedName": "Beyond.Gameplay.Core.Skill",
                    "properties": [{"signature": "isCasting  get=0x00000030"}],
                }
            ]
        }
        selected = select_methods(index, r"Skill::isCasting\.get")
        self.assertEqual(selected[0]["signature"], "isCasting.get()")
        self.assertEqual(selected[0]["rva"], 0x30)

    def test_select_methods_skips_unresolved_generic_methods(self):
        index = {
            "types": [
                {
                    "qualifiedName": "Beyond.Gameplay.Core.DependencyGraph`1",
                    "methods": [
                        {"signature": "System.Void Start()", "rva": None},
                        {"signature": "System.Void StartResolved()", "rva": 32},
                    ],
                }
            ]
        }

        selected = select_methods(index, r"DependencyGraph.*Start")

        self.assertEqual(
            [(item["signature"], item["rva"]) for item in selected],
            [("System.Void StartResolved()", 32)],
        )

    def test_runtime_region_marks_execute_protection(self):
        regions = runtime_regions(
            {
                "regions": [
                    {"rva": "0x10", "size": "0x20", "protect": 32, "dumped": True}
                ]
            }
        )
        self.assertTrue(regions[0]["executable"])
        self.assertIs(find_region(regions, 0x1F), regions[0])
        self.assertIsNone(find_region(regions, 0x30))

    def test_snapshot_size_must_match_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "runtime.bin"
            snapshot.write_bytes(b"\x00" * 8)
            with self.assertRaisesRegex(ValueError, "snapshot size does not match"):
                analyze_snapshot_methods(
                    snapshot,
                    {"layout": "virtual-rva", "moduleSize": "0x10", "regions": []},
                    {"types": []},
                    ".*",
                    100,
                )

    def test_control_flow_separates_direct_and_indirect_calls(self):
        # call 0x18; call rax; ret
        code = b"\xe8\x13\x00\x00\x00\xff\xd0\xc3" + (b"\x90" * 24)
        with tempfile.TemporaryDirectory() as directory:
            snapshot_path = Path(directory) / "runtime.bin"
            snapshot_path.write_bytes(code)
            with snapshot_path.open("rb") as source, mmap.mmap(
                source.fileno(), 0, access=mmap.ACCESS_READ
            ) as snapshot:
                analysis = analyze_snapshot_control_flow(
                    snapshot,
                    0,
                    [
                        {
                            "start": 0,
                            "end": len(code),
                            "dumped": True,
                            "executable": True,
                        }
                    ],
                    {0x18: ["Target::Method()"]},
                    100,
                )

        self.assertEqual(analysis["directCalls"][0]["targetRva"], "0x18")
        self.assertEqual(analysis["directCalls"][0]["symbols"], ["Target::Method()"])
        self.assertEqual(analysis["indirectCalls"][0]["operands"], "rax")


if __name__ == "__main__":
    unittest.main()
