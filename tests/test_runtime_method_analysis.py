import unittest

from tools.analyze_runtime_method_probes import (
    analyze_code,
    build_symbol_map,
    merge_ranges,
)


class RuntimeMethodAnalysisTests(unittest.TestCase):
    def test_walks_conditional_branches_and_records_direct_calls(self):
        # jne +6; call 0x1020; ret; ret
        code = bytes.fromhex("7506e819000000c3c3")

        result = analyze_code(code, 0x1000, {0x1020: ["Example::Target()"]})

        self.assertEqual(4, result["reachableInstructionCount"])
        self.assertEqual(
            [
                {
                    "siteRva": "0x1002",
                    "targetRva": "0x1020",
                    "symbols": ["Example::Target()"],
                }
            ],
            result["directCalls"],
        )
        self.assertEqual(["0x1007", "0x1008"], result["returnSites"])

    def test_stops_at_indirect_jump_without_decoding_unreachable_bytes(self):
        result = analyze_code(bytes.fromhex("ffe0cccc"), 0x2000)

        self.assertEqual(1, result["reachableInstructionCount"])
        self.assertEqual(
            [{"siteRva": "0x2000", "instruction": "jmp"}],
            result["indirectBranches"],
        )

    def test_does_not_revisit_a_loop(self):
        result = analyze_code(bytes.fromhex("ebfe"), 0x3000)

        self.assertEqual(1, result["reachableInstructionCount"])
        self.assertEqual([], result["returnSites"])

    def test_stops_at_trap_padding_before_the_next_method(self):
        result = analyze_code(bytes.fromhex("ccc3"), 0x4000)

        self.assertEqual(1, result["reachableInstructionCount"])
        self.assertEqual([], result["returnSites"])

    def test_builds_method_and_property_accessor_symbols(self):
        index = {
            "types": [
                {
                    "qualifiedName": "Example.Skill",
                    "methods": [
                        {"rva": 0x10, "signature": "System.Boolean Check()"}
                    ],
                    "properties": [
                        {"signature": "cost  get=0x00000020  set=0x00000030"}
                    ],
                }
            ]
        }

        self.assertEqual(
            {
                0x10: ["Example.Skill::System.Boolean Check()"],
                0x20: ["Example.Skill::cost.get"],
                0x30: ["Example.Skill::cost.set"],
            },
            build_symbol_map(index),
        )

    def test_merges_adjacent_instruction_ranges(self):
        self.assertEqual(
            [{"startRva": "0x10", "endRva": "0x15", "byteCount": 5}],
            merge_ranges([(0x12, 0x15), (0x10, 0x12)]),
        )


if __name__ == "__main__":
    unittest.main()
