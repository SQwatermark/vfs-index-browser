import hashlib
import tempfile
import unittest
from pathlib import Path

from tools.build_combat_evidence_manifest import build_manifest, parse_artifact


class CombatEvidenceManifestTests(unittest.TestCase):
    def test_hashes_and_normalizes_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "game" / "GameAssembly.dll"
            second = root / "dump" / "Gameplay.Beyond.dll.cs"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_bytes(b"assembly")
            second.write_bytes(b"types")

            manifest = build_manifest(
                [("il2cpp.types.gameplay", second), ("client.gameAssembly", first)],
                client_version="1.2.4",
                root=root,
            )

        self.assertEqual("EndfieldCombatEvidenceManifest", manifest["format"])
        self.assertEqual("1.2.4", manifest["clientVersion"])
        self.assertEqual(
            ["client.gameAssembly", "il2cpp.types.gameplay"],
            [artifact["name"] for artifact in manifest["artifacts"]],
        )
        self.assertEqual("game/GameAssembly.dll", manifest["artifacts"][0]["path"])
        self.assertEqual(
            hashlib.sha256(b"assembly").hexdigest(),
            manifest["artifacts"][0]["sha256"],
        )

    def test_rejects_duplicate_names(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "value.bin"
            path.write_bytes(b"value")
            with self.assertRaisesRegex(ValueError, "names must be unique"):
                build_manifest(
                    [("same", path), ("same", path)],
                    client_version="1",
                )

    def test_rejects_artifacts_outside_root(self):
        with tempfile.TemporaryDirectory() as root_directory:
            with tempfile.TemporaryDirectory() as other_directory:
                path = Path(other_directory) / "value.bin"
                path.write_bytes(b"value")
                with self.assertRaisesRegex(ValueError, "outside"):
                    build_manifest(
                        [("outside", path)],
                        client_version="1",
                        root=Path(root_directory),
                    )

    def test_parses_name_path_pair(self):
        name, path = parse_artifact("client.metadata=data/global-metadata.dat")
        self.assertEqual("client.metadata", name)
        self.assertEqual(Path("data/global-metadata.dat"), path)


if __name__ == "__main__":
    unittest.main()
