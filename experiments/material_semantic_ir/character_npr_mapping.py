"""Compile resolved CharacterNPR bindings into semantic IR and Blender plans.

The module is an isolated research adapter. It emits serializable descriptions
only and never imports Blender or production model code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .validate_ir import validate_references


IR_FORMAT = "MaterialSemanticIR"
IR_VERSION = "0.1.0"
PLAN_FORMAT = "BlenderNodeParameterPlan"
PLAN_VERSION = "0.1.0"
SILK_OP = "endfield.silkStockings.materialState.v1"
SILK_GROUP = "EF_SilkStockings_MaterialState_v1"
BOOLEAN_PROPERTIES = {
    "_SilkStockings",
    "_SilkStockingsAdvance",
    "_UseMetallicGlossMap",
}
SILK_PARAMETER_SOCKETS = (
    ("_SilkStockingsDryColor", "Dry Color"),
    ("_SilkStockingsWetColor", "Wet Color"),
    ("_SilkStockingsColor", "Edge Color"),
    ("_SilkStockingsMinAffect", "Minimum Affect"),
    ("_SilkStockingsMaxAffect", "Maximum Affect"),
    ("_SilkStockingsAdvance", "Advanced"),
    ("_SilkStockingsAnisoDirection", "Anisotropy Direction"),
    ("_SilkStockingsSpecularInt", "Specular Intensity"),
    ("_SilkStockingsSpecularMinAtMinWetness", "Dry Specular Minimum"),
    ("_SilkStockingsSpecularFalloff", "Specular Falloff"),
    ("_SilkStockingsSpecularValue", "Specular Offset"),
)


class CharacterNprMappingError(ValueError):
    """Raised when the bounded CharacterNPR mapping contract is not met."""


@dataclass(frozen=True)
class VariantIdentity:
    pass_name: str
    blob: int
    hlsl_uri: str
    material_keywords: tuple[str, ...]
    runtime_enabled_keywords: tuple[str, ...] = ()
    runtime_disabled_keywords: tuple[str, ...] = ()
    stage: str = "fragment"

    def __post_init__(self):
        if not self.pass_name:
            raise CharacterNprMappingError("variant pass_name must not be empty")
        if self.blob < 0:
            raise CharacterNprMappingError("variant blob must not be negative")
        if not self.hlsl_uri:
            raise CharacterNprMappingError("variant hlsl_uri must not be empty")


def build_character_npr_silk_ir(
    resolution: Mapping[str, Any],
    *,
    material_id: str,
    variant: VariantIdentity,
) -> dict[str, Any]:
    """Build the material-local b391 silk state graph from resolved bindings."""
    _validate_resolution(resolution)
    if not material_id:
        raise CharacterNprMappingError("material_id must not be empty")

    resolved_by_name = {
        binding["name"]: binding for binding in resolution["bindings"]
    }
    _require_properties(
        resolved_by_name,
        {
            "_BaseColor",
            "_BaseMap",
            "_UseMetallicGlossMap",
            "_Smoothness",
            "_MetallicGlossMap",
            "_SilkStockings",
            "_SilkStockingsDryColor",
            "_SilkStockingsWetColor",
            "_SilkStockingsColor",
            "_SilkStockingsMinAffect",
            "_SilkStockingsMaxAffect",
            "_SilkStockingsAdvance",
            "_SilkStockingsAnisoDirection",
            "_SilkStockingsMask",
            "_SilkStockingsSpecularInt",
            "_SilkStockingsSpecularMinAtMinWetness",
            "_SilkStockingsSpecularFalloff",
            "_SilkStockingsSpecularValue",
            "_SilkStockingsRainWetMaskScale",
            "_SilkStockingsAlbedoAffectType",
        },
    )

    silk_enabled = _effective_bool(resolved_by_name["_SilkStockings"])
    advanced = _effective_bool(resolved_by_name["_SilkStockingsAdvance"])
    packed_enabled = _effective_bool(resolved_by_name["_UseMetallicGlossMap"])
    if not silk_enabled:
        raise CharacterNprMappingError("silk mapping requires _SilkStockings != 0")
    _validate_keyword(variant, "_SILK_STOCKINGS", silk_enabled)
    _validate_keyword(variant, "_METALLICSPECGLOSSMAP", packed_enabled)

    ir_bindings = [
        _to_ir_binding(binding, resolution)
        for binding in resolution["bindings"]
    ]
    ir_bindings.extend(_runtime_bindings(variant))
    nodes = []

    def input_node(source_name: str) -> dict[str, str]:
        binding = next(
            item for item in ir_bindings if item["sourceName"] == source_name
        )
        node_id = f"input:{binding['id'].split(':', 1)[1]}"
        if not any(node["id"] == node_id for node in nodes):
            nodes.append(
                {
                    "id": node_id,
                    "op": "input.binding",
                    "inputs": {},
                    "outputs": {"value": binding["type"]},
                    "parameters": {"bindingId": binding["id"]},
                    "space": binding.get("space", "none"),
                    "evidence": binding["evidence"],
                }
            )
        return {"node": node_id, "output": "value"}

    base_texture = _sample_texture_node(
        nodes,
        resolved_by_name["_BaseMap"],
        ir_bindings,
        node_id="sample:baseMap",
        output_color_space="linear",
    )
    nodes.append(
        _node(
            "math:baseColor",
            "math.multiply",
            {"left": base_texture, "right": input_node("_BaseColor")},
            {"value": _type("color", lanes=4, color_space="linear")},
            evidence=_shader_evidence(variant, "_BaseMap * _BaseColor"),
        )
    )
    nodes.append(
        _node(
            "channel:baseAlpha",
            "channel.extract",
            {"value": base_texture},
            {"value": _type("float")},
            parameters={"channel": "a"},
            evidence=_shader_evidence(variant, "baseSample.a"),
        )
    )

    if packed_enabled:
        packed_texture = _sample_texture_node(
            nodes,
            resolved_by_name["_MetallicGlossMap"],
            ir_bindings,
            node_id="sample:metallicGloss",
            output_color_space="data",
        )
        nodes.append(
            _node(
                "channel:smoothness",
                "channel.extract",
                {"value": packed_texture},
                {"value": _type("float")},
                parameters={"channel": "a"},
                evidence=_shader_evidence(variant, "metallicGloss.a"),
            )
        )
        smoothness = {"node": "channel:smoothness", "output": "value"}
    else:
        smoothness = input_node("_Smoothness")
    nodes.append(
        _node(
            "math:perceptualRoughness",
            "math.oneMinus",
            {"value": smoothness},
            {"value": _type("float")},
            evidence=_shader_evidence(variant, "1 - smoothness"),
        )
    )

    silk_inputs = {
        "baseColor": {"node": "math:baseColor", "output": "value"},
        "shadowColor": input_node("upstreamShadowColor"),
        "baseAlpha": {"node": "channel:baseAlpha", "output": "value"},
        "perceptualRoughness": {
            "node": "math:perceptualRoughness",
            "output": "value",
        },
        "wetness": input_node("wetness"),
        "normal": input_node("normalWS"),
        "view": input_node("viewDirectionWS"),
        "dryColor": input_node("_SilkStockingsDryColor"),
        "wetColor": input_node("_SilkStockingsWetColor"),
        "edgeColor": input_node("_SilkStockingsColor"),
        "minimumAffect": input_node("_SilkStockingsMinAffect"),
        "maximumAffect": input_node("_SilkStockingsMaxAffect"),
        "advanced": input_node("_SilkStockingsAdvance"),
        "anisotropyDirection": input_node("_SilkStockingsAnisoDirection"),
        "specularIntensity": input_node("_SilkStockingsSpecularInt"),
        "drySpecularMinimum": input_node(
            "_SilkStockingsSpecularMinAtMinWetness"
        ),
        "specularFalloff": input_node("_SilkStockingsSpecularFalloff"),
        "specularOffset": input_node("_SilkStockingsSpecularValue"),
    }
    if advanced:
        silk_inputs["mask"] = _sample_texture_node(
            nodes,
            resolved_by_name["_SilkStockingsMask"],
            ir_bindings,
            node_id="sample:silkMask",
            output_color_space="data",
        )

    nodes.append(
        _node(
            "domain:silkMaterialState",
            SILK_OP,
            silk_inputs,
            {
                "baseColor": _type("color", lanes=3, color_space="linear"),
                "shadowColor": _type("color", lanes=3, color_space="linear"),
                "perceptualRoughness": _type("float"),
                "specularIntensity": _type("float"),
                "anisotropyDirection": _type("float"),
                "coverage": _type("float"),
                "affect": _type("float"),
            },
            parameters={
                "formula": "CharacterNPR-1.4.4-b391-material-local",
                "advanced": advanced,
            },
            evidence=_shader_evidence(variant, "silk material-local branch"),
        )
    )

    graph_outputs = {
        name: {"node": "domain:silkMaterialState", "output": name}
        for name in (
            "baseColor",
            "shadowColor",
            "perceptualRoughness",
            "specularIntensity",
            "anisotropyDirection",
            "coverage",
            "affect",
        )
    }
    diagnostics = _mapping_diagnostics(resolution, variant, graph_outputs)
    ir = {
        "format": IR_FORMAT,
        "version": IR_VERSION,
        "source": {
            "materialId": material_id,
            "shader": {
                "name": resolution["shader"]["name"],
                "archiveVersion": resolution["shader"]["archiveVersion"],
                "uri": resolution["shader"]["uri"],
            },
            "variant": {
                "stage": variant.stage,
                "pass": variant.pass_name,
                "blob": variant.blob,
                "materialKeywords": sorted(variant.material_keywords),
                "runtimeKeywords": {
                    "enabled": sorted(variant.runtime_enabled_keywords),
                    "disabled": sorted(variant.runtime_disabled_keywords),
                },
            },
            "frontend": {
                "kind": "manual",
                "tool": "material-binding-resolver-character-npr",
                "toolVersion": "0.1.0",
            },
        },
        "bindings": ir_bindings,
        "graph": {"nodes": nodes, "outputs": graph_outputs},
        "renderState": {
            "surface": "opaque",
            "depthWrite": True,
            "depthTest": "LEqual",
            "passes": [variant.pass_name],
        },
        "diagnostics": diagnostics,
    }
    validate_references(ir)
    return ir


def build_blender_parameter_plan(ir: Mapping[str, Any]) -> dict[str, Any]:
    """Map semantic bindings to a versioned Blender node-group interface."""
    validate_references(ir)
    domain_nodes = [node for node in ir["graph"]["nodes"] if node["op"] == SILK_OP]
    if len(domain_nodes) != 1:
        raise CharacterNprMappingError(
            f"expected exactly one {SILK_OP!r} node, found {len(domain_nodes)}"
        )
    bindings = {binding["sourceName"]: binding for binding in ir["bindings"]}
    dependency_binding_ids = {
        node["parameters"]["bindingId"]
        for node in ir["graph"]["nodes"]
        if "bindingId" in node["parameters"]
    }
    advanced = bool(bindings["_SilkStockingsAdvance"]["value"])
    inputs = {
        socket: _plan_input(bindings[property_name])
        for property_name, socket in SILK_PARAMETER_SOCKETS
    }
    inputs.update(
        {
            "Base Map": _plan_input(bindings["_BaseMap"]),
            "Base Color": _plan_input(bindings["_BaseColor"]),
            "Wetness": _plan_input(bindings["wetness"]),
            "Normal": _plan_input(bindings["normalWS"]),
            "View Direction": _plan_input(bindings["viewDirectionWS"]),
            "Upstream Shadow Color": _plan_input(bindings["upstreamShadowColor"]),
        }
    )
    if bindings["_MetallicGlossMap"]["id"] in dependency_binding_ids:
        inputs["Packed Material Map"] = _plan_input(bindings["_MetallicGlossMap"])
    if bindings["_Smoothness"]["id"] in dependency_binding_ids:
        inputs["Smoothness"] = _plan_input(bindings["_Smoothness"])
    inputs["Silk Mask"] = (
        _plan_input(bindings["_SilkStockingsMask"])
        if advanced
        else {
            "kind": "disabled",
            "reason": "_SilkStockingsAdvance == 0",
            "sourceBinding": bindings["_SilkStockingsAdvance"]["id"],
        }
    )
    return {
        "format": PLAN_FORMAT,
        "version": PLAN_VERSION,
        "source": {
            "materialId": ir["source"]["materialId"],
            "shader": ir["source"]["shader"],
            "variant": ir["source"]["variant"],
        },
        "nodeGroups": [
            {
                "name": SILK_GROUP,
                "semanticOp": SILK_OP,
                "inputs": inputs,
                "outputs": {
                    "Base Color": "baseColor",
                    "Shadow Color": "shadowColor",
                    "Perceptual Roughness": "perceptualRoughness",
                    "Specular Intensity": "specularIntensity",
                    "Anisotropy Direction": "anisotropyDirection",
                    "Coverage": "coverage",
                    "Affect": "affect",
                },
            }
        ],
        "diagnostics": [
            {
                "code": "BLENDER_SILK_NDF_NOT_IMPLEMENTED",
                "message": (
                    "参数计划只覆盖 b391 的材质局部状态；偏移半角向量的独立 NDF "
                    "仍需专用节点、OSL 或烘焙后端。"
                ),
            },
            {
                "code": "BLENDER_RUNTIME_INPUTS_REQUIRED",
                "message": (
                    "Wetness、Normal、View Direction 和 Upstream Shadow Color "
                    "必须由 Blender 宿主显式连接。"
                ),
            },
            {
                "code": "BLENDER_SILK_WETNESS_PARAMETERS_NOT_LOWERED",
                "message": (
                    "_SilkStockingsRainWetMaskScale 和 "
                    "_SilkStockingsAlbedoAffectType 属于尚未恢复的动态浸润/上游逻辑，"
                    "当前节点组不暴露这两个输入。"
                ),
            },
        ],
    }


def _to_ir_binding(binding, resolution):
    source_name = binding["name"]
    kind = binding["kind"]
    ir_type = _resolver_type(binding)
    evidence = _binding_evidence(resolution, source_name)
    if kind.startswith("texture"):
        resource = dict(binding["effective"])
        result = {
            "id": _material_binding_id(source_name),
            "kind": "texture",
            "sourceName": source_name,
            "type": ir_type,
            "space": binding["uv"]["set"],
            "textureBinding": {
                "resource": resource,
                "uv": {
                    "set": binding["uv"]["set"],
                    "scale": binding["uv"]["scale"],
                    "offset": binding["uv"]["offset"],
                },
                "colorSpace": binding["colorSpace"] or "unresolved",
                "sampler": {
                    "source": binding["sampling"]["effectiveSource"],
                    "effective": binding["sampling"]["effective"],
                },
            },
            "evidence": evidence,
        }
        if resource["kind"] == "resource":
            result["resourceId"] = resource["id"]
        return result

    value = binding["effective"]["value"]
    if source_name in BOOLEAN_PROPERTIES:
        value = bool(value)
    return {
        "id": _material_binding_id(source_name),
        "kind": "material",
        "sourceName": source_name,
        "type": ir_type,
        "value": value,
        "valueSource": binding["effective"]["source"],
        "evidence": evidence,
    }


def _runtime_bindings(variant):
    source = [{"uri": variant.hlsl_uri, "symbol": "runtime input"}]
    return [
        {
            "id": "runtime:wetness",
            "kind": "runtimeInput",
            "sourceName": "wetness",
            "type": _type("float"),
            "valueSource": "runtime",
            "evidence": {"level": "external", "sources": source},
        },
        {
            "id": "runtime:normalWS",
            "kind": "runtimeInput",
            "sourceName": "normalWS",
            "type": _type("vector", lanes=3),
            "space": "world",
            "valueSource": "runtime",
            "evidence": {"level": "external", "sources": source},
        },
        {
            "id": "runtime:viewDirectionWS",
            "kind": "runtimeInput",
            "sourceName": "viewDirectionWS",
            "type": _type("vector", lanes=3),
            "space": "world",
            "valueSource": "runtime",
            "evidence": {"level": "external", "sources": source},
        },
        {
            "id": "runtime:upstreamShadowColor",
            "kind": "runtimeInput",
            "sourceName": "upstreamShadowColor",
            "type": _type("color", lanes=3, color_space="linear"),
            "valueSource": "runtime",
            "evidence": {
                "level": "external",
                "sources": source,
                "note": "等待上游 Shadow Color 饱和度和亮度子图接入。",
            },
        },
    ]


def _sample_texture_node(
    nodes,
    resolved_binding,
    ir_bindings,
    *,
    node_id,
    output_color_space,
):
    binding_id = _material_binding_id(resolved_binding["name"])
    binding = next(item for item in ir_bindings if item["id"] == binding_id)
    nodes.append(
        _node(
            node_id,
            "texture.sample2d",
            {},
            {
                "rgba": _type(
                    "color",
                    lanes=4,
                    color_space=output_color_space,
                )
            },
            parameters={
                "bindingId": binding_id,
                "uv": binding["textureBinding"]["uv"],
                "sampler": binding["textureBinding"]["sampler"],
            },
            space=binding["space"],
            evidence=binding["evidence"],
        )
    )
    return {"node": node_id, "output": "rgba"}


def _mapping_diagnostics(resolution, variant, outputs):
    diagnostics = [
        {
            "code": "SILK_RUNTIME_WETNESS_EXTERNAL",
            "message": "动态浸润、高度浸润和每角色打包值尚未恢复，Wetness 保持外部输入。",
            "affectedOutputs": sorted(outputs),
            "source": {"uri": variant.hlsl_uri, "symbol": "wetness"},
        },
        {
            "code": "SILK_SPECULAR_NDF_NOT_LOWERED",
            "message": "当前 IR 只恢复材质局部状态，丝袜独立各向异性 NDF 尚未降级。",
            "affectedOutputs": ["specularIntensity", "anisotropyDirection"],
            "source": {"uri": variant.hlsl_uri, "symbol": "silk NDF"},
        },
        {
            "code": "SILK_WETNESS_PARAMETERS_NOT_LOWERED",
            "message": (
                "_SilkStockingsRainWetMaskScale 和 _SilkStockingsAlbedoAffectType "
                "尚未接入材质局部状态；动态浸润与其上游颜色逻辑保持外部边界。"
            ),
            "affectedOutputs": [
                "baseColor",
                "perceptualRoughness",
                "coverage",
            ],
            "source": {
                "uri": variant.hlsl_uri,
                "symbol": (
                    "_SilkStockingsRainWetMaskScale,"
                    "_SilkStockingsAlbedoAffectType"
                ),
            },
        },
    ]
    for item in resolution["diagnostics"]:
        if item["severity"] != "warning":
            continue
        diagnostics.append(
            {
                "code": item["code"],
                "message": item["message"],
                "affectedOutputs": sorted(outputs),
            }
        )
    return diagnostics


def _plan_input(binding):
    if binding["kind"] == "runtimeInput":
        return {
            "kind": "runtimeInput",
            "sourceBinding": binding["id"],
            "space": binding.get("space", "none"),
        }
    if binding["kind"] == "texture":
        return {
            "kind": "texture",
            "sourceBinding": binding["id"],
            **binding["textureBinding"],
        }
    return {
        "kind": "value",
        "sourceBinding": binding["id"],
        "value": binding["value"],
        "valueSource": binding["valueSource"],
    }


def _resolver_type(binding):
    kind = binding["kind"]
    if binding["name"] in BOOLEAN_PROPERTIES:
        return _type("boolean")
    if kind == "color":
        return _type("color", lanes=4, color_space="linear")
    if kind == "vector":
        return _type("vector", lanes=4)
    if kind in {"float", "integer"}:
        return _type(kind)
    if kind in {"texture2d", "textureCube", "texture3d"}:
        result = _type(kind)
        if binding["colorSpace"] in {"linear", "srgb", "data"}:
            result["colorSpace"] = binding["colorSpace"]
        return result
    raise CharacterNprMappingError(f"unsupported resolver binding kind {kind!r}")


def _type(kind, *, lanes=None, color_space=None):
    result = {"kind": kind}
    if lanes is not None:
        result["lanes"] = lanes
    if color_space is not None:
        result["colorSpace"] = color_space
    return result


def _node(
    node_id,
    op,
    inputs,
    outputs,
    *,
    parameters=None,
    space="none",
    evidence,
):
    return {
        "id": node_id,
        "op": op,
        "inputs": inputs,
        "outputs": outputs,
        "parameters": parameters or {},
        "space": space,
        "evidence": evidence,
    }


def _binding_evidence(resolution, source_name):
    return {
        "level": "exact",
        "sources": [
            {
                "uri": (
                    f"material-binding://{resolution['shader']['archiveVersion']}/"
                    f"{source_name}"
                ),
                "symbol": source_name,
            }
        ],
    }


def _shader_evidence(variant, symbol):
    return {
        "level": "recovered",
        "sources": [{"uri": variant.hlsl_uri, "symbol": symbol}],
    }


def _material_binding_id(source_name):
    return f"material:{source_name.lstrip('_')}"


def _effective_bool(binding):
    value = binding["effective"]["value"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CharacterNprMappingError(
            f"toggle {binding['name']!r} must resolve to a number"
        )
    return value != 0


def _validate_keyword(variant, keyword, enabled):
    present = keyword in variant.material_keywords
    if present != enabled:
        raise CharacterNprMappingError(
            f"material value and variant keyword disagree for {keyword}: "
            f"value enabled={enabled}, keyword present={present}"
        )


def _require_properties(bindings, required):
    missing = sorted(required - set(bindings))
    if missing:
        raise CharacterNprMappingError(
            f"binding resolution is missing required properties: {missing}"
        )


def _validate_resolution(resolution):
    if resolution.get("format") != "MaterialBindingResolution":
        raise CharacterNprMappingError("expected MaterialBindingResolution input")
    errors = [
        item for item in resolution.get("diagnostics", []) if item["severity"] == "error"
    ]
    if errors:
        codes = sorted({item["code"] for item in errors})
        raise CharacterNprMappingError(
            f"binding resolution contains error diagnostics: {codes}"
        )
