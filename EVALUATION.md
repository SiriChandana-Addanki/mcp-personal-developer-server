# Evaluation and benchmark

Run `python evaluate.py` to execute the fixed six-case suite and print JSON measurements: total, successful and failed cases, success rate, average, median/p50 and nearest-rank p95 latency, plus failures by category. Cases cover local search, Git status/diff, approved unittest invocation, a local log, and a real GitHub repository metadata request.

The script emits results from the current environment and does not commit generated measurements. GitHub availability, rate limits, and network access can affect that case. Baseline: **Not measured yet.** No baseline is inferred from this run. The benchmark contains a small fixed number of cases, so its p95 is descriptive rather than statistically stable.

The automated suite is `python -m unittest discover -s tests -v`; it covers normal operations plus path containment, sensitive names, disallowed commands, malformed input, missing logs, failed tests, timeout handling, missing executables, invalid repositories, Git failures, and mocked GitHub success/failures.
