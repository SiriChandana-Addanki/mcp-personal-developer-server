# Architecture

## Components and decisions

The project is organized as a small Python package with a stdlib-only runtime. `devserver.server` implements MCP JSON-RPC stdio framing, initialization, ping, tool and resource discovery, tool calls, and resource reads. `devserver.core.DeveloperTools` owns the policy checks, six tool implementations, explicit resource catalog, process boundaries, result limits, and audit events. `tests/` uses temporary directories and local Git repositories. `evaluate.py` measures a fixed set of cases.

```text
AI Host
  |
  v
MCP stdio
  |
  v
Developer Server
  |-- Tools: constrained actions (search, Git, tests, logs, GitHub)
  `-- Resources: explicit read-only project context
       |
       v
Security boundary
  |
  v
Project files / approved logs / GitHub metadata
  |
  `--> bounded MCP response; secret-free audit JSON to stderr
```

Tools perform constrained actions such as Git inspection or an approved test run. Resources expose constrained read-only context selected by explicit `project://` identifiers. The resource catalog contains existing project documentation and direct log files from the approved `logs/` and `log/` directories; it is not a filesystem browser.

There is no generic command runner. Explicit argv vectors and `shell=False` make the process surface inspectable. A custom stdlib JSON-RPC transport avoids a runtime framework dependency and keeps stdio behavior transparent. The protocol stdout is reserved for MCP messages.

## Container packaging

The Dockerfile runs `python -m devserver` directly as UID 10001, preserving MCP stdin/stdout and audit stderr without an HTTP listener or shell wrapper. It copies the application, tests, benchmark script, and documentation resources into `/home/app/project`; local `.git`, environment files, credentials, logs, and development environments are excluded. `PROJECT_ROOT` defaults to that packaged directory. A trusted project can be explicitly mounted and selected with `PROJECT_ROOT` when Git inspection and project test execution are required.

## Permission boundaries

- The configured root is canonicalized at startup; relative input paths are normalized, checked under it, and denied if any component is a symlink.
- Git runs only fixed read-only subcommands in that root.
- Tests accept only the `unittest` and `pytest` selectors and fixed server-defined argv.
- Logs can only be selected by relative path and allowed extension inside the project root; output is byte-capped and common inline credentials are redacted.
- GitHub uses a single metadata endpoint with an optional environment token; only selected metadata fields leave the API layer.
- Resources are listed from an explicit four-document allowlist plus direct `.log`, `.txt`, and `.jsonl` files in approved log directories. Reads enforce the same normalized project-root and symlink checks, sensitive-name rules, redaction for logs, and a 256,000-byte cap.
- Subprocess duration and retained stdout/stderr are bounded. Audit metadata contains no user arguments or credentials.

## Failure model

Tool calls return a stable `{ok, result, latency_ms, category}` envelope internally. MCP tool responses expose structured content and set `isError` for failures. Expected path, input, missing executable, timeout, Git, HTTP, and network errors have separate categories. Unexpected exceptions return a generic internal error. Startup fails clearly when `PROJECT_ROOT` is missing or not a directory.

## Limitations

The stdlib transport implements the core MCP lifecycle, tools, and resources surfaces, not optional MCP capabilities such as prompts, sampling, subscriptions, or progress notifications. There is no OS sandbox, so a trusted project's tests execute with the server's permissions. Symlink checks and reads are susceptible to filesystem races. Search and log redaction use heuristics and cannot guarantee secret detection. GitHub is a live external dependency for the benchmark's GitHub case.
