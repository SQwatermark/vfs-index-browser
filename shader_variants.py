"""Match generated Unity shader variants against exported material toggles."""

from __future__ import annotations

import re
from dataclasses import dataclass


TOGGLE_PATTERN = re.compile(r"\[Toggle\(([^)]+)\)\]\s+([A-Za-z_][A-Za-z0-9_]*)")
CONDITION_PATTERN = re.compile(r"(!?)defined\(([^)]+)\)")
INCLUDE_PATTERN = re.compile(r'#include\s+"([^"]+)"')


@dataclass(frozen=True)
class ShaderVariant:
    include: str
    enabled_runtime_keywords: tuple[str, ...]
    disabled_runtime_keywords: tuple[str, ...]


def shader_toggle_properties(shader_text: str) -> dict[str, str]:
    """Return keyword-to-material-property mappings declared by Toggle attributes."""

    return {
        keyword.strip(): property_name
        for keyword, property_name in TOGGLE_PATTERN.findall(shader_text)
    }


def active_material_keywords(
    shader_text: str, material_floats: dict[str, object]
) -> tuple[set[str], set[str]]:
    toggle_properties = shader_toggle_properties(shader_text)
    active = {
        keyword
        for keyword, property_name in toggle_properties.items()
        if material_floats.get(property_name) == 1.0
    }
    return active, set(toggle_properties)


def fragment_variants(
    shader_text: str,
    active_local_keywords: set[str],
    local_keywords: set[str],
) -> list[ShaderVariant]:
    """Find fragment variants compatible with all material-controlled keywords."""

    in_fragment_stage = False
    saw_fragment_include = False
    current_requirements: dict[str, bool] | None = None
    variants = []
    for line in shader_text.splitlines():
        if "// Stage: Fragment" in line and "Blob:" not in line:
            in_fragment_stage = True
            continue
        if in_fragment_stage and line.strip() == "#endif":
            if saw_fragment_include:
                break
            continue
        if not in_fragment_stage:
            continue
        stripped = line.strip()
        if stripped.startswith(("#if ", "#elif ")):
            current_requirements = {
                keyword: not bool(negated)
                for negated, keyword in CONDITION_PATTERN.findall(stripped)
            }
            continue
        if current_requirements is None:
            continue
        include_match = INCLUDE_PATTERN.search(stripped)
        if not include_match or "Fragment_" not in include_match.group(1):
            continue
        saw_fragment_include = True
        local_matches = all(
            required == (keyword in active_local_keywords)
            for keyword, required in current_requirements.items()
            if keyword in local_keywords
        )
        if local_matches:
            enabled_runtime = tuple(
                sorted(
                    keyword
                    for keyword, required in current_requirements.items()
                    if required and keyword not in local_keywords
                )
            )
            disabled_runtime = tuple(
                sorted(
                    keyword
                    for keyword, required in current_requirements.items()
                    if not required and keyword not in local_keywords
                )
            )
            variants.append(
                ShaderVariant(
                    include=include_match.group(1),
                    enabled_runtime_keywords=enabled_runtime,
                    disabled_runtime_keywords=disabled_runtime,
                )
            )
        current_requirements = None
    return variants
