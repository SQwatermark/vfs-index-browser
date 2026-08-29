"""External audio conversion adapter with atomic cache publication."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


class VgmstreamConversionService:
    def __init__(self, executable: Path, *, timeout_seconds: float = 60):
        self._executable = executable
        self._timeout_seconds = timeout_seconds

    def ensure_wav(self, wem_path: Path, wav_path: Path) -> Path:
        if wav_path.exists() and wav_path.stat().st_size > 0:
            return wav_path
        if not self._executable.exists():
            raise FileNotFoundError(
                f"vgmstream-cli.exe not found: {self._executable}"
            )
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = wav_path.with_name(
            f".{wav_path.name}.{os.getpid()}.{time.time_ns()}.tmp"
        )
        command = [str(self._executable), "-o", str(temporary), str(wem_path)]
        try:
            completed = subprocess.run(
                command,
                cwd=str(self._executable.parent),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout_seconds,
                check=False,
            )
            if completed.returncode != 0 or not temporary.exists():
                raise RuntimeError(
                    "vgmstream conversion failed: "
                    + (completed.stderr or completed.stdout or "")
                )
            os.replace(temporary, wav_path)
        finally:
            temporary.unlink(missing_ok=True)
        return wav_path
