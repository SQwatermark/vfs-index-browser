#!/usr/bin/env python3
"""Analyze selected IL2CPP methods from a virtual-RVA runtime module snapshot."""

from __future__ import annotations

import argparse
import json
import mmap
import re
from collections import deque
from pathlib import Path

from tools.analyze_runtime_method_probes import (
    PROPERTY_ACCESSOR_RE,
    build_symbol_map,
    merge_ranges,
    parse_address,
)


EXECUTE_PROTECTIONS = {0x10, 0x20, 0x40, 0x80}


def runtime_regions(metadata: dict) -> list[dict]:
    regions = []
    for item in metadata.get("regions", []):
        start = parse_address(item["rva"])
        size = parse_address(item["size"])
        regions.append(
            {
                **item,
                "start": start,
                "end": start + size,
                "executable": int(item.get("protect", 0)) in EXECUTE_PROTECTIONS,
            }
        )
    return regions


def find_region(regions: list[dict], rva: int) -> dict | None:
    return next((item for item in regions if item["start"] <= rva < item["end"]), None)


def select_methods(type_index: dict, pattern: str) -> list[dict]:
    matcher = re.compile(pattern, re.IGNORECASE)
    selected = []
    for type_info in type_index.get("types", []):
        type_name = type_info.get("qualifiedName") or type_info.get("name", "<unknown>")
        for method in type_info.get("methods", []):
            identity = f"{type_name}::{method.get('signature', '<unknown>()')}"
            # 泛型定义等条目可能没有可定位的 RVA；它们不属于运行时快照分析目标。
            if matcher.search(identity) and method.get("rva") is not None:
                selected.append(
                    {
                        "type": type_name,
                        "signature": method.get("signature"),
                        "token": method.get("token"),
                        "rva": parse_address(method["rva"]),
                    }
                )
        for prop in type_info.get("properties", []):
            signature = prop.get("signature", "")
            property_name = signature.split(maxsplit=1)[0] if signature else "<unknown>"
            for accessor, rva_hex in PROPERTY_ACCESSOR_RE.findall(signature):
                accessor_signature = f"{property_name}.{accessor}()"
                identity = f"{type_name}::{accessor_signature}"
                if matcher.search(identity):
                    selected.append(
                        {
                            "type": type_name,
                            "signature": accessor_signature,
                            "token": None,
                            "rva": int(rva_hex, 16),
                        }
                    )
    return selected


def analyze_snapshot_control_flow(
    snapshot: mmap.mmap,
    entry_rva: int,
    regions: list[dict],
    symbol_map: dict[int, list[str]],
    max_instructions: int,
) -> dict:
    """Walk branches across the full snapshot without following calls."""
    try:
        from capstone import CS_ARCH_X86, CS_GRP_CALL, CS_GRP_JUMP, CS_GRP_RET
        from capstone import CS_MODE_64, Cs
        from capstone.x86 import X86_OP_IMM
    except ImportError as error:
        raise RuntimeError(
            "capstone is required; install requirements-research.txt"
        ) from error

    decoder = Cs(CS_ARCH_X86, CS_MODE_64)
    decoder.detail = True
    pending = deque([entry_rva])
    visited_blocks: set[int] = set()
    visited_instructions: dict[int, object] = {}
    direct_calls: list[dict] = []
    indirect_calls: list[dict] = []
    external_branches: list[dict] = []
    indirect_branches: list[dict] = []
    return_sites: list[int] = []
    truncated = False

    def direct_target(instruction) -> int | None:
        if instruction.operands and instruction.operands[0].type == X86_OP_IMM:
            return instruction.operands[0].imm
        return None

    def is_executable(rva: int) -> bool:
        region = find_region(regions, rva)
        return bool(region and region.get("dumped") and region["executable"])

    while pending and not truncated:
        block_rva = pending.popleft()
        if block_rva in visited_blocks or not is_executable(block_rva):
            continue
        visited_blocks.add(block_rva)
        cursor = block_rva

        while is_executable(cursor):
            if cursor in visited_instructions:
                break
            if len(visited_instructions) >= max_instructions:
                truncated = True
                break

            decoded = list(decoder.disasm(snapshot[cursor : cursor + 15], cursor, count=1))
            if not decoded:
                break
            instruction = decoded[0]
            visited_instructions[cursor] = instruction
            fallthrough = cursor + instruction.size

            if instruction.mnemonic in {"int3", "ud2", "hlt"}:
                break

            if instruction.group(CS_GRP_CALL):
                target = direct_target(instruction)
                call = {
                        "siteRva": hex(cursor),
                        "instruction": instruction.mnemonic,
                        "operands": instruction.op_str,
                }
                if target is None:
                    indirect_calls.append(call)
                else:
                    direct_calls.append(
                        {
                            **call,
                            "targetRva": hex(target),
                            "symbols": symbol_map.get(target, []),
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

                target_is_other_method = target != entry_rva and target in symbol_map
                if is_executable(target) and not target_is_other_method:
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

    ranges = [
        (address, address + instruction.size)
        for address, instruction in visited_instructions.items()
    ]
    instructions = [
        {
            "rva": hex(address),
            "bytes": instruction.bytes.hex(),
            "mnemonic": instruction.mnemonic,
            "operands": instruction.op_str,
        }
        for address, instruction in sorted(visited_instructions.items())
    ]
    return {
        "reachableInstructionCount": len(visited_instructions),
        "reachableByteRanges": merge_ranges(ranges),
        "instructions": instructions,
        "directCalls": direct_calls,
        "indirectCalls": indirect_calls,
        "externalBranches": external_branches,
        "indirectBranches": indirect_branches,
        "returnSites": [hex(rva) for rva in sorted(return_sites)],
        "truncated": truncated,
    }


def analyze_snapshot_methods(
    snapshot_path: Path,
    metadata: dict,
    type_index: dict,
    pattern: str,
    max_instructions: int,
) -> dict:
    if metadata.get("layout") != "virtual-rva":
        raise ValueError(f"unsupported snapshot layout: {metadata.get('layout')}")

    module_size = parse_address(metadata["moduleSize"])
    if snapshot_path.stat().st_size != module_size:
        raise ValueError(
            f"snapshot size does not match moduleSize: {snapshot_path.stat().st_size} != {module_size}"
        )

    regions = runtime_regions(metadata)
    symbol_map = build_symbol_map(type_index)
    selected = select_methods(type_index, pattern)
    methods = []

    with snapshot_path.open("rb") as source, mmap.mmap(
        source.fileno(), 0, access=mmap.ACCESS_READ
    ) as snapshot:
        for method in selected:
            rva = method["rva"]
            region = find_region(regions, rva)
            result = {
                **method,
                "rva": hex(rva),
                "region": None,
                "analysis": None,
            }
            if region is None:
                result["error"] = "RVA is outside all snapshot regions"
                methods.append(result)
                continue

            result["region"] = {
                "startRva": hex(region["start"]),
                "endRva": hex(region["end"]),
                "protect": region.get("protect"),
                "dumped": bool(region.get("dumped")),
                "executable": region["executable"],
            }
            if not region.get("dumped"):
                result["error"] = "runtime region was not dumped"
                methods.append(result)
                continue
            if not region["executable"]:
                result["error"] = "RVA is not in an executable runtime region"
                methods.append(result)
                continue

            result["entryBytes"] = snapshot[rva : rva + 32].hex()
            result["analysis"] = analyze_snapshot_control_flow(
                snapshot,
                rva,
                regions,
                symbol_map,
                max_instructions,
            )
            methods.append(result)

    return {
        "format": "EndfieldRuntimeSnapshotMethodAnalysis",
        "version": 1,
        "module": metadata.get("module"),
        "moduleSize": metadata.get("moduleSize"),
        "pattern": pattern,
        "maxInstructionsPerMethod": max_instructions,
        "selectedMethodCount": len(selected),
        "methods": methods,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="virtual-RVA runtime module .bin")
    parser.add_argument("metadata", type=Path, help="runtime module snapshot metadata .json")
    parser.add_argument("type_index", type=Path, help="IL2CPP type index JSON")
    parser.add_argument("--match", required=True, help="regex matched against Type::Signature")
    parser.add_argument("--max-instructions", type=int, default=100000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    type_index = json.loads(args.type_index.read_text(encoding="utf-8"))
    report = analyze_snapshot_methods(
        args.snapshot,
        metadata,
        type_index,
        args.match,
        args.max_instructions,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {args.output} ({report['selectedMethodCount']} methods)")


if __name__ == "__main__":
    main()
