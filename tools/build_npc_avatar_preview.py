#!/usr/bin/env python3
"""从结构化 AvatarMesh 和 AnimeStudio 导出数据生成 NPC 预览 GLB。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from animestudio_animation import attach_animation_clip
from animestudio_model import load_standalone_material_payloads
from gltf_export import build_glb
from npc_avatar_model import (
    build_static_avatar_mesh_document,
    load_mesh_payloads,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将 NPC AvatarMesh 指定 LOD 的资源组装为预览 GLB。"
    )
    parser.add_argument(
        "avatar_mesh",
        type=Path,
        help="inspect_npc_avatar_mesh.py 生成的 JSON",
    )
    parser.add_argument("mesh_directory", type=Path, help="AnimeStudio Mesh JSON 目录")
    parser.add_argument("output", type=Path, help="GLB 输出路径")
    parser.add_argument(
        "--lod",
        type=int,
        choices=range(4),
        default=0,
        help="要导出的 LOD",
    )
    parser.add_argument(
        "--avatar",
        type=Path,
        help="可选的 AnimeStudio Avatar JSON，用于附加绑定骨架",
    )
    parser.add_argument(
        "--material-directory",
        type=Path,
        help="可选的 AnimeStudio Material JSON 目录",
    )
    parser.add_argument(
        "--texture-directory",
        type=Path,
        help="可选的 AnimeStudio Texture2D PNG 目录",
    )
    parser.add_argument(
        "--animation",
        type=Path,
        action="append",
        default=[],
        help="可重复指定的 AnimeStudio 紧凑 AnimationClip JSON",
    )
    parser.add_argument(
        "--model-document",
        type=Path,
        help="可选的 ModelDocument JSON 输出路径",
    )
    return parser.parse_args()


def load_texture_paths(root: Path) -> dict[str, str]:
    textures = {}
    for path in sorted(root.rglob("*.png")):
        key = path.stem.casefold()
        if key in textures:
            raise ValueError(f"纹理图片名称重复：{path.stem}")
        textures[key] = str(path.resolve())
    return textures


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def attach_animations(
    document: dict,
    geometry: bytes,
    paths: list[Path],
) -> bytes:
    for index, path in enumerate(paths):
        clip = load_json(path)
        clip_name = str(clip.get("name") or path.stem)
        geometry = attach_animation_clip(
            document,
            geometry,
            clip,
            animation_id=f"animation:{index}:{clip_name}",
            source={"file": str(path.resolve())},
        )
    return geometry


def main() -> int:
    args = parse_args()
    try:
        avatar_mesh = load_json(args.avatar_mesh)
        avatar = load_json(args.avatar) if args.avatar else None
        meshes = load_mesh_payloads(args.mesh_directory)
        materials = (
            load_standalone_material_payloads(args.material_directory)
            if args.material_directory
            else None
        )
        texture_uris = (
            load_texture_paths(args.texture_directory)
            if args.texture_directory
            else None
        )
        document, geometry = build_static_avatar_mesh_document(
            avatar_mesh,
            meshes,
            lod=args.lod,
            avatar=avatar,
            material_payloads=materials,
            texture_uris=texture_uris,
        )
        geometry = attach_animations(document, geometry, args.animation)
        glb = build_glb(
            document,
            geometry,
            lambda image: Path(image["uri"]).read_bytes(),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(glb)
    if args.model_document:
        args.model_document.parent.mkdir(parents=True, exist_ok=True)
        args.model_document.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(
        f"wrote {args.output}: {len(document['meshes'])} meshes, "
        f"{len(document['nodes'])} nodes, {len(document['skins'])} skins, "
        f"{len(document['materials'])} materials, "
        f"{len(document['animations'])} animations, {len(glb)} bytes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
