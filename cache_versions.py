"""Named versions for server-owned derived cache artifacts."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping


class CacheVersionRegistry:
    def __init__(self, versions: Mapping[str, int]):
        normalized: dict[str, int] = {}
        for name, version in versions.items():
            if not name or name.strip() != name:
                raise ValueError("cache version names must be non-empty and normalized")
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                raise ValueError(f"cache version must be a positive integer: {name}")
            normalized[name] = version
        self._versions = MappingProxyType(normalized)

    def version(self, name: str) -> int:
        try:
            return self._versions[name]
        except KeyError:
            raise KeyError(f"cache artifact is not registered: {name}") from None

    def diagnostics(self) -> dict[str, int]:
        return dict(self._versions)


CACHE_VERSIONS = CacheVersionRegistry(
    {
        "animation-clip-export": 5,
        "assetbundle-map": 1,
        "assetbundle-preview": 4,
        "audio-package": 2,
        "avatar-model-snapshot": 5,
        "cubemap-export": 2,
        "model-blend": 12,
        "model-glb": 4,
        "model-snapshot": 33,
        "monobehaviour-dump": 3,
        "monobehaviour-raw": 2,
        "projectile-component-export": 2,
        "string-path-hash": 1,
        "usm-video": 2,
    }
)
