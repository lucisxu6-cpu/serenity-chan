#!/usr/bin/env python3
"""Validate a Serenity theme research packet."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


def _load_json(path: Path) -> Mapping[str, Any]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _as_mapping(value: Any, label: str, errors: list[str]) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    errors.append(f"{label} must be an object")
    return {}


def _as_list(value: Any, label: str, errors: list[str]) -> list[Any]:
    if isinstance(value, list):
        return value
    errors.append(f"{label} must be an array")
    return []


def _non_empty(value: Any) -> bool:
    return bool(str(value or "").strip())


def validate_theme_research_packet(payload: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("contract_type") != "serenity_theme_research_packet":
        errors.append("contract_type must be serenity_theme_research_packet")
    if payload.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    for key in ["theme", "generated_at", "universe_path", "next_step"]:
        if not _non_empty(payload.get(key)):
            errors.append(f"{key} must not be empty")
    symbols: list[Any] = _as_list(payload.get("candidate_symbols"), "candidate_symbols", errors)
    candidate_count: Any = payload.get("candidate_count")
    if not isinstance(candidate_count, int) or candidate_count < 1:
        errors.append("candidate_count must be a positive integer")
    if isinstance(candidate_count, int) and candidate_count != len(symbols):
        errors.append("candidate_count must match candidate_symbols length")
    if len({_non_empty(symbol) and str(symbol) for symbol in symbols}) != len(symbols):
        errors.append("candidate_symbols must be unique and non-empty")
    if len(_as_list(payload.get("value_chain_layers"), "value_chain_layers", errors)) < 3:
        errors.append("value_chain_layers must contain at least 3 layers")
    if len([item for item in _as_list(payload.get("direction_research_questions"), "direction_research_questions", errors) if _non_empty(item)]) < 5:
        errors.append("direction_research_questions must contain at least 5 non-empty questions")
    if len([item for item in _as_list(payload.get("macro_evidence_tasks"), "macro_evidence_tasks", errors) if _non_empty(item)]) < 3:
        errors.append("macro_evidence_tasks must contain at least 3 non-empty tasks")
    if len([item for item in _as_list(payload.get("falsification_questions"), "falsification_questions", errors) if _non_empty(item)]) < 3:
        errors.append("falsification_questions must contain at least 3 non-empty questions")
    policy: Mapping[str, Any] = _as_mapping(payload.get("candidate_expansion_policy"), "candidate_expansion_policy", errors)
    minimum_deep_scan: Any = policy.get("minimum_deep_scan_candidates")
    required_minimum: int = min(20, candidate_count if isinstance(candidate_count, int) else 1)
    if not isinstance(minimum_deep_scan, int) or minimum_deep_scan < required_minimum:
        errors.append("candidate_expansion_policy.minimum_deep_scan_candidates must cover the current universe or at least the 20-candidate deep-scan target")
    for key in ["required_layer_coverage", "exclusion_rule"]:
        if not _non_empty(policy.get(key)):
            errors.append(f"candidate_expansion_policy.{key} must not be empty")
    return errors


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Validate theme research packet JSON")
    parser.add_argument("packet")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        errors: list[str] = validate_theme_research_packet(_load_json(Path(args.packet)))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("OK: theme research packet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
