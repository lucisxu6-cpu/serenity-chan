#!/usr/bin/env python3
"""Merge validated AI research overlays into the candidate comparison report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from build_comparison_report import build_comparison_report, to_markdown
except ModuleNotFoundError:  # pragma: no cover
    from scripts.build_comparison_report import build_comparison_report, to_markdown


def _load_json(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def load_overlays(values: Sequence[str]) -> dict[str, Mapping[str, Any]]:
    overlays: dict[str, Mapping[str, Any]] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--overlay must use SYMBOL=path")
        symbol, path_text = value.split("=", 1)
        if symbol in overlays:
            raise ValueError(f"duplicate overlay assignment for {symbol}")
        overlay = _load_json(Path(path_text))
        overlays[symbol] = overlay
    return overlays


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Merge AI research overlays into a Serenity + Chan comparison report")
    parser.add_argument("manifests", nargs="+", help="fetch manifest JSON paths")
    parser.add_argument("--overlay", action="append", default=[], help="SYMBOL=overlay.json")
    parser.add_argument("--format", choices=["json", "md", "both"], default="json")
    args = parser.parse_args(argv)
    try:
        report = build_comparison_report([Path(path) for path in args.manifests], load_overlays(args.overlay))
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
