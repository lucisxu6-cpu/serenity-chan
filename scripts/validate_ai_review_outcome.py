#!/usr/bin/env python3
"""Validate AI review failure/skip outcomes before they enter reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


OUTCOME_STATUSES: set[str] = {"FAILED_INSUFFICIENT_EVIDENCE", "CONFLICT_WITH_DATA", "SKIPPED_QUICK_AUDIT"}
REQUIRED_FIELDS: set[str] = {"symbol", "as_of_date", "ai_review_status", "reason"}
ALLOWED_FIELDS: set[str] = REQUIRED_FIELDS | {"required_evidence", "conflicting_fields", "source_refs", "research_questions"}


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(payload: Mapping[str, Any], key: str, errors: list[str]) -> list[str]:
    value: Any = payload.get(key, [])
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        errors.append(f"{key} must be an array")
        return []
    result: list[str] = []
    for item in value:
        if not _non_empty_string(item):
            errors.append(f"{key} must contain only non-empty strings")
            continue
        result.append(str(item).strip())
    return result


def validate_review_outcome(payload: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    missing: list[str] = sorted(REQUIRED_FIELDS - set(payload))
    if missing:
        errors.append(f"ai review outcome missing required keys: {', '.join(missing)}")
    unsupported: list[str] = sorted(set(payload) - ALLOWED_FIELDS)
    if unsupported:
        errors.append(f"ai review outcome contains unsupported keys: {', '.join(unsupported)}")

    for key in ["symbol", "as_of_date", "reason"]:
        if key in payload and not _non_empty_string(payload.get(key)):
            errors.append(f"{key} must be a non-empty string")
    status: str = str(payload.get("ai_review_status") or "")
    if status not in OUTCOME_STATUSES:
        errors.append(f"ai_review_status must be one of {sorted(OUTCOME_STATUSES)}")

    required_evidence: list[str] = _string_list(payload, "required_evidence", errors)
    conflicting_fields: list[str] = _string_list(payload, "conflicting_fields", errors)
    source_refs: list[str] = _string_list(payload, "source_refs", errors)
    research_questions: list[str] = _string_list(payload, "research_questions", errors)

    if status == "FAILED_INSUFFICIENT_EVIDENCE" and not required_evidence:
        errors.append("FAILED_INSUFFICIENT_EVIDENCE requires at least one required_evidence item")
    if status == "CONFLICT_WITH_DATA" and not conflicting_fields:
        errors.append("CONFLICT_WITH_DATA requires at least one conflicting_fields item")

    if errors:
        raise ValueError("; ".join(errors))

    return {
        "ok": True,
        "normalized_outcome": {
            "symbol": str(payload.get("symbol") or "").strip(),
            "as_of_date": str(payload.get("as_of_date") or "").strip(),
            "ai_review_status": status,
            "reason": str(payload.get("reason") or "").strip(),
            "required_evidence": required_evidence,
            "conflicting_fields": conflicting_fields,
            "source_refs": source_refs,
            "research_questions": research_questions,
        },
    }


def _load_json(path: str) -> Mapping[str, Any]:
    raw: str = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    payload: Any = json.loads(raw)
    if not isinstance(payload, Mapping):
        raise ValueError("ai review outcome JSON must be an object")
    return payload


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Validate a Serenity + Chan AI review outcome")
    parser.add_argument("outcome", help="Outcome JSON path or '-' for stdin")
    parser.add_argument("--json", action="store_true", help="emit machine-readable validation result")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        result: dict[str, Any] = validate_review_outcome(_load_json(args.outcome))
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"FAILED: {args.outcome}")
            print(f"- ERROR: {exc}")
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"OK: {args.outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
