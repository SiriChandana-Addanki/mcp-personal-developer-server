"""Secure, bounded implementations for the developer server's six tools."""
from __future__ import annotations

import json
import itertools
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

MAX_TEXT = 32_000
MAX_DIFF = 48_000
MAX_SEARCH_RESULTS = 100
MAX_SEARCH_FILES = 2_000
MAX_FILE_BYTES = 1_000_000
MAX_LOG_BYTES = 64_000
MAX_OUTPUT = 64_000
PROCESS_TIMEOUT = 120
MAX_RESOURCE_BYTES = 256_000
RESOURCE_DOCUMENTS = {
    "README.md": ("Project README", "text/markdown"),
    "ARCHITECTURE.md": ("Project architecture", "text/markdown"),
    "SECURITY.md": ("Project security model", "text/markdown"),
    "EVALUATION.md": ("Project evaluation guide", "text/markdown"),
}
RESOURCE_LOG_EXTENSIONS = {".log", ".txt", ".jsonl"}
MAX_RESOURCE_LOGS = 100
MAX_RESOURCE_LOG_SCAN = 1_000
SECRET_NAME = re.compile(r"(^|[._-])(\.env|secrets?|credentials?|tokens?|passwords?|private|id_rsa)([._-]|$)", re.I)
SENSITIVE_EXTENSIONS = {".pem", ".key", ".p12", ".pfx", ".kdbx"}
SKIP_DIRS = {".git", ".mcp", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}


class ToolError(Exception):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


def _bounded(value: str, limit: int = MAX_TEXT) -> str:
    return value[:limit] + ("\n[truncated]" if len(value) > limit else "")


def _redact(text: str) -> str:
    text = re.sub(r"(?i)(authorization\s*[=:]\s*)(?:Bearer\s+)?[^\s,;]+",
                  r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(token|password|secret|api[_-]?key)(\s*[=:]\s*)[^\s,;]+",
                  r"\1\2[REDACTED]", text)
    text = re.sub(r"(?i)\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b",
                  "[REDACTED]", text)
    return re.sub(r"(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
                  "[REDACTED PRIVATE KEY]", text)


def _sensitive_name(name: str) -> bool:
    return bool(SECRET_NAME.search(name) or any(
        suffix.lower() in SENSITIVE_EXTENSIONS for suffix in Path(name).suffixes))


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_path(root: Path, relative: str, *, must_exist: bool = False) -> Path:
    if not isinstance(relative, str) or not relative or len(relative) > 512:
        raise ToolError("invalid_input", "path must be a non-empty relative path of at most 512 characters")
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ToolError("path_denied", "absolute paths and parent traversal are not allowed")
    # Avoid Windows realpath, which may require privileges unavailable inside
    # restricted app containers. Normalize lexically and reject symlink parts.
    resolved = Path(os.path.abspath(os.path.join(str(root), str(candidate))))
    if not _inside(resolved, root):
        raise ToolError("path_denied", "requested path is outside the configured project root")
    cursor = root
    for part in resolved.relative_to(root).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ToolError("path_denied", "symbolic links are not allowed for file access")
    if must_exist and not resolved.exists():
        raise ToolError("not_found", "requested path does not exist")
    if any(_sensitive_name(part) for part in resolved.relative_to(root).parts):
        raise ToolError("sensitive_path", "sensitive paths cannot be read")
    return resolved


def _run_bounded(argv: list[str], cwd: Path, timeout: int = PROCESS_TIMEOUT) -> dict[str, Any]:
    """Run only a preconstructed argv, drain pipes while retaining bounded output."""
    try:
        child_env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
                     if key in os.environ}
        child_env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"})
        proc = subprocess.Popen(argv, cwd=str(cwd), env=child_env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                shell=False, close_fds=True)
    except FileNotFoundError:
        raise ToolError("missing_executable", "required executable was not found") from None
    except OSError:
        raise ToolError("process_error", "approved process could not be started") from None
    buffers: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    def drain(name: str, stream: Any) -> None:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            room = MAX_OUTPUT - len(buffers[name])
            if room > 0:
                buffers[name].extend(chunk[:room])
    threads = [threading.Thread(target=drain, args=(n, s), daemon=True)
               for n, s in (("stdout", proc.stdout), ("stderr", proc.stderr))]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        proc.wait()
    for thread in threads:
        thread.join(timeout=2)
    proc.stdout.close()
    proc.stderr.close()
    result = {key: bytes(val).decode("utf-8", errors="replace") for key, val in buffers.items()}
    for key, val in buffers.items():
        if len(val) >= MAX_OUTPUT:
            result[key] += "\n[truncated]"
    return {"returncode": proc.returncode, "timed_out": timed_out, **result}


class DeveloperTools:
    def __init__(self, project_root: str | Path, *, audit: Callable[[dict[str, Any]], None] | None = None,
                 github_token: str | None = None, github_api: str = "https://api.github.com",
                 process_timeout: int = PROCESS_TIMEOUT):
        self.root = Path(project_root).resolve()
        if not self.root.is_dir():
            raise ValueError("PROJECT_ROOT must be an existing directory")
        self.audit = audit or (lambda record: print(json.dumps(record, separators=(",", ":")), file=sys.stderr, flush=True))
        self.github_token = github_token if github_token is not None else os.getenv("GITHUB_TOKEN")
        self.github_api = github_api.rstrip("/")
        self.process_timeout = max(1, min(int(process_timeout), PROCESS_TIMEOUT))

    def call(self, name: str, arguments: dict[str, Any], request_id: Any = None) -> dict[str, Any]:
        started = time.perf_counter()
        # Never copy client-controlled IDs into audit logs; use our own correlation ID.
        correlation_id = str(uuid.uuid4())
        category = "success"
        try:
            if not isinstance(arguments, dict):
                raise ToolError("invalid_input", "tool arguments must be an object")
            method = {"search_project": self.search_project, "git_status": self.git_status,
                      "git_diff": self.git_diff, "run_tests": self.run_tests,
                      "read_logs": self.read_logs, "github_repo_info": self.github_repo_info}.get(name)
            if method is None:
                raise ToolError("unknown_tool", "unknown tool")
            result = method(**arguments)
            success = True
        except ToolError as exc:
            category, success = exc.category, False
            result = {"error": {"category": exc.category, "message": str(exc)}}
        except TypeError:
            category, success = "invalid_input", False
            result = {"error": {"category": category, "message": "invalid tool arguments"}}
        except Exception:
            category, success = "internal_error", False
            result = {"error": {"category": category, "message": "tool failed safely"}}
        elapsed = (time.perf_counter() - started) * 1000
        try:
            self.audit({"timestamp": datetime.now(timezone.utc).isoformat(), "tool": name,
                        "request_id": correlation_id, "success": success, "latency_ms": round(elapsed, 3),
                        "error_category": None if success else category})
        except Exception:
            pass
        return {"ok": success, "result": result, "latency_ms": round(elapsed, 3), "category": category}

    def _audit_resource(self, operation: str, started: float, request_id: Any,
                        success: bool, category: str) -> float:
        elapsed = (time.perf_counter() - started) * 1000
        try:
            self.audit({"timestamp": datetime.now(timezone.utc).isoformat(), "tool": operation,
                        "request_id": str(uuid.uuid4()), "success": success,
                        "latency_ms": round(elapsed, 3),
                        "error_category": None if success else category})
        except Exception:
            pass
        return elapsed

    def list_resources(self, request_id: Any = None) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            resources = []
            for relative, (label, mime_type) in self._resource_catalog().items():
                path = self.root / relative
                if path.stat().st_size > MAX_RESOURCE_BYTES:
                    continue
                resources.append({"uri": f"project://{relative}", "name": label,
                                  "description": f"Read-only {label.lower()} resource.",
                                  "mimeType": mime_type})
            category, success, result = "success", True, {"resources": resources}
        except Exception:
            category, success, result = "internal_error", False, {"error": {"category": "internal_error", "message": "resource listing failed safely"}}
        elapsed = self._audit_resource("resources/list", started, request_id, success, category)
        return {"ok": success, "result": result, "latency_ms": round(elapsed, 3), "category": category}

    def _resource_catalog(self) -> dict[str, tuple[str, str]]:
        catalog: dict[str, tuple[str, str]] = {}
        for relative, (label, mime_type) in RESOURCE_DOCUMENTS.items():
            try:
                path = _safe_path(self.root, relative, must_exist=True)
                if path.is_file():
                    catalog[relative] = (label, mime_type)
            except ToolError:
                continue
        log_count = 0
        for directory in ("logs", "log"):
            try:
                log_root = _safe_path(self.root, directory, must_exist=True)
                if not log_root.is_dir():
                    continue
                children = sorted(itertools.islice(log_root.iterdir(), MAX_RESOURCE_LOG_SCAN),
                                  key=lambda item: item.name.casefold())
            except (ToolError, OSError):
                continue
            for child in children:
                if log_count >= MAX_RESOURCE_LOGS:
                    break
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", child.name):
                    continue
                if child.suffix.lower() not in RESOURCE_LOG_EXTENSIONS:
                    continue
                relative = f"{directory}/{child.name}"
                try:
                    path = _safe_path(self.root, relative, must_exist=True)
                    if not path.is_file():
                        continue
                except ToolError:
                    continue
                mime_type = "application/x-ndjson" if path.suffix.lower() == ".jsonl" else "text/plain"
                catalog[relative] = (f"Project log: {child.name}", mime_type)
                log_count += 1
        return catalog

    @staticmethod
    def _resource_identifier(uri: str) -> str:
        if not isinstance(uri, str) or not uri or len(uri) > 2048:
            raise ToolError("invalid_input", "resource URI is malformed")
        if not uri.startswith("project://"):
            raise ToolError("invalid_input", "only project resource URIs are supported")
        try:
            parts = urllib.parse.urlsplit(uri)
        except ValueError:
            raise ToolError("invalid_input", "resource URI is malformed") from None
        if parts.scheme != "project" or not parts.netloc or parts.query or parts.fragment:
            raise ToolError("invalid_input", "resource URI is malformed")
        if any(char in uri for char in ("%", "\\", "\x00")) or any(char.isspace() for char in uri):
            raise ToolError("path_denied", "resource URI uses a denied path representation")
        identifier = parts.netloc + parts.path
        if identifier.startswith("/") or re.match(r"^[A-Za-z]:", identifier) or ":" in identifier:
            raise ToolError("path_denied", "absolute paths are not allowed as resources")
        segments = identifier.split("/")
        if any(segment in ("", ".", "..") for segment in segments):
            raise ToolError("path_denied", "resource path traversal is not allowed")
        if any(ord(char) < 32 for char in identifier):
            raise ToolError("invalid_input", "resource URI is malformed")
        return identifier

    def read_resource(self, uri: str, request_id: Any = None) -> dict[str, Any]:
        started = time.perf_counter()
        category, success = "success", True
        try:
            identifier = self._resource_identifier(uri)
            parts = identifier.split("/")
            if any(_sensitive_name(part) for part in parts):
                raise ToolError("sensitive_path", "sensitive paths cannot be read")
            log_candidate = (len(parts) == 2 and parts[0] in {"log", "logs"}
                             and Path(parts[1]).suffix.lower() in RESOURCE_LOG_EXTENSIONS)
            if identifier not in RESOURCE_DOCUMENTS and not log_candidate:
                raise ToolError("resource_not_found", "resource is not in the approved catalog")
            try:
                path = _safe_path(self.root, identifier, must_exist=True)
            except ToolError as exc:
                if exc.category == "not_found":
                    raise ToolError("resource_not_found", "resource was not found") from None
                raise
            catalog = self._resource_catalog()
            if identifier not in catalog:
                raise ToolError("resource_not_found", "resource was not found")
            if not path.is_file():
                raise ToolError("resource_not_found", "resource was not found")
            size = path.stat().st_size
            if size > MAX_RESOURCE_BYTES:
                raise ToolError("output_limit", "resource exceeds the maximum permitted size")
            try:
                with path.open("rb") as stream:
                    data = stream.read(MAX_RESOURCE_BYTES + 1)
            except OSError:
                raise ToolError("internal_error", "resource could not be read safely") from None
            if len(data) > MAX_RESOURCE_BYTES:
                raise ToolError("output_limit", "resource exceeds the maximum permitted size")
            _, mime_type = catalog[identifier]
            text = data.decode("utf-8", errors="replace")
            if identifier.split("/", 1)[0] in {"log", "logs"}:
                text = _redact(text)
            result = {"contents": [{"uri": uri, "mimeType": mime_type,
                                    "text": text}]}
        except ToolError as exc:
            category, success = exc.category, False
            result = {"error": {"category": category, "message": str(exc)}}
        except Exception:
            category, success = "internal_error", False
            result = {"error": {"category": category, "message": "resource read failed safely"}}
        elapsed = self._audit_resource("resources/read", started, request_id, success, category)
        return {"ok": success, "result": result, "latency_ms": round(elapsed, 3), "category": category}

    def search_project(self, query: str, path: str = ".", max_results: int = 50) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip() or len(query) > 256:
            raise ToolError("invalid_input", "query must contain 1 to 256 characters")
        if not isinstance(max_results, int) or isinstance(max_results, bool) or not 1 <= max_results <= MAX_SEARCH_RESULTS:
            raise ToolError("invalid_input", f"max_results must be between 1 and {MAX_SEARCH_RESULTS}")
        base = self.root if path == "." else _safe_path(self.root, path, must_exist=True)
        if not base.is_dir():
            raise ToolError("invalid_input", "search path must be a directory")
        found, scanned = [], 0
        needle = query.casefold()
        for current, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not SECRET_NAME.search(d)
                             and not (Path(current) / d).is_symlink())
            for filename in sorted(files):
                file_path = Path(current) / filename
                if SECRET_NAME.search(filename) or file_path.is_symlink():
                    continue
                scanned += 1
                if scanned > MAX_SEARCH_FILES:
                    break
                try:
                    if file_path.stat().st_size > MAX_FILE_BYTES:
                        continue
                    lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                except OSError:
                    continue
                for number, line in enumerate(lines, 1):
                    if needle in line.casefold():
                        found.append({"path": file_path.relative_to(self.root).as_posix(), "line": number,
                                      "text": _redact(_bounded(line, 500))})
                        if len(found) >= max_results:
                            return {"matches": found, "truncated": True}
            if scanned > MAX_SEARCH_FILES:
                break
        return {"matches": found, "truncated": scanned > MAX_SEARCH_FILES}

    def _git(self, args: list[str]) -> dict[str, Any]:
        if not (self.root / ".git").exists():
            raise ToolError("not_git_repository", "configured project root is not a Git repository")
        try:
            result = _run_bounded(["git", "-C", str(self.root), *args], self.root, self.process_timeout)
        except ToolError as exc:
            if exc.category == "missing_executable":
                raise
            raise ToolError("git_failure", "Git operation failed") from None
        if result["timed_out"]:
            raise ToolError("timeout", "Git operation timed out")
        if result["returncode"]:
            raise ToolError("git_failure", "Git operation failed")
        return result

    def git_status(self) -> dict[str, Any]:
        raw = self._git(["status", "--porcelain=v1", "--branch", "--untracked-files=normal"])["stdout"]
        lines = raw.splitlines()
        branch = ""
        changes = []
        for line in lines:
            if line.startswith("## "):
                branch = line[3:]
            elif len(line) >= 3:
                changes.append({"index": line[0], "worktree": line[1], "path": line[3:]})
        return {"branch": branch, "changes": changes}

    def git_diff(self, staged: bool = False) -> dict[str, Any]:
        if not isinstance(staged, bool):
            raise ToolError("invalid_input", "staged must be a boolean")
        args = ["diff", "--no-ext-diff", "--no-color", "--"]
        if staged:
            args = ["diff", "--cached", "--no-ext-diff", "--no-color", "--"]
        diff = self._git(args)["stdout"]
        return {"diff": _bounded(_redact(diff), MAX_DIFF), "truncated": len(diff) > MAX_DIFF}

    def run_tests(self, suite: str) -> dict[str, Any]:
        if suite not in ("unittest", "pytest"):
            raise ToolError("command_denied", "suite must be one of the approved values: unittest, pytest")
        if suite == "unittest":
            argv = [sys.executable, "-m", "unittest", "discover", "-s", "tests"]
        else:
            exe = shutil.which("pytest")
            if not exe:
                raise ToolError("missing_executable", "approved pytest executable was not found")
            argv = [exe, "-q"]
        result = _run_bounded(argv, self.root, self.process_timeout)
        if result["timed_out"]:
            raise ToolError("timeout", "test suite exceeded the 120 second limit")
        return {"suite": suite, "passed": result["returncode"] == 0,
                "returncode": result["returncode"], "stdout": result["stdout"], "stderr": result["stderr"]}

    def read_logs(self, path: str, max_bytes: int = 16_000) -> dict[str, Any]:
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 1 <= max_bytes <= MAX_LOG_BYTES:
            raise ToolError("invalid_input", f"max_bytes must be between 1 and {MAX_LOG_BYTES}")
        log_path = _safe_path(self.root, path, must_exist=True)
        approved = any(_inside(log_path, candidate) for candidate in
                       (self.root / "logs", self.root / "log"))
        if not approved or not log_path.is_file() or log_path.suffix.lower() not in {".log", ".txt", ".jsonl"}:
            raise ToolError("path_denied", "only .log, .txt, or .jsonl files inside the project log area can be read")
        try:
            with log_path.open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                size = stream.tell()
                stream.seek(max(0, size - max_bytes))
                data = stream.read(max_bytes)
        except OSError:
            raise ToolError("not_found", "log file could not be read") from None
        text = data.decode("utf-8", errors="replace")
        text = _redact(text)
        return {"path": log_path.relative_to(self.root).as_posix(), "content": text,
                "truncated": size > max_bytes, "bytes_returned": len(data)}

    def github_repo_info(self, owner: str, repo: str) -> dict[str, Any]:
        if not isinstance(owner, str) or not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", repo) or repo in {".", ".."}:
            raise ToolError("invalid_input", "owner or repository name is malformed")
        url = f"{self.github_api}/repos/{owner}/{repo}"
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "mcp-personal-developer-server/0.1"}
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                if response.status != 200:
                    raise ToolError("github_failure", "GitHub API returned an unexpected status")
                payload = json.loads(response.read(256_001))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise ToolError("not_found", "GitHub repository was not found") from None
            raise ToolError("github_failure", f"GitHub API request failed with status {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            raise ToolError("network_failure", "GitHub API request failed") from None
        if not isinstance(payload, dict):
            raise ToolError("github_failure", "GitHub API response was malformed")
        fields = {"full_name": payload.get("full_name"), "description": payload.get("description"),
                  "html_url": payload.get("html_url"), "private": payload.get("private"),
                  "default_branch": payload.get("default_branch"), "stargazers_count": payload.get("stargazers_count"),
                  "forks_count": payload.get("forks_count")}
        return {key: _redact(_bounded(value, 2_000)) if isinstance(value, str) else value
                for key, value in fields.items()}
