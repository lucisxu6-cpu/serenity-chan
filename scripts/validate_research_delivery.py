#!/usr/bin/env python3
"""Validate that a Serenity artifact is eligible for formal user delivery."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from build_comparison_report import validate_comparison_report
except ModuleNotFoundError:  # pragma: no cover
    from scripts.build_comparison_report import validate_comparison_report


FORMAL_AI_STATUSES: set[str] = {"COMPLETED", "FAILED_INSUFFICIENT_EVIDENCE", "CONFLICT_WITH_DATA"}
BLOCKED_CONTRACTS: set[str] = {
    "serenity_agent_research_queue",
    "serenity_diagnostic_baseline",
}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _ai_statuses(payload: Mapping[str, Any]) -> list[str]:
    statuses: list[str] = []
    for row in _as_list(payload.get("ai_review_status_matrix")):
        if isinstance(row, Mapping):
            status: str = str(row.get("ai_review_status") or "")
            if status:
                statuses.append(status)
    return statuses


def validate_delivery_payload(payload: Mapping[str, Any], *, source_path: str = "") -> list[str]:
    errors: list[str] = []
    contract_type: str = str(payload.get("contract_type") or "")
    if contract_type in BLOCKED_CONTRACTS:
        errors.append(f"{contract_type} is not a formal delivery artifact")
        return errors
    if contract_type and contract_type != "serenity_candidate_comparison_report":
        errors.append(f"{contract_type} is not a formal comparison-report delivery artifact")
        return errors
    if source_path:
        source_name: str = Path(source_path).name
        if source_name in {"comparison_internal_baseline.json", "comparison_diagnostic_baseline.json", "agent_research_queue.json"}:
            errors.append(f"{source_name} is not a formal delivery artifact")
    if "candidate_priority_ranking" in payload and "ai_review_status_matrix" in payload:
        comparison_errors: list[str] = validate_comparison_report(payload)
        if comparison_errors:
            errors.extend(comparison_errors)
        readiness: Mapping[str, Any] = _as_mapping(payload.get("report_readiness"))
        if readiness.get("stage") != "FINAL_REPORT_READY":
            errors.append("report_readiness.stage must be FINAL_REPORT_READY")
        if readiness.get("delivery_allowed") is not True:
            errors.append("report_readiness.delivery_allowed must be true")
        blocked_statuses: list[str] = sorted({status for status in _ai_statuses(payload) if status not in FORMAL_AI_STATUSES})
        if blocked_statuses:
            errors.append(f"formal delivery contains non-final AI statuses: {blocked_statuses}")
        return errors
    errors.append("formal delivery requires a candidate comparison report")
    return errors


def _load_json(path: Path) -> Mapping[str, Any]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Validate formal Serenity delivery readiness")
    parser.add_argument("path")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        errors: list[str] = validate_delivery_payload(_load_json(Path(args.path)), source_path=args.path)
    except Exception as exc:
        print(f"FAILED: {args.path}", file=sys.stderr)
        print(f"- ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if errors:
        print(f"FAILED: {args.path}", file=sys.stderr)
        for error in errors:
            print(f"- ERROR {error}", file=sys.stderr)
        return 1
    print(f"OK: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
