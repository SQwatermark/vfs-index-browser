import unittest
from pathlib import Path


PUBLISH_SCRIPT = (
    Path(__file__).resolve().parents[1] / "tools" / "Publish-Windows.ps1"
)
VALIDATE_SCRIPT = PUBLISH_SCRIPT.with_name("Test-WindowsRelease.ps1")
ACL_BUILD_SCRIPT = (
    PUBLISH_SCRIPT.parents[1]
    / "unity-worker"
    / "tools"
    / "Build-EndfieldAcl.ps1"
)


class ReleasePackagingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PUBLISH_SCRIPT.read_text(encoding="utf-8")
        cls.validator_source = VALIDATE_SCRIPT.read_text(encoding="utf-8")
        cls.acl_build_source = ACL_BUILD_SCRIPT.read_text(encoding="utf-8")

    def test_acceptance_tool_is_shipped_inside_the_release(self):
        self.assertIn('"tools/Test-WindowsRelease.ps1"', self.source)

    def test_operator_facing_deployment_documents_are_shipped(self):
        self.assertIn('"docs/deployment/windows-release.md"', self.source)
        self.assertIn('"docs/deployment/animestudio-archive.md"', self.source)

    def test_formal_release_refuses_an_implicitly_dirty_source_tree(self):
        self.assertIn("status --porcelain", self.source)
        self.assertIn('sourceTree = $SourceTree', self.source)
        self.assertIn("-not $AllowDirty", self.source)
        self.assertIn('sourceTree -ne "clean"', self.validator_source)

    def test_absolute_output_paths_are_not_joined_to_the_current_directory(self):
        self.assertIn("IsPathRooted", self.source)
        self.assertIn("IsPathRooted", self.validator_source)

    def test_release_scripts_support_windows_powershell_51_path_apis(self):
        self.assertNotIn("GetRelativePath", self.source)
        self.assertNotIn("GetRelativePath", self.validator_source)

    def test_python_runtime_libraries_are_required_release_files(self):
        self.assertIn('"_internal/VCRUNTIME140.dll"', self.validator_source)
        self.assertIn('"_internal/VCRUNTIME140_1.dll"', self.validator_source)
        self.assertIn('"_internal/ucrtbase.dll"', self.validator_source)

    def test_acl_bridge_rejects_dynamic_cpp_runtime_dependencies(self):
        self.assertIn("'/MT'", self.acl_build_source)
        self.assertIn("/dependents", self.acl_build_source)
        self.assertIn("VCRUNTIME", self.acl_build_source)
        self.assertIn("MSVCP", self.acl_build_source)

    def test_acl_bridge_preserves_unicode_paths_in_its_command_file(self):
        self.assertIn("chcp 65001", self.acl_build_source)
        self.assertIn("UTF8Encoding", self.acl_build_source)
        self.assertNotIn("-Encoding ASCII", self.acl_build_source)


if __name__ == "__main__":
    unittest.main()
