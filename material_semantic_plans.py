"""Build bounded Blender material plans from a local shader archive."""

from __future__ import annotations

from pathlib import Path

from experiments.material_binding_resolver import ShaderSource, resolve_material_bindings
from experiments.material_semantic_ir.character_npr_mapping import (
    VariantIdentity,
    build_blender_parameter_plan,
    build_character_npr_silk_ir,
)
from shader_variants import (
    active_material_keywords,
    fragment_variants,
    select_preview_fragment_variant,
)


ARCHIVE_VERSION = "1.4.4"
CHARACTER_NPR_SHADER = "HGRP/CharacterNPR"
CHARACTER_NPR_PATH = Path(
    "Assets/packages/com.hg.render-pipelines/runtime/shaders/materials/"
    "characternpr/characternpr.shader"
)


def build_blender_material_plans(
    document: dict,
    archive_root: Path,
) -> dict[str, dict]:
    """Return material-id keyed plans for currently supported shader features."""

    candidates = [
        material
        for material in document.get("materials", [])
        if material.get("sourceMaterial", {}).get("shader") == CHARACTER_NPR_SHADER
        and isinstance(material.get("previewPbr", {}).get("silkStockings"), dict)
    ]
    if not candidates:
        return {}

    shader_path = archive_root / CHARACTER_NPR_PATH
    if not shader_path.is_file():
        raise FileNotFoundError(f"CharacterNPR shader archive entry not found: {shader_path}")
    shader_text = shader_path.read_text(encoding="utf-8")

    plans = {}
    for material in candidates:
        source_material = material["sourceMaterial"]
        active_keywords, local_keywords = active_material_keywords(
            shader_text,
            source_material.get("floats", {}),
        )
        selected = select_preview_fragment_variant(
            fragment_variants(shader_text, active_keywords, local_keywords)
        )
        resolution = resolve_material_bindings(
            shader_text,
            ShaderSource(
                archive_version=ARCHIVE_VERSION,
                uri=shader_path.as_posix(),
            ),
            source_material,
        )
        semantic_ir = build_character_npr_silk_ir(
            resolution,
            material_id=material["id"],
            variant=VariantIdentity(
                pass_name=selected.pass_name,
                blob=selected.blob,
                hlsl_uri=(shader_path.parent / selected.include).as_posix(),
                material_keywords=tuple(sorted(active_keywords)),
                runtime_enabled_keywords=selected.enabled_runtime_keywords,
                runtime_disabled_keywords=selected.disabled_runtime_keywords,
            ),
        )
        plans[material["id"]] = build_blender_parameter_plan(semantic_ir)
    return plans
