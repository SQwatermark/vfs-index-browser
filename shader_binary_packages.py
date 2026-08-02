"""Read AnimeStudio's lossless Endfield Shader binary package manifests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from dxbc import DxbcError, inspect_dxbc


PACKAGE_FORMAT = "AnimeStudioEndfieldShaderBinaryPackage"
PACKAGE_VERSION = "1.0.0"


class ShaderBinaryPackageError(ValueError):
    """Raised when an exported package violates the documented contract."""


@dataclass(frozen=True)
class ShaderProgramSnippet:
    table_index: int
    format: str
    path: Path
    size: int
    decoded_path: Path | None = None
    decoded_format: str | None = None
    stage: str | None = None
    shader_model: str | None = None


@dataclass(frozen=True)
class ShaderSubProgram:
    blob_id: str
    platform_index: int
    segment_index: int
    index: int
    program_type: int
    declared_program_kind: str
    keywords: frozenset[str]
    snippets: tuple[ShaderProgramSnippet, ...]


@dataclass(frozen=True)
class ShaderBinaryPackage:
    root: Path
    shader_name: str
    source_file: str
    path_id: int
    status: str
    subprograms: tuple[ShaderSubProgram, ...]

    def find_subprograms(
        self,
        *,
        required_keywords: Iterable[str] = (),
        forbidden_keywords: Iterable[str] = (),
        declared_program_kind: str | None = None,
    ) -> tuple[ShaderSubProgram, ...]:
        """Return candidates without guessing pass, stage, or runtime keyword state."""

        required = frozenset(required_keywords)
        forbidden = frozenset(forbidden_keywords)
        if required & forbidden:
            overlap = ", ".join(sorted(required & forbidden))
            raise ShaderBinaryPackageError(
                f"keywords cannot be both required and forbidden: {overlap}"
            )
        return tuple(
            program
            for program in self.subprograms
            if required <= program.keywords
            and not forbidden & program.keywords
            and (
                declared_program_kind is None
                or program.declared_program_kind == declared_program_kind
            )
        )


def load_shader_binary_package(manifest_path: Path) -> ShaderBinaryPackage:
    """Load and validate one ``*.shader.binary/manifest.json`` package."""

    manifest_path = Path(manifest_path)
    root = manifest_path.parent
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ShaderBinaryPackageError(
            f"cannot read Shader binary package manifest {manifest_path}: {error}"
        ) from error

    _expect(data.get("format") == PACKAGE_FORMAT, "unexpected package format")
    _expect(data.get("version") == PACKAGE_VERSION, "unsupported package version")
    _expect(data.get("status") in {"complete", "partial"}, "invalid package status")
    shader = _mapping(data.get("shader"), "shader")

    programs: list[ShaderSubProgram] = []
    for blob in _list(data.get("blobs"), "blobs"):
        blob = _mapping(blob, "blob")
        if "id" not in blob:
            _validate_external_pointer_record(blob)
            continue
        blob_id = _string(blob.get("id"), "blob.id")
        for segment in _list(blob.get("segments", []), f"blob {blob_id} segments"):
            segment = _mapping(segment, f"blob {blob_id} segment")
            platform_index = _integer(segment.get("platformIndex"), "platformIndex")
            segment_index = _integer(segment.get("segmentIndex"), "segmentIndex")
            for program in _list(segment.get("subPrograms", []), "subPrograms"):
                program = _mapping(program, "subProgram")
                if program.get("programContainerStatus") != "parsed":
                    continue
                programs.append(
                    _parse_subprogram(
                        root,
                        blob_id,
                        platform_index,
                        segment_index,
                        program,
                    )
                )

    return ShaderBinaryPackage(
        root=root,
        shader_name=_string(shader.get("name"), "shader.name"),
        source_file=_string(shader.get("sourceFile"), "shader.sourceFile"),
        path_id=_integer(shader.get("pathId"), "shader.pathId"),
        status=data["status"],
        subprograms=tuple(programs),
    )


def _validate_external_pointer_record(data: dict) -> None:
    """Validate metadata emitted before an external blob's storage record."""

    _integer(data.get("index"), "external pointer index")
    _integer(data.get("fileId"), "external pointer fileId")
    _integer(data.get("pathId"), "external pointer pathId")
    _expect(
        isinstance(data.get("resolved"), bool),
        "external pointer resolved must be a boolean",
    )
    if data["resolved"]:
        _string(data.get("sourceFile"), "external pointer sourceFile")
        _integer(data.get("sourcePathId"), "external pointer sourcePathId")


def _parse_subprogram(
    root: Path,
    blob_id: str,
    platform_index: int,
    segment_index: int,
    data: dict,
) -> ShaderSubProgram:
    encoding = data.get("programContainerEncoding")
    _expect(
        encoding == "endfield-gpu-program-table-v1",
        f"unsupported GPU program container encoding: {encoding!r}",
    )
    program_root = (
        root
        / blob_id
        / f"platform-{platform_index:03d}-segment-{segment_index:03d}.subprograms"
    )
    snippets = tuple(
        _parse_snippet(program_root, snippet)
        for snippet in _list(data.get("programSnippets"), "programSnippets")
    )
    _expect(bool(snippets), "parsed GPU program container has no snippets")
    table_indices = [snippet.table_index for snippet in snippets]
    _expect(
        len(table_indices) == len(set(table_indices)),
        "GPU program container has duplicate table indices",
    )
    return ShaderSubProgram(
        blob_id=blob_id,
        platform_index=platform_index,
        segment_index=segment_index,
        index=_integer(data.get("index"), "subProgram.index"),
        program_type=_integer(data.get("programType"), "subProgram.programType"),
        declared_program_kind=_string(
            data.get("declaredProgramKind"), "subProgram.declaredProgramKind"
        ),
        keywords=frozenset(
            _string(keyword, "subProgram keyword")
            for keyword in _list(data.get("keywords"), "subProgram.keywords")
        ),
        snippets=snippets,
    )


def _parse_snippet(blob_root: Path, data: object) -> ShaderProgramSnippet:
    data = _mapping(data, "programSnippet")
    file_path = _package_file(blob_root, data.get("file"), "programSnippet.file")
    declared_size = _integer(data.get("size"), "programSnippet.size")
    _expect(
        file_path.stat().st_size == declared_size,
        f"snippet size mismatch for {file_path}",
    )

    decoded_path = None
    decoded_format = data.get("decodedFormat")
    if data.get("decodedFile") is not None:
        decoded_path = _package_file(
            blob_root, data.get("decodedFile"), "programSnippet.decodedFile"
        )
        _expect(
            isinstance(decoded_format, str) and bool(decoded_format),
            "decoded snippet file has no decoded format",
        )
        if data.get("decodedSize") is not None:
            _expect(
                decoded_path.stat().st_size
                == _integer(data.get("decodedSize"), "programSnippet.decodedSize"),
                f"decoded snippet size mismatch for {decoded_path}",
            )
    else:
        _expect(decoded_format is None, "decoded format has no decoded snippet file")

    snippet_format = _string(data.get("format"), "programSnippet.format")
    stage = None
    shader_model = None
    if snippet_format == "dxbc":
        try:
            inspection = inspect_dxbc(file_path.read_bytes())
        except (OSError, DxbcError) as error:
            raise ShaderBinaryPackageError(
                f"cannot inspect DXBC snippet {file_path}: {error}"
            ) from error
        stage = inspection.stage
        shader_model = inspection.shader_model

    return ShaderProgramSnippet(
        table_index=_integer(data.get("tableIndex"), "programSnippet.tableIndex"),
        format=snippet_format,
        path=file_path,
        size=declared_size,
        decoded_path=decoded_path,
        decoded_format=decoded_format,
        stage=stage,
        shader_model=shader_model,
    )


def _package_file(root: Path, value: object, field: str) -> Path:
    relative = PurePosixPath(_string(value, field))
    _expect(not relative.is_absolute() and ".." not in relative.parts, f"unsafe {field}")
    path = root.joinpath(*relative.parts)
    _expect(path.is_file(), f"missing package file: {path}")
    return path


def _mapping(value: object, field: str) -> dict:
    _expect(isinstance(value, dict), f"{field} must be an object")
    return value


def _list(value: object, field: str) -> list:
    _expect(isinstance(value, list), f"{field} must be an array")
    return value


def _string(value: object, field: str) -> str:
    _expect(isinstance(value, str), f"{field} must be a string")
    return value


def _integer(value: object, field: str) -> int:
    _expect(
        isinstance(value, int) and not isinstance(value, bool),
        f"{field} must be an integer",
    )
    return value


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise ShaderBinaryPackageError(message)
