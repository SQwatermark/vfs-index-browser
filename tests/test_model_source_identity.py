import tempfile
import unittest
from pathlib import Path

from model_source_identity import avatar_model_source_identity, ordinary_model_source_identity


def record(identifier: int, chunk: Path) -> dict:
    return {
        "id": identifier,
        "length": chunk.stat().st_size,
        "offset": identifier,
        "chunk_path": str(chunk),
    }


class ModelSourceIdentityTests(unittest.TestCase):
    def test_ordinary_identity_preserves_dependency_order_and_full_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry, dependency = root / "entry.chk", root / "dependency.chk"
            entry.write_bytes(b"entry")
            dependency.write_bytes(b"dependency")
            identity = ordinary_model_source_identity(
                record(1, entry), entry,
                {"asset_index": 7, "path": "assets/model.prefab", "bundle_name": "model.ab"},
                [(record(2, dependency), dependency)],
                [{"name": "missing.ab"}],
                builder_mtime_ns=11,
                tool_artifacts=[{"worker": 1}],
            )
            self.assertEqual(1, identity["recordId"])
            self.assertEqual(2, identity["dependencies"][0]["recordId"])
            self.assertEqual(7, identity["assetIndex"])
            self.assertEqual([{"name": "missing.ab"}], identity["missingDependencyBundles"])

    def test_avatar_identity_includes_plan_lod_builders_and_bundle_closure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chunk, builder = root / "bundle.chk", root / "builder.py"
            chunk.write_bytes(b"bundle")
            builder.write_text("builder", encoding="utf-8")
            source = record(3, chunk)
            identity = avatar_model_source_identity(
                source, chunk, {"asset_index": 9, "path": "assets/avatar.asset"},
                lod=2,
                avatar_mesh={"mesh": 1},
                resource_plan={"plan": 1},
                bundle_sources=[(source, chunk)],
                builder_paths=[builder],
                tool_artifacts=[{"worker": 2}],
            )
            self.assertEqual(2, identity["lod"])
            self.assertEqual({"mesh": 1}, identity["avatarMesh"])
            self.assertEqual(3, identity["bundles"][0]["recordId"])
            self.assertEqual(builder.stat().st_mtime_ns, identity["builders"]["builder.py"])


if __name__ == "__main__":
    unittest.main()
