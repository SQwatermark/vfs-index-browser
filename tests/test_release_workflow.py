import unittest
from pathlib import Path


WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "windows-release.yml"
)


class ReleaseWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_uses_locked_windows_build_environment(self):
        self.assertIn("runs-on: windows-2025", self.source)
        self.assertIn('python-version: "3.13"', self.source)
        self.assertIn('dotnet-version: "9.0.200"', self.source)
        self.assertIn("permissions:\n  contents: read", self.source)

    def test_runs_the_production_publish_and_validation_commands(self):
        self.assertIn("./tools/Publish-Windows.ps1", self.source)
        self.assertIn("./tools/Test-WindowsRelease.ps1", self.source)
        self.assertIn("-IsolatedRuntime", self.source)
        self.assertIn("endfield-vfs-browser-win-x64.zip.sha256", self.source)
        self.assertNotIn("-SkipTests", self.source)


if __name__ == "__main__":
    unittest.main()
