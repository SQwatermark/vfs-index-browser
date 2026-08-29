"""Search model animation candidates and assemble stable preview URLs."""

from __future__ import annotations

from typing import Callable, Mapping
from urllib.parse import urlencode

from npc_avatar_config import is_avatar_mesh_asset_path


class ModelAnimationCatalogService:
    def __init__(
        self,
        default_query_builder: Callable[[str], str],
        *,
        animation_version: int,
        blend_version: int,
    ) -> None:
        self._default_query = default_query_builder
        self._animation_version = animation_version
        self._blend_version = blend_version

    def search(self, query: Mapping[str, list[str]], resolved: tuple) -> dict:
        index, model_asset, _, _ = resolved
        asset_path = str(model_asset["path"])
        default_query = query.get(
            "queryHint", [self._default_query(asset_path)]
        )[0].strip()
        search_query = query.get("q", [default_query])[0].strip()
        page = int(query.get("page", ["1"])[0])
        page_size = int(query.get("pageSize", ["50"])[0])
        lod = int(query.get("lod", ["0"])[0])
        is_avatar = is_avatar_mesh_asset_path(asset_path)
        if is_avatar and lod not in range(4):
            raise ValueError("invalid AvatarMesh LOD")
        result = index.search_animation_assets(
            search_query, page=page, page_size=page_size
        )
        manifest_id = int(query["manifestId"][0])
        model_index = int(model_asset["asset_index"])
        for animation in result["files"]:
            parameters = {
                "manifestId": manifest_id,
                "assetIndex": model_index,
                **({"lod": lod} if is_avatar else {}),
                "animationAssetIndex": int(animation["assetIndex"]),
            }
            encoded = urlencode(parameters)
            animation["previewUrl"] = (
                f"/api/manifest-asset/model-animation?{encoded}"
                f"&v={self._animation_version}"
            )
            animation["blendUrl"] = (
                f"/api/manifest-asset/model-blend?{encoded}&v={self._blend_version}"
            )
        return {
            "kind": "modelAnimationCandidates",
            "modelAsset": model_asset,
            "defaultQuery": default_query,
            **result,
        }
