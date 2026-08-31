"""Single environment-backed configuration entry for the local VFS service."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


def resolve_application_root(
    module_file: str | Path,
    *,
    executable: str | Path,
    frozen: bool,
) -> Path:
    """Resolve data/assets beside the executable in a frozen Windows package."""

    anchor = Path(executable).parent if frozen else Path(module_file).parent
    return anchor.resolve()


def _path(environ: Mapping[str, str], name: str, default: Path) -> Path:
    return Path(environ.get(name, str(default)))


def parse_port(raw: str | int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"port must be an integer: {raw}") from None
    if not 1 <= value <= 65535:
        raise ValueError(f"port must be between 1 and 65535: {raw}")
    return value


def _choice(
    environ: Mapping[str, str],
    name: str,
    default: str,
    choices: set[str],
) -> str:
    value = environ.get(name, default).lower()
    if value not in choices:
        raise ValueError(f"{name} must be one of {', '.join(sorted(choices))}: {value}")
    return value


def discover_blender_executable(
    environ: Mapping[str, str],
    which: Callable[[str], str | None],
) -> Path:
    configured = environ.get("BLENDER_EXE") or which("blender")
    if configured:
        return Path(configured)
    install_root = Path(environ.get("ProgramFiles", r"C:\Program Files")) / "Blender Foundation"
    installed = sorted(
        install_root.glob("Blender */blender.exe"),
        key=lambda path: tuple(int(value) for value in re.findall(r"\d+", path.parent.name)),
        reverse=True,
    )
    return installed[0] if installed else install_root / "Blender 4.3" / "blender.exe"


@dataclass(frozen=True)
class RuntimeConfig:
    project_root: Path
    data_root: Path
    default_index: Path
    database: Path
    public_dir: Path
    internal_cache: Path
    audio_dialog_database: Path
    wwise_database: Path
    shader_archive_root: Path
    memorypack_schema: Path
    memorypack_union_map: Path
    blender_executable: Path
    blender_model_importer: Path
    blender_action_switcher: Path
    vgmstream_cli: Path
    usm_convert: Path
    ffmpeg: str
    host: str
    port: int
    log_level: str
    log_format: str

    @classmethod
    def load(
        cls,
        project_root: Path,
        *,
        environ: Mapping[str, str] | None = None,
        which: Callable[[str], str | None] = shutil.which,
    ) -> "RuntimeConfig":
        values = os.environ if environ is None else environ
        project_root = project_root.resolve()
        data_root = _path(values, "VFS_BROWSER_DATA_ROOT", project_root / "data")
        return cls(
            project_root=project_root,
            data_root=data_root,
            default_index=_path(
                values,
                "VFS_BROWSER_INDEX",
                data_root / "endfield-vfs-index.jsonl.tgz",
            ),
            database=_path(
                values,
                "VFS_BROWSER_DB",
                data_root / "endfield-vfs-index.sqlite",
            ),
            public_dir=_path(values, "VFS_BROWSER_PUBLIC_DIR", project_root / "public"),
            internal_cache=_path(
                values,
                "VFS_BROWSER_INTERNAL_CACHE",
                data_root / "internal-cache",
            ),
            audio_dialog_database=_path(
                values,
                "VFS_BROWSER_AUDIO_DIALOG_DB",
                data_root / "audio-dialog-index.sqlite",
            ),
            wwise_database=_path(
                values,
                "VFS_BROWSER_WWISE_DB",
                data_root / "wwise-index.sqlite",
            ),
            shader_archive_root=_path(
                values,
                "VFS_BROWSER_SHADER_ARCHIVE_ROOT",
                data_root / "shader-archives" / "1.4.4",
            ),
            memorypack_schema=_path(
                values,
                "VFS_BROWSER_MEMORYPACK_SCHEMA",
                project_root / "schemas" / "memorypack-known-schema.json",
            ),
            memorypack_union_map=_path(
                values,
                "VFS_BROWSER_MEMORYPACK_UNION_MAP",
                project_root / "schemas" / "memorypack-known-unions.json",
            ),
            blender_executable=discover_blender_executable(values, which),
            blender_model_importer=project_root / "tools" / "blender_import_model.py",
            blender_action_switcher=project_root / "tools" / "blender_action_switcher.py",
            vgmstream_cli=_path(
                values,
                "VGMSTREAM_CLI",
                project_root / "tools" / "vgmstream" / "vgmstream-cli.exe",
            ),
            usm_convert=_path(
                values,
                "USM_CONVERT",
                project_root / "tools" / "usm-convert.exe",
            ),
            ffmpeg=values.get("FFMPEG", "ffmpeg"),
            host=values.get("VFS_BROWSER_HOST", "127.0.0.1"),
            port=parse_port(values.get("VFS_BROWSER_PORT", "8765")),
            log_level=_choice(
                values,
                "VFS_BROWSER_LOG_LEVEL",
                "info",
                {"debug", "info", "warning", "error", "critical"},
            ),
            log_format=_choice(
                values,
                "VFS_BROWSER_LOG_FORMAT",
                "json",
                {"json", "text"},
            ),
        )
