import json
import struct
import tempfile
import unittest
from pathlib import Path

from shader_binary_packages import (
    ShaderBinaryPackageError,
    load_shader_binary_package,
)


class ShaderBinaryPackageTests(unittest.TestCase):
    def test_loads_and_filters_parsed_programs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snippet_root = (
                root
                / "lod-000"
                / "platform-000-segment-000.subprograms"
                / "subprogram-0007.gpu-program"
            )
            snippet_root.mkdir(parents=True)
            snippet = self._dxbc(stage=1)
            (snippet_root / "snippet-00.dxbc").write_bytes(snippet)
            manifest = self._manifest(
                program=self._program(
                    keywords=["_SILK_STOCKINGS", "_NORMALMAP"],
                    file="subprogram-0007.gpu-program/snippet-00.dxbc",
                    size=len(snippet),
                )
            )
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")

            package = load_shader_binary_package(path)

            self.assertEqual("HGRP/CharacterNPR", package.shader_name)
            self.assertEqual(1, len(package.subprograms))
            self.assertEqual("vertex", package.subprograms[0].snippets[0].stage)
            self.assertEqual("5_0", package.subprograms[0].snippets[0].shader_model)
            self.assertEqual(
                (package.subprograms[0],),
                package.find_subprograms(
                    required_keywords={"_SILK_STOCKINGS"},
                    forbidden_keywords={"VFX_CHARACTER_DISSOLVE"},
                    declared_program_kind="endfield-d3d11",
                ),
            )
            self.assertEqual(
                (),
                package.find_subprograms(
                    required_keywords={"VFX_CHARACTER_DISSOLVE"}
                ),
            )

    def test_rejects_missing_or_size_mismatched_snippets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snippet_root = (
                root
                / "lod-000"
                / "platform-000-segment-000.subprograms"
                / "subprogram-0007.gpu-program"
            )
            snippet_root.mkdir(parents=True)
            (snippet_root / "snippet-00.dxbc").write_bytes(self._dxbc(stage=0))
            manifest = self._manifest(
                program=self._program(
                    keywords=[],
                    file="subprogram-0007.gpu-program/snippet-00.dxbc",
                    size=5,
                )
            )
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ShaderBinaryPackageError, "size mismatch"):
                load_shader_binary_package(path)

    def test_does_not_treat_raw_only_program_as_decoded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            program = self._program(keywords=[], file="unused", size=0)
            program["programContainerStatus"] = "raw-only"
            manifest = self._manifest(program=program)
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")

            package = load_shader_binary_package(path)

            self.assertEqual((), package.subprograms)

    def test_accepts_external_pointer_metadata_before_blob(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._manifest(
                program=self._program(keywords=[], file="unused", size=0)
            )
            manifest["blobs"][0]["segments"][0]["subPrograms"][0][
                "programContainerStatus"
            ] = "raw-only"
            manifest["blobs"].insert(
                0,
                {
                    "index": 0,
                    "fileId": 1,
                    "pathId": 456,
                    "resolved": True,
                    "sourceFile": "external.resource",
                    "sourcePathId": 456,
                },
            )
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")

            package = load_shader_binary_package(path)

            self.assertEqual("HGRP/CharacterNPR", package.shader_name)
            self.assertEqual((), package.subprograms)

    @staticmethod
    def _program(*, keywords, file, size):
        return {
            "index": 7,
            "programType": 33,
            "declaredProgramKind": "endfield-d3d11",
            "keywords": keywords,
            "programContainerStatus": "parsed",
            "programContainerEncoding": "endfield-gpu-program-table-v1",
            "programSnippets": [
                {
                    "tableIndex": 0,
                    "offset": 176,
                    "size": size,
                    "file": file,
                    "format": "dxbc",
                }
            ],
        }

    @staticmethod
    def _manifest(*, program):
        return {
            "format": "AnimeStudioEndfieldShaderBinaryPackage",
            "version": "1.0.0",
            "status": "complete",
            "shader": {
                "name": "HGRP/CharacterNPR",
                "pathId": 123,
                "sourceFile": "character.ab",
            },
            "blobs": [
                {
                    "id": "lod-000",
                    "segments": [
                        {
                            "platformIndex": 0,
                            "segmentIndex": 0,
                            "subPrograms": [program],
                        }
                    ],
                }
            ],
        }

    @staticmethod
    def _dxbc(*, stage):
        version = (stage << 16) | (5 << 4)
        bytecode = struct.pack("<II", version, 2)
        chunk = b"SHEX" + struct.pack("<I", len(bytecode)) + bytecode
        total_size = 36 + len(chunk)
        return (
            b"DXBC"
            + bytes(16)
            + struct.pack("<III", 1, total_size, 1)
            + struct.pack("<I", 36)
            + chunk
        )


if __name__ == "__main__":
    unittest.main()
