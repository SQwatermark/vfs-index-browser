import tempfile
import unittest
from pathlib import Path

from assetbundle_browser import (
    find_exported_file,
    list_export_directory,
    metadata_by_export_name,
    metadata_for_file,
    resolve_export_path,
)


class AssetBundleBrowserTests(unittest.TestCase):
    def test_path_id_suffix_binds_file_to_exact_metadata(self):
        entry = {
            "Name": "Idle",
            "PathID": 3251858251387051210,
            "Type": "AnimationClip",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "AnimationClip" / "Idle_p2D20EBD9BDD91CCA.anim"
            child.parent.mkdir()
            child.write_text("clip", encoding="utf-8")
            meta = {"assetEntries": [entry]}

            self.assertEqual(
                entry,
                metadata_for_file(child, root, metadata_by_export_name(meta)),
            )
            self.assertEqual(
                (child, entry),
                find_exported_file(root, meta, "AnimationClip", "Idle", str(entry["PathID"])),
            )

    def test_lists_direct_children_and_recursive_folder_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "Texture2D" / "nested"
            nested.mkdir(parents=True)
            (nested / "icon.png").write_bytes(b"png")

            listing = list_export_directory(root, "", {}, lambda _path: "image")

            self.assertEqual("Texture2D", listing["dirs"][0]["path"])
            self.assertEqual(1, listing["dirs"][0]["fileCount"])
            self.assertEqual(3, listing["dirs"][0]["totalBytes"])

    def test_rejects_path_escape_and_ambiguous_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "TextAsset" / "same.txt"
            entries = [
                {"Name": "same", "PathID": 1, "Type": "TextAsset"},
                {"Name": "same", "PathID": 2, "Type": "TextAsset"},
            ]

            self.assertIsNone(resolve_export_path(root, "../outside"))
            self.assertIsNone(
                metadata_for_file(child, root, metadata_by_export_name({"assetEntries": entries}))
            )


if __name__ == "__main__":
    unittest.main()
