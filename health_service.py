"""Build the public service-health document from injected diagnostics."""

from __future__ import annotations

from collections.abc import Callable, Sequence


REQUIRED_UNITY_WORKER_CAPABILITIES = (
    "decodeProjectileComponent",
    "exportMonoBehaviourRaw",
    "exportMonoBehaviourTypeTreeDump",
    "buildAssetMap",
    "buildCabMap",
    "exportObjectSnapshots",
    "exportIdentifiedTextures",
    "exportCubemapFaces",
    "exportBundlePreviewMedia",
    "exportAnimationClipJson",
)


class HealthService:
    def __init__(
        self,
        diagnose_worker: Callable[[Sequence[str]], dict],
        diagnose_optional_tools: Callable[[], list[dict]],
        diagnose_cache_versions: Callable[[], dict],
    ) -> None:
        self._diagnose_worker = diagnose_worker
        self._diagnose_optional_tools = diagnose_optional_tools
        self._diagnose_cache_versions = diagnose_cache_versions

    def build(
        self,
        *,
        index_freshness: dict,
        index_rebuild: dict,
        manifest_index: dict,
        secondary_audio_indexes: dict,
        secondary_audio_rebuild: dict,
    ) -> dict:
        worker = self._diagnose_worker(list(REQUIRED_UNITY_WORKER_CAPABILITIES))
        blocking_statuses = {"stale", "unavailable"}
        ready = (
            worker["status"] == "ready"
            and index_freshness.get("status") not in blocking_statuses
            and manifest_index.get("status") != "unavailable"
            and secondary_audio_indexes.get("status") not in blocking_statuses
        )
        return {
            "apiVersion": 1,
            "status": "ready" if ready else "degraded",
            "indexFreshness": index_freshness,
            "indexRebuild": index_rebuild,
            "manifestIndex": manifest_index,
            "secondaryAudioIndexes": secondary_audio_indexes,
            "secondaryAudioRebuild": secondary_audio_rebuild,
            "unityWorker": worker,
            "optionalTools": self._diagnose_optional_tools(),
            "cacheVersions": self._diagnose_cache_versions(),
            "legacyTools": [],
        }
