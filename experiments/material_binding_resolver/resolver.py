"""Resolve Unity Material instance values against ShaderLab property defaults.

This module is intentionally independent from ModelDocument and Blender. It
models binding facts only; it does not infer rendering semantics from names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from numbers import Real
from typing import Any, Mapping


FORMAT = "MaterialBindingResolution"
VERSION = "0.1.0"
SHADER_NAME_RE = re.compile(r'\bShader\s+"([^"]+)"')
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
TEXTURE_TYPES = {"2D": "texture2d", "Cube": "textureCube", "3D": "texture3d"}
SCALAR_TYPES = {"Float": "float", "Int": "integer"}
VECTOR_CHANNELS = {
    "Color": ("r", "g", "b", "a"),
    "Vector": ("x", "y", "z", "w"),
}
UNITY_MATERIAL_VECTOR_CHANNELS = ("r", "g", "b", "a")


class MaterialBindingError(ValueError):
    """Raised when the resolver input contract is invalid."""


class ShaderLabParseError(MaterialBindingError):
    """Raised when the strict ShaderLab property subset cannot be parsed."""


@dataclass(frozen=True)
class ShaderSource:
    archive_version: str
    uri: str
    accepted_material_shader_ids: tuple[str, ...] = ()

    def __post_init__(self):
        if not self.archive_version:
            raise MaterialBindingError("shader archive_version must not be empty")
        if not self.uri:
            raise MaterialBindingError("shader uri must not be empty")


def parse_shaderlab_properties(shader_text: str) -> dict[str, Any]:
    """Parse the strict, one-property-per-line subset used by shader archives."""
    shader_match = SHADER_NAME_RE.search(shader_text)
    if shader_match is None:
        raise ShaderLabParseError("Shader declaration was not found")

    body, first_line = _extract_block(shader_text, "Properties")
    properties = []
    seen = set()
    for offset, raw_line in enumerate(body.splitlines(), start=0):
        line_number = first_line + offset
        line = _strip_line_comment(raw_line).strip()
        if not line:
            continue
        prop = _parse_property_line(line, line_number)
        if prop["name"] in seen:
            raise ShaderLabParseError(
                f"line {line_number}: duplicate property {prop['name']!r}"
            )
        seen.add(prop["name"])
        properties.append(prop)

    return {"name": shader_match.group(1), "properties": properties}


def resolve_material_bindings(
    shader_text: str,
    shader_source: ShaderSource,
    material: Mapping[str, Any],
    *,
    texture_metadata: Mapping[str, Mapping[str, Any]] | None = None,
    texture_rules: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve Shader defaults and Material overrides into explicit bindings.

    ``texture_rules`` represents facts recovered from Shader/HLSL reflection,
    such as UV set and an explicit sampler. ``texture_metadata`` represents
    Texture asset facts. Neither source is inferred from property names.
    """
    parsed_shader = parse_shaderlab_properties(shader_text)
    source_material = _require_mapping(material, "material")
    texture_metadata = texture_metadata or {}
    texture_rules = texture_rules or {}
    diagnostics = []

    material_shader = source_material.get("shader")
    accepted_shader_ids = {
        parsed_shader["name"],
        *shader_source.accepted_material_shader_ids,
    }
    if material_shader not in accepted_shader_ids:
        diagnostics.append(
            _diagnostic(
                "error",
                "MATERIAL_SHADER_MISMATCH",
                f"Material shader {material_shader!r} does not match "
                f"{parsed_shader['name']!r}.",
            )
        )

    instance_groups = {
        "texture": _require_mapping(
            source_material.get("textureEnvironments", {}),
            "material.textureEnvironments",
        ),
        "integer": _require_mapping(
            source_material.get("ints", {}), "material.ints"
        ),
        "float": _require_mapping(
            source_material.get("floats", {}), "material.floats"
        ),
        "color": _require_mapping(
            source_material.get("colors", {}), "material.colors"
        ),
        "vector": _require_mapping(
            source_material.get("colors", {}), "material.colors"
        ),
    }

    bindings = []
    declared_names = set()
    for prop in parsed_shader["properties"]:
        declared_names.add(prop["name"])
        if prop["kind"].startswith("texture"):
            bindings.append(
                _resolve_texture_binding(
                    prop,
                    instance_groups["texture"],
                    texture_metadata,
                    texture_rules,
                    diagnostics,
                )
            )
        else:
            bindings.append(
                _resolve_value_binding(prop, instance_groups, diagnostics)
            )

    unmatched = {}
    for group_name, values in (
        ("textureEnvironments", instance_groups["texture"]),
        ("ints", instance_groups["integer"]),
        ("floats", instance_groups["float"]),
        ("colors", instance_groups["color"]),
    ):
        extras = {
            name: value for name, value in values.items() if name not in declared_names
        }
        if not extras:
            continue
        unmatched[group_name] = extras
        for name in sorted(extras):
            diagnostics.append(
                _diagnostic(
                    "warning",
                    "MATERIAL_PROPERTY_UNDECLARED",
                    f"Material property {name!r} is not declared by this Shader version.",
                    property_name=name,
                )
            )

    unused_rules = sorted(set(texture_rules) - declared_names)
    for name in unused_rules:
        diagnostics.append(
            _diagnostic(
                "warning",
                "TEXTURE_RULE_UNDECLARED",
                f"Texture rule {name!r} does not reference a declared Shader property.",
                property_name=name,
            )
        )

    return {
        "format": FORMAT,
        "version": VERSION,
        "shader": {
            "name": parsed_shader["name"],
            "archiveVersion": shader_source.archive_version,
            "uri": shader_source.uri,
            "materialShader": material_shader,
        },
        "bindings": bindings,
        "unmatchedInstanceProperties": unmatched,
        "diagnostics": diagnostics,
    }


def _resolve_value_binding(prop, groups, diagnostics):
    group = groups[prop["kind"]]
    has_override = prop["name"] in group
    raw_override = group.get(prop["name"])
    override = None
    effective_value = prop["defaultValue"]
    effective_source = "shaderDefault"

    if has_override:
        try:
            normalized = _normalize_value(prop["kind"], raw_override)
        except MaterialBindingError as exc:
            diagnostics.append(
                _diagnostic(
                    "error",
                    "INSTANCE_VALUE_TYPE_MISMATCH",
                    str(exc),
                    property_name=prop["name"],
                )
            )
        else:
            override = {"value": normalized, "source": "materialInstance"}
            effective_value = normalized
            effective_source = "materialInstance"

    return {
        **_binding_header(prop),
        "default": {"value": prop["defaultValue"], "source": "shaderDefault"},
        "instanceOverride": override,
        "effective": {"value": effective_value, "source": effective_source},
    }


def _resolve_texture_binding(
    prop,
    environments,
    texture_metadata,
    texture_rules,
    diagnostics,
):
    environment = environments.get(prop["name"])
    if environment is not None and not isinstance(environment, Mapping):
        diagnostics.append(
            _diagnostic(
                "error",
                "TEXTURE_ENVIRONMENT_TYPE_MISMATCH",
                f"Texture environment {prop['name']!r} must be an object.",
                property_name=prop["name"],
            )
        )
        environment = None

    texture_id = environment.get("textureId") if environment else None
    scale = _normalize_vec2(
        environment.get("scale", [1.0, 1.0]) if environment else [1.0, 1.0],
        f"{prop['name']}.scale",
    )
    offset = _normalize_vec2(
        environment.get("offset", [0.0, 0.0]) if environment else [0.0, 0.0],
        f"{prop['name']}.offset",
    )
    rule = texture_rules.get(prop["name"], {})
    if not isinstance(rule, Mapping):
        raise MaterialBindingError(f"texture rule {prop['name']!r} must be an object")
    metadata = texture_metadata.get(texture_id, {}) if texture_id else {}
    if not isinstance(metadata, Mapping):
        raise MaterialBindingError(f"texture metadata {texture_id!r} must be an object")

    shader_sampler = rule.get("sampler")
    texture_sampler = metadata.get("sampler")
    _validate_sampler(shader_sampler, f"texture rule {prop['name']!r}")
    _validate_sampler(texture_sampler, f"texture metadata {texture_id!r}")
    if shader_sampler is not None:
        effective_sampler = dict(shader_sampler)
        sampler_source = "shaderRule"
    elif texture_sampler is not None:
        effective_sampler = dict(texture_sampler)
        sampler_source = "textureAsset"
    else:
        effective_sampler = None
        sampler_source = "unresolved"
        if texture_id:
            diagnostics.append(
                _diagnostic(
                    "warning",
                    "TEXTURE_SAMPLER_UNRESOLVED",
                    f"Texture property {prop['name']!r} has no sampler metadata.",
                    property_name=prop["name"],
                )
            )

    color_space = metadata.get("colorSpace")
    if texture_id and color_space not in {"linear", "srgb", "data"}:
        diagnostics.append(
            _diagnostic(
                "warning",
                "TEXTURE_COLOR_SPACE_UNRESOLVED",
                f"Texture {texture_id!r} has no recognized color space.",
                property_name=prop["name"],
            )
        )
        color_space = None

    default_resource = {
        "kind": "builtin",
        "name": prop["defaultValue"],
        "source": "shaderDefault",
    }
    instance_override = None
    effective_resource = default_resource
    if environment is not None:
        instance_override = {
            "textureId": texture_id,
            "source": "materialInstance",
        }
        if texture_id:
            effective_resource = {
                "kind": "resource",
                "id": texture_id,
                "source": "materialInstance",
            }

    return {
        **_binding_header(prop),
        "default": default_resource,
        "instanceOverride": instance_override,
        "effective": effective_resource,
        "uv": {
            "set": rule.get("uvSet", "uv0"),
            "scale": scale,
            "offset": offset,
            "transformSource": "materialInstance" if environment else "identityDefault",
        },
        "colorSpace": color_space,
        "sampling": {
            "shader": dict(shader_sampler) if shader_sampler is not None else None,
            "texture": dict(texture_sampler) if texture_sampler is not None else None,
            "effective": effective_sampler,
            "effectiveSource": sampler_source,
        },
    }


def _binding_header(prop):
    return {
        "name": prop["name"],
        "kind": prop["kind"],
        "declaredType": prop["declaredType"],
        "displayName": prop["displayName"],
        "attributes": prop["attributes"],
        "toggleKeyword": prop.get("toggleKeyword"),
    }


def _parse_property_line(line: str, line_number: int) -> dict[str, Any]:
    attributes = []
    rest = line
    while rest.startswith("["):
        end = rest.find("]")
        if end < 0:
            raise ShaderLabParseError(f"line {line_number}: unterminated attribute")
        attributes.append(_parse_attribute(rest[1:end], line_number))
        rest = rest[end + 1 :].lstrip()

    name_match = IDENTIFIER_RE.match(rest)
    if name_match is None:
        raise ShaderLabParseError(f"line {line_number}: expected property name")
    name = name_match.group(0)
    rest = rest[name_match.end() :].lstrip()
    if not rest.startswith("("):
        raise ShaderLabParseError(f"line {line_number}: expected property declaration")
    declaration, end = _balanced_content(rest, 0, "(", ")", line_number)
    parts = _split_top_level(declaration, ",")
    if len(parts) != 2:
        raise ShaderLabParseError(
            f"line {line_number}: expected display name and property type"
        )
    display_name = _parse_quoted(parts[0].strip(), line_number)
    declared_type = parts[1].strip()
    kind = _property_kind(declared_type, line_number)
    rest = rest[end + 1 :].lstrip()
    if not rest.startswith("="):
        raise ShaderLabParseError(f"line {line_number}: expected '='")
    default_value = _parse_default(kind, rest[1:].strip(), line_number)

    toggle = next(
        (
            attribute["arguments"]
            for attribute in attributes
            if attribute["name"] == "Toggle" and attribute["arguments"]
        ),
        None,
    )
    return {
        "name": name,
        "kind": kind,
        "declaredType": declared_type,
        "displayName": display_name,
        "attributes": attributes,
        "toggleKeyword": toggle,
        "defaultValue": default_value,
    }


def _parse_attribute(text: str, line_number: int) -> dict[str, Any]:
    text = text.strip()
    match = IDENTIFIER_RE.match(text)
    if match is None:
        raise ShaderLabParseError(f"line {line_number}: invalid attribute {text!r}")
    name = match.group(0)
    suffix = text[match.end() :].strip()
    if not suffix:
        arguments = None
    elif suffix.startswith("("):
        arguments, end = _balanced_content(suffix, 0, "(", ")", line_number)
        if suffix[end + 1 :].strip():
            raise ShaderLabParseError(f"line {line_number}: invalid attribute suffix")
        arguments = arguments.strip()
    else:
        raise ShaderLabParseError(f"line {line_number}: invalid attribute syntax")
    return {"name": name, "arguments": arguments}


def _property_kind(declared_type: str, line_number: int) -> str:
    if declared_type in SCALAR_TYPES:
        return SCALAR_TYPES[declared_type]
    if declared_type in VECTOR_CHANNELS:
        return declared_type.lower()
    if declared_type in TEXTURE_TYPES:
        return TEXTURE_TYPES[declared_type]
    if re.fullmatch(r"Range\(\s*[-+0-9.eE]+\s*,\s*[-+0-9.eE]+\s*\)", declared_type):
        return "float"
    raise ShaderLabParseError(
        f"line {line_number}: unsupported property type {declared_type!r}"
    )


def _parse_default(kind: str, text: str, line_number: int):
    if kind.startswith("texture"):
        match = re.fullmatch(r'"([^"]*)"\s*\{\s*\}', text)
        if match is None:
            raise ShaderLabParseError(f"line {line_number}: invalid texture default")
        return match.group(1)
    if kind in {"color", "vector"}:
        match = re.fullmatch(r"\(([^)]*)\)", text)
        if match is None:
            raise ShaderLabParseError(f"line {line_number}: invalid vector default")
        values = [_parse_number(item.strip(), line_number) for item in match.group(1).split(",")]
        if len(values) != 4:
            raise ShaderLabParseError(f"line {line_number}: expected four default components")
        return values
    value = _parse_number(text, line_number)
    if kind == "integer":
        if not float(value).is_integer():
            raise ShaderLabParseError(f"line {line_number}: integer default is not integral")
        return int(value)
    return value


def _normalize_value(kind: str, value: Any):
    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise MaterialBindingError(f"expected integer override, got {value!r}")
        return value
    if kind == "float":
        if isinstance(value, bool) or not isinstance(value, Real):
            raise MaterialBindingError(f"expected numeric override, got {value!r}")
        return float(value)
    channels = VECTOR_CHANNELS["Color" if kind == "color" else "Vector"]
    if isinstance(value, Mapping):
        storage_channels = channels
        if kind == "vector" and not all(channel in value for channel in channels):
            storage_channels = UNITY_MATERIAL_VECTOR_CHANNELS
        if not all(channel in value for channel in storage_channels):
            raise MaterialBindingError(
                f"expected {kind} channels {channels}"
                + (
                    f" or Unity Material channels {UNITY_MATERIAL_VECTOR_CHANNELS}"
                    if kind == "vector"
                    else ""
                )
                + f", got {value!r}"
            )
        value = [value[channel] for channel in storage_channels]
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise MaterialBindingError(f"expected four-component {kind}, got {value!r}")
    if any(isinstance(item, bool) or not isinstance(item, Real) for item in value):
        raise MaterialBindingError(f"expected numeric {kind}, got {value!r}")
    return [float(item) for item in value]


def _normalize_vec2(value, owner):
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, Real) for item in value)
    ):
        raise MaterialBindingError(f"{owner} must contain two numbers")
    return [float(item) for item in value]


def _validate_sampler(value, owner):
    if value is None:
        return
    if not isinstance(value, Mapping):
        raise MaterialBindingError(f"{owner} sampler must be an object")
    allowed = {"name", "filter", "wrapU", "wrapV", "wrapW", "anisotropy"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise MaterialBindingError(f"{owner} sampler has unknown fields: {unknown}")


def _extract_block(text: str, keyword: str) -> tuple[str, int]:
    match = re.search(rf"\b{re.escape(keyword)}\b", text)
    if match is None:
        raise ShaderLabParseError(f"{keyword} block was not found")
    opening = text.find("{", match.end())
    if opening < 0:
        raise ShaderLabParseError(f"{keyword} block has no opening brace")
    body, closing = _balanced_content(text, opening, "{", "}", 1)
    first_line = text.count("\n", 0, opening) + 2
    return body, first_line


def _balanced_content(text, opening, left, right, line_number):
    depth = 0
    quote = False
    escaped = False
    for index in range(opening, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quote = False
            continue
        if char == '"':
            quote = True
        elif char == left:
            depth += 1
        elif char == right:
            depth -= 1
            if depth == 0:
                return text[opening + 1 : index], index
    raise ShaderLabParseError(f"line {line_number}: unterminated {left}{right} block")


def _split_top_level(text: str, separator: str) -> list[str]:
    parts = []
    start = 0
    depth = 0
    quote = False
    for index, char in enumerate(text):
        if char == '"':
            quote = not quote
        elif not quote and char == "(":
            depth += 1
        elif not quote and char == ")":
            depth -= 1
        elif not quote and depth == 0 and char == separator:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


def _parse_quoted(text: str, line_number: int) -> str:
    match = re.fullmatch(r'"([^"]*)"', text)
    if match is None:
        raise ShaderLabParseError(f"line {line_number}: display name must be quoted")
    return match.group(1)


def _parse_number(text: str, line_number: int) -> float:
    try:
        return float(text)
    except ValueError as exc:
        raise ShaderLabParseError(
            f"line {line_number}: invalid numeric default {text!r}"
        ) from exc


def _strip_line_comment(line: str) -> str:
    quote = False
    for index in range(len(line) - 1):
        if line[index] == '"':
            quote = not quote
        elif not quote and line[index : index + 2] == "//":
            return line[:index]
    return line


def _require_mapping(value: Any, owner: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MaterialBindingError(f"{owner} must be an object")
    return value


def _diagnostic(severity, code, message, *, property_name=None):
    result = {"severity": severity, "code": code, "message": message}
    if property_name is not None:
        result["property"] = property_name
    return result
