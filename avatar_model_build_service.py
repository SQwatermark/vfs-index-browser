"""Application service coordinating one Avatar model build."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import quote

from model_build_session import ModelBuildSession
from model_source_identity import avatar_model_source_identity
from model_worker_service import ModelBundleInput


class AvatarModelBuildService:
    def __init__(
        self,
        run_store: object,
        worker_service: object,
        document_service: object,
        plan_loader: Callable[..., tuple[dict, dict, dict]],
        bundle_closure: Callable[[object, dict], list[dict]],
        source_resolver: Callable[[list[dict]], tuple[list[tuple[dict, Path]], list[dict]]],
        container_selector: Callable[[dict], list[str]],
        worker_identity: Callable[[], object],
        *,
        version: int,
        builder_paths: Iterable[Path],
    ) -> None:
        self._runs = run_store
        self._worker = worker_service
        self._documents = document_service
        self._load_plan = plan_loader
        self._bundle_closure = bundle_closure
        self._resolve_sources = source_resolver
        self._containers = container_selector
        self._worker_identity = worker_identity
        self._version = version
        self._builder_paths = tuple(builder_paths)

    def ensure(
        self,
        index: object,
        asset: dict,
        bundle_record: dict,
        bundle_chunk: Path,
        lod: int,
        *,
        cancel_event: object | None = None,
        progress: Callable[[dict], None] | None = None,
    ) -> tuple[dict, dict, Path]:
        if lod not in range(4):
            raise ValueError(f"LOD must be in 0..3, got {lod}")

        if progress is not None:
            progress({"stage": "avatarPlan", "completed": 1, "total": 5})
        avatar_mesh, plan, plan_meta = self._load_plan(
            index,
            asset,
            bundle_record,
            bundle_chunk,
            lod,
            cancel_event=cancel_event,
        )
        bundles = self._bundle_closure(index, plan)
        bundle_sources, missing_bundles = self._resolve_sources(bundles)
        if missing_bundles:
            names = ", ".join(str(bundle["name"]) for bundle in missing_bundles)
            raise FileNotFoundError(f"AvatarMesh dependency bundles are missing: {names}")

        record_id, asset_index = int(bundle_record["id"]), int(asset["asset_index"])
        cache_root, runs_root, pointer_path = self._runs.cache_paths(
            record_id, asset_index, lod=lod
        )
        source_identity = avatar_model_source_identity(
            bundle_record,
            bundle_chunk,
            asset,
            lod=lod,
            avatar_mesh=avatar_mesh,
            resource_plan=plan,
            bundle_sources=bundle_sources,
            builder_paths=self._builder_paths,
            tool_artifacts=self._worker_identity(),
        )
        cached = self._runs.load_cached(
            cache_root,
            pointer_path,
            version=self._version,
            source_identity=source_identity,
            geometry_required=True,
        )
        if cached is not None:
            document, meta, model_path = cached
            if progress is not None:
                progress({"stage": "cache", "completed": 5, "total": 5})
            return document, meta, model_path

        session = ModelBuildSession(
            runs_root,
            "avatar",
            record_id,
            asset_index,
            5,
            lod=lod,
            progress=progress,
        )
        if not bundle_sources:
            raise RuntimeError("AvatarMesh resource plan produced no Bundle inputs")
        staged = self._worker.stage_inputs(session.run_root, [
            ModelBundleInput(
                f"record:{int(record['id'])}",
                record,
                chunk,
                f"bundle-{int(record['id'])}.ab",
            )
            for record, chunk in bundle_sources
        ])
        primary_input_id = staged[0]["inputId"]
        session.report("cabMap", 2)
        cab_result = self._worker.build_cab_map(
            staged,
            session.cab_root,
            session.worker_request_id("cab"),
            cancel_event=cancel_event,
        )
        session.report("objects", 3)
        object_result = self._worker.export_objects(
            staged,
            session.cab_root / "cab-map.json",
            session.object_root,
            session.worker_request_id("objects"),
            primary_input_id=primary_input_id,
            selection_input_ids=[item["inputId"] for item in staged],
            included_types=["Mesh", "Material", "Avatar"],
            containers=self._containers(plan),
            cancel_event=cancel_event,
        )
        session.add_step("buildCABMap", cab_result)
        session.add_step("exportObjectSnapshots", object_result)

        assembly = self._documents.load(session.object_root, plan)
        session.report("textures", 4)
        if assembly.texture_selections:
            texture_result = self._worker.export_textures(
                staged,
                session.cab_root / "cab-map.json",
                session.texture_root,
                session.worker_request_id("textures"),
                primary_input_id=primary_input_id,
                selections=assembly.texture_selections,
                cancel_event=cancel_event,
            )
            session.add_step("exportIdentifiedTextures", texture_result)
            self._documents.attach_exported_textures(
                assembly,
                texture_result,
                lambda relative: (
                    f"/api/manifest-asset/model-texture?recordId={record_id}"
                    f"&assetIndex={asset_index}&lod={lod}"
                    f"&run={quote(session.request_id)}&path={quote(relative)}"
                ),
            )

        document, geometry = self._documents.build(
            avatar_mesh,
            assembly,
            lod=lod,
            buffer_uri=(
                f"/api/manifest-asset/model-buffer?recordId={record_id}"
                f"&assetIndex={asset_index}&lod={lod}&run={quote(session.request_id)}"
            ),
        )
        meta = session.metadata(
            version=self._version,
            source=source_identity,
            scope="avatarMeshBundleClosure",
            resourcePlan=plan,
            planRun=plan_meta,
        )
        model_path = self._runs.publish(
            cache_root,
            pointer_path,
            session.run_root,
            document=document,
            meta=meta,
            geometry=geometry,
            geometry_required=True,
            before_pointer=lambda: session.report("publish", 5),
        )
        return document, meta, model_path
