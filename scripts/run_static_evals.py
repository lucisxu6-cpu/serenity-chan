#!/usr/bin/env python3
"""Run static contract evals for serenity-chan-stock-skill."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

try:
    from validate_output_contract import validate_text
    from validate_output_contract_json import validate_contract
    from build_falsification_dashboard import build_from_output_contract
    from serenity_chan_scorecard import score
except ModuleNotFoundError:  # pragma: no cover - supports python -m scripts.run_static_evals
    from scripts.validate_output_contract import validate_text
    from scripts.validate_output_contract_json import validate_contract
    from scripts.build_falsification_dashboard import build_from_output_contract
    from scripts.serenity_chan_scorecard import score


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run static Serenity + Chan eval cases")
    parser.add_argument("--cases", default="evals/static_cases.json", help="JSON case file")
    args = parser.parse_args(argv)

    root = Path.cwd()
    cases_path = root / args.cases
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    failures = 0

    for case in cases:
        name = case["name"]
        expect_pass = bool(case["expect_pass"])
        kind = case.get("kind", "report")
        findings: list[str] = []
        result_payload: dict[str, Any] = {}

        if kind == "report":
            report_path = root / case["report"]
            result = validate_text(report_path.read_text(encoding="utf-8"))
            actual_pass = result.ok
            findings = [f"{f.severity.upper()} {f.code}: {f.message}" for f in result.findings]
            result_payload = result.extracted
        elif kind == "scorecard":
            scorecard_path = root / case["scorecard"]
            try:
                result_payload = score(json.loads(scorecard_path.read_text(encoding="utf-8")))
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "output_json":
            contract_path = root / case["contract"]
            try:
                result_payload = validate_contract(json.loads(contract_path.read_text(encoding="utf-8")))
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "dashboard_from_output_json":
            contract_path = root / case["contract"]
            try:
                dashboard = build_from_output_contract(json.loads(contract_path.read_text(encoding="utf-8")))
                monitors = dashboard.get("monitors", [])
                result_payload = {
                    "ok": True,
                    "has_valuation_monitor": any(
                        isinstance(monitor, dict) and monitor.get("category") == "valuation"
                        for monitor in monitors
                    ),
                }
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        else:
            raise ValueError(f"unknown static eval kind: {kind}")

        expected_result = case.get("expected_result", {})
        result_matches = all(result_payload.get(k) == v for k, v in expected_result.items())
        passed = actual_pass == expect_pass and (not actual_pass or result_matches)
        marker = "PASS" if passed else "FAIL"
        print(f"[{marker}] {name}: expected {'pass' if expect_pass else 'fail'}, got {'pass' if actual_pass else 'fail'}")
        if not passed:
            failures += 1
            if expected_result and actual_pass and not result_matches:
                print(f"  - expected result fields: {expected_result}")
                print(f"  - actual result fields: {result_payload}")
            for finding in findings:
                print(f"  - {finding}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
