"""协调 NPC AvatarMesh 资源计划的加载与公开文档组装。"""

from __future__ import annotations

from typing import Callable

from npc_avatar_config import (
    attach_resolved_paths,
    is_avatar_mesh_asset_path,
    parse_avatar_mesh,
    summarize_avatar_mesh,
)
from npc_avatar_resources import build_avatar_mesh_resource_plan
from string_path_hash import StringPathHashIndex


class AvatarResourcePlanError(RuntimeError):
    pass


class AvatarResourcePlanService:
    def __init__(
        self,
        dump_loader: Callable | None = None,
        path_hash_provider: Callable | None = None,
        *,
        plan_loader: Callable | None = None,
    ) -> None:
        self._load_dump = dump_loader
        self._provide_path_hash = path_hash_provider
        self._load_plan_override = plan_loader

    def build_from_resolved(self, resolved: tuple, lod: int) -> dict:
        index, asset, bundle_record, bundle_chunk = resolved
        if not is_avatar_mesh_asset_path(str(asset["path"])):
            raise AvatarResourcePlanError(
                "resource is not an NPC AvatarMesh asset"
            )
        loader = self._load_plan_override or self.load
        avatar_mesh, plan, plan_meta = loader(
            index,
            asset,
            bundle_record,
            bundle_chunk,
            lod,
        )
        return self.build(asset, avatar_mesh, plan, plan_meta)

    def load(
        self,
        index: object,
        asset: dict,
        bundle_record: dict,
        bundle_chunk: object,
        lod: int,
        *,
        cancel_event: object | None = None,
    ) -> tuple[dict, dict, dict]:
        if self._load_dump is None or self._provide_path_hash is None:
            raise RuntimeError("AvatarMesh resource dependencies are not configured")
        cancel_options = (
            {"cancel_event": cancel_event} if cancel_event is not None else {}
        )
        exported = self._load_dump(
            bundle_record,
            bundle_chunk,
            asset,
            **cancel_options,
        )
        if exported is None:
            raise RuntimeError("AnimeStudio produced no AvatarMesh TypeTree dump")
        dump_path, dump_meta = exported
        avatar_mesh = parse_avatar_mesh(
            dump_path.read_text(encoding="utf-8", errors="replace")
        )
        path_hash_file, path_hash_meta = self._provide_path_hash()
        attach_resolved_paths(avatar_mesh, StringPathHashIndex(path_hash_file))
        plan = build_avatar_mesh_resource_plan(index, avatar_mesh, lod=lod)
        return avatar_mesh, plan, {
            "dump": dump_meta,
            "stringPathHash": path_hash_meta,
        }

    @staticmethod
    def bundle_closure(index: object, plan: dict) -> list[dict]:
        bundles: dict[int, dict] = {}
        for direct in plan.get("bundles", []):
            bundle_index = int(direct["bundleIndex"])
            bundles[bundle_index] = {
                "bundleIndex": bundle_index,
                "name": str(direct["bundleName"]),
            }
            for dependency in index.bundle_dependencies(bundle_index):
                bundles[int(dependency["bundleIndex"])] = dependency
        return [bundles[key] for key in sorted(bundles)]

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
