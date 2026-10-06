import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devserver.core import DeveloperTools, MAX_RESOURCE_BYTES


DOCUMENTS = {
    "README.md": "readme content marker",
    "ARCHITECTURE.md": "architecture content marker",
    "SECURITY.md": "security content marker",
    "EVALUATION.md": "evaluation content marker",
}


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temp.name)
        for name, content in DOCUMENTS.items():
            (self.root / name).write_text(content, encoding="utf-8")
        self.audit = []
        self.tools = DeveloperTools(self.root, audit=self.audit.append)

    def tearDown(self):
        self.temp.cleanup()

    def test_list_contains_only_available_allowlisted_documents(self):
        listed = self.tools.list_resources()
        self.assertTrue(listed["ok"])
        resources = listed["result"]["resources"]
        self.assertEqual({item["uri"] for item in resources}, {f"project://{name}" for name in DOCUMENTS})
        self.assertTrue(all(item["name"] and item["mimeType"] == "text/markdown" for item in resources))
        (self.root / "SECURITY.md").unlink()
        self.assertNotIn("project://SECURITY.md", {item["uri"] for item in self.tools.list_resources()["result"]["resources"]})

    def test_all_document_resources_read_as_text(self):
        for name, content in DOCUMENTS.items():
            with self.subTest(name=name):
                outcome = self.tools.read_resource(f"project://{name}")
                self.assertTrue(outcome["ok"])
                item = outcome["result"]["contents"][0]
                self.assertEqual(item["uri"], f"project://{name}")
                self.assertEqual(item["mimeType"], "text/markdown")
                self.assertEqual(item["text"], content)

    def test_only_approved_log_files_are_listed_and_log_secrets_are_redacted(self):
        logs = self.root / "logs"
        logs.mkdir()
        (logs / "app.log").write_text("service started\ntoken=resource-log-secret\n", encoding="utf-8")
        (logs / "not-a-log.py").write_text("must not be catalogued", encoding="utf-8")
        listed = {item["uri"] for item in self.tools.list_resources()["result"]["resources"]}
        self.assertIn("project://logs/app.log", listed)
        self.assertNotIn("project://logs/not-a-log.py", listed)
        outcome = self.tools.read_resource("project://logs/app.log")
        self.assertTrue(outcome["ok"])
        self.assertIn("[REDACTED]", outcome["result"]["contents"][0]["text"])
        self.assertNotIn("resource-log-secret", json.dumps(outcome))

    def test_unknown_malformed_and_unsupported_uris_are_rejected(self):
        cases = {
            "project://notes.txt": "resource_not_found",
            "not a uri": "invalid_input",
            "file:///etc/passwd": "invalid_input",
            "https://example.com/README.md": "invalid_input",
            "project://": "invalid_input",
            "project://README.md?raw=1": "invalid_input",
        }
        for uri, category in cases.items():
            with self.subTest(uri=uri):
                outcome = self.tools.read_resource(uri)
                self.assertFalse(outcome["ok"])
                self.assertEqual(outcome["category"], category)

    def test_path_attacks_are_rejected(self):
        cases = (
            "project://../outside.txt",
            "project://docs/../../outside.txt",
            "project://C:/Windows/System32/win.ini",
            "project:///etc/passwd",
            "project://..\\outside.txt",
            "project://%2e%2e/outside.txt",
            "project://%2E%2E%5coutside.txt",
        )
        for uri in cases:
            with self.subTest(uri=uri):
                outcome = self.tools.read_resource(uri)
                self.assertFalse(outcome["ok"])
                self.assertIn(outcome["category"], {"path_denied", "invalid_input"})

    def test_sensitive_resource_names_are_denied(self):
        (self.root / ".env").write_text("API_TOKEN=never-return-this", encoding="utf-8")
        (self.root / "credentials.pem").write_text("PRIVATE KEY never-return-this", encoding="utf-8")
        for uri in ("project://.env", "project://credentials.pem"):
            outcome = self.tools.read_resource(uri)
            self.assertFalse(outcome["ok"])
            self.assertEqual(outcome["category"], "sensitive_path")
            self.assertNotIn("never-return-this", json.dumps(outcome))

    def test_symlink_escape_is_denied(self):
        outside = self.root.parent / (self.root.name + "-outside.md")
        outside.write_text("outside sentinel", encoding="utf-8")
        target = self.root / "README.md"
        target.unlink()
        try:
            target.symlink_to(outside)
        except (OSError, NotImplementedError):
            outside.unlink(missing_ok=True)
            self.skipTest("symlink creation unavailable")
        try:
            outcome = self.tools.read_resource("project://README.md")
            self.assertFalse(outcome["ok"])
            self.assertEqual(outcome["category"], "path_denied")
            self.assertNotIn("outside sentinel", json.dumps(outcome))
        finally:
            target.unlink(missing_ok=True)
            outside.unlink(missing_ok=True)

    def test_missing_and_oversized_resources_are_safe(self):
        (self.root / "README.md").unlink()
        missing = self.tools.read_resource("project://README.md")
        self.assertEqual(missing["category"], "resource_not_found")
        large = self.root / "README.md"
        large.write_bytes(b"X" * (MAX_RESOURCE_BYTES + 1))
        oversized = self.tools.read_resource("project://README.md")
        self.assertEqual(oversized["category"], "output_limit")
        self.assertNotIn("X" * 32, json.dumps(oversized))

    def test_unexpected_read_failure_is_safe(self):
        with patch.object(Path, "open", side_effect=PermissionError("private OS detail")):
            outcome = self.tools.read_resource("project://README.md")
        self.assertEqual(outcome["category"], "internal_error")
        self.assertNotIn("private OS detail", json.dumps(outcome))

    def test_audit_records_resource_operations_without_uri_or_content(self):
        self.tools.list_resources(request_id="caller-controlled-id")
        content = "unique-resource-body-marker"
        (self.root / "README.md").write_text(content, encoding="utf-8")
        self.tools.read_resource("project://README.md", request_id="another-client-id")
        audit_text = json.dumps(self.audit)
        self.assertEqual([row["tool"] for row in self.audit], ["resources/list", "resources/read"])
        self.assertTrue(all(row["success"] and row["latency_ms"] >= 0 and row["request_id"] for row in self.audit))
        self.assertNotIn(content, audit_text)
        self.assertNotIn("project://README.md", audit_text)
        self.assertNotIn("caller-controlled-id", audit_text)


if __name__ == "__main__":
    unittest.main()
