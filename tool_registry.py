"""Resolve and report optional external tool capabilities without executing them."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


@dataclass(frozen=True)
class ToolCapability:
    name: str
    configured: str
    resolved_path: Path | None

    @property
    def available(self) -> bool:
        return self.resolved_path is not None

    def as_json(self) -> dict:
        return {
            "name": self.name,
            "configured": self.configured,
            "available": self.available,
            "resolvedPath": str(self.resolved_path) if self.resolved_path else None,
        }


class ToolRegistry:
    def __init__(
        self,
        commands: Mapping[str, str | Path],
        *,
        which: Callable[[str], str | None] = shutil.which,
    ):
        self._capabilities = {
            name: self._resolve(name, configured, which)
            for name, configured in commands.items()
        }

    @staticmethod
    def _resolve(
        name: str,
        configured: str | Path,
        which: Callable[[str], str | None],
    ) -> ToolCapability:
        raw = str(configured)
        explicit = Path(raw)
        resolved = explicit.resolve() if explicit.is_file() else None
        if resolved is None and explicit.name == raw:
            discovered = which(raw)
            candidate = Path(discovered) if discovered else None
            resolved = candidate.resolve() if candidate and candidate.is_file() else None
        return ToolCapability(name, raw, resolved)

    def capability(self, name: str) -> ToolCapability:
        try:
            return self._capabilities[name]
        except KeyError:
            raise KeyError(f"optional tool is not registered: {name}") from None

    def available(self, name: str) -> bool:
        return self.capability(name).available

    def diagnostics(self) -> list[dict]:
        return [
            capability.as_json()
            for capability in self._capabilities.values()
        ]
