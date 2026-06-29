#!/usr/bin/env python3
"""Validate the Serenity-to-Laplace strategy input contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


REQUIRED_ROOT: set[str] = {
    "contract_type",
    "schema_version",
    "generated_at",
    "source_report_path",
    "strategy_input_ready",
    "companion_skill",
    "decision_context",
    "comparison_summary",
    "observed_facts",
    "forecast_variables",
    "strategy_questions",
    "laplace_execution",
    "ledger_seed",
}
STRATEGY_READY_AI_STATUSES: set[str] = {"COMPLETED", "FAILED_INSUFFICIENT_EVIDENCE", "CONFLICT_WITH_DATA"}
REQUIRED_CANDIDATE: set[str] = {
    "symbol",
    "market",
    "rating_cap",
    "research_priority_score",
    "action_priority_score",
    "action_readiness",
    "primary_gate",
    "data_evidence_cap",
    "ai_review_status",
    "market_implied_growth",
    "evidence_supported_growth",
    "research_debt_count",
}
REQUIRED_VARIABLE: set[str] = {
    "variable",
    "role",
    "direction",
    "observability",
    "change_speed",
    "dominant_lens",
    "confidence",
    "why",
}


def _load_json(path: Path) -> Mapping[str, Any]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _missing(obj: Mapping[str, Any], required: set[str], label: str) -> list[str]:
    return [f"{label} missing required key: {key}" for key in sorted(required) if key not in obj]


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


def _non_empty_text(value: Any) -> bool:
    return bool(str(value or "").strip())


def validate_strategy_input(payload: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    errors.extend(_missing(payload, REQUIRED_ROOT, "strategy_input"))
    if payload.get("contract_type") != "serenity_laplace_strategy_input":
        errors.append("contract_type must be serenity_laplace_strategy_input")
    if payload.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    source_report_path: str = str(payload.get("source_report_path") or "")
    blocked_source_names: tuple[str, ...] = (
        "comparison_baseline.json",
        "comparison_internal_baseline.json",
        "comparison_diagnostic_baseline.json",
        "agent_research_queue.json",
    )
    if source_report_path.endswith(blocked_source_names):
        errors.append("source_report_path cannot be an internal baseline or agent queue artifact")

    readiness: Mapping[str, Any] = _as_mapping(payload.get("strategy_input_ready"), "strategy_input_ready", errors)
    if readiness.get("status") != "READY":
        errors.append("strategy_input_ready.status must be READY")
    if readiness.get("source_report_type") != "completed_ai_research":
        errors.append("strategy_input_ready.source_report_type must be completed_ai_research")
    report_readiness: Mapping[str, Any] = _as_mapping(readiness.get("report_readiness"), "strategy_input_ready.report_readiness", errors)
    if report_readiness.get("stage") != "FINAL_REPORT_READY":
        errors.append("strategy_input_ready.report_readiness.stage must be FINAL_REPORT_READY")
    if report_readiness.get("delivery_allowed") is not True:
        errors.append("strategy_input_ready.report_readiness.delivery_allowed must be true")
    ready_statuses: list[Any] = _as_list(readiness.get("ai_review_statuses"), "strategy_input_ready.ai_review_statuses", errors)
    blocked_ready_statuses: list[str] = sorted({str(status) for status in ready_statuses if str(status) not in STRATEGY_READY_AI_STATUSES})
    if blocked_ready_statuses:
        errors.append(f"strategy_input_ready.ai_review_statuses contains non-strategy-ready statuses: {blocked_ready_statuses}")

    companion: Mapping[str, Any] = _as_mapping(payload.get("companion_skill"), "companion_skill", errors)
    for key in ["name", "path", "entrypoint"]:
        if not _non_empty_text(companion.get(key)):
            errors.append(f"companion_skill.{key} must not be empty")

    context: Mapping[str, Any] = _as_mapping(payload.get("decision_context"), "decision_context", errors)
    for key in ["object", "horizon", "geography", "decision_use", "default_profile"]:
        if not _non_empty_text(context.get(key)):
            errors.append(f"decision_context.{key} must not be empty")

    summary: Mapping[str, Any] = _as_mapping(payload.get("comparison_summary"), "comparison_summary", errors)
    for key in ["candidate_count", "as_of", "semantic_coherence", "decision_mode", "ranking_validity"]:
        if key != "candidate_count" and not _non_empty_text(summary.get(key)):
            errors.append(f"comparison_summary.{key} must not be empty")

    facts: Mapping[str, Any] = _as_mapping(payload.get("observed_facts"), "observed_facts", errors)
    candidates: list[Any] = _as_list(facts.get("candidates"), "observed_facts.candidates", errors)
    if not candidates:
        errors.append("observed_facts.candidates must contain at least one candidate")
    for index, item in enumerate(candidates):
        row: Mapping[str, Any] = _as_mapping(item, f"observed_facts.candidates[{index}]", errors)
        errors.extend(_missing(row, REQUIRED_CANDIDATE, f"observed_facts.candidates[{index}]"))
        if not _non_empty_text(row.get("symbol")):
            errors.append(f"observed_facts.candidates[{index}].symbol must not be empty")
        status: str = str(row.get("ai_review_status") or "")
        if status not in STRATEGY_READY_AI_STATUSES:
            errors.append(f"observed_facts.candidates[{index}].ai_review_status is not strategy-ready: {status}")

    variables: list[Any] = _as_list(payload.get("forecast_variables"), "forecast_variables", errors)
    if len(variables) < 3:
        errors.append("forecast_variables must contain at least 3 variables")
    for index, item in enumerate(variables):
        row = _as_mapping(item, f"forecast_variables[{index}]", errors)
        errors.extend(_missing(row, REQUIRED_VARIABLE, f"forecast_variables[{index}]"))

    questions: list[Any] = _as_list(payload.get("strategy_questions"), "strategy_questions", errors)
    if len([item for item in questions if _non_empty_text(item)]) < 3:
        errors.append("strategy_questions must contain at least 3 non-empty questions")

    execution: Mapping[str, Any] = _as_mapping(payload.get("laplace_execution"), "laplace_execution", errors)
    must_read: list[Any] = _as_list(execution.get("must_read"), "laplace_execution.must_read", errors)
    if "companion-skills/laplace-forecast/SKILL.md" not in must_read:
        errors.append("laplace_execution.must_read must include companion-skills/laplace-forecast/SKILL.md")
    required_sections: list[Any] = _as_list(execution.get("required_output_sections"), "laplace_execution.required_output_sections", errors)
    for section in ["Forecast", "Decision", "Scenarios", "Triggers", "Invalidation", "Action plan"]:
        if section not in required_sections:
            errors.append(f"laplace_execution.required_output_sections must include {section}")

    ledger: Mapping[str, Any] = _as_mapping(payload.get("ledger_seed"), "ledger_seed", errors)
    for key in ["question", "horizon", "object", "geography", "decision_use", "claims"]:
        if key != "claims" and not _non_empty_text(ledger.get(key)):
            errors.append(f"ledger_seed.{key} must not be empty")
    _as_list(ledger.get("claims"), "ledger_seed.claims", errors)
    return errors


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Validate Laplace strategy input JSON")
    parser.add_argument("strategy_input")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        errors: list[str] = validate_strategy_input(_load_json(Path(args.strategy_input)))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("OK: laplace strategy input")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
