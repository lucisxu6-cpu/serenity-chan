#!/usr/bin/env python3
"""Validate a Serenity Laplace strategy judgment before rendering."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from validate_laplace_strategy_input import validate_strategy_input
except ModuleNotFoundError:  # pragma: no cover
    from scripts.validate_laplace_strategy_input import validate_strategy_input


REQUIRED_ROOT: set[str] = {
    "contract_type",
    "schema_version",
    "source_strategy_input_path",
    "as_of_date",
    "forecast",
    "decision",
    "decision_model",
    "observed",
    "inferred",
    "judgment",
    "dominant_variables",
    "scenarios",
    "triggers",
    "invalidation",
    "next_evidence",
    "action_plan",
    "confidence",
    "ledger_claims",
}
CONFIDENCE: set[str] = {"LOW", "MEDIUM", "HIGH"}


def _load_json(path: Path) -> Mapping[str, Any]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _string(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any, label: str, errors: list[str], *, min_items: int = 1) -> list[Any]:
    if not isinstance(value, list):
        errors.append(f"{label} must be an array")
        return []
    if len(value) < min_items:
        errors.append(f"{label} must contain at least {min_items} item(s)")
    for index, item in enumerate(value):
        if not _string(item):
            errors.append(f"{label}[{index}] must be a non-empty string")
    return value


def _mapping(value: Any, label: str, errors: list[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        errors.append(f"{label} must be an object")
        return {}
    return value


def _probability(value: Any, label: str, errors: list[str]) -> Optional[float]:
    try:
        number: float = float(value)
    except (TypeError, ValueError):
        errors.append(f"{label} must be numeric")
        return None
    if number < 0 or number > 1:
        errors.append(f"{label} must be between 0 and 1")
        return None
    return number


def _strategy_input_payload(strategy_input_path: Optional[Path], errors: list[str]) -> Mapping[str, Any]:
    if strategy_input_path is None:
        return {}
    payload: Mapping[str, Any] = _load_json(strategy_input_path)
    input_errors: list[str] = validate_strategy_input(payload)
    if input_errors:
        errors.extend(f"strategy_input: {error}" for error in input_errors)
    return payload


def validate_strategy_judgment(
    payload: Mapping[str, Any],
    *,
    strategy_input_path: Optional[Path] = None,
) -> list[str]:
    errors: list[str] = []
    missing: list[str] = sorted(REQUIRED_ROOT - set(payload))
    if missing:
        errors.append("strategy judgment missing keys: " + ", ".join(missing))
    if payload.get("contract_type") != "serenity_laplace_strategy_judgment":
        errors.append("contract_type must be serenity_laplace_strategy_judgment")
    if payload.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    for key in ["source_strategy_input_path", "as_of_date", "forecast", "decision", "decision_model"]:
        if not _string(payload.get(key)):
            errors.append(f"{key} must be a non-empty string")
    for key in ["observed", "inferred", "judgment", "invalidation", "next_evidence", "action_plan"]:
        _list(payload.get(key), key, errors)
    if payload.get("confidence") not in CONFIDENCE:
        errors.append("confidence must be LOW, MEDIUM, or HIGH")

    variables_value: Any = payload.get("dominant_variables")
    variables: list[Any] = variables_value if isinstance(variables_value, list) else []
    if not isinstance(variables_value, list):
        errors.append("dominant_variables must be an array")
    elif len(variables) < 3:
        errors.append("dominant_variables must contain at least 3 item(s)")
    for index, item in enumerate(variables):
        row: Mapping[str, Any] = _mapping(item, f"dominant_variables[{index}]", errors)
        for key in ["variable", "role", "direction", "why"]:
            if not _string(row.get(key)):
                errors.append(f"dominant_variables[{index}].{key} must be non-empty")
        if row.get("confidence") not in CONFIDENCE:
            errors.append(f"dominant_variables[{index}].confidence must be LOW, MEDIUM, or HIGH")

    scenarios: Mapping[str, Any] = _mapping(payload.get("scenarios"), "scenarios", errors)
    scenario_probs: list[float] = []
    for key in ["base", "upside", "downside"]:
        scenario: Mapping[str, Any] = _mapping(scenarios.get(key), f"scenarios.{key}", errors)
        if not _string(scenario.get("summary")):
            errors.append(f"scenarios.{key}.summary must be non-empty")
        probability: Optional[float] = _probability(scenario.get("probability"), f"scenarios.{key}.probability", errors)
        if probability is not None:
            scenario_probs.append(probability)
        _list(scenario.get("conditions"), f"scenarios.{key}.conditions", errors)
    if len(scenario_probs) == 3 and not 0.95 <= sum(scenario_probs) <= 1.05:
        errors.append("scenario probabilities must sum to approximately 1")

    triggers: Mapping[str, Any] = _mapping(payload.get("triggers"), "triggers", errors)
    for key in ["30d", "90d", "180d"]:
        _list(triggers.get(key), f"triggers.{key}", errors)

    ledger_claims: Any = payload.get("ledger_claims")
    if not isinstance(ledger_claims, list):
        errors.append("ledger_claims must be an array")
    else:
        for index, claim in enumerate(ledger_claims):
            row: Mapping[str, Any] = _mapping(claim, f"ledger_claims[{index}]", errors)
            for key in ["claim", "resolution_date", "resolution_criteria"]:
                if not _string(row.get(key)):
                    errors.append(f"ledger_claims[{index}].{key} must be non-empty")
            _probability(row.get("probability"), f"ledger_claims[{index}].probability", errors)

    strategy_input: Mapping[str, Any] = _strategy_input_payload(strategy_input_path, errors)
    if strategy_input_path is not None:
        expected_path: str = str(strategy_input_path.resolve())
        supplied_path: str = _string(payload.get("source_strategy_input_path"))
        if supplied_path and supplied_path != expected_path:
            errors.append("source_strategy_input_path must match the validated strategy input path")
    comparison_summary: Mapping[str, Any] = strategy_input.get("comparison_summary") if isinstance(strategy_input.get("comparison_summary"), Mapping) else {}
    if str(comparison_summary.get("ranking_validity") or "") in {"PARTIAL", "INVALID"}:
        decision_text: str = _string(payload.get("decision")) + " " + " ".join(str(item) for item in payload.get("action_plan", []) if item)
        if not any(token in decision_text for token in ["观察", "等待", "门控", "补证", "跟踪", "不产生正式决策对象"]):
            errors.append("PARTIAL/INVALID ranking requires a gated watch/wait/evidence-first decision")
    return errors


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Validate a Laplace strategy judgment JSON")
    parser.add_argument("judgment", help="strategy judgment JSON")
    parser.add_argument("--strategy-input", help="validated laplace_strategy_input.json")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        errors: list[str] = validate_strategy_judgment(
            _load_json(Path(args.judgment)),
            strategy_input_path=Path(args.strategy_input) if args.strategy_input else None,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("OK: laplace strategy judgment")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
