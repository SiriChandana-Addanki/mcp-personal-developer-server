# Evaluation and benchmark

Run `python evaluate.py` to execute the fixed six-case suite and print JSON measurements: total, successful and failed cases, success rate, average, median/p50 and nearest-rank p95 latency, plus failures by category. Cases cover local search, Git status/diff, approved unittest invocation, a local log, and a real GitHub repository metadata request.

The script emits results from the current environment and does not commit generated measurements. GitHub availability, rate limits, and network access can affect that case. Baseline: **Not measured yet.** No baseline is inferred from this run. The benchmark contains a small fixed number of cases, so its p95 is descriptive rather than statistically stable.

The automated suite is `python -m unittest discover -s tests -v`; it covers normal tool operations plus path containment, sensitive names, disallowed commands, malformed input, missing logs, failed tests, timeout handling, missing executables, invalid repositories, Git failures, and mocked GitHub success/failures. `tests/test_resources.py` covers all four documentation resource reads, log-resource allowlisting/redaction, URI validation, traversal and absolute-path variants, symlink escape, sensitive names, size enforcement, missing/unexpected read failures, and audit leakage checks.

The resource adversarial checks execute each attack input independently and inspect both the outcome category and serialized result for sentinel content. They include parent and nested traversal, Windows and POSIX absolute paths, backslashes, encoded traversal, symlink escape, `.env`, credential/key files, unsupported scheme, unknown resource, oversized content, malformed URI, and audit-log leakage.

## Observed run

On 2026-10-06, the final standalone six-case benchmark run had 6 total cases: 5 successful, 1 failed, and an 83.33% success rate. Measured latency was 73.9522 ms average, 79.6735 ms median/p50, and 169.05 ms nearest-rank p95. The only failure category was `network_failure` for the live GitHub case because outbound network access was unavailable. These are measurements of this run, not a historical baseline; the baseline remains **Not measured yet.**

The regression run completed 23 tests successfully, including 10 resource tests. A separate run executed 14 individual adversarial resource inputs; all 14 were rejected as expected, and none exposed sentinel content. The full cases and outcomes are reported in the implementation task's final report.

## Owner-side container run

The repository owner completed validation in the tested Windows Docker Desktop environment. The container test command `docker run --rm --entrypoint python mcp-personal-developer-server -m unittest discover -s tests -v` passed **23/23 tests** in **0.613 seconds** (`OK`).

The container benchmark completed **6/6 cases successfully (100%)**. Measured average latency was **986.5515 ms**, median/p50 was **5.6525 ms**, and nearest-rank p95 was **5590.466 ms**. Per-case measurements were: `github_repo_info` **5590.466 ms** (successful), `search_project` **1.239 ms**, `git_status` **8.665 ms**, `git_diff` **2.640 ms**, `run_tests` **315.218 ms**, and `read_logs` **1.081 ms**. The average and p95 are dominated by the external GitHub HTTP call in this run; the average is not representative of the local MCP operations. These measurements describe this run only. Baseline: **Not measured yet.**

The owner also reported that MCP stdio smoke validation and `resources/list` succeeded in the container. This observed run validates the tested Windows Docker Desktop environment only; it does not establish production reliability or OS-level sandboxing.
