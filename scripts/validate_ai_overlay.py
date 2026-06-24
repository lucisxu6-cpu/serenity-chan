#!/usr/bin/env python3
"""Validate an AI research overlay before it can affect ranking or action gates."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


REQUIRED_FIELDS = {
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
}
SOURCE_LEVELS = {"L0", "L1", "L2", "L3", "L4"}
AI_CONFIDENCE = {"LOW", "MEDIUM", "HIGH"}
GROWTH = {"H0", "H1", "H2", "H3", "H4", "H5", "UNKNOWN"}
GROWTH_ORDER = {"H0": 0, "H1": 1, "H2": 2, "H3": 3, "H4": 4, "H5": 5, "UNKNOWN": -1}


def _as_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except Exception:
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any, label: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{label} must be an array")
        return []
    result = [str(item).strip() for item in value if str(item).strip()]
    if len(result) != len(value):
        errors.append(f"{label} must contain only non-empty strings")
    return result


def validate_overlay(payload: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    missing = sorted(REQUIRED_FIELDS - set(payload))
    if missing:
        errors.append(f"overlay missing required keys: {', '.join(missing)}")

    for key in ["symbol", "as_of_date", "layer", "bottleneck_reason", "revenue_transmission"]:
        if key in payload and not _is_non_empty_string(payload.get(key)):
            errors.append(f"{key} must be a non-empty string")

    serenity_fit = _as_float(payload.get("serenity_fit"))
    if serenity_fit is None or serenity_fit < 0 or serenity_fit > 1:
        errors.append("serenity_fit must be a number between 0 and 1")
        serenity_fit = 0.0

    ai_confidence = str(payload.get("ai_confidence") or "")
    if ai_confidence not in AI_CONFIDENCE:
        errors.append(f"ai_confidence must be one of {sorted(AI_CONFIDENCE)}")

    evidence_refs = payload.get("key_evidence_refs", [])
    if not isinstance(evidence_refs, list):
        errors.append("key_evidence_refs must be an array")
        evidence_refs = []
    if not evidence_refs:
        errors.append("key_evidence_refs must include at least one evidence reference")

    strong_primary_refs = 0
    for idx, item in enumerate(evidence_refs):
        label = f"key_evidence_refs[{idx}]"
        if not isinstance(item, Mapping):
            errors.append(f"{label} must be an object")
            continue
        for key in ["claim", "source_ref"]:
            if not _is_non_empty_string(item.get(key)):
                errors.append(f"{label}.{key} must be a non-empty string")
        level = str(item.get("source_level") or "")
        if level not in SOURCE_LEVELS:
            errors.append(f"{label}.source_level must be one of {sorted(SOURCE_LEVELS)}")
        confidence = _as_float(item.get("confidence"))
        if confidence is None or confidence < 0 or confidence > 1:
            errors.append(f"{label}.confidence must be a number between 0 and 1")
            confidence = 0.0
        if level in {"L0", "L1"} and confidence >= 0.65:
            strong_primary_refs += 1

    _string_list(payload.get("contrary_evidence", []), "contrary_evidence", errors)
    research_questions = _string_list(payload.get("research_questions", []), "research_questions", errors)
    if ai_confidence in {"MEDIUM", "HIGH"} and not research_questions:
        warnings.append("Medium/high confidence overlay should still name the next research questions.")

    if (serenity_fit >= 0.72 or ai_confidence == "HIGH") and strong_primary_refs == 0:
        errors.append("high-fit or high-confidence overlay requires at least one L0/L1 evidence reference with confidence >= 0.65")

    implied = payload.get("market_implied_growth")
    supported = payload.get("evidence_supported_growth")
    if implied is not None and str(implied) not in GROWTH:
        errors.append(f"market_implied_growth must be one of {sorted(GROWTH)}")
    if supported is not None and str(supported) not in GROWTH:
        errors.append(f"evidence_supported_growth must be one of {sorted(GROWTH)}")
    if implied is not None and supported is not None:
        implied_order = GROWTH_ORDER.get(str(implied), -1)
        supported_order = GROWTH_ORDER.get(str(supported), -1)
        if implied_order >= 4 and supported_order < implied_order and payload.get("h4_h5_evidence_bar_met") is not False:
            errors.append("H4/H5 market-implied growth above evidence-supported growth requires h4_h5_evidence_bar_met=false")

    normalized = dict(payload)
    normalized["serenity_fit"] = round(float(serenity_fit or 0.0), 4)
    if normalized.get("layer_score") is None:
        normalized["layer_score"] = round(normalized["serenity_fit"] * 100.0, 2)
    if normalized.get("company_fit") is None:
        normalized["company_fit"] = round(normalized["serenity_fit"] * 100.0, 2)

    if errors:
        raise ValueError("; ".join(errors))
    return {"ok": True, "warnings": warnings, "normalized_overlay": normalized}


def _load_json(path: str) -> Mapping[str, Any]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, Mapping):
        raise ValueError("overlay JSON must be an object")
    return payload


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a Serenity + Chan AI research overlay")
    parser.add_argument("overlay", help="Overlay JSON path or '-' for stdin")
    parser.add_argument("--json", action="store_true", help="emit machine-readable validation result")
    args = parser.parse_args(argv)
    try:
        result = validate_overlay(_load_json(args.overlay))
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"FAILED: {args.overlay}")
            print(f"- ERROR: {exc}")
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"OK: {args.overlay}")
        for warning in result.get("warnings", []):
            print(f"- WARNING: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
