"""Match generated Unity shader variants against exported material toggles."""

from __future__ import annotations

import re
from dataclasses import dataclass


TOGGLE_PATTERN = re.compile(r"\[Toggle\(([^)]+)\)\]\s+([A-Za-z_][A-Za-z0-9_]*)")
CONDITION_PATTERN = re.compile(r"(!?)defined\(([^)]+)\)")
INCLUDE_PATTERN = re.compile(r'#include\s+"([^"]+)"')
FRAGMENT_INCLUDE_PATTERN = re.compile(
    r"(?:^|/)(Sub\d+_Pass\d+)_Fragment_b(\d+)\.hlsl$"
)
TRANSIENT_PREVIEW_KEYWORDS = frozenset(
    {
        "VFX_CHARACTER_DISSOLVE",
        "_ALPHABLEND_ON",
    }
)


@dataclass(frozen=True)
class ShaderVariant:
    include: str
    enabled_runtime_keywords: tuple[str, ...]
    disabled_runtime_keywords: tuple[str, ...]

    @property
    def pass_name(self) -> str:
        return _fragment_include_identity(self.include)[0]

    @property
    def blob(self) -> int:
        return _fragment_include_identity(self.include)[1]


class ShaderVariantSelectionError(ValueError):
    """Raised when a stable preview variant cannot be selected."""


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


def select_preview_fragment_variant(
    variants: list[ShaderVariant],
) -> ShaderVariant:
    """Select the ordinary opaque preview variant.

    Runtime-only branches such as dissolve are not material identity. Prefer a
    branch where those transient effects are disabled, then require the result
    to be unique so a new shader layout cannot silently change the preview.
    """

    if not variants:
        raise ShaderVariantSelectionError("no compatible fragment variant found")

    def score(variant: ShaderVariant) -> tuple[int, int]:
        enabled = set(variant.enabled_runtime_keywords)
        disabled = set(variant.disabled_runtime_keywords)
        return (
            len(enabled & TRANSIENT_PREVIEW_KEYWORDS),
            -len(disabled & TRANSIENT_PREVIEW_KEYWORDS),
        )

    best_score = min(score(variant) for variant in variants)
    best = [variant for variant in variants if score(variant) == best_score]
    if len(best) != 1:
        includes = ", ".join(variant.include for variant in best)
        raise ShaderVariantSelectionError(
            f"preview variant selection is ambiguous: {includes}"
        )
    return best[0]


def _fragment_include_identity(include: str) -> tuple[str, int]:
    match = FRAGMENT_INCLUDE_PATTERN.search(include)
    if not match:
        raise ShaderVariantSelectionError(
            f"unexpected generated fragment include path: {include}"
        )
    return match.group(1), int(match.group(2))
