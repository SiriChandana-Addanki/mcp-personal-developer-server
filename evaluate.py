"""Repeatable, local tool benchmark. Results are measured each time it runs."""
import json
import statistics
import tempfile
import time
from pathlib import Path

from devserver.core import DeveloperTools


def main():
    with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
        root = Path(folder)
        (root / "src").mkdir()
        (root / "src" / "app.py").write_text("benchmark marker\n", encoding="utf-8")
        (root / "logs").mkdir()
        (root / "logs" / "app.log").write_text("service started\n", encoding="utf-8")
        (root / "tests").mkdir()
        (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (root / "tests" / "test_benchmark.py").write_text(
            "import unittest\nclass BenchmarkTest(unittest.TestCase):\n def test_smoke(self): self.assertTrue(True)\n",
            encoding="utf-8")
        import subprocess
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        tools = DeveloperTools(root, audit=lambda _record: None)
        cases = [
            ("search_project", {"query": "benchmark marker"}, lambda r: bool(r["matches"])),
            ("git_status", {}, lambda r: "branch" in r),
            ("git_diff", {}, lambda r: "diff" in r),
            ("run_tests", {"suite": "unittest"}, lambda r: r["passed"]),
            ("read_logs", {"path": "logs/app.log"}, lambda r: "service started" in r["content"]),
            ("github_repo_info", {"owner": "octocat", "repo": "Hello-World"}, lambda r: bool(r.get("full_name"))),
        ]
        measurements, failures = [], {}
        for name, args, check in cases:
            started = time.perf_counter()
            outcome = tools.call(name, args)
            latency = (time.perf_counter() - started) * 1000
            success = outcome["ok"] and check(outcome["result"])
            measurements.append({"tool": name, "success": success, "latency_ms": round(latency, 3),
                                 "category": "success" if success else outcome["category"]})
            if not success:
                category = outcome["category"] if not outcome["ok"] else "assertion_failure"
                failures[category] = failures.get(category, 0) + 1
        latencies = [item["latency_ms"] for item in measurements]
        ordered = sorted(latencies)
        p95 = ordered[max(0, __import__("math").ceil(0.95 * len(ordered)) - 1)] if ordered else None
        success_count = sum(item["success"] for item in measurements)
        report = {"total_cases": len(measurements), "successful_cases": success_count,
                  "failed_cases": len(measurements) - success_count,
                  "success_rate": success_count / len(measurements) if measurements else 0,
                  "latency_ms": {"average": statistics.mean(latencies) if latencies else None,
                                 "median_p50": statistics.median(latencies) if latencies else None, "p95": p95},
                  "failures_by_category": failures, "cases": measurements,
                  "baseline": "Not measured yet."}
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
