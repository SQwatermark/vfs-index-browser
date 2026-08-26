#!/usr/bin/env python3
"""Extract MemoryPack schema hints from Il2CppDumper AI-friendly dumps.

The game stores some logical `.json` resources as MemoryPack payloads. The
`*ForMemoryPack` wrapper classes in `MemoryPack.Beyond.dll.cs` expose generated
setter properties whose order matches the serialized member order. This helper
turns those class blocks into a compact JSON schema skeleton, then enriches the
members with runtime field types and offsets from the AI-friendly C# dumps.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import deque
from pathlib import Path
from typing import Iterable


CLASS_RE = re.compile(r"^CLASS: (.+)$")
TOKEN_RE = re.compile(r"^TOKEN:\s+(0x[0-9a-fA-F]+)")
PROPERTY_RE = re.compile(r"^  __([A-Za-z0-9_]+)__\s+set=")
FIELD_OFFSET_RE = re.compile(r"//\s*(?:static\s*@\s*)?(0x[0-9a-fA-F]+)")
GENERIC_RE = re.compile(r"^([^<]+)<(.+)>$")
WRAPPER_INSTANCE_SUFFIXES = ("__realInstance", "___instance", "__instance")

FIELD_MODIFIERS = {
    "public",
    "private",
    "protected",
    "internal",
    "static",
    "readonly",
    "const",
    "volatile",
    "new",
    "unsafe",
}

DEFAULT_ROOT_CLASSES = [
    "Beyond.Gameplay.Core.SkillData",
    "Beyond.Gameplay.Core.BuffData",
    "Beyond.Gameplay.Core.ActionGroupData",
    "Beyond.Gameplay.Core.SequenceActionData",
    "Beyond.Gameplay.Core.CastData",
]

SCALAR_TYPE_PREFIXES = (
    "System.",
    "UnityEngine.",
    "Google.Protobuf.",
)

AMBIGUOUS_SHORT_CLASS_NAMES = {
    "Data",
    "Config",
    "Settings",
    "Context",
    "Item",
    "Entry",
    "Value",
}


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump-root", type=Path, required=True, help="Il2CppDumper AI-friendly dump directory")
    parser.add_argument("--output", type=Path, required=True, help="JSON output path")
    parser.add_argument("--union-map", type=Path, help="Also include all recovered concrete union types as schema roots")
    parser.add_argument(
        "--class",
        dest="classes",
        action="append",
        help="Runtime class name, e.g. Beyond.Gameplay.Core.SkillData. Can be repeated.",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=4,
        help="Maximum depth for following nested MemoryPack wrapper field types. Defaults to 4.",
    )
    return parser.parse_args(list(argv))


def wrapper_name(runtime_class: str) -> str:
    return runtime_class.replace(".", "_").replace("+", "_") + "ForMemoryPack"


def read_memorypack_dump_file(dump_root: Path) -> list[str]:
    path = dump_root / "MemoryPack.Beyond.dll.cs"
    if not path.exists():
        raise SystemExit(f"dump file not found: {path}")
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def iter_class_blocks(lines: list[str]) -> Iterable[tuple[str, int, int, list[str]]]:
    for start, line in enumerate(lines):
        match = CLASS_RE.match(line)
        if not match:
            continue

        end = start
        while end < len(lines) and lines[end] != "END_CLASS":
            end += 1
        yield match.group(1).strip(), start, min(end + 1, len(lines)), lines[start : min(end + 1, len(lines))]


def extract_wrapper_from_block(wrapper_class: str, start: int, end: int, block: list[str]) -> dict:
    members: list[str] = []
    instance_type = None
    extends = None
    for item in block:
        if item.startswith("EXTENDS:"):
            extends = item.split(":", 1)[1].strip()
            continue
        parsed_instance_type = parse_wrapper_instance_type(item)
        if parsed_instance_type:
            instance_type = parsed_instance_type
        property_match = PROPERTY_RE.match(item)
        if property_match:
            name = property_match.group(1)
            if name != "_instance":
                members.append(name)

    return {
        "class": instance_type,
        "wrapper": wrapper_class,
        "instanceType": instance_type,
        "memberCount": len(members),
        "members": members,
        "ownMembers": members,
        "extendsWrapper": extends,
        "lineStart": start + 1,
        "lineEnd": end,
    }


def parse_wrapper_instance_type(line: str) -> str | None:
    if FIELD_OFFSET_RE.search(line) is None:
        return None
    before_comment = line.split("//", 1)[0].strip()
    tokens = remove_field_modifiers(before_comment.split())
    candidate = " ".join(tokens)
    for suffix in WRAPPER_INSTANCE_SUFFIXES:
        if not candidate.endswith(suffix):
            continue
        field_type = candidate[: -len(suffix)].strip()
        return field_type or None
    return None


def extract_wrappers(lines: list[str]) -> dict[str, dict]:
    wrappers_by_class: dict[str, dict] = {}
    for wrapper_class, start, end, block in iter_class_blocks(lines):
        if not wrapper_class.endswith("ForMemoryPack"):
            continue
        wrapper = extract_wrapper_from_block(wrapper_class, start, end, block)
        wrappers_by_class[wrapper_class] = wrapper

    for wrapper_class in wrappers_by_class:
        resolve_wrapper_members(wrapper_class, wrappers_by_class, set())

    wrappers: dict[str, dict] = {}
    for wrapper in wrappers_by_class.values():
        if wrapper.get("instanceType"):
            wrappers[wrapper["instanceType"]] = wrapper
    return wrappers


def resolve_wrapper_members(wrapper_class: str, wrappers_by_class: dict[str, dict], visiting: set[str]) -> list[str]:
    wrapper = wrappers_by_class[wrapper_class]
    if wrapper.get("_membersResolved"):
        return wrapper["members"]
    if wrapper_class in visiting:
        return wrapper["ownMembers"]

    visiting.add(wrapper_class)
    inherited: list[str] = []
    base_wrapper = wrapper.get("extendsWrapper")
    if base_wrapper in wrappers_by_class:
        inherited = resolve_wrapper_members(base_wrapper, wrappers_by_class, visiting)
    members = [*inherited, *wrapper["ownMembers"]]
    wrapper["members"] = members
    wrapper["memberCount"] = len(members)
    wrapper["_membersResolved"] = True
    visiting.remove(wrapper_class)
    return members


def extract_wrapper(wrappers: dict[str, dict], class_name: str) -> dict:
    wrapper = wrappers.get(class_name)
    if wrapper:
        return wrapper
    return {
        "class": class_name,
        "wrapper": wrapper_name(class_name),
        "missing": True,
    }


def parse_runtime_classes(dump_root: Path) -> dict:
    by_name: dict[str, list[dict]] = {}
    all_classes: list[dict] = []
    for path in sorted(dump_root.glob("*.cs")):
        if path.name == "MemoryPack.Beyond.dll.cs":
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for class_name, start, end, block in iter_class_blocks(lines):
            runtime_class = {
                "class": class_name,
                "sourceFile": path.name,
                "lineStart": start + 1,
                "lineEnd": end,
                "token": None,
                "extends": None,
                "implements": [],
                "fieldLines": [],
            }
            in_fields = False
            for item in block:
                token_match = TOKEN_RE.match(item)
                if token_match:
                    runtime_class["token"] = int(token_match.group(1), 16)
                    continue
                if item.startswith("EXTENDS:"):
                    runtime_class["extends"] = item.split(":", 1)[1].strip()
                    continue
                if item.startswith("IMPLEMENTS:"):
                    runtime_class["implements"] = item.split(":", 1)[1].strip().split()
                    continue
                if item == "FIELDS:":
                    in_fields = True
                    continue
                if item in {"PROPERTIES:", "METHODS:", "END_CLASS"}:
                    in_fields = False
                    continue
                if in_fields and FIELD_OFFSET_RE.search(item):
                    runtime_class["fieldLines"].append(item)
            by_name.setdefault(class_name, []).append(runtime_class)
            all_classes.append(runtime_class)
    return {"byName": by_name, "all": all_classes}


def remove_field_modifiers(tokens: list[str]) -> list[str]:
    remaining = list(tokens)
    while remaining and remaining[0] in FIELD_MODIFIERS:
        remaining.pop(0)
    return remaining


def parse_field_line_for_member(line: str, member_name: str) -> dict | None:
    offset_match = FIELD_OFFSET_RE.search(line)
    if not offset_match:
        return None

    before_comment = line.split("//", 1)[0].strip()
    tokens = remove_field_modifiers(before_comment.split())
    if not tokens:
        return None

    candidate = " ".join(tokens)
    suffix_with_space = " " + member_name
    if candidate.endswith(suffix_with_space):
        field_type = candidate[: -len(suffix_with_space)].strip()
    elif candidate.endswith(member_name):
        # Il2CppDumper may emit "Some.Namespace.TypefieldName" with no
        # separating whitespace. The wrapper member name lets us split it.
        field_type = candidate[: -len(member_name)].strip()
    else:
        return None

    if not field_type:
        return None

    return {
        "name": member_name,
        "type": field_type,
        "offset": offset_match.group(1),
        "raw": line.strip(),
        "isStatic": "static" in before_comment.split(),
    }


def find_runtime_field(runtime_class: dict | None, member_name: str) -> dict | None:
    if not runtime_class:
        return None
    matches = [
        parsed
        for line in runtime_class.get("fieldLines", [])
        for parsed in [parse_field_line_for_member(line, member_name)]
        if parsed and not parsed["isStatic"]
    ]
    if not matches:
        return None
    exact_spacing = [match for match in matches if f" {member_name}  //" in match["raw"]]
    return exact_spacing[0] if exact_spacing else matches[0]


def find_runtime_field_in_hierarchy(
    runtime_classes: dict,
    runtime_class: dict | None,
    member_name: str,
    seen: set[str] | None = None,
) -> dict | None:
    field = find_runtime_field(runtime_class, member_name)
    if field or not runtime_class:
        return field

    seen = seen or set()
    class_name = runtime_class["class"]
    if class_name in seen:
        return None
    seen.add(class_name)

    base = runtime_class.get("extends")
    if not base:
        return None
    base_candidates = runtime_classes["byName"].get(base, [])
    base_class = choose_direct_runtime_candidate(base_candidates, [member_name])
    return find_runtime_field_in_hierarchy(runtime_classes, base_class, member_name, seen)


def split_generic_args(args: str) -> list[str]:
    result: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(args):
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
        elif char == "," and depth == 0:
            result.append(args[start:index].strip())
            start = index + 1
    tail = args[start:].strip()
    if tail:
        result.append(tail)
    return result


def strip_type_suffixes(type_name: str) -> tuple[str, list[str]]:
    wrappers: list[str] = []
    current = type_name.strip()
    while current.endswith("[]"):
        wrappers.append("array")
        current = current[:-2].strip()
    return current, wrappers


def analyze_type(type_name: str, wrappers_by_instance: dict[str, dict]) -> dict:
    base_type, wrappers = strip_type_suffixes(type_name)
    generic_match = GENERIC_RE.match(base_type)
    if generic_match:
        generic_type = generic_match.group(1).strip()
        generic_args = split_generic_args(generic_match.group(2))
        nested = [analyze_type(arg, wrappers_by_instance) for arg in generic_args]
        element_types = [item["type"] for item in nested]
        return {
            "type": type_name,
            "baseType": generic_type,
            "kind": "generic",
            "genericArgs": nested,
            "elementTypes": element_types,
            "arrayDepth": len(wrappers),
            "hasWrapper": generic_type in wrappers_by_instance,
            "wrapper": wrappers_by_instance.get(generic_type, {}).get("wrapper"),
        }

    if wrappers:
        return {
            "type": type_name,
            "baseType": base_type,
            "kind": "array",
            "elementTypes": [base_type],
            "arrayDepth": len(wrappers),
            "hasWrapper": base_type in wrappers_by_instance,
            "wrapper": wrappers_by_instance.get(base_type, {}).get("wrapper"),
        }

    is_scalar = base_type.startswith(SCALAR_TYPE_PREFIXES)
    return {
        "type": type_name,
        "baseType": base_type,
        "kind": "scalar" if is_scalar else "object",
        "elementTypes": [],
        "arrayDepth": 0,
        "hasWrapper": base_type in wrappers_by_instance,
        "wrapper": wrappers_by_instance.get(base_type, {}).get("wrapper"),
    }


def collect_referenced_wrapper_types(type_info: dict) -> list[str]:
    found: list[str] = []
    if type_info.get("hasWrapper"):
        found.append(type_info["baseType"])
    for arg in type_info.get("genericArgs", []):
        found.extend(collect_referenced_wrapper_types(arg))
    for element_type in type_info.get("elementTypes", []):
        if element_type != type_info.get("baseType"):
            found.append(element_type)
    return found


def build_derived_index(runtime_classes: dict[str, dict], wrappers_by_instance: dict[str, dict]) -> dict[str, list[str]]:
    derived: dict[str, list[str]] = {}
    for runtime_class in runtime_classes["all"]:
        class_name = runtime_class["class"]
        if class_name not in wrappers_by_instance:
            continue
        base = runtime_class.get("extends")
        if base:
            derived.setdefault(base, []).append(class_name)
    for values in derived.values():
        values.sort()
    return derived


def short_class_name(class_name: str) -> str:
    return class_name.replace("+", ".").rsplit(".", 1)[-1]


def owner_short_class_name(class_name: str) -> str | None:
    parts = class_name.replace("+", ".").split(".")
    if len(parts) < 2:
        return None
    return parts[-2]


def runtime_field_match_count(runtime_classes: dict, runtime_class: dict, member_names: list[str]) -> int:
    return sum(1 for member in member_names if find_runtime_field_in_hierarchy(runtime_classes, runtime_class, member))


def choose_direct_runtime_candidate(candidates: list[dict], member_names: list[str]) -> dict | None:
    if len(candidates) == 1:
        return candidates[0]
    scored = [
        (sum(1 for member in member_names if find_runtime_field(candidate, member)), candidate)
        for candidate in candidates
    ]
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored or scored[0][0] == 0:
        return None
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None
    return scored[0][1]


def choose_runtime_candidate(runtime_classes: dict, candidates: list[dict], member_names: list[str]) -> dict | None:
    if len(candidates) == 1:
        return candidates[0]
    scored = [
        (runtime_field_match_count(runtime_classes, candidate, member_names), candidate)
        for candidate in candidates
    ]
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored or scored[0][0] == 0:
        return None
    tied = [candidate for score, candidate in scored if score == scored[0][0]]
    if len(tied) > 1:
        signatures = [runtime_member_signature(runtime_classes, candidate, member_names) for candidate in tied]
        if any(signature != signatures[0] for signature in signatures[1:]):
            return None
    return scored[0][1]


def block_distance(left: dict, right: dict) -> int | None:
    if left.get("sourceFile") != right.get("sourceFile"):
        return None
    if left["lineEnd"] < right["lineStart"]:
        return right["lineStart"] - left["lineEnd"]
    if right["lineEnd"] < left["lineStart"]:
        return left["lineStart"] - right["lineEnd"]
    return 0


def runtime_candidates_by_name_or_short(runtime_classes: dict, class_name: str) -> list[dict]:
    result = list(runtime_classes["byName"].get(class_name, []))
    seen = {id(candidate) for candidate in result}
    for name, candidates in runtime_classes["byName"].items():
        if short_class_name(name) != class_name:
            continue
        for candidate in candidates:
            if id(candidate) not in seen:
                result.append(candidate)
                seen.add(id(candidate))
    return result


def token_distance(child: dict, owner: dict) -> int | None:
    child_token = child.get("token")
    owner_token = owner.get("token")
    if child_token is None or owner_token is None:
        return None
    distance = child_token - owner_token
    return distance if distance > 0 else None


def choose_contextual_runtime_candidate(
    runtime_classes: dict,
    candidates: list[dict],
    member_names: list[str],
    owner_name: str | None,
) -> dict | None:
    if not candidates or not owner_name:
        return None

    owner_candidates = runtime_candidates_by_name_or_short(runtime_classes, owner_name)
    if not owner_candidates:
        return None

    scored = [
        (runtime_field_match_count(runtime_classes, candidate, member_names), candidate)
        for candidate in candidates
    ]
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored or scored[0][0] == 0:
        return None

    top_score = scored[0][0]
    tied = [candidate for score, candidate in scored if score == top_score]

    token_located: list[tuple[int, dict]] = []
    for candidate in tied:
        distances = [
            distance
            for owner in owner_candidates
            for distance in [token_distance(candidate, owner)]
            if distance is not None
        ]
        if distances:
            token_located.append((min(distances), candidate))

    token_located.sort(key=lambda item: item[0])
    if token_located:
        if len(token_located) > 1 and token_located[0][0] == token_located[1][0]:
            return None
        return token_located[0][1]

    located: list[tuple[int, dict]] = []
    for candidate in tied:
        distances = [
            distance
            for owner in owner_candidates
            for distance in [block_distance(candidate, owner)]
            if distance is not None
        ]
        if distances:
            located.append((min(distances), candidate))

    located.sort(key=lambda item: item[0])
    if not located:
        return None
    if len(located) > 1 and located[0][0] == located[1][0]:
        return None
    return located[0][1]


def runtime_member_signature(runtime_classes: dict, runtime_class: dict, member_names: list[str]) -> tuple[tuple[str, str | None], ...]:
    return tuple(
        (
            member,
            (find_runtime_field_in_hierarchy(runtime_classes, runtime_class, member) or {}).get("type"),
        )
        for member in member_names
    )


def resolve_runtime_class(class_name: str, runtime_classes: dict, member_names: list[str]) -> tuple[dict | None, str | None]:
    candidates = runtime_classes["byName"].get(class_name, [])
    runtime_class = choose_runtime_candidate(runtime_classes, candidates, member_names)
    if runtime_class:
        return runtime_class, None

    short_name = short_class_name(class_name)
    short_name_candidates = runtime_classes["byName"].get(short_name, [])
    runtime_class = choose_runtime_candidate(runtime_classes, short_name_candidates, member_names)
    if runtime_class:
        return runtime_class, short_name

    owner_name = owner_short_class_name(class_name)
    runtime_class = choose_contextual_runtime_candidate(runtime_classes, short_name_candidates, member_names, owner_name)
    if runtime_class:
        return runtime_class, f"{short_name} near {owner_name}"

    if short_name in AMBIGUOUS_SHORT_CLASS_NAMES:
        return None, None

    suffix_matches = [
        candidate
        for name, candidates_for_name in runtime_classes["byName"].items()
        for candidate in candidates_for_name
        if name.endswith("." + short_name) or name.endswith("+" + short_name)
    ]
    runtime_class = choose_runtime_candidate(runtime_classes, suffix_matches, member_names)
    if runtime_class:
        return runtime_class, runtime_class["class"]
    return None, None


def build_class_schema(
    class_name: str,
    wrappers_by_instance: dict[str, dict],
    runtime_classes: dict[str, dict],
    derived_index: dict[str, list[str]],
) -> tuple[dict, list[str]]:
    wrapper = extract_wrapper(wrappers_by_instance, class_name)
    if wrapper.get("missing"):
        return wrapper, []

    runtime_class, runtime_lookup_fallback = resolve_runtime_class(
        class_name,
        runtime_classes,
        wrapper["members"],
    )
    member_details: list[dict] = []
    missing_runtime_fields: list[str] = []
    referenced_types: list[str] = []

    for member in wrapper["members"]:
        runtime_field = find_runtime_field_in_hierarchy(runtime_classes, runtime_class, member)
        if not runtime_field:
            member_details.append({"name": member, "missingRuntimeField": True})
            missing_runtime_fields.append(member)
            continue

        type_info = analyze_type(runtime_field["type"], wrappers_by_instance)
        member_detail = {
            "name": member,
            "type": runtime_field["type"],
            "offset": runtime_field["offset"],
            "typeInfo": type_info,
        }

        direct_derived = derived_index.get(type_info["baseType"], [])
        if direct_derived:
            member_detail["derivedWrapperTypes"] = direct_derived
            member_detail["derivedWrapperCount"] = len(direct_derived)

        member_details.append(member_detail)
        referenced_types.extend(collect_referenced_wrapper_types(type_info))
        referenced_types.extend(direct_derived)

    schema = dict(wrapper)
    schema["runtimeSourceFile"] = runtime_class.get("sourceFile") if runtime_class else None
    schema["runtimeClassName"] = runtime_class.get("class") if runtime_class else None
    schema["runtimeClassLookupFallback"] = runtime_lookup_fallback
    schema["runtimeLineStart"] = runtime_class.get("lineStart") if runtime_class else None
    schema["runtimeLineEnd"] = runtime_class.get("lineEnd") if runtime_class else None
    schema["extends"] = runtime_class.get("extends") if runtime_class else None
    schema["implements"] = runtime_class.get("implements", []) if runtime_class else []
    schema["memberDetails"] = member_details
    schema["missingRuntimeFields"] = missing_runtime_fields
    schema["runtimeClassMissing"] = runtime_class is None
    schema["headerByteExpected"] = wrapper["memberCount"] if wrapper["memberCount"] < 128 else None
    return schema, sorted(set(referenced_types))


def build_recursive_schema(
    root_classes: list[str],
    wrappers_by_instance: dict[str, dict],
    runtime_classes: dict[str, dict],
    max_depth: int,
) -> list[dict]:
    derived_index = build_derived_index(runtime_classes, wrappers_by_instance)
    queue = deque((class_name, 0) for class_name in root_classes)
    seen: set[str] = set()
    schemas: list[dict] = []

    while queue:
        class_name, depth = queue.popleft()
        if class_name in seen:
            continue
        seen.add(class_name)
        schema, referenced_types = build_class_schema(class_name, wrappers_by_instance, runtime_classes, derived_index)
        schema["depth"] = depth
        schemas.append(schema)

        if depth >= max_depth:
            continue
        for referenced_type in referenced_types:
            if referenced_type in seen or referenced_type not in wrappers_by_instance:
                continue
            queue.append((referenced_type, depth + 1))

    return schemas


def main(argv: Iterable[str]) -> int:
    args = parse_args(argv)
    lines = read_memorypack_dump_file(args.dump_root)
    wrappers_by_instance = extract_wrappers(lines)
    runtime_classes = parse_runtime_classes(args.dump_root)
    classes = args.classes or DEFAULT_ROOT_CLASSES
    if args.union_map:
        union_map = json.loads(args.union_map.read_text(encoding="utf-8"))
        classes = include_union_roots(classes, union_map)
    schemas = build_recursive_schema(classes, wrappers_by_instance, runtime_classes, args.max_depth)
    report = {
        "kind": "EndfieldMemoryPackSchema",
        "dumpRoot": str(args.dump_root),
        "sourceFile": "MemoryPack.Beyond.dll.cs",
        "rootClasses": classes,
        "maxDepth": args.max_depth,
        "wrapperCount": len(wrappers_by_instance),
        "runtimeClassCount": len(runtime_classes["all"]),
        "classes": schemas,
        "note": (
            "member order is extracted from generated __field__ setters in *ForMemoryPack wrappers; "
            "runtime field types and offsets are matched by member name from AI-friendly class dumps"
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def include_union_roots(classes: list[str], union_map: dict) -> list[str]:
    """Nested/generic wrapper inheritance is not always in the runtime index."""
    if not isinstance(union_map, dict):
        raise SystemExit("union map must be an object")
    variants = []
    for entries in union_map.values():
        if not isinstance(entries, dict) or any(not isinstance(value, str) or not value for value in entries.values()):
            raise SystemExit("union map entries must map tags to concrete type names")
        variants.extend(entries.values())
    return list(dict.fromkeys([*classes, *sorted(set(variants))]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
