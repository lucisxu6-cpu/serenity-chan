#!/usr/bin/env python3
"""Validate a Serenity + Chan candidate-comparison report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from build_comparison_report import validate_comparison_report
except ModuleNotFoundError:  # pragma: no cover
    from scripts.build_comparison_report import validate_comparison_report


def validate_file(path: Path) -> list[str]:
    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, Mapping):
        return ["comparison report JSON must be an object"]
    return validate_comparison_report(loaded)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Validate a Serenity candidate-comparison JSON report")
    parser.add_argument("comparison_report", help="comparison_output_contract JSON report")
    args: argparse.Namespace = parser.parse_args(argv)

    try:
        errors: list[str] = validate_file(Path(args.comparison_report))
    except Exception as exc:
        print(f"FAILED: {args.comparison_report}", file=sys.stderr)
        print(f"- ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if errors:
        print(f"FAILED: {args.comparison_report}", file=sys.stderr)
        for error in errors:
            print(f"- ERROR {error}", file=sys.stderr)
        return 1
    print(f"OK: {args.comparison_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
