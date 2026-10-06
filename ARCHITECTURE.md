# Architecture

## Components and decisions

The project is organized as a small Python package with a stdlib-only runtime. `devserver.server` implements MCP JSON-RPC stdio framing, initialization, ping, tool discovery, and tool calls. `devserver.core.DeveloperTools` owns the policy checks, six tool implementations, process boundaries, result limits, and audit events. `tests/` uses temporary directories and local Git repositories. `evaluate.py` measures a fixed set of cases.

```text
AI Host
  | MCP client over stdio
  v
devserver.server (MCP JSON-RPC: initialize, tools/list, tools/call)
  | validated fixed tool name + structured arguments
  v
DeveloperTools security boundary
  |-- search: canonical project paths, skip sensitive/generated content
  |-- Git: fixed status/diff argv, configured cwd only
  |-- tests: fixed suite selector, no user command arguments
  |-- logs: project-contained approved extensions and byte cap
  `-- GitHub: one HTTPS API metadata GET, optional environment token
  |
  `-- JSON result to client; secret-free structured audit JSON to stderr
```

There is no generic command runner. Explicit argv vectors and `shell=False` make the process surface inspectable. A custom stdlib JSON-RPC transport avoids a runtime framework dependency and keeps stdio behavior transparent. The protocol stdout is reserved for MCP messages.

## Permission boundaries

- The configured root is canonicalized at startup; relative input paths are normalized, checked under it, and denied if any component is a symlink.
- Git runs only fixed read-only subcommands in that root.
- Tests accept only the `unittest` and `pytest` selectors and fixed server-defined argv.
- Logs can only be selected by relative path and allowed extension inside the project root; output is byte-capped and common inline credentials are redacted.
- GitHub uses a single metadata endpoint with an optional environment token; only selected metadata fields leave the API layer.
- Subprocess duration and retained stdout/stderr are bounded. Audit metadata contains no user arguments or credentials.

## Failure model

Tool calls return a stable `{ok, result, latency_ms, category}` envelope internally. MCP tool responses expose structured content and set `isError` for failures. Expected path, input, missing executable, timeout, Git, HTTP, and network errors have separate categories. Unexpected exceptions return a generic internal error. Startup fails clearly when `PROJECT_ROOT` is missing or not a directory.

## Limitations

The stdlib transport implements the core MCP lifecycle and tools surface, not optional MCP capabilities such as resources, prompts, sampling, or progress notifications. There is no OS sandbox, so a trusted project's tests execute with the server's permissions. Symlink checks and reads are susceptible to filesystem races. Search skips known secret naming patterns but cannot guarantee secret detection. GitHub is a live external dependency for the benchmark's GitHub case.
