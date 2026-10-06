# MCP Personal Developer Server

A small local MCP server that gives an AI host controlled, read-oriented access to project search, Git status and diffs, approved tests, project logs, and basic GitHub repository metadata. It solves the gap between a useful coding assistant and an unrestricted shell: the MCP host can request a few explicit operations while the server owns the project boundary and execution policy.

This is a v1 implementation, not a production-readiness claim. It uses Python's standard library and has no runtime dependencies.

## Data and control flow

```text
AI Host -> MCP stdio client -> JSON-RPC MCP server
                              -> validate tool and bounded arguments
                              -> project/Git/test/log/GitHub operation
                              -> structured result + bounded audit event (stderr)
```

The MCP server exposes exactly six tools: `search_project`, `git_status`, `git_diff`, `run_tests`, `read_logs`, and `github_repo_info`. It does not expose arbitrary shell or Git commands.

## Setup and local execution

Python 3.10 or newer is required. No install step is needed for direct execution:

```powershell
$env:PROJECT_ROOT = (Get-Location).Path
python -m devserver
```

Configure the host's MCP client to launch `python -m devserver` with the repository as its working directory and `PROJECT_ROOT` set to the desired repository. The process speaks newline-delimited JSON-RPC on stdin/stdout; diagnostics and audit events go to stderr. Do not route protocol stdout to a log file.

| Variable | Purpose |
| --- | --- |
| `PROJECT_ROOT` | Existing project directory used for all local operations; defaults to process working directory. |
| `GITHUB_TOKEN` | Optional token for private repositories or higher GitHub API limits. Never logged. |

## Tools

- `search_project(query, path=".", max_results=50)` searches bounded text files, skipping common generated/dependency folders and sensitive names.
- `git_status()` returns branch and changed paths/status codes.
- `git_diff(staged=false)` returns a read-only diff capped at 48 KB.
- `run_tests(suite)` accepts only `unittest` or `pytest`; commands and arguments are server-owned.
- `read_logs(path, max_bytes=16000)` reads `.log`, `.txt`, or `.jsonl` files within the project root and redacts common inline credential assignments.
- `github_repo_info(owner, repo)` makes a bounded-time GitHub API GET and returns a small metadata subset.

## Security and permissions

See [SECURITY.md](SECURITY.md) for the threat boundary, input checks, output limits, and limitations. In brief: paths are normalized and checked under the canonical configured root; symlinks are denied; private/sensitive file names are skipped or denied; subprocesses use argument arrays, `shell=False`, fixed operations, timeouts, and capped pipe buffers; outputs are bounded; failures avoid returning raw exception details; and GitHub tokens are environment-based and never included in audit records.

The test tool executes project tests, which are code from the configured repository. Only enable it for repositories you trust. The server does not claim to sandbox malicious test code.

## Testing and evaluation

Run the deterministic standard-library tests:

```powershell
python -m unittest discover -s tests -v
```

Run the repeatable benchmark:

```powershell
python evaluate.py
```

The benchmark runs local cases and a GitHub API case (which needs network access); it prints measured case totals, success rate, average/median/p95 latency, and failures by category. It uses no fabricated baseline; the baseline is recorded as `Not measured yet.` See [EVALUATION.md](EVALUATION.md).

## Observability and failure handling

Each tool call emits a JSON audit record to stderr with UTC timestamp, tool name, generated correlation id, success, latency in milliseconds, and a short error category. It deliberately excludes tool arguments, client-supplied IDs, and environment variables. Tool errors are structured and categorized (`invalid_input`, `path_denied`, `timeout`, `git_failure`, `network_failure`, etc.); GitHub and process failures do not include tokens or raw exception content.

## Limitations and future improvements

This implementation is local and single-process. Path checks reduce traversal and symlink exposure but do not provide OS-level isolation against concurrent filesystem changes. Searching and log redaction use practical filename and text heuristics; they cannot identify every secret format. Approved test suites run with the server user's OS permissions. GitHub requests are limited to one repository metadata endpoint. Future work could add platform sandboxing, stronger secret scanning, configurable approved test targets, and integration tests against multiple MCP hosts.
