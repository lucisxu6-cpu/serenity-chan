#!/usr/bin/env python3
"""Convert AI committee research outputs into a validated overlay."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from validate_ai_overlay import validate_overlay
except ModuleNotFoundError:  # pragma: no cover
    from scripts.validate_ai_overlay import validate_overlay


OVERLAY_FIELDS: set[str] = {
    "symbol",
    "as_of_date",
    "layer",
    "bottleneck_reason",
    "revenue_transmission",
    "serenity_fit",
    "layer_score",
    "company_fit",
    "evidence_supported_growth",
    "h4_h5_evidence_bar_met",
    "required_next_evidence",
    "posterior_basis",
    "key_evidence_refs",
    "contrary_evidence",
    "research_questions",
    "ai_confidence",
}


def _load_json(path: Path) -> Mapping[str, Any]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _committee_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(payload.get("overlay"), Mapping):
        return payload["overlay"]  # type: ignore[return-value]
    outputs: Any = payload.get("committee_review_outputs")
    if isinstance(outputs, Mapping):
        if isinstance(outputs.get("overlay"), Mapping):
            return outputs["overlay"]  # type: ignore[return-value]
        return outputs
    return payload


def committee_to_overlay(payload: Mapping[str, Any]) -> dict[str, Any]:
    candidate: Mapping[str, Any] = _committee_payload(payload)
    overlay: dict[str, Any] = {
        key: candidate[key]
        for key in OVERLAY_FIELDS
        if key in candidate
    }

    if "posterior_basis" not in overlay and candidate.get("consensus"):
        overlay["posterior_basis"] = str(candidate.get("consensus"))
    if "contrary_evidence" not in overlay and candidate.get("dissent"):
        dissent: Any = candidate.get("dissent")
        overlay["contrary_evidence"] = dissent if isinstance(dissent, list) else [str(dissent)]
    if "required_next_evidence" not in overlay and candidate.get("upgrade_conditions"):
        conditions: Any = candidate.get("upgrade_conditions")
        if isinstance(conditions, list):
            overlay["required_next_evidence"] = "; ".join(str(item) for item in conditions if item)
        else:
            overlay["required_next_evidence"] = str(conditions)
    if "research_questions" not in overlay and candidate.get("open_questions"):
        questions: Any = candidate.get("open_questions")
        overlay["research_questions"] = questions if isinstance(questions, list) else [str(questions)]

    validated: dict[str, Any] = validate_overlay(overlay)
    return dict(validated["normalized_overlay"])


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Convert AI committee output to ai_research_overlay JSON")
    parser.add_argument("committee_output")
    parser.add_argument("--out")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        overlay: dict[str, Any] = committee_to_overlay(_load_json(Path(args.committee_output)))
        text: str = json.dumps(overlay, ensure_ascii=False, indent=2)
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
