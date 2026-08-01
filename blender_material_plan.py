"""Validate and read the Blender material-plan interchange format."""

from __future__ import annotations

from collections.abc import Mapping


PLAN_FORMAT = "BlenderNodeParameterPlan"
PLAN_VERSION = "0.1.0"
SILK_GROUP = "EF_SilkStockings_MaterialState_v1"


def silk_plan_inputs(plan: Mapping | None) -> Mapping[str, Mapping] | None:
    if not isinstance(plan, Mapping):
        return None
    if plan.get("format") != PLAN_FORMAT or plan.get("version") != PLAN_VERSION:
        return None
    groups = plan.get("nodeGroups")
    if not isinstance(groups, list):
        return None
    matches = [
        group
        for group in groups
        if isinstance(group, Mapping) and group.get("name") == SILK_GROUP
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("inputs"), Mapping):
        return None
    return matches[0]["inputs"]


def plan_value(inputs: Mapping[str, Mapping], name: str):
    binding = inputs.get(name)
    if isinstance(binding, Mapping) and binding.get("kind") == "value":
        return binding.get("value")
    return None


def plan_texture_id(inputs: Mapping[str, Mapping], name: str) -> str | None:
    binding = inputs.get(name)
    if not isinstance(binding, Mapping) or binding.get("kind") != "texture":
        return None
    resource = binding.get("resource")
    if not isinstance(resource, Mapping) or resource.get("kind") != "resource":
        return None
    value = resource.get("id")
    return value if isinstance(value, str) and value else None
