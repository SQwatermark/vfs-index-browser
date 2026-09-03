"""Application service coordinating one ordinary manifest model build."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import quote

from model_build_session import ModelBuildSession
from model_source_identity import ordinary_model_source_identity
from model_worker_service import ModelBundleInput


class OrdinaryModelBuildService:
    def __init__(
        self,
        run_store: object,
        worker_service: object,
        document_service: object,
        worker_identity: Callable[[], object],
        *,
        version: int,
        snapshot_types: Iterable[str],
    ) -> None:
        self._runs = run_store
        self._worker = worker_service
        self._documents = document_service
        self._worker_identity = worker_identity
        self._version = version
        self._snapshot_types = tuple(snapshot_types)

    def ensure(
        self,
        record: dict,
        chunk_path: Path,
        asset: dict,
        dependency_bundles: list[dict],
        dependency_sources: list[tuple[dict, Path]],
        missing_dependency_bundles: list[dict],
        *,
        cancel_event: object | None = None,
        progress: Callable[[dict], None] | None = None,
    ) -> tuple[dict, dict]:
        record_id, asset_index = int(record["id"]), int(asset["asset_index"])
        cache_root, runs_root, pointer_path = self._runs.cache_paths(record_id, asset_index)
        source_identity = ordinary_model_source_identity(
            record,
            chunk_path,
            asset,
            dependency_sources,
            missing_dependency_bundles,
            builder_mtime_ns=self._documents.builder_mtime_ns,
            tool_artifacts=self._worker_identity(),
        )
        cached = self._runs.load_cached(
            cache_root,
            pointer_path,
            version=self._version,
            source_identity=source_identity,
            geometry_required=False,
        )
        if cached is not None:
            document, meta, _model_path = cached
            if progress is not None:
                progress({"stage": "cache", "completed": 4, "total": 4})
            return document, meta

        session = ModelBuildSession(
            runs_root, "model", record_id, asset_index, 4, progress=progress
        )
        staged = self._worker.stage_inputs(session.run_root, [
            ModelBundleInput("manifest:primary", record, chunk_path, "entry.ab"),
            *[
                ModelBundleInput(
                    f"record:{int(dependency['id'])}",
                    dependency,
                    dependency_chunk,
                    f"dependency-{int(dependency['id'])}.ab",
                )
                for dependency, dependency_chunk in dependency_sources
            ],
        ])
        session.report("cabMap", 1)
        cab_result = self._worker.build_cab_map(
            staged, session.cab_root, session.worker_request_id("cab"),
            cancel_event=cancel_event,
        )
        session.report("objects", 2)
        object_result = self._worker.export_objects(
            staged,
            session.cab_root / "cab-map.json",
            session.object_root,
            session.worker_request_id("objects"),
            primary_input_id="manifest:primary",
            selection_input_ids=[item["inputId"] for item in staged],
            included_types=self._snapshot_types,
            # A manifest asset identifies one exact object container.  Exporting the
            # whole Bundle makes unrelated prefab roots indistinguishable and also
            # defeats the worker's explicit-container validation boundary.
            containers=[str(asset["path"])],
            cancel_event=cancel_event,
        )
        session.add_step("buildCABMap", cab_result)
        session.add_step("exportObjectSnapshots", object_result)
        assembly = self._documents.assemble(
            session.object_root,
            logical_path=str(asset["path"]),
            bundle=str(asset["bundle_name"]),
            buffer_uri=(
                f"/api/manifest-asset/model-buffer?recordId={record_id}"
                f"&assetIndex={asset_index}&run={quote(session.request_id)}"
            ),
        )
        session.report("textures", 3)
        if assembly.textures:
            texture_result = self._worker.export_textures(
                staged,
                session.cab_root / "cab-map.json",
                session.texture_root,
                session.worker_request_id("textures"),
                primary_input_id="manifest:primary",
                selections=[
                    {"sourceFile": identity.source_file, "pathId": identity.path_id}
                    for identity in assembly.textures
                ],
                cancel_event=cancel_event,
            )
            session.add_step("exportIdentifiedTextures", texture_result)
            self._documents.attach_exported_textures(
                assembly,
                texture_result,
                lambda relative: (
                    f"/api/manifest-asset/model-texture?recordId={record_id}"
                    f"&assetIndex={asset_index}&run={quote(session.request_id)}"
                    f"&path={quote(relative)}"
                ),
            )
        self._documents.finalize(assembly, missing_dependency_bundles)
        meta = session.metadata(
            version=self._version,
            source=source_identity,
            scope="manifestDependencyClosure",
            dependencyBundles=dependency_bundles,
            missingDependencyBundles=missing_dependency_bundles,
        )
        self._runs.publish(
            cache_root,
            pointer_path,
            session.run_root,
            document=assembly.document,
            meta=meta,
            geometry=assembly.geometry,
            geometry_required=False,
            before_pointer=lambda: session.report("publish", 4),
        )
        return assembly.document, meta
