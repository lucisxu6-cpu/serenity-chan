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
    expansion_status: str = str(payload.get("candidate_expansion_status") or "")
    if expansion_status not in {"CURATED_FULL", "CURATED_INITIAL", "AI_EXPANSION_REQUIRED"}:
        errors.append("candidate_expansion_status is unknown")
    coverage: Mapping[str, Any] = _as_mapping(payload.get("layer_coverage"), "layer_coverage", errors)
    if not coverage:
        errors.append("layer_coverage must not be empty")
    for layer, count in coverage.items():
        if not _non_empty(layer) or not isinstance(count, int) or count < 0:
            errors.append("layer_coverage must map non-empty layer names to non-negative integers")
    if len(_as_list(payload.get("value_chain_layers"), "value_chain_layers", errors)) < 3:
        errors.append("value_chain_layers must contain at least 3 layers")
    per_candidate_tasks: list[Any] = _as_list(payload.get("per_candidate_research_tasks"), "per_candidate_research_tasks", errors)
    if len(per_candidate_tasks) != len(symbols):
        errors.append("per_candidate_research_tasks must contain one task row per candidate")
    task_symbols: set[str] = set()
    for index, item in enumerate(per_candidate_tasks):
        row: Mapping[str, Any] = _as_mapping(item, f"per_candidate_research_tasks[{index}]", errors)
        symbol: str = str(row.get("symbol") or "").strip()
        if symbol:
            task_symbols.add(symbol)
        for key in ["symbol", "layer"]:
            if not _non_empty(row.get(key)):
                errors.append(f"per_candidate_research_tasks[{index}].{key} must not be empty")
        if len([task for task in _as_list(row.get("evidence_tasks"), f"per_candidate_research_tasks[{index}].evidence_tasks", errors) if _non_empty(task)]) < 3:
            errors.append(f"per_candidate_research_tasks[{index}].evidence_tasks must contain at least 3 non-empty tasks")
        if len([trigger for trigger in _as_list(row.get("downgrade_triggers"), f"per_candidate_research_tasks[{index}].downgrade_triggers", errors) if _non_empty(trigger)]) < 2:
            errors.append(f"per_candidate_research_tasks[{index}].downgrade_triggers must contain at least 2 non-empty triggers")
    if task_symbols != {str(symbol) for symbol in symbols}:
        errors.append("per_candidate_research_tasks symbols must match candidate_symbols")
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
