#!/usr/bin/env python3
"""Validate AI overlays, merge them into a comparison report, and optionally render Markdown."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from build_comparison_report import build_comparison_report, to_markdown
    from validate_ai_overlay import validate_overlay
    from validate_ai_review_outcome import validate_review_outcome
    from validate_comparison_report import validate_file
except ModuleNotFoundError:  # pragma: no cover
    from scripts.build_comparison_report import build_comparison_report, to_markdown
    from scripts.validate_ai_overlay import validate_overlay
    from scripts.validate_ai_review_outcome import validate_review_outcome
    from scripts.validate_comparison_report import validate_file


def _load_json(path: Path) -> Mapping[str, Any]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _manifest_symbol(path: Path) -> str:
    payload: Mapping[str, Any] = _load_json(path)
    symbol: Any = payload.get("symbol")
    if isinstance(symbol, Mapping):
        resolved: str = str(symbol.get("symbol") or "").strip()
    else:
        resolved = str(symbol or "").strip()
    if not resolved:
        raise ValueError(f"{path} missing manifest symbol")
    return resolved


def load_validated_overlays(values: Sequence[str]) -> dict[str, Mapping[str, Any]]:
    overlays: dict[str, Mapping[str, Any]] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--overlay must use SYMBOL=path")
        symbol: str
        path_text: str
        symbol, path_text = value.split("=", 1)
        if symbol in overlays:
            raise ValueError(f"duplicate overlay assignment for {symbol}")
        validated: dict[str, Any] = validate_overlay(_load_json(Path(path_text)))
        overlay: Mapping[str, Any] = validated["normalized_overlay"]
        overlay_symbol: str = str(overlay.get("symbol") or "")
        if overlay_symbol != symbol:
            raise ValueError(f"overlay assignment {symbol} does not match overlay.symbol {overlay_symbol}")
        overlays[symbol] = overlay
    return overlays


def load_validated_outcomes(values: Sequence[str]) -> dict[str, Mapping[str, Any]]:
    outcomes: dict[str, Mapping[str, Any]] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--ai-outcome must use SYMBOL=path")
        symbol: str
        path_text: str
        symbol, path_text = value.split("=", 1)
        if symbol in outcomes:
            raise ValueError(f"duplicate AI review outcome assignment for {symbol}")
        validated: dict[str, Any] = validate_review_outcome(_load_json(Path(path_text)))
        outcome: Mapping[str, Any] = validated["normalized_outcome"]
        outcome_symbol: str = str(outcome.get("symbol") or "")
        if outcome_symbol != symbol:
            raise ValueError(f"AI review outcome assignment {symbol} does not match outcome.symbol {outcome_symbol}")
        outcomes[symbol] = outcome
    return outcomes


def build_validated_merged_report(
    manifest_paths: Sequence[Path],
    overlay_values: Sequence[str],
    outcome_values: Sequence[str],
) -> dict[str, Any]:
    overlays: dict[str, Mapping[str, Any]] = load_validated_overlays(overlay_values)
    outcomes: dict[str, Mapping[str, Any]] = load_validated_outcomes(outcome_values)
    overlap: set[str] = set(overlays) & set(outcomes)
    if overlap:
        raise ValueError(f"candidate(s) cannot have both --overlay and --ai-outcome: {', '.join(sorted(overlap))}")
    candidate_symbols: set[str] = {_manifest_symbol(path) for path in manifest_paths}
    missing: set[str] = candidate_symbols - set(overlays) - set(outcomes)
    if missing:
        raise ValueError(f"missing AI result for candidate(s): {', '.join(sorted(missing))}")
    report: dict[str, Any] = build_comparison_report(manifest_paths, overlays, outcomes)
    with tempfile.TemporaryDirectory(prefix="serenity-merged-") as temp_dir:
        report_path: Path = Path(temp_dir) / "comparison_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        errors: list[str] = validate_file(report_path)
    if errors:
        raise ValueError("; ".join(errors))
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Validate and merge AI overlays")
    parser.add_argument("manifests", nargs="+", help="fetch manifest JSON paths")
    parser.add_argument("--overlay", action="append", default=[], help="SYMBOL=overlay.json")
    parser.add_argument("--ai-outcome", action="append", default=[], help="SYMBOL=ai_review_outcome.json")
    parser.add_argument("--report-out", help="write merged comparison JSON")
    parser.add_argument("--markdown-out", help="write merged comparison Markdown")
    parser.add_argument("--format", choices=["json", "md", "both"], default="json")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        report: dict[str, Any] = build_validated_merged_report(
            [Path(path) for path in args.manifests],
            args.overlay,
            args.ai_outcome,
        )
        if args.report_out:
            Path(args.report_out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        markdown: str = to_markdown(report)
        if args.markdown_out:
            Path(args.markdown_out).write_text(markdown + "\n", encoding="utf-8")
        if args.format == "json":
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif args.format == "md":
            print(markdown)
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            print("\n---\n")
            print(markdown)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
