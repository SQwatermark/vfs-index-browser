"""Assemble the public resource-plan document for one NPC AvatarMesh asset."""

from __future__ import annotations

from npc_avatar_config import summarize_avatar_mesh


class AvatarResourcePlanService:
    def build(
        self,
        asset: dict,
        avatar_mesh: dict,
        plan: dict,
        plan_meta: dict,
    ) -> dict:
        dump = plan_meta["dump"]
        return {
            "kind": "avatarMeshResourcePlan",
            "asset": asset,
            "summary": summarize_avatar_mesh(avatar_mesh),
            "avatarMesh": avatar_mesh,
            "plan": plan,
            "run": {
                "dump": {
                    "builtAtEpoch": dump.get("builtAtEpoch"),
                    "toolArtifacts": dump.get("source", {}).get("toolArtifacts", []),
                },
                "stringPathHash": plan_meta["stringPathHash"],
            },
        }
