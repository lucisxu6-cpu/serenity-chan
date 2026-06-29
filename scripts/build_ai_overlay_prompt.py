#!/usr/bin/env python3
"""Build the prompt package an AI agent uses to produce a validated overlay."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from build_ai_committee_packet import build_ai_committee_packet
    from build_ai_review_packet import build_ai_review_packet
except ModuleNotFoundError:  # pragma: no cover
    from scripts.build_ai_committee_packet import build_ai_committee_packet
    from scripts.build_ai_review_packet import build_ai_review_packet


def _load_optional_json(path: Optional[Path]) -> Mapping[str, Any]:
    if path is None:
        return {}
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def build_ai_overlay_prompt(
    manifest_path: Path,
    *,
    theme_universe_path: Optional[Path] = None,
    theme_research_packet_path: Optional[Path] = None,
) -> dict[str, Any]:
    review_packet: dict[str, Any] = build_ai_review_packet(manifest_path)
    committee_packet: dict[str, Any] = build_ai_committee_packet(manifest_path)
    theme_universe: Mapping[str, Any] = _load_optional_json(theme_universe_path)
    theme_research_packet: Mapping[str, Any] = _load_optional_json(theme_research_packet_path)
    symbol: str = str(review_packet.get("symbol") or "")
    return {
        "packet_type": "serenity_ai_overlay_prompt",
        "symbol": symbol,
        "manifest_path": str(manifest_path.resolve()),
        "instruction": (
            "You are the AI research reviewer. Use the review and committee packets to write "
            "one JSON object matching assets/ai_research_overlay.schema.json when evidence is sufficient, "
            "or one JSON object matching assets/ai_review_outcome.schema.json when evidence is insufficient, "
            "conflicts with deterministic data, or the task is a quick audit. Do not invent facts. "
            "Cite source artifacts and keep unsupported claims as research questions. Write all user-facing "
            "research text in Chinese while preserving machine enum fields in English."
        ),
        "hard_constraints": [
            "Do not override deterministic market_implied_growth, PE/PS, data-quality, FX, or valuation-stage fields.",
            "Use L0/L1 evidence for high-confidence or H4/H5 evidence-supported growth.",
            "Write bottleneck_reason, revenue_transmission, contrary_evidence, research_questions, reason, and required_evidence in Chinese.",
            "Include at least one contrary_evidence item and at least two concrete research_questions.",
            "If evidence is insufficient, output a validated ai_review_outcome instead of a forged overlay.",
        ],
        "theme_context": {
            "theme_universe_path": str(theme_universe_path.resolve()) if theme_universe_path else "",
            "theme_research_packet_path": str(theme_research_packet_path.resolve()) if theme_research_packet_path else "",
            "theme_universe": dict(theme_universe),
            "theme_research_packet": dict(theme_research_packet),
            "usage_rule": "When present, use theme context to map the candidate into the value chain, compare it with same-layer alternatives, and keep macro/theme claims separate from company-specific evidence.",
        },
        "expected_output": {
            "json_only": True,
            "success_schema_path": "assets/ai_research_overlay.schema.json",
            "outcome_schema_path": "assets/ai_review_outcome.schema.json",
            "validate_overlay_with": "python scripts/validate_ai_overlay.py <overlay.json>",
            "validate_outcome_with": "python scripts/validate_ai_review_outcome.py <ai_review_outcome.json>",
        },
        "review_packet": review_packet,
        "committee_packet": committee_packet,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Build an AI overlay generation prompt package")
    parser.add_argument("manifest", help="fetch manifest JSON path")
    parser.add_argument("--theme-universe", help="optional theme_candidate_universe.json for direction context")
    parser.add_argument("--theme-research-packet", help="optional theme_research_packet.json for direction-level AI questions")
    parser.add_argument("--out", help="write prompt package JSON to this path")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        payload: dict[str, Any] = build_ai_overlay_prompt(
            Path(args.manifest),
            theme_universe_path=Path(args.theme_universe) if args.theme_universe else None,
            theme_research_packet_path=Path(args.theme_research_packet) if args.theme_research_packet else None,
        )
        text: str = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
