# Security model

## Assets and trust boundary

The server may read project source, local logs, Git metadata, and optional GitHub credentials. Inputs arrive from an MCP client and are untrusted. `PROJECT_ROOT` and the server's OS identity define the maximum local scope; configure them deliberately. Repository test code is trusted code because the server runs it with its own OS permissions.

## Implemented controls

- **No arbitrary shell:** no shell tool exists. Test suite names are limited to `unittest` and `pytest`; argv is constructed by the server. `subprocess.Popen` uses `shell=False`, closed extra descriptors, and no stdin.
- **Git allowlist:** Git is invoked only as fixed `status --porcelain` or read-only `diff` commands against the configured root.
- **Resource allowlist:** only existing README/architecture/security/evaluation documents and direct `.log`, `.txt`, or `.jsonl` files under `logs/` or `log/` are catalogued. Resource URIs must use the `project://` namespace and pass strict traversal, absolute-path, encoding, separator, containment, symlink, and sensitive-path checks. Other schemes and unlisted identifiers are rejected; the resource interface cannot read arbitrary paths.
- **Resource size and content:** each resource read is capped at 256,000 bytes. The server checks the file size before reading and reads at most one byte beyond the cap to detect a concurrent growth; oversized content is rejected without returning a partial payload. Log resources reuse the existing redaction rules. Resource audit events include the operation and generated correlation id but not the URI or content.
- **Path containment:** paths must be relative; `..` and absolute paths are rejected. Normalized containment checks block paths outside the canonical root, and every symlink component is denied. Search also avoids symlinks; Git receives no user-controlled path arguments.
- **Sensitive data:** search skips files and directories with sensitive names. Explicit file reads and Resources also reject common key/certificate extensions through the shared path policy. Search results, diffs, and logs redact common `token=`, `password=`, `secret=`, `authorization=`, GitHub/AWS token forms, and private-key blocks. GitHub credentials are taken from `GITHUB_TOKEN`, sent only to GitHub, and excluded from errors and audit logs.
- **Resource limits:** arguments have length/range caps; project search caps files and results; candidate text files are size-capped; subprocesses have a hard maximum timeout and concurrent pipe-draining with fixed-size retained buffers; diffs and logs have byte/character limits; GitHub has a 10-second timeout and response read cap.
- **Safe failures:** errors are categorized with generic messages; raw subprocess stderr and exceptions are not returned for infrastructure failures. Tool arguments are never written into audit logs.
- **Least privilege:** the server performs local read operations, runs the explicitly requested test suite, and makes a single GitHub repository GET. It does not modify Git state or files.

## Residual risks

This is not a sandbox. Test code can perform any action allowed to the server's OS account. Filesystem containment checks can race with concurrent mutation. Secret-name and log-value redaction heuristics are incomplete; callers should not treat project logs as guaranteed secret-free. The Python process inherits its environment, although audit records never serialize it. Restrict the process account, working directory, environment, and network access at the OS/host level for higher assurance.

## Reporting

Audit JSON records go to stderr and contain timestamp, tool, request id, success, latency, and error category. They intentionally omit paths, search queries, subprocess output, and credentials. MCP stdout contains protocol responses only.
