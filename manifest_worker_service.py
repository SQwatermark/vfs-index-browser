"""Application service for manifest assets exported through the Unity worker."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from cache_versions import CACHE_VERSIONS
from assetbundle_worker_service import manifest_asset_entries
from projectile_data import ProjectileDecodeError
from unity_worker import UnityWorkerError
from worker_run_service import WorkerRunService


CUBEMAP_FACE_NAMES = (
    "PositiveX",
    "NegativeX",
    "PositiveY",
    "NegativeY",
    "PositiveZ",
    "NegativeZ",
)

_MONOBEHAVIOUR_SUFFIXES = frozenset({".asset", ".prefab"})
_CUBEMAP_SUFFIXES = frozenset({".exr", ".hdr", ".cubemap"})


def _file_suffix(path: str) -> str:
    return Path(path.replace("\\", "/")).suffix.casefold()


def _published_path(root: Path, relative: str) -> Path | None:
    candidate = (root / relative.replace("\\", "/").strip("/")).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


class ManifestWorkerService:
    def __init__(
        self,
        cache_root: Path,
        worker: object,
        write_file_slice: Callable[[dict, Path, Path], None],
        runs: WorkerRunService,
    ) -> None:
        self._cache_root = cache_root
        self._worker = worker
        self._write_file_slice = write_file_slice
        self._runs = runs

    def _run_paths(
        self,
        record: dict,
        asset_index: int,
        export_name: str,
    ) -> tuple[Path, Path]:
        root = (
            self._cache_root
            / str(record["id"])
            / "manifest-assets"
            / str(asset_index)
            / export_name
        )
        return root / "runs", root / "meta.json"

    def ensure_export(
        self,
        record: dict,
        chunk_path: Path,
        asset: dict,
        *,
        export_name: str,
        version: int,
        identity_extra: dict,
        invoke: Callable[[Path, Path, str, str, object | None], dict],
        derive: Callable[[Path, list[Path]], dict[str, str]] | None = None,
        validate: Callable[[Path, list[Path], dict], None] | None = None,
        cancel_event: object | None = None,
        allowed_suffixes: frozenset[str] | None = _MONOBEHAVIOUR_SUFFIXES,
    ) -> tuple[Path, list[Path], dict]:
        if allowed_suffixes is not None and _file_suffix(str(asset["path"])) not in allowed_suffixes:
            raise ValueError("Unity worker export does not support this asset suffix")

        asset_index = int(asset["asset_index"])
        runs_root, meta_path = self._run_paths(record, asset_index, export_name)
        normalized_container = str(asset["path"]).replace("\\", "/").strip("/")
        source_identity = {
            "recordId": int(record["id"]),
            "length": int(record["length"]),
            "offset": int(record["offset"]),
            "chunkPath": str(record["chunk_path"]),
            "chunkMtimeNs": chunk_path.stat().st_mtime_ns,
            "assetIndex": asset_index,
            "assetPath": normalized_container,
            "toolArtifacts": self._worker.artifact_identity(),
            **identity_extra,
        }

        def run_export(
            run_root: Path,
            export_root: Path,
            request_id: str,
            cancel: object | None,
        ) -> dict:
            source_path = run_root / "source.ab"
            self._write_file_slice(record, chunk_path, source_path)
            return invoke(source_path, export_root, normalized_container, request_id, cancel)

        return self._runs.ensure(
            runs_root=runs_root,
            meta_path=meta_path,
            request_prefix=f"{export_name}-{asset_index}",
            version=version,
            source_identity=source_identity,
            invoke=run_export,
            derive=derive,
            validate=validate,
            cancel_event=cancel_event,
        )

    def ensure_projectile_component(
        self,
        record: dict,
        chunk_path: Path,
        asset: dict,
        projectile_id: str,
        *,
        cancel_event: object | None = None,
    ) -> tuple[Path, dict] | None:
        if _file_suffix(str(asset["path"])) not in _MONOBEHAVIOUR_SUFFIXES:
            return None
        try:
            export_root, artifacts, meta = self.ensure_export(
                record,
                chunk_path,
                asset,
                export_name="projectile-component",
                version=CACHE_VERSIONS.version("projectile-component-export"),
                identity_extra={"projectileId": projectile_id},
                invoke=lambda source, output, container, request_id, cancel: (
                    self._worker.decode_projectile_component(
                        input_path=source,
                        output_directory=output,
                        container=container,
                        projectile_id=projectile_id,
                        request_id=request_id,
                        cancel_event=cancel,
                    )
                ),
                cancel_event=cancel_event,
            )
        except UnityWorkerError:
            raise
        except RuntimeError as error:
            raise ProjectileDecodeError(str(error)) from error
        if len(artifacts) != 1:
            raise ProjectileDecodeError(
                f"expected one projectile artifact, found {len(artifacts)}"
            )
        return export_root, meta

    def ensure_animation_clip(
        self,
        record: dict,
        chunk_path: Path,
        asset: dict,
        map_meta: dict,
        *,
        cancel_event: object | None = None,
    ) -> tuple[dict, Path, dict]:
        animation_name = str(asset["path"]).rsplit("##", 1)[-1]
        animation_name = animation_name.replace("\\", "/").rsplit("/", 1)[-1]
        if animation_name.casefold().endswith(".anim"):
            animation_name = animation_name[:-5]

        matches = [
            entry
            for entry in manifest_asset_entries(map_meta, str(asset["path"]))
            if str(entry.get("Type") or "") == "AnimationClip"
            and str(entry.get("Name") or "").casefold() == animation_name.casefold()
        ]
        if len(matches) != 1:
            raise RuntimeError(
                "AnimationClip AssetMap identity must match exactly once, "
                f"found {len(matches)} for {animation_name!r}"
            )
        path_id = int(matches[0]["PathID"])
        expected_name = str(matches[0]["Name"])

        def validate(_root: Path, artifacts: list[Path], result: dict) -> None:
            if len(artifacts) != 1:
                raise RuntimeError(
                    f"expected one AnimationClip artifact, found {len(artifacts)}"
                )
            described = result.get("artifacts") or []
            if len(described) != 1:
                raise RuntimeError("AnimationClip worker result is missing its artifact")
            try:
                artifact_path_id = int(described[0].get("pathId"))
            except (TypeError, ValueError) as error:
                raise RuntimeError("AnimationClip worker returned an invalid PathID") from error
            if (
                artifact_path_id != path_id
                or str(described[0].get("name") or "") != expected_name
            ):
                raise RuntimeError("AnimationClip worker returned a different asset identity")
            document = json.loads(artifacts[0].read_text(encoding="utf-8-sig"))
            if (
                document.get("format") != "AnimeStudioAnimationClip"
                or document.get("version") != "1.1.0"
                or document.get("name") != expected_name
            ):
                raise RuntimeError("AnimationClip worker returned an incompatible document")

        export_root, artifacts, meta = self.ensure_export(
            record,
            chunk_path,
            asset,
            export_name="animation",
            version=CACHE_VERSIONS.version("animation-clip-export"),
            identity_extra={"pathId": path_id, "animationName": expected_name},
            invoke=lambda source, output, _container, request_id, cancel: (
                self._worker.export_animation_clip_json(
                    input_path=source,
                    output_directory=output,
                    path_id=path_id,
                    expected_name=expected_name,
                    request_id=request_id,
                    cancel_event=cancel,
                )
            ),
            validate=validate,
            cancel_event=cancel_event,
            allowed_suffixes=None,
        )
        target = artifacts[0]
        clip = json.loads(target.read_text(encoding="utf-8-sig"))
        meta["relativePath"] = target.relative_to(export_root).as_posix()
        return clip, target, meta

    def ensure_cubemap_export(
        self,
        record: dict,
        chunk_path: Path,
        asset: dict,
    ) -> tuple[dict[str, Path], dict] | None:
        if _file_suffix(str(asset["path"])) not in _CUBEMAP_SUFFIXES:
            return None

        def validate_faces(_root: Path, _paths: list[Path], result: dict) -> None:
            faces = [str(item.get("face") or "") for item in result.get("artifacts", [])]
            if len(faces) != len(CUBEMAP_FACE_NAMES) or set(faces) != set(CUBEMAP_FACE_NAMES):
                raise RuntimeError("Unity worker returned an incomplete Cubemap face set")

        export_root, artifact_paths, meta = self.ensure_export(
            record,
            chunk_path,
            asset,
            export_name="cubemap",
            version=CACHE_VERSIONS.version("cubemap-export"),
            identity_extra={},
            invoke=lambda source, output, container, request_id, cancel: (
                self._worker.export_cubemap_faces(
                    input_path=source,
                    output_directory=output,
                    container=container,
                    request_id=request_id,
                    cancel_event=cancel,
                )
            ),
            validate=validate_faces,
            allowed_suffixes=_CUBEMAP_SUFFIXES,
        )
        paths_by_relative = {
            path.relative_to(export_root).as_posix(): path for path in artifact_paths
        }
        faces = {}
        for artifact in meta.get("workerResult", {}).get("artifacts", []):
            face = str(artifact.get("face") or "")
            path = paths_by_relative.get(str(artifact.get("relativePath") or ""))
            if face in CUBEMAP_FACE_NAMES and path is not None and face not in faces:
                faces[face] = path
        if set(faces) != set(CUBEMAP_FACE_NAMES):
            raise RuntimeError("Unity worker returned an incomplete Cubemap face set")
        return faces, meta

    def ensure_monobehaviour_dump(
        self,
        record: dict,
        chunk_path: Path,
        asset: dict,
        *,
        cancel_event: object | None = None,
    ) -> tuple[Path, dict] | None:
        if _file_suffix(str(asset["path"])) not in _MONOBEHAVIOUR_SUFFIXES:
            return None

        def build_combined_dump(export_root: Path, artifacts: list[Path]) -> dict[str, str]:
            sections = []
            for path in artifacts:
                relative = path.relative_to(export_root).as_posix()
                text = path.read_text(encoding="utf-8", errors="replace").rstrip()
                sections.append(f"===== {relative} =====\n{text}")
            combined = export_root / "combined-dump.txt"
            combined.write_text("\n\n".join(sections) + "\n", encoding="utf-8")
            return {"combinedDump": combined.relative_to(export_root).as_posix()}

        export_root, artifacts, meta = self.ensure_export(
            record,
            chunk_path,
            asset,
            export_name="monobehaviour-typetree",
            version=CACHE_VERSIONS.version("monobehaviour-dump"),
            identity_extra={},
            invoke=lambda source, output, container, request_id, cancel: (
                self._worker.export_monobehaviour_typetree_dump(
                    input_path=source,
                    output_directory=output,
                    container=container,
                    request_id=request_id,
                    cancel_event=cancel,
                )
            ),
            derive=build_combined_dump,
            cancel_event=cancel_event,
        )
        if not artifacts:
            return None
        relative = str(meta["derivedFiles"]["combinedDump"]["relativePath"])
        dump_path = _published_path(export_root, relative)
        if dump_path is None or not dump_path.is_file():
            raise RuntimeError("published TypeTree combined dump is unavailable")
        return dump_path, meta

    def ensure_monobehaviour_raw(
        self,
        record: dict,
        chunk_path: Path,
        asset: dict,
        *,
        cancel_event: object | None = None,
    ) -> tuple[Path, dict]:
        if _file_suffix(str(asset["path"])) not in _MONOBEHAVIOUR_SUFFIXES:
            raise ValueError("raw MonoBehaviour export requires an .asset or .prefab")
        _root, artifacts, meta = self.ensure_export(
            record,
            chunk_path,
            asset,
            export_name="monobehaviour-raw",
            version=CACHE_VERSIONS.version("monobehaviour-raw"),
            identity_extra={},
            invoke=lambda source, output, container, request_id, cancel: (
                self._worker.export_monobehaviour_raw(
                    input_path=source,
                    output_directory=output,
                    container=container,
                    request_id=request_id,
                    cancel_event=cancel,
                )
            ),
            cancel_event=cancel_event,
        )
        if len(artifacts) != 1:
            raise RuntimeError(
                f"expected one raw MonoBehaviour artifact, found {len(artifacts)}"
            )
        meta["exportedFile"] = meta["exportedFiles"][0]
        return artifacts[0], meta
