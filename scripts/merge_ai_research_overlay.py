#!/usr/bin/env python3
"""Merge validated AI research overlays into the candidate comparison report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

try:
    from build_comparison_report import to_markdown
    from validate_and_merge_ai_overlay import build_validated_merged_report
except ModuleNotFoundError:  # pragma: no cover
    from scripts.build_comparison_report import to_markdown
    from scripts.validate_and_merge_ai_overlay import build_validated_merged_report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Merge AI research overlays into a Serenity + Chan comparison report")
    parser.add_argument("manifests", nargs="+", help="fetch manifest JSON paths")
    parser.add_argument("--overlay", action="append", default=[], help="SYMBOL=overlay.json")
    parser.add_argument("--ai-outcome", action="append", default=[], help="SYMBOL=ai_review_outcome.json")
    parser.add_argument("--format", choices=["json", "md", "both"], default="json")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        report: dict[str, Any] = build_validated_merged_report(
            [Path(path) for path in args.manifests],
            args.overlay,
            args.ai_outcome,
        )
        if args.format == "json":
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif args.format == "md":
            print(to_markdown(report))
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            print("\n---\n")
            print(to_markdown(report))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
