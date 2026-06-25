#!/usr/bin/env python3
"""Build a multi-role AI research committee packet from a fetch manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from build_ai_review_packet import build_ai_review_packet
except ModuleNotFoundError:  # pragma: no cover
    from scripts.build_ai_review_packet import build_ai_review_packet


COMMITTEE_ROLES: list[dict[str, Any]] = [
    {
        "role": "Serenity Mapper",
        "mandate": "Map the company to the value-chain bottleneck, revenue-transmission path, and market misclassification.",
        "must_answer": [
            "Which layer controls the narrowest bottleneck?",
            "How does demand transmit into this issuer's revenue and margin?",
            "What disclosure would prove or disprove the bottleneck thesis?",
        ],
    },
    {
        "role": "Fundamental Auditor",
        "mandate": "Audit financial quality, cash conversion, balance-sheet risk, and industry-specific reporting fit.",
        "must_answer": [
            "Are revenue, profit, operating cash flow, assets, liabilities, and equity consistent?",
            "Are receivables, inventory, capex, or financing signs contradicting the thesis?",
            "Does this issuer need bank, insurance, securities, or other industry-specific metrics?",
        ],
    },
    {
        "role": "Valuation Skeptic",
        "mandate": "Challenge market-implied growth, PE/PS preflight conclusions, and valuation-stage boundaries.",
        "must_answer": [
            "What H-level is implied by current valuation inputs?",
            "What evidence-supported H-level is actually proven?",
            "What would move preflight valuation toward verified or deep valuation?",
        ],
    },
    {
        "role": "Timing Gatekeeper",
        "mandate": "Separate company quality from current action timing and Chan/GF-DMA buy-point discipline.",
        "must_answer": [
            "Is there a confirmed buy point or only a watch structure?",
            "Which technical data gaps block action claims?",
            "What price/structure condition would upgrade action readiness?",
        ],
    },
    {
        "role": "Bear Case Counsel",
        "mandate": "Construct the strongest opposing thesis, contrary evidence, and falsification triggers.",
        "must_answer": [
            "What would make the current thesis wrong?",
            "Which missing filings or data gaps are most dangerous?",
            "What concrete event should downgrade or eliminate the candidate?",
        ],
    },
]


OVERLAY_REQUIRED_OUTPUTS: list[str] = [
    "symbol",
    "as_of_date",
    "layer",
    "bottleneck_reason",
    "revenue_transmission",
    "serenity_fit",
    "key_evidence_refs",
    "contrary_evidence",
    "research_questions",
    "ai_confidence",
]
OVERLAY_OPTIONAL_OUTPUTS: list[str] = [
    "layer_score",
    "company_fit",
    "evidence_supported_growth",
    "h4_h5_evidence_bar_met",
    "required_next_evidence",
    "posterior_basis",
]
COMMITTEE_REVIEW_OUTPUTS: list[str] = [
    "consensus",
    "dissent",
    "upgrade_conditions",
    "downgrade_conditions",
]
COMMITTEE_TO_OVERLAY_MAPPING: dict[str, str] = {
    "consensus": "posterior_basis",
    "dissent": "contrary_evidence",
    "upgrade_conditions": "required_next_evidence or research_questions",
    "downgrade_conditions": "contrary_evidence or research_questions",
}


def build_ai_committee_packet(manifest_path: Path) -> dict[str, Any]:
    base_packet: Any = build_ai_review_packet(manifest_path)
    if not isinstance(base_packet, Mapping):
        raise ValueError("AI review packet must be a JSON object")
    overlay_allowed_outputs: list[str] = OVERLAY_REQUIRED_OUTPUTS + OVERLAY_OPTIONAL_OUTPUTS
    return {
        "packet_type": "serenity_ai_research_committee",
        "manifest_path": str(manifest_path),
        "evidence_constraints": [
            "Do not override deterministic market_implied_growth, PE/PS, h4_h5_evidence_bar_met, or data-quality gates.",
            "Every upgrade claim must cite L0/L1 evidence or remain a research question.",
            "L3/L4 evidence can generate leads but cannot support H4/H5 or S/A conclusions by itself.",
            "Final overlays must include at least one contrary_evidence item and at least two concrete research_questions.",
            "Open research_debt remains blocking until cleared, scoped out, or explicitly tied to a lower rating/action state.",
        ],
        "base_ai_review_packet": dict(base_packet),
        "committee_roles": COMMITTEE_ROLES,
        "committee_review_outputs": COMMITTEE_REVIEW_OUTPUTS,
        "required_overlay_outputs": OVERLAY_REQUIRED_OUTPUTS,
        "optional_overlay_outputs": OVERLAY_OPTIONAL_OUTPUTS,
        "overlay_output_contract": {
            "schema_path": "assets/ai_research_overlay.schema.json",
            "allowed_fields": overlay_allowed_outputs,
            "committee_fields_are_not_overlay_fields": True,
        },
        "committee_to_overlay_mapping": COMMITTEE_TO_OVERLAY_MAPPING,
        "overlay_instructions": [
            "Write committee review notes separately from the final overlay JSON.",
            "The final overlay JSON may contain only required_overlay_outputs and optional_overlay_outputs.",
            "If evidence_supported_growth is H4 or H5, set h4_h5_evidence_bar_met=true and cite L0/L1 evidence; otherwise keep the claim below H4/H5.",
            "Compress consensus, dissent, upgrade conditions, and downgrade conditions into posterior_basis, contrary_evidence, required_next_evidence, and research_questions before validation.",
        ],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Build a Serenity AI research committee packet")
    parser.add_argument("manifest", help="fetch manifest JSON")
    parser.add_argument("--out", help="write packet JSON to this path")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        packet: dict[str, Any] = build_ai_committee_packet(Path(args.manifest))
        text: str = json.dumps(packet, ensure_ascii=False, indent=2)
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
