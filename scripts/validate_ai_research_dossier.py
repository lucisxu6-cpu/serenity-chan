#!/usr/bin/env python3
"""Validate an AI research dossier before it is merged into Serenity reports."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from validate_ai_overlay import SOURCE_LEVELS, _as_float, evidence_context_from_manifest
except ModuleNotFoundError:  # pragma: no cover
    from scripts.validate_ai_overlay import SOURCE_LEVELS, _as_float, evidence_context_from_manifest


REQUIRED_ROOT: set[str] = {
    "contract_type",
    "schema_version",
    "symbol",
    "as_of_date",
    "research_status",
    "source_reading_log",
    "research_path",
    "observed",
    "inferred",
    "judgment",
    "claim_graph",
    "causal_chain",
    "same_layer_comparison",
    "bear_case",
    "scenario_view",
    "trigger_table",
    "action_conditions",
    "confidence_dampers",
    "overlay_projection",
}
RESEARCH_STATUSES: set[str] = {"COMPLETED", "FAILED_INSUFFICIENT_EVIDENCE", "CONFLICT_WITH_DATA"}
READ_STATUSES: set[str] = {"READ", "PARTIAL_READ", "UNAVAILABLE", "NOT_DECISION_RELEVANT"}
CLAIM_STATUSES: set[str] = {"SUPPORTED", "PARTIAL", "CONFLICTED", "UNSUPPORTED"}
HYPOTHESIS_STATUSES: set[str] = {"SUPPORTED", "PARTIAL", "CONFLICTED", "UNTESTED"}
CAUSAL_EVIDENCE_STATUSES: set[str] = {"DIRECT", "LEAD", "INFERRED", "MISSING", "CONFLICTED"}
CONFIDENCE: set[str] = {"LOW", "MEDIUM", "HIGH"}
GROWTH: set[str] = {"H0", "H1", "H2", "H3", "H4", "H5", "UNKNOWN"}
PROJECTION_FIELDS: set[str] = {
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
    "ai_confidence",
    "thesis_quality_delta",
    "evidence_confidence_delta",
    "risk_adjustment",
    "action_condition_summary",
}
# Match overlay source resolution: generic aliases require exact refs.
GENERIC_CONTEXT_KEYS: set[str] = {
    "manifest",
    "aireviewpacket",
    "deterministicmatrices",
    "financialqualitymatrix",
    "valuationinputmatrix",
    "growthhypothesismatrix",
}


def _string(value: Any) -> str:
    return str(value or "").strip()


def _non_empty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _list(value: Any, label: str, errors: list[str], *, min_items: int = 1) -> list[Any]:
    if not isinstance(value, list):
        errors.append(f"{label} must be an array")
        return []
    if len(value) < min_items:
        errors.append(f"{label} must contain at least {min_items} item(s)")
    return value


def _string_list(value: Any, label: str, errors: list[str], *, min_items: int = 1) -> list[str]:
    rows: list[Any] = _list(value, label, errors, min_items=min_items)
    result: list[str] = []
    for index, item in enumerate(rows):
        if not _non_empty(item):
            errors.append(f"{label}[{index}] must be a non-empty string")
            continue
        result.append(str(item).strip())
    return result


def _mapping(value: Any, label: str, errors: list[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        errors.append(f"{label} must be an object")
        return {}
    return value


def _reject_unsupported_keys(value: Mapping[str, Any], allowed: set[str], label: str, errors: list[str]) -> None:
    unsupported: list[str] = sorted(set(value) - allowed)
    if unsupported:
        errors.append(f"{label} contains unsupported keys: {', '.join(unsupported)}")


def _score(value: Any, label: str, errors: list[str], *, minimum: float = 0.0, maximum: float = 100.0) -> Optional[float]:
    number: Optional[float] = _as_float(value)
    if number is None or number < minimum or number > maximum:
        errors.append(f"{label} must be a number between {minimum:g} and {maximum:g}")
        return None
    return round(number, 4)


def _validate_source_reading_log(rows_value: Any, errors: list[str], evidence_context: Optional[Mapping[str, str]]) -> list[str]:
    source_refs: list[str] = []
    rows: list[Any] = _list(rows_value, "source_reading_log", errors)
    for index, item in enumerate(rows):
        row: Mapping[str, Any] = _mapping(item, f"source_reading_log[{index}]", errors)
        _reject_unsupported_keys(row, {"source_ref", "source_level", "read_status", "finding", "claim_boundary"}, f"source_reading_log[{index}]", errors)
        source_ref: str = _string(row.get("source_ref"))
        if not source_ref:
            errors.append(f"source_reading_log[{index}].source_ref must be non-empty")
        else:
            source_refs.append(source_ref)
            if evidence_context is not None and not _source_ref_available(source_ref, evidence_context):
                errors.append(f"source_reading_log[{index}].source_ref cannot be resolved to manifest evidence: {source_ref}")
        if _string(row.get("source_level")) not in SOURCE_LEVELS:
            errors.append(f"source_reading_log[{index}].source_level must be one of {sorted(SOURCE_LEVELS)}")
        if _string(row.get("read_status")) not in READ_STATUSES:
            errors.append(f"source_reading_log[{index}].read_status must be one of {sorted(READ_STATUSES)}")
        if not _non_empty(row.get("finding")):
            errors.append(f"source_reading_log[{index}].finding must be non-empty")
        if "claim_boundary" in row and not isinstance(row.get("claim_boundary"), str):
            errors.append(f"source_reading_log[{index}].claim_boundary must be a string when supplied")
    return source_refs


def _source_ref_available(source_ref: str, evidence_context: Mapping[str, str]) -> bool:
    compact_ref: str = "".join(ch for ch in source_ref.lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    if not compact_ref:
        return False
    for key, text in evidence_context.items():
        compact_key: str = "".join(ch for ch in str(key).lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
        if (
            compact_ref == compact_key
            or (len(compact_ref) >= 12 and compact_ref in compact_key)
            or (len(compact_key) >= 12 and compact_key not in GENERIC_CONTEXT_KEYS and compact_key in compact_ref)
        ) and str(text).strip():
            return bool(str(text).strip())
    ref_terms: set[str] = {term for term in re.split(r"[^a-z0-9一-龥]+", source_ref.lower()) if len(term) >= 3}
    if not ref_terms:
        return False
    for key, text in evidence_context.items():
        if not str(text).strip():
            continue
        key_terms: set[str] = {term for term in re.split(r"[^a-z0-9一-龥]+", str(key).lower()) if len(term) >= 3}
        if len(ref_terms & key_terms) >= 2:
            return True
    return False


def _validate_claim_graph(rows_value: Any, errors: list[str], known_refs: Sequence[str]) -> None:
    known: set[str] = set(str(item) for item in known_refs)
    rows: list[Any] = _list(rows_value, "claim_graph", errors)
    has_supported_or_partial: bool = False
    for index, item in enumerate(rows):
        row: Mapping[str, Any] = _mapping(item, f"claim_graph[{index}]", errors)
        _reject_unsupported_keys(row, {"claim", "supporting_refs", "opposing_refs", "status", "decision_impact"}, f"claim_graph[{index}]", errors)
        if not _non_empty(row.get("claim")):
            errors.append(f"claim_graph[{index}].claim must be non-empty")
        status: str = _string(row.get("status"))
        if status not in CLAIM_STATUSES:
            errors.append(f"claim_graph[{index}].status must be one of {sorted(CLAIM_STATUSES)}")
        if status in {"SUPPORTED", "PARTIAL"}:
            has_supported_or_partial = True
        for key in ["supporting_refs", "opposing_refs"]:
            if key not in row:
                errors.append(f"claim_graph[{index}].{key} is required")
            refs: list[str] = _string_list(row.get(key, []), f"claim_graph[{index}].{key}", errors, min_items=0)
            unknown_refs: list[str] = [ref for ref in refs if known and ref not in known]
            if unknown_refs:
                errors.append(f"claim_graph[{index}].{key} contains refs not present in source_reading_log: {', '.join(unknown_refs)}")
        if not _non_empty(row.get("decision_impact")):
            errors.append(f"claim_graph[{index}].decision_impact must be non-empty")
    if rows and not has_supported_or_partial:
        errors.append("claim_graph must contain at least one SUPPORTED or PARTIAL claim")


def _validate_research_path(value: Any, errors: list[str], known_refs: Sequence[str]) -> None:
    path: Mapping[str, Any] = _mapping(value, "research_path", errors)
    _reject_unsupported_keys(
        path,
        {"core_question", "decision_use", "base_rate_anchor", "reflexivity_check", "hypotheses", "evidence_tests", "unresolved_questions"},
        "research_path",
        errors,
    )
    for key in ["core_question", "decision_use"]:
        if not _non_empty(path.get(key)):
            errors.append(f"research_path.{key} must be non-empty")
    for key in ["base_rate_anchor", "reflexivity_check"]:
        if key in path and not isinstance(path.get(key), str):
            errors.append(f"research_path.{key} must be a string when supplied")

    hypotheses: list[Any] = _list(path.get("hypotheses"), "research_path.hypotheses", errors, min_items=2)
    has_tested_hypothesis: bool = False
    for index, item in enumerate(hypotheses):
        row: Mapping[str, Any] = _mapping(item, f"research_path.hypotheses[{index}]", errors)
        _reject_unsupported_keys(row, {"hypothesis", "current_view", "why_it_matters", "evidence_needed"}, f"research_path.hypotheses[{index}]", errors)
        for key in ["hypothesis", "why_it_matters", "evidence_needed"]:
            if not _non_empty(row.get(key)):
                errors.append(f"research_path.hypotheses[{index}].{key} must be non-empty")
        current_view: str = _string(row.get("current_view"))
        if current_view not in HYPOTHESIS_STATUSES:
            errors.append(f"research_path.hypotheses[{index}].current_view must be one of {sorted(HYPOTHESIS_STATUSES)}")
        if current_view in {"SUPPORTED", "PARTIAL", "CONFLICTED"}:
            has_tested_hypothesis = True

    known: set[str] = set(str(item) for item in known_refs)
    tests: list[Any] = _list(path.get("evidence_tests"), "research_path.evidence_tests", errors, min_items=2)
    has_non_missing_test: bool = False
    for index, item in enumerate(tests):
        row: Mapping[str, Any] = _mapping(item, f"research_path.evidence_tests[{index}]", errors)
        _reject_unsupported_keys(row, {"test", "method", "current_result", "evidence_status", "source_refs"}, f"research_path.evidence_tests[{index}]", errors)
        for key in ["test", "method", "current_result"]:
            if not _non_empty(row.get(key)):
                errors.append(f"research_path.evidence_tests[{index}].{key} must be non-empty")
        evidence_status: str = _string(row.get("evidence_status"))
        if evidence_status not in CAUSAL_EVIDENCE_STATUSES:
            errors.append(f"research_path.evidence_tests[{index}].evidence_status must be one of {sorted(CAUSAL_EVIDENCE_STATUSES)}")
        if evidence_status in {"DIRECT", "LEAD", "INFERRED", "CONFLICTED"}:
            has_non_missing_test = True
        if "source_refs" not in row:
            errors.append(f"research_path.evidence_tests[{index}].source_refs is required")
        refs: list[str] = _string_list(row.get("source_refs", []), f"research_path.evidence_tests[{index}].source_refs", errors, min_items=0)
        unknown_refs: list[str] = [ref for ref in refs if known and ref not in known]
        if unknown_refs:
            errors.append(f"research_path.evidence_tests[{index}].source_refs contains refs not present in source_reading_log: {', '.join(unknown_refs)}")

    _string_list(path.get("unresolved_questions"), "research_path.unresolved_questions", errors)
    if hypotheses and not has_tested_hypothesis:
        errors.append("research_path.hypotheses must contain at least one SUPPORTED, PARTIAL, or CONFLICTED hypothesis")
    if tests and not has_non_missing_test:
        errors.append("research_path.evidence_tests must contain at least one non-MISSING evidence test")


def _validate_causal_chain(rows_value: Any, errors: list[str]) -> None:
    rows: list[Any] = _list(rows_value, "causal_chain", errors, min_items=3)
    for index, item in enumerate(rows):
        row: Mapping[str, Any] = _mapping(item, f"causal_chain[{index}]", errors)
        _reject_unsupported_keys(row, {"step", "mechanism", "evidence_status"}, f"causal_chain[{index}]", errors)
        for key in ["step", "mechanism"]:
            if not _non_empty(row.get(key)):
                errors.append(f"causal_chain[{index}].{key} must be non-empty")
        if _string(row.get("evidence_status")) not in CAUSAL_EVIDENCE_STATUSES:
            errors.append(f"causal_chain[{index}].evidence_status must be one of {sorted(CAUSAL_EVIDENCE_STATUSES)}")


def _validate_scenario_view(value: Any, errors: list[str]) -> None:
    scenarios: Mapping[str, Any] = _mapping(value, "scenario_view", errors)
    _reject_unsupported_keys(scenarios, {"base", "upside", "downside"}, "scenario_view", errors)
    probabilities: list[float] = []
    for key in ["base", "upside", "downside"]:
        scenario: Mapping[str, Any] = _mapping(scenarios.get(key), f"scenario_view.{key}", errors)
        _reject_unsupported_keys(scenario, {"summary", "probability", "conditions"}, f"scenario_view.{key}", errors)
        if not _non_empty(scenario.get("summary")):
            errors.append(f"scenario_view.{key}.summary must be non-empty")
        probability: Optional[float] = _score(scenario.get("probability"), f"scenario_view.{key}.probability", errors, minimum=0.0, maximum=1.0)
        if probability is not None:
            probabilities.append(probability)
        _string_list(scenario.get("conditions"), f"scenario_view.{key}.conditions", errors)
    if len(probabilities) == 3 and not 0.95 <= sum(probabilities) <= 1.05:
        errors.append("scenario_view probabilities must sum to approximately 1")


def _validate_trigger_table(value: Any, errors: list[str]) -> None:
    table: Mapping[str, Any] = _mapping(value, "trigger_table", errors)
    _reject_unsupported_keys(table, {"30d", "90d", "180d"}, "trigger_table", errors)
    for key in ["30d", "90d", "180d"]:
        _string_list(table.get(key), f"trigger_table.{key}", errors)


def _validate_action_conditions(value: Any, errors: list[str]) -> None:
    conditions: Mapping[str, Any] = _mapping(value, "action_conditions", errors)
    _reject_unsupported_keys(conditions, {"upgrade_to_action", "add_or_size_up", "trim_or_exit", "stay_research_only"}, "action_conditions", errors)
    for key in ["upgrade_to_action", "add_or_size_up", "trim_or_exit", "stay_research_only"]:
        _string_list(conditions.get(key), f"action_conditions.{key}", errors)


def _validate_overlay_projection(value: Any, errors: list[str], research_status: str) -> dict[str, Any]:
    projection: Mapping[str, Any] = _mapping(value, "overlay_projection", errors)
    unsupported: list[str] = sorted(set(projection) - PROJECTION_FIELDS)
    if unsupported:
        errors.append("overlay_projection contains unsupported keys: " + ", ".join(unsupported))
    for key in ["layer", "bottleneck_reason", "revenue_transmission", "required_next_evidence", "posterior_basis"]:
        if not _non_empty(projection.get(key)):
            errors.append(f"overlay_projection.{key} must be non-empty")
    serenity_fit: Optional[float] = _score(projection.get("serenity_fit"), "overlay_projection.serenity_fit", errors, minimum=0.0, maximum=1.0)
    for key in ["layer_score", "company_fit"]:
        if projection.get(key) is not None:
            _score(projection.get(key), f"overlay_projection.{key}", errors)
    growth: str = _string(projection.get("evidence_supported_growth"))
    if growth not in GROWTH:
        errors.append(f"overlay_projection.evidence_supported_growth must be one of {sorted(GROWTH)}")
    if growth in {"H4", "H5"} and projection.get("h4_h5_evidence_bar_met") is not True:
        errors.append("H4/H5 overlay_projection requires h4_h5_evidence_bar_met=true")
    if _string(projection.get("ai_confidence")) not in CONFIDENCE:
        errors.append("overlay_projection.ai_confidence must be LOW, MEDIUM, or HIGH")
    if not _non_empty(projection.get("action_condition_summary")):
        errors.append("overlay_projection.action_condition_summary must be non-empty")
    for key in ["thesis_quality_delta", "evidence_confidence_delta", "risk_adjustment"]:
        if key not in projection:
            errors.append(f"overlay_projection.{key} is required")
        else:
            _score(projection.get(key), f"overlay_projection.{key}", errors, minimum=-8.0, maximum=8.0)
    if research_status != "COMPLETED" and serenity_fit is not None and serenity_fit > 0.55:
        errors.append("non-completed dossier cannot project serenity_fit above 0.55")
    return dict(projection)


def validate_dossier(
    payload: Mapping[str, Any],
    *,
    evidence_context: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    errors: list[str] = []
    missing: list[str] = sorted(REQUIRED_ROOT - set(payload))
    if missing:
        errors.append("dossier missing required keys: " + ", ".join(missing))
    unsupported_root: list[str] = sorted(set(payload) - REQUIRED_ROOT)
    if unsupported_root:
        errors.append("dossier contains unsupported root keys: " + ", ".join(unsupported_root))
    if payload.get("contract_type") != "serenity_ai_research_dossier":
        errors.append("contract_type must be serenity_ai_research_dossier")
    if payload.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    for key in ["symbol", "as_of_date"]:
        if not _non_empty(payload.get(key)):
            errors.append(f"{key} must be non-empty")
    research_status: str = _string(payload.get("research_status"))
    if research_status not in RESEARCH_STATUSES:
        errors.append(f"research_status must be one of {sorted(RESEARCH_STATUSES)}")

    source_refs: list[str] = _validate_source_reading_log(payload.get("source_reading_log"), errors, evidence_context)
    _validate_research_path(payload.get("research_path"), errors, source_refs)
    for key in ["observed", "inferred", "judgment", "bear_case", "confidence_dampers"]:
        _string_list(payload.get(key), key, errors)
    _validate_claim_graph(payload.get("claim_graph"), errors, source_refs)
    _validate_causal_chain(payload.get("causal_chain"), errors)
    same_layer_rows: list[Any] = _list(payload.get("same_layer_comparison"), "same_layer_comparison", errors, min_items=0)
    for index, item in enumerate(same_layer_rows):
        row: Mapping[str, Any] = _mapping(item, f"same_layer_comparison[{index}]", errors)
        _reject_unsupported_keys(row, {"peer", "comparison_axis", "relative_position", "evidence_needed"}, f"same_layer_comparison[{index}]", errors)
        for key in ["peer", "comparison_axis", "relative_position", "evidence_needed"]:
            if not _non_empty(row.get(key)):
                errors.append(f"same_layer_comparison[{index}].{key} must be non-empty")
    _validate_scenario_view(payload.get("scenario_view"), errors)
    _validate_trigger_table(payload.get("trigger_table"), errors)
    _validate_action_conditions(payload.get("action_conditions"), errors)
    projection: dict[str, Any] = _validate_overlay_projection(payload.get("overlay_projection"), errors, research_status)

    if research_status == "COMPLETED":
        if not any(str(item).strip() for item in payload.get("judgment", []) if isinstance(item, str)):
            errors.append("COMPLETED dossier requires non-empty judgment")
        if not source_refs:
            errors.append("COMPLETED dossier requires at least one source_reading_log item")

    if errors:
        raise ValueError("; ".join(errors))
    normalized: dict[str, Any] = dict(payload)
    normalized["overlay_projection"] = projection
    return {"ok": True, "normalized_dossier": normalized}


def _load_json(path: str) -> Mapping[str, Any]:
    raw: str = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    payload: Any = json.loads(raw)
    if not isinstance(payload, Mapping):
        raise ValueError("dossier JSON must be an object")
    return payload


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Validate a Serenity AI research dossier")
    parser.add_argument("dossier", help="Dossier JSON path or '-' for stdin")
    parser.add_argument("--manifest", help="optional fetch manifest used to resolve source_ref evidence")
    parser.add_argument("--json", action="store_true", help="emit machine-readable validation result")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        context: Optional[Mapping[str, str]] = evidence_context_from_manifest(Path(args.manifest)) if args.manifest else None
        result: dict[str, Any] = validate_dossier(_load_json(args.dossier), evidence_context=context)
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"FAILED: {args.dossier}")
            print(f"- ERROR: {exc}")
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"OK: {args.dossier}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
