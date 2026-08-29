import unittest
from pathlib import Path
from server import (
    ASSETBUNDLE_EXPORT_TYPES,
    internal_preview_kind,
    manifest_asset_entries,
)


class ServerPreviewTests(unittest.TestCase):
    def test_animation_clips_are_exported_and_previewed_as_text(self):
        self.assertIn("AnimationClip", ASSETBUNDLE_EXPORT_TYPES)
        self.assertEqual("text", internal_preview_kind(Path("idle.anim")))

    def test_manifest_fbx_sub_asset_matches_unique_export_name(self):
        entry = {"Name": "Idle", "Container": "", "Type": "AnimationClip"}
        self.assertEqual(
            [entry],
            manifest_asset_entries(
                {"assetEntries": [entry]},
                "assets/character/idle.fbx##idle",
            ),
        )

    def test_manifest_fbx_sub_asset_rejects_ambiguous_export_name(self):
        entries = [
            {"Name": "Idle", "Container": "", "Type": "AnimationClip"},
            {"Name": "idle", "Container": "", "Type": "AnimationClip"},
        ]
        self.assertEqual(
            [],
            manifest_asset_entries(
                {"assetEntries": entries},
                "assets/character/idle.fbx##idle",
            ),
        )

if __name__ == "__main__":
    unittest.main()
