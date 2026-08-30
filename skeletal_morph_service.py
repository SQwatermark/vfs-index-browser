"""Resolve, parse, and bake one skeletal-morph animation from Manifest assets."""

from __future__ import annotations

from typing import Callable

from skeletal_morph import (
    bake_morph_animation,
    merge_morph_avatars,
    morph_avatar_asset_names,
    morph_clip_asset_path,
    parse_morph_avatar,
    parse_morph_clip,
)


class SkeletalMorphService:
    def __init__(
        self,
        resolve_asset_bundle: Callable,
        export_monobehaviour_raw: Callable,
    ) -> None:
        self._resolve_asset_bundle = resolve_asset_bundle
        self._export_raw = export_monobehaviour_raw

    def build(
        self,
        index,
        model_asset: dict,
        animation_asset: dict,
        document: dict,
    ) -> dict:
        sidecar_path = morph_clip_asset_path(str(animation_asset["path"]))
        sidecar_matches = index.assets_by_path(sidecar_path)
        if len(sidecar_matches) != 1:
            raise RuntimeError(
                f"expected one skeletal-morph sidecar {sidecar_path!r}, "
                f"found {len(sidecar_matches)}"
            )

        avatar_names = morph_avatar_asset_names(str(model_asset["path"]))
        avatar_matches = []
        for position, avatar_name in enumerate(avatar_names):
            matches = [
                asset
                for asset in index.assets_by_name(avatar_name)
                if "/skeletalmorph/skeletalmorphcfg/"
                in str(asset["path"]).casefold()
            ]
            if len(matches) > 1 or (position == 0 and len(matches) != 1):
                expected = "one" if position == 0 else "at most one"
                raise RuntimeError(
                    f"expected {expected} skeletal-morph avatar {avatar_name!r}, "
                    f"found {len(matches)}"
                )
            avatar_matches.extend(matches)
        if not avatar_matches:
            raise RuntimeError(
                f"expected a skeletal-morph avatar for {model_asset['path']!r}"
            )

        sidecar_asset, sidecar_record, sidecar_chunk = self._resolve_asset_bundle(
            index,
            int(sidecar_matches[0]["assetIndex"]),
        )
        sidecar_raw, _ = self._export_raw(
            sidecar_record,
            sidecar_chunk,
            sidecar_asset,
        )
        clip = parse_morph_clip(sidecar_raw.read_bytes())
        avatar_assets = []
        avatars = []
        for avatar_match in avatar_matches:
            avatar_asset, avatar_record, avatar_chunk = self._resolve_asset_bundle(
                index,
                int(avatar_match["assetIndex"]),
            )
            avatar_raw, _ = self._export_raw(
                avatar_record,
                avatar_chunk,
                avatar_asset,
            )
            avatar_assets.append(avatar_asset)
            avatars.append(parse_morph_avatar(avatar_raw.read_bytes()))
        avatar = merge_morph_avatars(tuple(avatars))
        animation_index = int(animation_asset["asset_index"])
        return bake_morph_animation(
            document,
            clip,
            avatar,
            animation_id=f"animation:{animation_index}",
            source={
                "logicalPath": str(animation_asset["path"]),
                "bundle": str(animation_asset["bundle_name"]),
                "morphClipPath": str(sidecar_asset["path"]),
                "morphAvatarPaths": [
                    str(asset["path"]) for asset in avatar_assets
                ],
            },
        )
