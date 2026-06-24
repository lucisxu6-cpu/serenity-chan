#!/usr/bin/env python3
"""Validate structured Serenity + Chan output-contract JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from market_source_policy import flatten_sources, mismatched_sources_for_market
except ModuleNotFoundError:  # pragma: no cover - supports python -m scripts.validate_output_contract_json
    from scripts.market_source_policy import flatten_sources, mismatched_sources_for_market

MARKETS = {"CN_A", "US", "HK", "GLOBAL", "OTHER", "UNKNOWN"}
STATUSES = {"OK", "PARTIAL", "STALE", "FAILED", "PENDING", "NOT_APPLICABLE", "NOT_REQUESTED"}
UNAVAILABLE_STATUSES = {"FAILED", "PENDING", "NOT_APPLICABLE", "NOT_REQUESTED"}
RATINGS = {"S", "A", "B", "C", "D", "OBSERVE_ONLY"}
RATING_ORDER = {"OBSERVE_ONLY": 0, "D": 1, "C": 2, "B": 3, "A": 4, "S": 5}
SOURCE_LEVELS = {"L0", "L1", "L2", "L3", "L4"}
CONFIDENCE = {"Strong", "Medium", "Weak", "Unverified"}
GROWTH = {"H0", "H1", "H2", "H3", "H4", "H5", "UNKNOWN"}
GROWTH_ORDER = {"H0": 0, "H1": 1, "H2": 2, "H3": 3, "H4": 4, "H5": 5, "UNKNOWN": -1}
ACTIONS = {"观察", "等待买点", "等待二买", "等待三买", "小仓试错", "核心候选", "强观察", "剔除", "不参与", "数据不足"}
GAP_TYPES = {"ACCESS_FAILURE", "SCOPE_NOT_REQUESTED", "SOURCE_NOT_IMPLEMENTED", "SOURCE_UNAVAILABLE", "ISSUER_NON_DISCLOSURE", "NOT_MACHINE_READABLE", "CONFLICTING_SOURCES", "STALE_DATA", "EVIDENCE_DEPTH_LIMIT", "ADJUSTMENT_BASIS_UNVERIFIED", "NOT_MATERIAL", "POLICY_BLOCKED"}
DECISION_IMPACTS = {"THESIS_IMPACT", "EVIDENCE_IMPACT", "ACTION_IMPACT", "VALUATION_IMPACT", "ENGINEERING_GAP", "NO_IMPACT"}
ACTION_READINESS = {"CORE_CANDIDATE", "STRONG_OBSERVE", "CANDIDATE_POOL", "WAIT_FOR_BUY_POINT", "DATA_GATED", "RESEARCH_GATED", "LEAD_TRACKING", "ELIMINATE", "OBSERVE_ONLY"}
WATCHLIST_BUCKETS = {"CORE_CANDIDATE", "STRONG_OBSERVE", "CANDIDATE_POOL", "DATA_GATED", "RESEARCH_GATED", "LEAD_TRACKING", "ELIMINATE", "OBSERVE_ONLY"}
REQUIRED_ROOT = {"market_route", "data_quality", "data_acquisition", "decision_matrix", "rating", "rating_cap", "evidence", "falsification", "action", "uncertainty"}
REQUIRED_DATA_QUALITY = {"market_resolution", "current_price", "adjusted_history", "valuation_inputs", "financials", "filings"}
REQUIRED_DATA_ACQUISITION_STATUS = {"current_quote", "price_history_adjusted", "valuation_inputs", "financials", "filings_announcements"}
REQUIRED_DATA_GAP = {"dataset", "status", "gap_type", "decision_impact", "rating_impact", "next_action"}
REQUIRED_RESEARCH_DEBT = {"dataset", "priority", "gap_type", "decision_impact", "next_action"}
REQUIRED_MANUAL_TASK = {"dataset", "priority", "target_source", "objective"}
REQUIRED_DECISION_MATRIX = {"thesis_quality_score", "evidence_confidence_score", "market_payoff_score", "action_readiness", "candidate_priority_score", "watchlist_bucket"}
REQUIRED_UNCERTAINTY = {"confirmed", "inferred", "missing", "downgrade_trigger"}


def _load_json(path: str) -> dict[str, Any]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("output contract JSON must be an object")
    return data


def _missing(obj: Mapping[str, Any], required: set[str], label: str) -> list[str]:
    return [f"{label} missing required key: {key}" for key in sorted(required) if key not in obj]


def _rating_above(value: str, cap: str) -> bool:
    return RATING_ORDER.get(value, -1) > RATING_ORDER[cap]


def _growth_order(value: str) -> int:
    return GROWTH_ORDER.get(value, -1)


def _has_h4_h5_valuation_gap(implied: str, supported: str, h4_h5_evidence_bar_met: Any = None) -> bool:
    implied_order = _growth_order(implied)
    supported_order = _growth_order(supported)
    if implied_order < 4:
        return False
    if supported_order < implied_order:
        return True
    return h4_h5_evidence_bar_met is False


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


def _score_0_100(value: Any, label: str, errors: list[str]) -> None:
    try:
        number = float(value)
    except Exception:
        errors.append(f"{label} must be numeric 0-100")
        return
    if number < 0 or number > 100:
        errors.append(f"{label} must be in 0-100, got {number}")


def validate_contract(data: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    errors.extend(_missing(data, REQUIRED_ROOT, "contract"))

    route = _as_mapping(data.get("market_route", {}), "market_route", errors)
    market = str(route.get("market", "UNKNOWN"))
    if market not in MARKETS:
        errors.append(f"market_route.market must be one of {sorted(MARKETS)}, got {market!r}")
    if not isinstance(route.get("primary_disclosure_sources", []), list):
        errors.append("market_route.primary_disclosure_sources must be an array")
    if not isinstance(route.get("forbidden_sources", []), list):
        errors.append("market_route.forbidden_sources must be an array")
    primary_sources = flatten_sources(route.get("primary_disclosure_sources", []) if isinstance(route.get("primary_disclosure_sources", []), list) else [])
    for mismatch in mismatched_sources_for_market(market, primary_sources):
        errors.append(
            "market_route.primary_disclosure_sources contains wrong-market source "
            f"{mismatch.text!r} for {mismatch.expected_market}; source belongs to {mismatch.source_market}"
        )

    data_quality = _as_mapping(data.get("data_quality", {}), "data_quality", errors)
    errors.extend(_missing(data_quality, REQUIRED_DATA_QUALITY, "data_quality"))
    statuses: dict[str, str] = {}
    for key in sorted(REQUIRED_DATA_QUALITY | {"supply_chain_evidence"}):
        if key not in data_quality:
            continue
        status = str(data_quality.get(key))
        statuses[key] = status
        if status not in STATUSES:
            errors.append(f"data_quality.{key} must be one of {sorted(STATUSES)}, got {status!r}")

    rating = str(data.get("rating", ""))
    rating_cap = str(data.get("rating_cap", ""))
    if rating not in RATINGS:
        errors.append(f"rating must be one of {sorted(RATINGS)}, got {rating!r}")
    if rating_cap not in RATINGS:
        errors.append(f"rating_cap must be one of {sorted(RATINGS)}, got {rating_cap!r}")
    if rating in RATINGS and rating_cap in RATINGS and _rating_above(rating, rating_cap):
        errors.append(f"rating {rating} exceeds rating_cap {rating_cap}")

    if market in {"OTHER", "UNKNOWN"} and rating_cap != "OBSERVE_ONLY":
        errors.append("OTHER/UNKNOWN market requires rating_cap OBSERVE_ONLY")

    data_acquisition = _as_mapping(data.get("data_acquisition", {}), "data_acquisition", errors)
    errors.extend(_missing(data_acquisition, {"status_by_dataset", "data_gaps", "research_debt", "manual_retrieval_tasks"}, "data_acquisition"))
    status_by_dataset = _as_mapping(data_acquisition.get("status_by_dataset", {}), "data_acquisition.status_by_dataset", errors)
    errors.extend(_missing(status_by_dataset, REQUIRED_DATA_ACQUISITION_STATUS, "data_acquisition.status_by_dataset"))
    for key in sorted(REQUIRED_DATA_ACQUISITION_STATUS):
        if key not in status_by_dataset:
            continue
        status = str(status_by_dataset.get(key))
        if status not in STATUSES:
            errors.append(f"data_acquisition.status_by_dataset.{key} must be one of {sorted(STATUSES)}, got {status!r}")

    data_gaps = _as_list(data_acquisition.get("data_gaps", []), "data_acquisition.data_gaps", errors)
    material_gap_count = 0
    material_gap_datasets: set[str] = set()
    for idx, item in enumerate(data_gaps):
        label = f"data_acquisition.data_gaps[{idx}]"
        gap = _as_mapping(item, label, errors)
        errors.extend(_missing(gap, REQUIRED_DATA_GAP, label))
        if gap.get("status") and gap.get("status") not in STATUSES:
            errors.append(f"{label}.status must be one of {sorted(STATUSES)}")
        if gap.get("gap_type") and gap.get("gap_type") not in GAP_TYPES:
            errors.append(f"{label}.gap_type must be one of {sorted(GAP_TYPES)}")
        if gap.get("decision_impact") and gap.get("decision_impact") not in DECISION_IMPACTS:
            errors.append(f"{label}.decision_impact must be one of {sorted(DECISION_IMPACTS)}")
        if gap.get("decision_impact") in {"THESIS_IMPACT", "EVIDENCE_IMPACT", "ACTION_IMPACT", "VALUATION_IMPACT"}:
            material_gap_count += 1
            if str(gap.get("dataset", "")).strip():
                material_gap_datasets.add(str(gap.get("dataset")))

    research_debt = _as_list(data_acquisition.get("research_debt", []), "data_acquisition.research_debt", errors)
    has_critical_debt = False
    debt_datasets: set[str] = set()
    for idx, item in enumerate(research_debt):
        label = f"data_acquisition.research_debt[{idx}]"
        debt = _as_mapping(item, label, errors)
        errors.extend(_missing(debt, REQUIRED_RESEARCH_DEBT, label))
        priority = str(debt.get("priority") or "")
        if priority and priority not in {"critical", "high", "medium", "low"}:
            errors.append(f"{label}.priority must be one of ['critical', 'high', 'medium', 'low']")
        if priority == "critical":
            has_critical_debt = True
        if str(debt.get("dataset", "")).strip():
            debt_datasets.add(str(debt.get("dataset")))
        if debt.get("gap_type") and debt.get("gap_type") not in GAP_TYPES:
            errors.append(f"{label}.gap_type must be one of {sorted(GAP_TYPES)}")
        if debt.get("decision_impact") and debt.get("decision_impact") not in DECISION_IMPACTS:
            errors.append(f"{label}.decision_impact must be one of {sorted(DECISION_IMPACTS)}")

    manual_tasks = _as_list(data_acquisition.get("manual_retrieval_tasks", []), "data_acquisition.manual_retrieval_tasks", errors)
    manual_task_datasets: set[str] = set()
    for idx, item in enumerate(manual_tasks):
        label = f"data_acquisition.manual_retrieval_tasks[{idx}]"
        task = _as_mapping(item, label, errors)
        errors.extend(_missing(task, REQUIRED_MANUAL_TASK, label))
        priority = str(task.get("priority") or "")
        if priority and priority not in {"critical", "high", "medium", "low"}:
            errors.append(f"{label}.priority must be one of ['critical', 'high', 'medium', 'low']")
        if str(task.get("dataset", "")).strip():
            manual_task_datasets.add(str(task.get("dataset")))
    missing_debt = sorted(material_gap_datasets - debt_datasets)
    if missing_debt:
        errors.append(f"material data gaps require research debt for datasets: {missing_debt}")
    missing_manual_tasks = sorted(debt_datasets - manual_task_datasets)
    if missing_manual_tasks:
        errors.append(f"research_debt requires manual retrieval tasks for datasets: {missing_manual_tasks}")

    decision_matrix = _as_mapping(data.get("decision_matrix", {}), "decision_matrix", errors)
    errors.extend(_missing(decision_matrix, REQUIRED_DECISION_MATRIX, "decision_matrix"))
    for key in ["thesis_quality_score", "evidence_confidence_score", "market_payoff_score", "candidate_priority_score"]:
        if key in decision_matrix:
            _score_0_100(decision_matrix.get(key), f"decision_matrix.{key}", errors)
    action_readiness = str(decision_matrix.get("action_readiness", ""))
    watchlist_bucket = str(decision_matrix.get("watchlist_bucket", ""))
    if action_readiness and action_readiness not in ACTION_READINESS:
        errors.append(f"decision_matrix.action_readiness must be one of {sorted(ACTION_READINESS)}")
    if watchlist_bucket and watchlist_bucket not in WATCHLIST_BUCKETS:
        errors.append(f"decision_matrix.watchlist_bucket must be one of {sorted(WATCHLIST_BUCKETS)}")
    action = str(data.get("action", ""))

    if statuses.get("current_price") in UNAVAILABLE_STATUSES and rating_cap in RATINGS and _rating_above(rating_cap, "B"):
        errors.append("current_price unavailable requires rating_cap B or lower")
    if statuses.get("adjusted_history") in UNAVAILABLE_STATUSES and rating_cap in RATINGS and _rating_above(rating_cap, "B"):
        errors.append("adjusted_history unavailable requires rating_cap B or lower")
    if statuses.get("valuation_inputs") in UNAVAILABLE_STATUSES:
        if action in {"核心候选", "小仓试错"}:
            errors.append("valuation_inputs unavailable cannot use core/test-position action")
        if action_readiness == "CORE_CANDIDATE":
            errors.append("valuation_inputs unavailable cannot use CORE_CANDIDATE action readiness")
    if statuses.get("financials") in UNAVAILABLE_STATUSES:
        if rating in RATINGS and _rating_above(rating, "B"):
            errors.append("financials unavailable cannot support S/A rating")
        if rating_cap in RATINGS and _rating_above(rating_cap, "B"):
            errors.append("financials unavailable requires rating_cap B or lower")
    if statuses.get("filings") in UNAVAILABLE_STATUSES:
        if rating in RATINGS and _rating_above(rating, "B"):
            errors.append("filings unavailable cannot support S/A rating")
        if rating_cap in RATINGS and _rating_above(rating_cap, "B"):
            errors.append("filings unavailable requires rating_cap B or lower")
    if has_critical_debt and rating in RATINGS and _rating_above(rating, "B"):
        errors.append("critical research_debt cannot support S/A rating")
    if has_critical_debt and action in {"核心候选", "小仓试错"}:
        errors.append("critical research_debt cannot use core/test-position action")
    if has_critical_debt and action_readiness == "CORE_CANDIDATE":
        errors.append("critical research_debt cannot use CORE_CANDIDATE action readiness")
    if material_gap_count and watchlist_bucket == "CORE_CANDIDATE":
        errors.append("material data gaps cannot use CORE_CANDIDATE watchlist bucket")

    evidence = _as_list(data.get("evidence", []), "evidence", errors)
    if not evidence:
        errors.append("evidence must contain at least one item")
    evidence_sources: list[str] = []
    for idx, item in enumerate(evidence):
        label = f"evidence[{idx}]"
        ev = _as_mapping(item, label, errors)
        for key in ["claim", "source", "source_level", "confidence"]:
            if not str(ev.get(key, "")).strip():
                errors.append(f"{label}.{key} must not be empty")
        if ev.get("source_level") and ev.get("source_level") not in SOURCE_LEVELS:
            errors.append(f"{label}.source_level must be one of {sorted(SOURCE_LEVELS)}")
        if ev.get("confidence") and ev.get("confidence") not in CONFIDENCE:
            errors.append(f"{label}.confidence must be one of {sorted(CONFIDENCE)}")
        if str(ev.get("source", "")).strip():
            evidence_sources.append(str(ev.get("source", "")))
    for mismatch in mismatched_sources_for_market(market, evidence_sources):
        errors.append(
            f"evidence source {mismatch.text!r} is a {mismatch.source_market} source, "
            f"not valid primary evidence for {mismatch.expected_market}"
        )

    falsification = _as_list(data.get("falsification", []), "falsification", errors)
    if not any(str(item).strip() for item in falsification):
        errors.append("falsification must include at least one concrete trigger")

    action = str(data.get("action", ""))
    if action not in ACTIONS:
        errors.append(f"action must be one of {sorted(ACTIONS)}, got {action!r}")

    uncertainty = _as_mapping(data.get("uncertainty", {}), "uncertainty", errors)
    errors.extend(_missing(uncertainty, REQUIRED_UNCERTAINTY, "uncertainty"))
    for key in REQUIRED_UNCERTAINTY:
        if key in uncertainty and not str(uncertainty.get(key, "")).strip():
            errors.append(f"uncertainty.{key} must not be empty")

    growth = data.get("growth_hypothesis")
    if growth is not None:
        growth_map = _as_mapping(growth, "growth_hypothesis", errors)
        implied = str(growth_map.get("market_implied", "UNKNOWN"))
        supported = str(growth_map.get("evidence_supported", "UNKNOWN"))
        if implied not in GROWTH:
            errors.append(f"growth_hypothesis.market_implied must be one of {sorted(GROWTH)}")
        if supported not in GROWTH:
            errors.append(f"growth_hypothesis.evidence_supported must be one of {sorted(GROWTH)}")
        if _has_h4_h5_valuation_gap(implied, supported, growth_map.get("h4_h5_evidence_bar_met")):
            if rating_cap in RATINGS and _rating_above(rating_cap, "B"):
                errors.append("H4/H5 market-implied growth with weaker evidence requires rating_cap B or lower")
            if action in {"核心候选", "小仓试错"}:
                errors.append("H4/H5 market-implied growth with weaker evidence cannot use core/test-position action")
        if growth_map.get("h4_h5_evidence_bar_met") is False and rating_cap in RATINGS and _rating_above(rating_cap, "B"):
            errors.append("h4_h5_evidence_bar_met=false requires rating_cap B or lower")

    layer_rank = data.get("value_chain_layer_rank")
    if layer_rank is not None:
        layers = _as_list(layer_rank, "value_chain_layer_rank", errors)
        for idx, item in enumerate(layers):
            layer = _as_mapping(item, f"value_chain_layer_rank[{idx}]", errors)
            if not str(layer.get("layer", "")).strip():
                errors.append(f"value_chain_layer_rank[{idx}].layer must not be empty")
            if not str(layer.get("bottleneck_reason", "")).strip():
                errors.append(f"value_chain_layer_rank[{idx}].bottleneck_reason must not be empty")

    if warnings and not errors:
        return {"ok": True, "warnings": warnings}
    if errors:
        raise ValueError("; ".join(errors))
    return {"ok": True, "warnings": warnings}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate structured Serenity + Chan output-contract JSON")
    parser.add_argument("contract", help="Output contract JSON path or '-' for stdin")
    parser.add_argument("--json", action="store_true", help="emit machine-readable result")
    args = parser.parse_args(argv)

    try:
        result = validate_contract(_load_json(args.contract))
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"FAILED: {args.contract}")
            print(f"- ERROR: {exc}")
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"OK: {args.contract}")
        for warning in result.get("warnings", []):
            print(f"- WARNING: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
