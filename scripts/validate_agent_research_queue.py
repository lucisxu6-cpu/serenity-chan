#!/usr/bin/env python3
"""Validate the internal agent research queue emitted by formal workflows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


def _require(payload: Mapping[str, Any], key: str, expected: Any, errors: list[str]) -> None:
    if payload.get(key) != expected:
        errors.append(f"{key} must be {expected!r}")


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _as_list(value: Any, label: str, errors: list[str], *, min_items: int = 1) -> list[Any]:
    if not isinstance(value, list):
        errors.append(f"{label} must be an array")
        return []
    if len(value) < min_items:
        errors.append(f"{label} must contain at least {min_items} item(s)")
    return value


def validate_agent_research_queue(payload: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    # Formal mode queue is an execution contract, so unsupported fields are
    # rejected instead of passed through as optional metadata.
    allowed_root_fields: set[str] = {
        "contract_type",
        "schema_version",
        "workflow_status",
        "research_mode",
        "artifact_role",
        "terminal",
        "delivery_allowed",
        "next_phase",
        "out_dir",
        "fetch_summaries",
        "ai_artifacts",
        "internal_baseline_report",
        "missing_ai_result_symbols",
        "work_items",
        "execution_policy",
    }
    unsupported_root_fields: list[str] = sorted(set(payload) - allowed_root_fields)
    if unsupported_root_fields:
        errors.append(f"unsupported root fields: {', '.join(unsupported_root_fields)}")
    _require(payload, "contract_type", "serenity_agent_research_queue", errors)
    _require(payload, "schema_version", "1.0", errors)
    _require(payload, "workflow_status", "AGENT_RESEARCH_QUEUE_READY", errors)
    _require(payload, "research_mode", "formal", errors)
    _require(payload, "artifact_role", "internal_agent_work_queue", errors)
    _require(payload, "terminal", False, errors)
    _require(payload, "delivery_allowed", False, errors)
    _require(payload, "next_phase", "execute_agent_research", errors)
    for key in ["out_dir", "internal_baseline_report"]:
        if not _non_empty_string(payload.get(key)):
            errors.append(f"{key} must be a non-empty string")
    _as_list(payload.get("fetch_summaries"), "fetch_summaries", errors, min_items=0)
    _as_list(payload.get("ai_artifacts"), "ai_artifacts", errors, min_items=0)
    missing_symbols: list[Any] = _as_list(payload.get("missing_ai_result_symbols"), "missing_ai_result_symbols", errors, min_items=0)
    if not missing_symbols:
        errors.append("missing_ai_result_symbols must contain at least one symbol")
    work_items: list[Any] = _as_list(payload.get("work_items"), "work_items", errors, min_items=0)
    if not work_items:
        errors.append("work_items must contain at least one item")
    missing_symbol_set: set[str] = {str(symbol) for symbol in missing_symbols if str(symbol)}
    work_symbols: set[str] = set()
    required_item_fields: set[str] = {
        "symbol",
        "required_action",
        "manifest_path",
        "review_packet",
        "committee_packet",
        "overlay_prompt",
        "theme_universe",
        "theme_research_packet",
        "dossier_schema",
        "overlay_schema",
        "outcome_schema",
        "dossier_output_path",
        "overlay_output_path",
        "outcome_output_path",
        "allowed_results",
        "research_expansion_protocol",
        "validation_commands",
        "guardrails",
    }
    for index, item in enumerate(work_items):
        if not isinstance(item, Mapping):
            errors.append(f"work_items[{index}] must be an object")
            continue
        unsupported_item_fields: list[str] = sorted(set(item) - required_item_fields)
        if unsupported_item_fields:
            errors.append(f"work_items[{index}] unsupported fields: {', '.join(unsupported_item_fields)}")
        missing_fields: list[str] = sorted(required_item_fields - set(item))
        if missing_fields:
            errors.append(f"work_items[{index}] missing fields: {', '.join(missing_fields)}")
        symbol: str = str(item.get("symbol") or "")
        if not symbol:
            errors.append(f"work_items[{index}].symbol must be non-empty")
        work_symbols.add(symbol)
        if item.get("required_action") != "produce_validated_ai_research_package":
            errors.append(f"work_items[{index}].required_action is invalid")
        if item.get("dossier_schema") != "assets/ai_research_dossier.schema.json":
            errors.append(f"work_items[{index}].dossier_schema is invalid")
        if item.get("overlay_schema") != "assets/ai_research_overlay.schema.json":
            errors.append(f"work_items[{index}].overlay_schema is invalid")
        if item.get("outcome_schema") != "assets/ai_review_outcome.schema.json":
            errors.append(f"work_items[{index}].outcome_schema is invalid")
        allowed_results: list[Any] = _as_list(item.get("allowed_results"), f"work_items[{index}].allowed_results", errors, min_items=0)
        if len(allowed_results) < 3:
            errors.append(f"work_items[{index}].allowed_results must contain at least three items")
        if any(str(result).startswith("SKIPPED_QUICK_AUDIT") for result in allowed_results):
            errors.append(f"work_items[{index}].allowed_results must not allow SKIPPED_QUICK_AUDIT in formal mode")
        if len(_as_list(item.get("research_expansion_protocol"), f"work_items[{index}].research_expansion_protocol", errors, min_items=0)) < 5:
            errors.append(f"work_items[{index}].research_expansion_protocol must contain at least five items")
        for key in ["manifest_path", "review_packet", "committee_packet", "overlay_prompt", "dossier_output_path", "overlay_output_path", "outcome_output_path"]:
            if not _non_empty_string(item.get(key)):
                errors.append(f"work_items[{index}].{key} must be non-empty")
        validation_commands: list[Any] = _as_list(item.get("validation_commands"), f"work_items[{index}].validation_commands", errors, min_items=0)
        if len(validation_commands) < 3:
            errors.append(f"work_items[{index}].validation_commands must contain dossier, overlay, and outcome validators")
        command_text: str = "\n".join(str(command) for command in validation_commands)
        if "validate_ai_research_dossier.py" not in command_text:
            errors.append(f"work_items[{index}].validation_commands must include validate_ai_research_dossier.py")
        if len(_as_list(item.get("guardrails"), f"work_items[{index}].guardrails", errors, min_items=0)) < 3:
            errors.append(f"work_items[{index}].guardrails must contain at least three items")
    if missing_symbol_set and work_symbols != missing_symbol_set:
        errors.append("work_items symbols must exactly match missing_ai_result_symbols")
    policy: Any = payload.get("execution_policy")
    if not isinstance(policy, Mapping):
        errors.append("execution_policy must be an object")
    else:
        allowed_policy_fields: set[str] = {"current_agent_executes_work_items", "internal_baseline_role", "quick_audit_allowed", "forbidden"}
        unsupported_policy_fields: list[str] = sorted(set(policy) - allowed_policy_fields)
        if unsupported_policy_fields:
            errors.append(f"execution_policy unsupported fields: {', '.join(unsupported_policy_fields)}")
        if policy.get("current_agent_executes_work_items") is not True:
            errors.append("execution_policy.current_agent_executes_work_items must be true")
        if policy.get("internal_baseline_role") != "data_diagnostics_only":
            errors.append("execution_policy.internal_baseline_role must be data_diagnostics_only")
        if policy.get("quick_audit_allowed") is not False:
            errors.append("execution_policy.quick_audit_allowed must be false")
        if len(_as_list(policy.get("forbidden"), "execution_policy.forbidden", errors, min_items=0)) < 3:
            errors.append("execution_policy.forbidden must contain at least three items")
    return errors


def _load_json(path: Path) -> Mapping[str, Any]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Validate agent_research_queue.json")
    parser.add_argument("path")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        errors: list[str] = validate_agent_research_queue(_load_json(Path(args.path)))
    except Exception as exc:
        print(f"FAILED: {args.path}", file=sys.stderr)
        print(f"- ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if errors:
        print(f"FAILED: {args.path}", file=sys.stderr)
        for error in errors:
            print(f"- ERROR {error}", file=sys.stderr)
        return 1
    print("OK: agent_research_queue")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
