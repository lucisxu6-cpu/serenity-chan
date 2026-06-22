#!/usr/bin/env python3
"""Run static contract evals for serenity-chan-stock-skill."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

from validate_output_contract import validate_text


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
        report_path = root / case["report"]
        expect_pass = bool(case["expect_pass"])
        result = validate_text(report_path.read_text(encoding="utf-8"))
        passed = result.ok == expect_pass
        marker = "PASS" if passed else "FAIL"
        print(f"[{marker}] {name}: expected {'pass' if expect_pass else 'fail'}, got {'pass' if result.ok else 'fail'}")
        if not passed:
            failures += 1
            for finding in result.findings:
                print(f"  - {finding.severity.upper()} {finding.code}: {finding.message}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
