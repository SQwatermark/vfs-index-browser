import ast
import re
import subprocess
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = REPOSITORY_ROOT / "server.py"


def call_name(node: ast.Call) -> str:
    try:
        return ast.unparse(node.func)
    except (AttributeError, ValueError):
        return ""


class ServerArchitectureBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(SERVER_PATH.read_text(encoding="utf-8"))
        cls.handler = next(
            node
            for node in cls.tree.body
            if isinstance(node, ast.ClassDef) and node.name == "BrowserHandler"
        )

    def test_request_methods_do_not_parse_binary_formats(self):
        forbidden = {
            "parse_sparkbuffer",
            "decrypt_vfs_file",
            "MemoryPackReader",
            "Decoder",
            "struct.unpack",
            "struct.unpack_from",
        }
        violations = []
        for method in self.handler.body:
            if not isinstance(method, ast.FunctionDef) or not method.name.startswith(
                ("do_", "handle_", "serve_")
            ):
                continue
            for node in ast.walk(method):
                if isinstance(node, ast.Call) and call_name(node) in forbidden:
                    violations.append(f"{method.name}: {call_name(node)}")

        self.assertEqual([], violations)

    def test_http_server_does_not_launch_processes(self):
        forbidden = {
            "subprocess.run",
            "subprocess.Popen",
            "subprocess.call",
            "subprocess.check_call",
            "subprocess.check_output",
            "os.system",
        }
        violations = [
            call_name(node)
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call) and call_name(node) in forbidden
        ]

        self.assertEqual([], violations)

    def test_production_entrypoints_do_not_restore_legacy_animestudio_cli(self):
        forbidden = (
            "AnimeStudio.CLI",
            "ANIMESTUDIO_CLI",
            "ANIMESTUDIO_EXE",
            "data/research/AnimeStudio",
            "data\\research\\AnimeStudio",
        )
        entrypoints = (
            SERVER_PATH,
            SERVER_PATH.with_name("runtime_config.py"),
            SERVER_PATH.with_name("unity_worker.py"),
        )
        violations = {
            path.name: [value for value in forbidden if value in path.read_text(encoding="utf-8")]
            for path in entrypoints
        }

        self.assertEqual(
            {},
            {name: values for name, values in violations.items() if values},
        )

    def test_production_modules_do_not_embed_developer_machine_paths(self):
        developer_path = re.compile(
            r"(?:[a-z]:[\\/](?:projects|users)[\\/]|(?:/home|/users)/[^/\\]+[\\/])",
            re.IGNORECASE,
        )
        violations = {}
        tracked = subprocess.run(
            ["git", "ls-files", "--", "*.py"],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        production_modules = (
            REPOSITORY_ROOT / relative
            for relative in tracked
            if Path(relative).parent == Path(".")
        )
        for path in production_modules:
            matches = sorted(set(developer_path.findall(path.read_text(encoding="utf-8"))))
            if matches:
                violations[path.name] = matches

        self.assertEqual({}, violations)


if __name__ == "__main__":
    unittest.main()
