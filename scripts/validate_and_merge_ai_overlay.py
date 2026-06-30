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
    from validate_ai_research_dossier import validate_dossier
    from validate_ai_overlay import evidence_context_from_manifest, validate_overlay
    from validate_ai_review_outcome import validate_review_outcome
    from validate_comparison_report import validate_file
except ModuleNotFoundError:  # pragma: no cover
    from scripts.build_comparison_report import build_comparison_report, to_markdown
    from scripts.validate_ai_research_dossier import validate_dossier
    from scripts.validate_ai_overlay import evidence_context_from_manifest, validate_overlay
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


def load_validated_overlays(values: Sequence[str], evidence_contexts: Optional[Mapping[str, Mapping[str, str]]] = None) -> dict[str, Mapping[str, Any]]:
    overlays: dict[str, Mapping[str, Any]] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--overlay must use SYMBOL=path")
        symbol: str
        path_text: str
        symbol, path_text = value.split("=", 1)
        if symbol in overlays:
            raise ValueError(f"duplicate overlay assignment for {symbol}")
        context: Optional[Mapping[str, str]] = evidence_contexts.get(symbol) if evidence_contexts is not None else None
        validated: dict[str, Any] = validate_overlay(_load_json(Path(path_text)), evidence_context=context)
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


def load_validated_dossiers(
    values: Sequence[str],
    evidence_contexts: Mapping[str, Mapping[str, str]],
) -> dict[str, Mapping[str, Any]]:
    dossiers: dict[str, Mapping[str, Any]] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--dossier must use SYMBOL=path")
        symbol: str
        path_text: str
        symbol, path_text = value.split("=", 1)
        if symbol in dossiers:
            raise ValueError(f"duplicate AI research dossier assignment for {symbol}")
        context: Optional[Mapping[str, str]] = evidence_contexts.get(symbol)
        if context is None:
            raise ValueError(f"AI research dossier supplied for non-candidate symbol: {symbol}")
        validated: dict[str, Any] = validate_dossier(_load_json(Path(path_text)), evidence_context=context)
        dossier: Mapping[str, Any] = validated["normalized_dossier"]
        dossier_symbol: str = str(dossier.get("symbol") or "")
        if dossier_symbol != symbol:
            raise ValueError(f"AI research dossier assignment {symbol} does not match dossier.symbol {dossier_symbol}")
        dossiers[symbol] = dossier
    return dossiers


def build_validated_merged_report(
    manifest_paths: Sequence[Path],
    overlay_values: Sequence[str],
    outcome_values: Sequence[str],
    dossier_values: Sequence[str] = (),
) -> dict[str, Any]:
    manifest_by_symbol: dict[str, Path] = {_manifest_symbol(path): path for path in manifest_paths}
    evidence_contexts: dict[str, Mapping[str, str]] = {
        symbol: evidence_context_from_manifest(path)
        for symbol, path in manifest_by_symbol.items()
    }
    overlays: dict[str, Mapping[str, Any]] = load_validated_overlays(overlay_values, evidence_contexts)
    outcomes: dict[str, Mapping[str, Any]] = load_validated_outcomes(outcome_values)
    dossiers: dict[str, Mapping[str, Any]] = load_validated_dossiers(dossier_values, evidence_contexts)
    overlap: set[str] = set(overlays) & set(outcomes)
    if overlap:
        raise ValueError(f"candidate(s) cannot have both --overlay and --ai-outcome: {', '.join(sorted(overlap))}")
    candidate_symbols: set[str] = set(manifest_by_symbol)
    missing: set[str] = candidate_symbols - set(overlays) - set(outcomes)
    if missing:
        raise ValueError(f"missing AI result for candidate(s): {', '.join(sorted(missing))}")
    missing_dossiers: set[str] = candidate_symbols - set(dossiers)
    if missing_dossiers:
        raise ValueError(f"missing AI research dossier for candidate(s): {', '.join(sorted(missing_dossiers))}")
    for symbol, dossier in dossiers.items():
        dossier_status: str = str(dossier.get("research_status") or "")
        if symbol in overlays and dossier_status != "COMPLETED":
            raise ValueError(f"AI research dossier for {symbol} must be COMPLETED when an overlay is supplied")
        if symbol in outcomes:
            outcome_status: str = str(outcomes[symbol].get("ai_review_status") or "")
            if dossier_status != outcome_status:
                raise ValueError(f"AI research dossier for {symbol} must match outcome status {outcome_status}")
    report: dict[str, Any] = build_comparison_report(manifest_paths, overlays, outcomes, ai_research_dossiers=dossiers)
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
    parser.add_argument("--dossier", action="append", default=[], help="SYMBOL=ai_research_dossier.json")
    parser.add_argument("--report-out", help="write merged comparison JSON")
    parser.add_argument("--markdown-out", help="write merged comparison Markdown")
    parser.add_argument("--format", choices=["json", "md", "both"], default="json")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        report: dict[str, Any] = build_validated_merged_report(
            [Path(path) for path in args.manifests],
            args.overlay,
            args.ai_outcome,
            args.dossier,
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
