"""Reference translation of CharacterNPR 1.4.4 silk-stockings math.

This module translates only the material-local equations verified against
``characternpr/Sub0_Pass0_Fragment_b391.hlsl``. It deliberately does not
invent CharacterVolume, HGRP light, wetness, shadow, exposure, or fog values.
Callers must provide those inputs explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

Float3 = tuple[float, float, float]
Float4 = tuple[float, float, float, float]


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def lerp(start: float, end: float, weight: float) -> float:
    return start + (end - start) * weight


def lerp3(start: Float3, end: Float3, weight: float) -> Float3:
    return tuple(lerp(start[index], end[index], weight) for index in range(3))


def multiply3(left: Float3, right: Float3) -> Float3:
    return tuple(left[index] * right[index] for index in range(3))


def add3(left: Float3, right: Float3) -> Float3:
    return tuple(left[index] + right[index] for index in range(3))


def scale3(vector: Float3, scalar: float) -> Float3:
    return tuple(component * scalar for component in vector)


def dot3(left: Float3, right: Float3) -> float:
    return sum(left[index] * right[index] for index in range(3))


def cross3(left: Float3, right: Float3) -> Float3:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def normalize3(vector: Sequence[float]) -> Float3:
    length_squared = sum(component * component for component in vector)
    if length_squared <= 0.0:
        raise ValueError("cannot normalize a zero-length vector")
    inverse_length = 1.0 / math.sqrt(length_squared)
    return tuple(float(component) * inverse_length for component in vector)


@dataclass(frozen=True)
class SilkStockingsProperties:
    """Defaults declared by the 1.4.4 ``characternpr.shader`` properties."""

    dry_color: Float3 = (1.0, 1.0, 1.0)
    wet_color: Float3 = (1.0, 1.0, 1.0)
    color: Float4 = (0.0, 0.0, 0.0, 1.0)
    minimum_affect: float = 0.05
    maximum_affect: float = 0.9
    advanced: bool = False
    anisotropy_direction: float = 0.0
    specular_intensity: float = 5.0
    specular_minimum_at_minimum_wetness: float = 0.0
    specular_falloff: float = 0.8
    specular_value: float = 2.0


@dataclass(frozen=True)
class SilkStockingsState:
    """Intermediate values produced by b391 lines 633-657."""

    perceptual_roughness: float
    specular_intensity: float
    anisotropy_direction: float
    coverage: float
    affect: float
    lit_albedo: Float3
    shadow_albedo: Float3


def evaluate_material_state(
    *,
    base_color: Float3,
    shadow_color: Float3,
    base_alpha: float,
    perceptual_roughness: float,
    wetness: float,
    normal: Float3,
    view: Float3,
    properties: SilkStockingsProperties,
    mask: Float4 | None = None,
) -> SilkStockingsState:
    """Translate the material-local stockings branch from b391.

    ``mask`` must be present only when ``properties.advanced`` is true. Its
    channels retain the game's R/G/B/A semantics.
    """

    wetness = clamp(wetness, 0.0, 1.0)
    base_alpha = clamp(base_alpha, 0.0, 1.0)
    specular_base = properties.specular_intensity * lerp(
        properties.specular_minimum_at_minimum_wetness,
        1.0,
        wetness,
    )

    if properties.advanced:
        if mask is None:
            raise ValueError("advanced silk-stockings mode requires a RGBA mask")
        perceptual_roughness = lerp(
            perceptual_roughness,
            1.0 - mask[2],
            wetness,
        )
        specular_intensity = specular_base * mask[0]
        anisotropy_direction = clamp(mask[1] * 2.0 - 1.0, -0.95, 0.95)
        coverage = clamp(
            lerp(base_alpha, mask[3], wetness) + 1.0 - properties.color[3],
            0.0,
            1.0,
        )
    else:
        if mask is not None:
            raise ValueError("non-advanced silk-stockings mode does not sample a mask")
        specular_intensity = specular_base
        anisotropy_direction = -lerp(
            properties.anisotropy_direction,
            0.5,
            clamp(base_alpha * 0.5, 0.0, 1.0),
        )
        coverage = clamp(
            base_alpha + 1.0 - properties.color[3],
            0.0,
            1.0,
        )

    normal = normalize3(normal)
    view = normalize3(view)
    normal_dot_view = clamp(dot3(normal, view), 0.0, 1.0)
    edge_power = clamp(
        math.pow(1.05 - normal_dot_view, coverage * 2.0),
        0.0,
        1.0,
    )
    affect = lerp(
        properties.minimum_affect,
        properties.maximum_affect,
        edge_power,
    )
    tint = lerp3(properties.dry_color, properties.wet_color, wetness)
    edge_color = properties.color[:3]

    return SilkStockingsState(
        perceptual_roughness=perceptual_roughness,
        specular_intensity=specular_intensity,
        anisotropy_direction=anisotropy_direction,
        coverage=coverage,
        affect=affect,
        lit_albedo=lerp3(multiply3(base_color, tint), edge_color, affect),
        shadow_albedo=lerp3(multiply3(shadow_color, tint), edge_color, affect),
    )


def evaluate_silk_ndf(
    *,
    state: SilkStockingsState,
    normal: Float3,
    view: Float3,
    tangent: Float3,
    tangent_handedness: float,
    main_half_vector: Float3,
    properties: SilkStockingsProperties,
) -> float:
    """Translate the dedicated silk NDF from b391 lines 1081-1142.

    The return value includes ``state.specular_intensity`` but not SpecRamp,
    light color, CharacterParams13.w, visibility, or exposure.
    """

    normal = normalize3(normal)
    view = normalize3(view)
    tangent = normalize3(
        add3(tangent, scale3(normal, -dot3(tangent, normal)))
    )
    bitangent = scale3(cross3(normal, tangent), tangent_handedness)
    half_vector = normalize3(
        add3(
            normalize3(main_half_vector),
            scale3(view, properties.specular_value),
        )
    )

    alpha = max(
        state.perceptual_roughness * state.perceptual_roughness,
        0.0078125,
    )
    falloff = 1.0 - clamp(
        state.coverage * properties.specular_falloff,
        0.0,
        1.0,
    )
    direction = state.anisotropy_direction * falloff
    alpha_tangent = alpha * (1.0 - direction)
    alpha_bitangent = alpha * (1.0 + direction)
    alpha_product = alpha_tangent * alpha_bitangent

    vector = (
        alpha_bitangent * dot3(tangent, half_vector),
        alpha_tangent * dot3(bitangent, half_vector),
        alpha_product * dot3(normal, half_vector),
    )
    denominator = dot3(vector, vector) ** 2
    numerator = alpha_product**3
    ndf = (
        clamp(numerator / denominator, 0.0, 20.0)
        if denominator != numerator
        else 1.0
    )
    return state.specular_intensity * ndf
