#!/usr/bin/env python3
"""Analyze reachable control flow in captured IL2CPP runtime method bytes."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path


PROPERTY_ACCESSOR_RE = re.compile(r"\b(get|set|add|remove|raise)=0x([0-9A-Fa-f]+)")


def parse_address(value: int | str) -> int:
    return int(value, 0) if isinstance(value, str) else value


def build_symbol_map(type_index: dict) -> dict[int, list[str]]:
    """Collect method and property-accessor symbols by RVA."""
    symbols: dict[int, list[str]] = defaultdict(list)
    for type_info in type_index.get("types", []):
        type_name = type_info.get("qualifiedName") or type_info.get("name", "<unknown>")
        for method in type_info.get("methods", []):
            rva = parse_address(method["rva"])
            signature = method.get("signature", "<unknown>()")
            symbols[rva].append(f"{type_name}::{signature}")

        for prop in type_info.get("properties", []):
            signature = prop.get("signature", "")
            property_name = signature.split(maxsplit=1)[0] if signature else "<unknown>"
            for accessor, rva_hex in PROPERTY_ACCESSOR_RE.findall(signature):
                symbols[int(rva_hex, 16)].append(
                    f"{type_name}::{property_name}.{accessor}"
                )

    return {rva: sorted(set(names)) for rva, names in symbols.items()}


def merge_ranges(ranges: list[tuple[int, int]]) -> list[dict[str, int | str]]:
    if not ranges:
        return []
    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [
        {"startRva": hex(start), "endRva": hex(end), "byteCount": end - start}
        for start, end in merged
    ]


def analyze_code(
    code: bytes,
    entry_rva: int,
    symbol_map: dict[int, list[str]] | None = None,
) -> dict:
    """Walk reachable x64 basic blocks inside one captured byte window."""
    try:
        from capstone import CS_ARCH_X86, CS_GRP_CALL, CS_GRP_JUMP, CS_GRP_RET
        from capstone import CS_MODE_64, Cs
        from capstone.x86 import X86_OP_IMM
    except ImportError as error:
        raise RuntimeError(
            "capstone is required; install requirements-research.txt"
        ) from error

    symbol_map = symbol_map or {}
    window_end = entry_rva + len(code)
    decoder = Cs(CS_ARCH_X86, CS_MODE_64)
    decoder.detail = True

    pending = deque([entry_rva])
    visited_blocks: set[int] = set()
    visited_instructions: dict[int, object] = {}
    direct_calls: list[dict] = []
    external_branches: list[dict] = []
    indirect_branches: list[dict] = []
    return_sites: list[int] = []

    def direct_target(instruction) -> int | None:
        if instruction.operands and instruction.operands[0].type == X86_OP_IMM:
            return instruction.operands[0].imm
        return None

    while pending:
        block_rva = pending.popleft()
        if block_rva in visited_blocks or not entry_rva <= block_rva < window_end:
            continue
        visited_blocks.add(block_rva)
        cursor = block_rva

        while entry_rva <= cursor < window_end:
            if cursor in visited_instructions:
                break
            offset = cursor - entry_rva
            decoded = list(decoder.disasm(code[offset : offset + 15], cursor, count=1))
            if not decoded:
                break
            instruction = decoded[0]
            visited_instructions[cursor] = instruction
            fallthrough = cursor + instruction.size

            # IL2CPP emits int3 after its non-returning exception helpers. Do not
            # fall through into the next, densely packed method in the capture.
            if instruction.mnemonic in {"int3", "ud2", "hlt"}:
                break

            if instruction.group(CS_GRP_CALL):
                target = direct_target(instruction)
                direct_calls.append(
                    {
                        "siteRva": hex(cursor),
                        "targetRva": hex(target) if target is not None else None,
                        "symbols": symbol_map.get(target, []) if target is not None else [],
                    }
                )
                cursor = fallthrough
                continue

            if instruction.group(CS_GRP_RET):
                return_sites.append(cursor)
                break

            if instruction.group(CS_GRP_JUMP):
                target = direct_target(instruction)
                if target is None:
                    indirect_branches.append(
                        {"siteRva": hex(cursor), "instruction": instruction.mnemonic}
                    )
                    break

                if entry_rva <= target < window_end:
                    pending.append(target)
                else:
                    external_branches.append(
                        {
                            "siteRva": hex(cursor),
                            "targetRva": hex(target),
                            "symbols": symbol_map.get(target, []),
                        }
                    )

                if instruction.mnemonic == "jmp":
                    break
                pending.append(fallthrough)
                break

            cursor = fallthrough

    instruction_ranges = [
        (address, address + instruction.size)
        for address, instruction in visited_instructions.items()
    ]
    return {
        "reachableInstructionCount": len(visited_instructions),
        "reachableByteRanges": merge_ranges(instruction_ranges),
        "directCalls": direct_calls,
        "externalBranches": external_branches,
        "indirectBranches": indirect_branches,
        "returnSites": [hex(rva) for rva in sorted(return_sites)],
    }


def analyze_report(probe_report: dict, type_index: dict) -> dict:
    symbol_map = build_symbol_map(type_index)
    methods = []
    for method in probe_report.get("methods", []):
        entry_rva = parse_address(method["resolvedRva"])
        entry_bytes = bytes.fromhex(method.get("entryBytes", ""))
        analysis = analyze_code(entry_bytes, entry_rva, symbol_map)
        methods.append(
            {
                "assembly": method.get("assembly"),
                "type": method.get("type"),
                "method": method.get("method"),
                "token": method.get("token"),
                "entryRva": hex(entry_rva),
                "capturedByteCount": len(entry_bytes),
                **analysis,
            }
        )

    return {
        "format": "CombatRuntimeMethodAnalysis",
        "version": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceFormat": probe_report.get("format"),
        "module": probe_report.get("module"),
        "methodCount": len(methods),
        "methods": methods,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("probe_report", type=Path)
    parser.add_argument("type_index", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    probe_report = json.loads(args.probe_report.read_text(encoding="utf-8"))
    if probe_report.get("format") != "Il2CppMethodProbeReport":
        raise SystemExit(f"unsupported probe report: {probe_report.get('format')!r}")
    type_index = json.loads(args.type_index.read_text(encoding="utf-8"))

    report = analyze_report(probe_report, type_index)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"analyzed {report['methodCount']} runtime methods into {args.output}")


if __name__ == "__main__":
    main()
