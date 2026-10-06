import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devserver.core import DeveloperTools, _redact


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temp.name)
        self.records = []
        self.tools = DeveloperTools(self.root, audit=self.records.append)
        (self.root / "src").mkdir()
        (self.root / "src" / "sample.py").write_text("needle = 'hello'\n", encoding="utf-8")
        (self.root / "logs").mkdir()
        (self.root / "logs" / "app.log").write_text("started\ntoken=secretvalue\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "add", "src/sample.py"], cwd=self.root, check=True)
        subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"], cwd=self.root, check=True)
        (self.root / "src" / "sample.py").write_text("needle = 'changed'\ntoken=ghp_123456789012345678901234567890\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_search_and_status_and_diff(self):
        self.assertEqual(self.tools.search_project("needle")["matches"][0]["path"], "src/sample.py")
        self.assertEqual(self.tools.git_status()["branch"], "master")
        self.assertIn("needle", self.tools.git_diff()["diff"])
        self.assertNotIn("ghp_123456789012345678901234567890", self.tools.git_diff()["diff"])

    def test_search_redacts_secret_values_and_key_files(self):
        (self.root / "src" / "credentials.pem").write_text("private", encoding="utf-8")
        matches = self.tools.search_project("token=")["matches"]
        self.assertEqual(len(matches), 2)
        self.assertTrue(all("[REDACTED]" in match["text"] for match in matches))
        self.assertNotIn("secretvalue", json.dumps(matches))
        self.assertEqual(self.tools.call("read_logs", {"path": "src/credentials.pem"})["category"], "sensitive_path")

    def test_unittest_suite_runs(self):
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "__init__.py").write_text("", encoding="utf-8")
        (tests / "test_discovery.py").write_text(
            "import unittest\n"
            "class DiscoveryTest(unittest.TestCase):\n"
            "    def test_discovery(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        result = self.tools.run_tests("unittest")
        self.assertTrue(result["passed"], result)

    def test_log_is_bounded_and_redacted(self):
        result = self.tools.read_logs("logs/app.log")
        self.assertIn("[REDACTED]", result["content"])
        self.assertNotIn("secretvalue", result["content"])
        self.assertEqual(_redact("authorization=Bearer verysecretvalue"), "authorization=[REDACTED]")

    def test_path_traversal_and_sensitive_paths_rejected(self):
        for path in ("../outside.txt", "src/../../outside.txt", ".env"):
            with self.subTest(path=path):
                result = self.tools.call("read_logs", {"path": path})
                self.assertFalse(result["ok"])

    def test_symlink_outside_root_rejected(self):
        outside = self.root.parent / (self.root.name + "-outside.log")
        outside.write_text("sensitive", encoding="utf-8")
        link = self.root / "outside.log"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            outside.unlink(missing_ok=True)
            self.skipTest("symlink creation unavailable")
        try:
            self.assertFalse(self.tools.call("read_logs", {"path": "outside.log"})["ok"])
        finally:
            link.unlink(missing_ok=True)
            outside.unlink(missing_ok=True)

    def test_search_input_and_unknown_commands_rejected(self):
        self.assertFalse(self.tools.call("search_project", {"query": ""})["ok"])
        for suite in ("python -c import os", "git reset --hard", "pytest; whoami"):
            result = self.tools.call("run_tests", {"suite": suite})
            self.assertFalse(result["ok"])
            self.assertEqual(result["category"], "command_denied")

    def test_missing_log_and_bad_maximum(self):
        self.assertFalse(self.tools.call("read_logs", {"path": "logs/missing.log"})["ok"])
        self.assertFalse(self.tools.call("read_logs", {"path": "logs/app.log", "max_bytes": 999999})["ok"])

    def test_test_failure_and_timeout(self):
        failing = self.root / "tests"
        failing.mkdir()
        (failing / "__init__.py").write_text("", encoding="utf-8")
        (failing / "test_failure.py").write_text("import unittest\nclass T(unittest.TestCase):\n def test_bad(self): self.fail('expected')\n", encoding="utf-8")
        self.assertFalse(self.tools.run_tests("unittest")["passed"])
        with patch("devserver.core._run_bounded", return_value={"timed_out": True, "returncode": -9, "stdout": "", "stderr": ""}):
            result = self.tools.call("run_tests", {"suite": "unittest"})
            self.assertFalse(result["ok"])
            self.assertEqual(result["category"], "timeout")

    def test_missing_executable(self):
        with patch("devserver.core.shutil.which", return_value=None):
            result = self.tools.call("run_tests", {"suite": "pytest"})
        self.assertEqual(result["category"], "missing_executable")

    def test_non_git_repository_and_git_failure(self):
        other = Path(tempfile.mkdtemp(dir=Path(__file__).resolve().parents[1]))
        try:
            no_git = DeveloperTools(other, audit=lambda _: None)
            self.assertEqual(no_git.call("git_status", {})["category"], "not_git_repository")
        finally:
            import shutil
            shutil.rmtree(other)
        with patch("devserver.core._run_bounded", return_value={"timed_out": False, "returncode": 1, "stdout": "", "stderr": ""}):
            self.assertEqual(self.tools.call("git_status", {})["category"], "git_failure")

    def test_github_success_failure_and_missing_credentials(self):
        def response(payload):
            class Fake:
                status = 200
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def read(self, _size): return json.dumps(payload).encode()
            return Fake()
        payload = {"full_name": "octo/project", "private": False, "html_url": "https://github.com/octo/project"}
        with patch("devserver.core.urllib.request.urlopen", return_value=response(payload)) as opener:
            self.assertEqual(self.tools.github_repo_info("octo", "project")["full_name"], "octo/project")
            self.assertNotIn("Authorization", opener.call_args.args[0].headers)
        with patch("devserver.core.urllib.request.urlopen", side_effect=OSError("network")):
            self.assertEqual(self.tools.call("github_repo_info", {"owner": "octo", "repo": "project"})["category"], "network_failure")
        with patch("devserver.core.urllib.request.urlopen", side_effect=__import__("urllib.error", fromlist=["HTTPError"]).HTTPError("url", 404, "missing", {}, None)):
            self.assertEqual(self.tools.call("github_repo_info", {"owner": "octo", "repo": "missing"})["category"], "not_found")

    def test_audit_records_no_arguments_or_secrets(self):
        self.tools.call("search_project", {"query": "needle"}, request_id="test-id")
        self.assertTrue(self.records[-1]["request_id"])
        self.assertTrue(self.records[-1]["success"])
        self.assertNotIn("needle", json.dumps(self.records[-1]))


if __name__ == "__main__":
    unittest.main()
