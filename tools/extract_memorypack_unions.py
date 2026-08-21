#!/usr/bin/env python3
"""Recover MemoryPack union tags from an initialized IL2CPP runtime snapshot.

MemoryPack's generated union formatter registers ``derived wrapper type ->
UInt16 tag`` pairs in its static constructor. An initialized runtime snapshot
contains an ``Il2CppType`` for every registered wrapper. Its type-definition
handle can be matched back to the wrapper token in the AI-friendly dump.

The tool updates one union base in an existing union-map JSON. It deliberately
fails on unresolved tags, conflicting anchors, or stale dump/runtime pairs so
that a game update cannot silently produce a partial mapping.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import struct
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable

from capstone import CS_ARCH_X86, CS_MODE_64, Cs
from capstone.x86_const import X86_REG_RIP

try:
    from tools.extract_memorypack_schema import (
        extract_wrapper_from_block,
        iter_class_blocks,
        read_memorypack_dump_file,
        wrapper_name,
    )
except ModuleNotFoundError:
    from extract_memorypack_schema import (
        extract_wrapper_from_block,
        iter_class_blocks,
        read_memorypack_dump_file,
        wrapper_name,
    )


DEFAULT_BASE_CLASS = "Beyond.Gameplay.Core.AbilityAction.AbilityActionData"
TYPE_DEFINITION_TOKEN_BASE = 0x02000001


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True, help="Initialized virtual-RVA runtime .bin snapshot")
    parser.add_argument("--dump-root", type=Path, required=True, help="AI-friendly dump directory")
    parser.add_argument("--union-map", type=Path, required=True, help="Existing union map used as calibration anchors")
    parser.add_argument("--output", type=Path, required=True, help="Updated union map output")
    parser.add_argument("--base-class", default=DEFAULT_BASE_CLASS, help="Runtime union base class")
    parser.add_argument(
        "--max-cctor-bytes",
        type=lambda value: int(value, 0),
        default=0x20000,
        help="Maximum formatter static-constructor bytes to disassemble",
    )
    return parser.parse_args(list(argv))


def load_runtime(runtime_path: Path) -> tuple[bytes, int]:
    metadata_path = runtime_path.with_suffix(".json")
    if not metadata_path.exists():
        raise SystemExit(f"runtime metadata not found: {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("layout") != "virtual-rva":
        raise SystemExit(f"unsupported runtime layout: {metadata.get('layout')!r}")
    return runtime_path.read_bytes(), int(metadata["moduleBase"], 16)


def find_formatter_cctor(lines: list[str], base_class: str) -> int:
    formatter_class = f"{wrapper_name(base_class)}Formatter"
    for class_name, _start, _end, block in iter_class_blocks(lines):
        if class_name != formatter_class:
            continue
        for line in block:
            match = re.search(r"RVA=(0x[0-9A-Fa-f]+).*System\.Void \.cctor\(\)", line)
            if match:
                return int(match.group(1), 16)
        raise SystemExit(f"formatter has no static constructor: {formatter_class}")
    raise SystemExit(f"formatter class not found: {formatter_class}")


def read_qword(image: bytes, offset: int) -> int:
    if offset < 0 or offset + 8 > len(image):
        raise SystemExit(f"runtime read outside snapshot: {offset:#x}")
    return struct.unpack_from("<Q", image, offset)[0]


def extract_tag_slots(image: bytes, cctor_rva: int, max_bytes: int) -> dict[int, int]:
    decoder = Cs(CS_ARCH_X86, CS_MODE_64)
    decoder.detail = True
    instructions = list(decoder.disasm(image[cctor_rva : cctor_rva + max_bytes], cctor_rva))
    result: dict[int, int] = {}
    for index, instruction in enumerate(instructions):
        if result and instruction.mnemonic in {"ret", "jmp"}:
            break
        operands = instruction.operands
        if not (
            instruction.mnemonic == "mov"
            and len(operands) == 2
            and instruction.reg_name(operands[0].reg) == "rcx"
            and operands[1].type == 3
            and operands[1].mem.base == X86_REG_RIP
        ):
            continue
        slot = instruction.address + instruction.size + operands[1].mem.disp
        candidates = instructions[index + 1 : index + 16]
        if not any(candidate.mnemonic == "call" for candidate in candidates):
            continue
        if not any(
            candidate.mnemonic == "mov"
            and len(candidate.operands) == 2
            and candidate.reg_name(candidate.operands[0].reg) == "r9"
            and candidate.operands[1].type == 3
            and candidate.operands[1].mem.base == X86_REG_RIP
            for candidate in candidates
        ):
            continue
        for candidate in candidates:
            candidate_operands = candidate.operands
            if (
                candidate.mnemonic == "mov"
                and len(candidate_operands) == 2
                and candidate.reg_name(candidate_operands[0].reg) == "r8d"
                and candidate_operands[1].type == 2
            ):
                tag = candidate_operands[1].imm
            else:
                continue
            if tag in result and result[tag] != slot:
                raise SystemExit(f"union tag {tag} is registered from multiple slots")
            result[tag] = slot
            break

    # Tag 0 uses ``xor r8d, r8d``. Its wrapper instance is cached through rbx,
    # so the associated Il2CppType slot is the nearest preceding RIP load.
    for index, instruction in enumerate(instructions):
        operands = instruction.operands
        if not (
            instruction.mnemonic == "xor"
            and len(operands) == 2
            and instruction.reg_name(operands[0].reg) == "r8d"
            and instruction.reg_name(operands[1].reg) == "r8d"
            and any(candidate.mnemonic == "call" for candidate in instructions[index + 1 : index + 5])
        ):
            continue
        for candidate in reversed(instructions[max(0, index - 16) : index]):
            candidate_operands = candidate.operands
            if (
                candidate.mnemonic == "mov"
                and len(candidate_operands) == 2
                and candidate.reg_name(candidate_operands[0].reg) == "rbx"
                and candidate_operands[1].type == 3
                and candidate_operands[1].mem.base == X86_REG_RIP
            ):
                result[0] = candidate.address + candidate.size + candidate_operands[1].mem.disp
                break
        if 0 in result:
            break
    if not result:
        raise SystemExit(f"no union registrations found at RVA {cctor_rva:#x}")
    expected = set(range(max(result) + 1))
    if set(result) != expected:
        missing = sorted(expected - set(result))
        raise SystemExit(f"union registration tags are not contiguous; missing {missing[:20]}")
    return result


def extract_wrapper_tokens(lines: list[str]) -> tuple[dict[int, str], dict[str, int]]:
    token_to_type: dict[int, str] = {}
    type_to_token: dict[str, int] = {}
    for wrapper_class, start, end, block in iter_class_blocks(lines):
        wrapper = extract_wrapper_from_block(wrapper_class, start, end, block)
        runtime_type = wrapper.get("instanceType")
        if not runtime_type:
            continue
        token_line = next((line for line in block if line.startswith("TOKEN:")), None)
        if not token_line:
            continue
        token_match = re.search(r"0x[0-9A-Fa-f]+", token_line)
        if not token_match:
            continue
        token = int(token_match.group(0), 16)
        token_to_type[token] = runtime_type
        type_to_token[runtime_type] = token
    return token_to_type, type_to_token


def read_type_definition_handles(image: bytes, module_base: int, tag_slots: dict[int, int]) -> dict[int, int]:
    result: dict[int, int] = {}
    for tag, slot in tag_slots.items():
        il2cpp_type_pointer = read_qword(image, slot)
        type_offset = il2cpp_type_pointer - module_base
        if not 0 <= type_offset <= len(image) - 12:
            raise SystemExit(
                f"tag {tag} metadata slot is not initialized to an Il2CppType inside the snapshot: "
                f"{il2cpp_type_pointer:#x}"
            )
        type_handle, bits = struct.unpack_from("<QI", image, type_offset)
        type_kind = (bits >> 16) & 0xFF
        if type_kind != 0x12:  # IL2CPP_TYPE_CLASS
            raise SystemExit(f"tag {tag} points to unexpected Il2CppType kind {type_kind:#x}")
        result[tag] = type_handle
    return result


def infer_type_definition_layout(
    handles: dict[int, int],
    known_mapping: dict[int, str],
    type_to_token: dict[str, int],
) -> tuple[int, int]:
    anchors = []
    for tag, runtime_type in known_mapping.items():
        token = type_to_token.get(runtime_type)
        handle = handles.get(tag)
        if token is not None and handle is not None:
            anchors.append((handle, token - TYPE_DEFINITION_TOKEN_BASE))
    if len(anchors) < 3:
        raise SystemExit("at least three valid existing union mappings are required as layout anchors")

    size_candidates: Counter[int] = Counter()
    for left_index, (left_handle, left_token_index) in enumerate(anchors):
        for right_handle, right_token_index in anchors[left_index + 1 :]:
            token_delta = right_token_index - left_token_index
            handle_delta = right_handle - left_handle
            if token_delta and handle_delta % token_delta == 0:
                candidate = handle_delta // token_delta
                if 0 < candidate <= 0x400:
                    size_candidates[candidate] += 1
    if not size_candidates:
        raise SystemExit("cannot infer Il2CppTypeDefinition size from union anchors")
    type_definition_size, support = size_candidates.most_common(1)[0]
    if support < 3:
        raise SystemExit("Il2CppTypeDefinition size has insufficient anchor support")

    base_candidates = Counter(
        handle - token_index * type_definition_size
        for handle, token_index in anchors
    )
    type_definition_base, base_support = base_candidates.most_common(1)[0]
    if base_support < 3:
        raise SystemExit("Il2CppTypeDefinition base has insufficient anchor support")
    return type_definition_base, type_definition_size


def recover_mapping(
    handles: dict[int, int],
    token_to_type: dict[int, str],
    type_definition_base: int,
    type_definition_size: int,
) -> dict[int, str]:
    result: dict[int, str] = {}
    for tag, handle in sorted(handles.items()):
        delta = handle - type_definition_base
        if delta < 0 or delta % type_definition_size:
            raise SystemExit(f"tag {tag} has an unaligned type-definition handle: {handle:#x}")
        token = TYPE_DEFINITION_TOKEN_BASE + delta // type_definition_size
        runtime_type = token_to_type.get(token)
        if not runtime_type:
            raise SystemExit(f"tag {tag} resolves to missing wrapper token {token:#x}")
        result[tag] = runtime_type
    return result


def main(argv: Iterable[str]) -> int:
    args = parse_args(argv)
    lines = read_memorypack_dump_file(args.dump_root)
    union_map = json.loads(args.union_map.read_text(encoding="utf-8"))
    known_mapping = {
        int(tag): runtime_type
        for tag, runtime_type in union_map.get(args.base_class, {}).items()
    }
    image, module_base = load_runtime(args.runtime)
    cctor_rva = find_formatter_cctor(lines, args.base_class)
    tag_slots = extract_tag_slots(image, cctor_rva, args.max_cctor_bytes)
    handles = read_type_definition_handles(image, module_base, tag_slots)
    token_to_type, type_to_token = extract_wrapper_tokens(lines)
    type_definition_base, type_definition_size = infer_type_definition_layout(
        handles,
        known_mapping,
        type_to_token,
    )
    recovered = recover_mapping(
        handles,
        token_to_type,
        type_definition_base,
        type_definition_size,
    )
    union_map[args.base_class] = {str(tag): runtime_type for tag, runtime_type in recovered.items()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(union_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Recovered {len(recovered)} tags for {args.base_class}; "
        f"Il2CppTypeDefinition size={type_definition_size:#x}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
