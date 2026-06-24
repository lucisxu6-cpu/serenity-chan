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
    from validate_ai_overlay import validate_overlay
except ModuleNotFoundError:  # pragma: no cover
    from scripts.build_comparison_report import build_comparison_report, to_markdown
    from scripts.validate_ai_overlay import validate_overlay


def _load_json(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def overlay_to_profile(overlay: Mapping[str, Any]) -> dict[str, Any]:
    validated = validate_overlay(overlay)["normalized_overlay"]
    profile = {
        "layer": validated["layer"],
        "bottleneck_reason": validated["bottleneck_reason"],
        "layer_score": validated["layer_score"],
        "company_fit": validated["company_fit"],
        "serenity_fit": validated["serenity_fit"],
        "revenue_transmission": validated["revenue_transmission"],
        "evidence_gap": "; ".join(validated.get("research_questions", [])) or "AI overlay supplied evidence-backed layer mapping.",
        "ai_confidence": validated["ai_confidence"],
        "key_evidence_refs": validated.get("key_evidence_refs", []),
        "contrary_evidence": validated.get("contrary_evidence", []),
        "research_questions": validated.get("research_questions", []),
    }
    for key in [
        "market_implied_growth",
        "evidence_supported_growth",
        "growth_gap",
        "h4_h5_evidence_bar_met",
        "required_next_evidence",
        "posterior_basis",
    ]:
        if key in validated:
            profile[key] = validated[key]
    return profile


def load_overlay_profiles(values: Sequence[str]) -> dict[str, Mapping[str, Any]]:
    profiles: dict[str, Mapping[str, Any]] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--overlay must use SYMBOL=path")
        symbol, path_text = value.split("=", 1)
        overlay = _load_json(Path(path_text))
        overlay_symbol = str(overlay.get("symbol") or "")
        if overlay_symbol and overlay_symbol != symbol:
            raise ValueError(f"overlay assignment {symbol} does not match overlay.symbol {overlay_symbol}")
        profiles[symbol] = overlay_to_profile(overlay)
    return profiles


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Merge AI research overlays into a Serenity + Chan comparison report")
    parser.add_argument("manifests", nargs="+", help="fetch manifest JSON paths")
    parser.add_argument("--overlay", action="append", default=[], help="SYMBOL=overlay.json")
    parser.add_argument("--format", choices=["json", "md", "both"], default="json")
    args = parser.parse_args(argv)
    try:
        report = build_comparison_report([Path(path) for path in args.manifests], load_overlay_profiles(args.overlay))
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
