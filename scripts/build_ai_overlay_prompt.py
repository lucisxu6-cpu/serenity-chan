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


def build_ai_overlay_prompt(manifest_path: Path) -> dict[str, Any]:
    review_packet: dict[str, Any] = build_ai_review_packet(manifest_path)
    committee_packet: dict[str, Any] = build_ai_committee_packet(manifest_path)
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
    parser.add_argument("--out", help="write prompt package JSON to this path")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        payload: dict[str, Any] = build_ai_overlay_prompt(Path(args.manifest))
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
