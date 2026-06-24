#!/usr/bin/env python3
"""
Serenity + Chan decision scorecard.

The scorecard ranks a stock by investable candidate priority.
It separates thesis quality, evidence confidence, market payoff, timing, and
action readiness so weak evidence cannot be averaged away by attractive themes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

try:
    from data_contracts import DataGapType, DataStatus, Market, RatingCap, stricter_cap
except ModuleNotFoundError:  # pragma: no cover - supports python -m scripts.serenity_chan_scorecard
    from scripts.data_contracts import DataGapType, DataStatus, Market, RatingCap, stricter_cap


class Rating(str, Enum):
    S = "S"
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    OBSERVE_ONLY = "OBSERVE_ONLY"


MODULES = {
    "layer": {
        "weight": 0.30,
        "label": "Layer Quality",
    },
    "company_thesis": {
        "weight": 0.35,
        "label": "Company Thesis",
    },
    "evidence_confidence": {
        "weight": 0.20,
        "label": "Evidence Confidence",
    },
    "risk_controls": {
        "weight": 0.15,
        "label": "Risk Controls",
    },
}

PRIORITY_WEIGHTS = {
    "thesis_quality": 0.40,
    "evidence_confidence": 0.25,
    "market_payoff": 0.25,
    "action_readiness": 0.10,
}

REQUIRED_TOP_LEVEL = {
    "ticker",
    "market",
    "as_of_date",
    "layer",
    "company_thesis",
    "evidence_confidence",
    "market_payoff",
    "technical_timing",
    "risk_controls",
    "data_acquisition",
    "blockers",
}

ALLOWED_MARKETS = {market.value for market in Market}
ALLOWED_BLOCKER_SEVERITIES = {"critical", "major", "minor"}
ALLOWED_BLOCKER_EFFECTS = {"rating_cap_b", "rating_cap_c", "action_block", "priority_haircut", "eliminate"}


def _rating_0_5(value: Any, label: str) -> float:
    try:
        v = float(value)
    except Exception as exc:
        raise ValueError(f"{label} must be numeric 0-5") from exc
    if v < 0 or v > 5:
        raise ValueError(f"{label} must be in 0-5, got {v}")
    return v


def _module_score(module: Mapping[str, Any], label: str) -> Tuple[float, Dict[str, Any]]:
    if not module:
        raise ValueError(f"{label} must be a non-empty object of 0-5 ratings")
    ratings = []
    details = {}
    for k, v in module.items():
        rating = _rating_0_5(v, f"{label}.{k}")
        ratings.append(rating)
        details[k] = rating
    score = sum(ratings) / len(ratings) * 20.0
    return score, {"score": round(score, 2), "average_rating": round(sum(ratings) / len(ratings), 2), "details": details}


def _rating_from_score(score: float) -> Rating:
    if score >= 88:
        return Rating.S
    if score >= 76:
        return Rating.A
    if score >= 64:
        return Rating.B
    if score >= 50:
        return Rating.C
    return Rating.D


def _rating_to_cap(rating: Rating) -> RatingCap:
    return RatingCap(rating.value)


def _cap_to_rating(cap: RatingCap) -> Rating:
    return Rating(cap.value)


def _apply_cap(rating: Rating, cap: RatingCap) -> Rating:
    if rating == Rating.OBSERVE_ONLY or cap == RatingCap.OBSERVE_ONLY:
        return Rating.OBSERVE_ONLY
    capped = stricter_cap(_rating_to_cap(rating), cap)
    return _cap_to_rating(capped)


def _status_score(status: str) -> float:
    return {
        DataStatus.OK.value: 100.0,
        DataStatus.PARTIAL.value: 70.0,
        DataStatus.STALE.value: 55.0,
        DataStatus.PENDING.value: 45.0,
        DataStatus.NOT_REQUESTED.value: 35.0,
        DataStatus.NOT_APPLICABLE.value: 35.0,
        DataStatus.FAILED.value: 25.0,
    }.get(status, 25.0)


def _data_readiness(data_acquisition: Mapping[str, Any]) -> Dict[str, Any]:
    statuses = data_acquisition.get("status_by_dataset", {})
    if not isinstance(statuses, Mapping):
        raise ValueError("data_acquisition.status_by_dataset must be an object")
    weights = {
        "current_quote": 0.18,
        "price_history_adjusted": 0.22,
        "valuation_inputs": 0.12,
        "financials": 0.26,
        "filings_announcements": 0.22,
    }
    score = 0.0
    details: Dict[str, Any] = {}
    for dataset, weight in weights.items():
        status = str(statuses.get(dataset, DataStatus.NOT_REQUESTED.value))
        if status not in {item.value for item in DataStatus}:
            raise ValueError(f"data_acquisition.status_by_dataset.{dataset} has unknown status {status!r}")
        dataset_score = _status_score(status)
        score += dataset_score * weight
        details[dataset] = {"status": status, "score": dataset_score, "weight": weight}
    return {"score": round(score, 2), "details": details}


def _has_critical_debt(data_acquisition: Mapping[str, Any]) -> bool:
    debt = data_acquisition.get("research_debt", [])
    if not isinstance(debt, list):
        raise ValueError("data_acquisition.research_debt must be an array")
    return any(isinstance(item, Mapping) and str(item.get("priority")) == "critical" for item in debt)


def _validate_research_debt(data_acquisition: Mapping[str, Any]) -> None:
    debt = data_acquisition.get("research_debt")
    if not isinstance(debt, list):
        raise ValueError("data_acquisition.research_debt must be an array")
    allowed_gap_types = {item.value for item in DataGapType}
    for idx, item in enumerate(debt):
        if not isinstance(item, Mapping):
            raise ValueError(f"data_acquisition.research_debt[{idx}] must be an object")
        for key in ["dataset", "priority", "gap_type", "decision_impact", "next_action"]:
            if not str(item.get(key, "")).strip():
                raise ValueError(f"data_acquisition.research_debt[{idx}].{key} must not be empty")
        if item.get("priority") not in {"critical", "high", "medium", "low"}:
            raise ValueError(f"data_acquisition.research_debt[{idx}].priority is unknown: {item.get('priority')!r}")
        if item.get("gap_type") not in allowed_gap_types:
            raise ValueError(f"data_acquisition.research_debt[{idx}].gap_type is unknown: {item.get('gap_type')!r}")


def _validate_gap_debt_links(data_acquisition: Mapping[str, Any]) -> None:
    gaps = data_acquisition.get("data_gaps")
    debt = data_acquisition.get("research_debt")
    manual_tasks = data_acquisition.get("manual_retrieval_tasks")
    if not isinstance(gaps, list) or not isinstance(debt, list):
        return
    if not isinstance(manual_tasks, list):
        raise ValueError("data_acquisition.manual_retrieval_tasks must be an array")
    material_gap_datasets = {
        str(item.get("dataset"))
        for item in gaps
        if isinstance(item, Mapping)
        and item.get("decision_impact") in {"THESIS_IMPACT", "EVIDENCE_IMPACT", "ACTION_IMPACT", "VALUATION_IMPACT"}
    }
    debt_datasets = {
        str(item.get("dataset"))
        for item in debt
        if isinstance(item, Mapping)
    }
    missing_debt = sorted(material_gap_datasets - debt_datasets)
    if missing_debt:
        raise ValueError(f"material data gaps require research debt for datasets: {', '.join(missing_debt)}")
    task_datasets = {
        str(item.get("dataset"))
        for item in manual_tasks
        if isinstance(item, Mapping)
    }
    missing_tasks = sorted(debt_datasets - task_datasets)
    if missing_tasks:
        raise ValueError(f"research debt requires manual retrieval tasks for datasets: {', '.join(missing_tasks)}")


def _gap_controls(data_acquisition: Mapping[str, Any]) -> Dict[str, Any]:
    gaps = data_acquisition.get("data_gaps", [])
    if gaps is None:
        gaps = []
    if not isinstance(gaps, list):
        raise ValueError("data_acquisition.data_gaps must be an array")
    material_gaps: List[Dict[str, Any]] = []
    cap = RatingCap.S
    evidence_multiplier = 1.0
    action_blocked = False
    evidence_blocked = False
    allowed_gap_types = {item.value for item in DataGapType}
    for idx, item in enumerate(gaps):
        if not isinstance(item, Mapping):
            raise ValueError(f"data_acquisition.data_gaps[{idx}] must be an object")
        gap_type = str(item.get("gap_type") or "")
        if gap_type not in allowed_gap_types:
            raise ValueError(f"data_acquisition.data_gaps[{idx}].gap_type unknown: {gap_type!r}")
        impact = str(item.get("decision_impact") or "")
        dataset = str(item.get("dataset") or "")
        if impact in {"EVIDENCE_IMPACT", "ACTION_IMPACT", "THESIS_IMPACT", "VALUATION_IMPACT"}:
            material_gaps.append(dict(item))
        if dataset in {"financials", "filings_announcements"} and gap_type in {
            DataGapType.SCOPE_NOT_REQUESTED.value,
            DataGapType.SOURCE_UNAVAILABLE.value,
            DataGapType.ACCESS_FAILURE.value,
            DataGapType.NOT_MACHINE_READABLE.value,
        }:
            cap = stricter_cap(cap, RatingCap.B)
            evidence_multiplier = min(evidence_multiplier, 0.72)
            evidence_blocked = True
        if dataset in {"current_quote", "price_history_adjusted"} and impact == "ACTION_IMPACT":
            action_blocked = True
            cap = stricter_cap(cap, RatingCap.B)
    return {
        "rating_cap": cap,
        "evidence_multiplier": evidence_multiplier,
        "action_blocked": action_blocked,
        "evidence_blocked": evidence_blocked,
        "material_gaps": material_gaps,
    }


def _blocker_controls(blockers: Sequence[Any]) -> Dict[str, Any]:
    cap = RatingCap.S
    multiplier = 1.0
    action_blocked = False
    eliminate = False
    active: List[Dict[str, Any]] = []
    for idx, item in enumerate(blockers):
        if not isinstance(item, Mapping):
            raise ValueError(f"blockers[{idx}] must be an object")
        name = str(item.get("name") or "").strip()
        severity = str(item.get("severity") or "")
        effect = str(item.get("effect") or "")
        if not name:
            raise ValueError(f"blockers[{idx}].name must not be empty")
        if severity not in ALLOWED_BLOCKER_SEVERITIES:
            raise ValueError(f"blockers[{idx}].severity must be one of {sorted(ALLOWED_BLOCKER_SEVERITIES)}")
        if effect not in ALLOWED_BLOCKER_EFFECTS:
            raise ValueError(f"blockers[{idx}].effect must be one of {sorted(ALLOWED_BLOCKER_EFFECTS)}")
        active.append({"name": name, "severity": severity, "effect": effect})
        if effect == "rating_cap_b":
            cap = stricter_cap(cap, RatingCap.B)
        elif effect == "rating_cap_c":
            cap = stricter_cap(cap, RatingCap.C)
        elif effect == "action_block":
            action_blocked = True
            multiplier = min(multiplier, 0.85)
        elif effect == "priority_haircut":
            multiplier = min(multiplier, 0.90 if severity == "minor" else 0.78)
        elif effect == "eliminate":
            eliminate = True
            cap = stricter_cap(cap, RatingCap.D)
            multiplier = min(multiplier, 0.45)
    return {
        "rating_cap": cap,
        "priority_multiplier": multiplier,
        "action_blocked": action_blocked,
        "eliminate": eliminate,
        "active": active,
    }


def _payoff_multiplier(score: float) -> float:
    if score >= 82:
        return 1.08
    if score >= 68:
        return 1.0
    if score >= 55:
        return 0.86
    return 0.65


def _action_readiness(
    *,
    technical_score: float,
    data_readiness_score: float,
    risk_score: float,
    gap_controls: Mapping[str, Any],
    blocker_controls: Mapping[str, Any],
) -> Dict[str, Any]:
    score = technical_score * 0.45 + data_readiness_score * 0.35 + risk_score * 0.20
    if gap_controls.get("action_blocked") or gap_controls.get("evidence_blocked") or blocker_controls.get("action_blocked"):
        state = "DATA_GATED"
        score = min(score, 58.0 if gap_controls.get("action_blocked") else 62.0)
    elif blocker_controls.get("eliminate"):
        state = "ELIMINATE"
        score = min(score, 40.0)
    elif score >= 82:
        state = "CORE_CANDIDATE"
    elif score >= 68:
        state = "STRONG_OBSERVE"
    elif technical_score < 55:
        state = "WAIT_FOR_BUY_POINT"
    elif score >= 55:
        state = "CANDIDATE_POOL"
    else:
        state = "LEAD_TRACKING"
    return {"score": round(score, 2), "state": state}


def _watchlist_bucket(priority_score: float, rating: Rating, action_state: str) -> str:
    if rating == Rating.OBSERVE_ONLY:
        return "OBSERVE_ONLY"
    if action_state == "ELIMINATE" or rating == Rating.D:
        return "ELIMINATE"
    if action_state == "DATA_GATED":
        return "DATA_GATED"
    if priority_score >= 82 and rating in {Rating.S, Rating.A}:
        return "CORE_CANDIDATE"
    if priority_score >= 70:
        return "STRONG_OBSERVE"
    if priority_score >= 58:
        return "CANDIDATE_POOL"
    return "LEAD_TRACKING"


def validate_input_shape(data: Mapping[str, Any]) -> None:
    missing = sorted(k for k in REQUIRED_TOP_LEVEL if k not in data)
    if missing:
        raise ValueError(f"scorecard missing required top-level keys: {', '.join(missing)}")
    market = str(data.get("market"))
    if market not in ALLOWED_MARKETS:
        raise ValueError(f"market must be one of {sorted(ALLOWED_MARKETS)}, got {market!r}")
    as_of_date = data.get("as_of_date")
    if as_of_date != "YYYY-MM-DD":
        try:
            dt.date.fromisoformat(str(as_of_date))
        except ValueError as exc:
            raise ValueError("as_of_date must be YYYY-MM-DD") from exc
    for module in ["layer", "company_thesis", "evidence_confidence", "market_payoff", "technical_timing", "risk_controls"]:
        value = data.get(module)
        if not isinstance(value, Mapping) or not value:
            raise ValueError(f"{module} must be a non-empty object of 0-5 ratings")
        for key, rating in value.items():
            _rating_0_5(rating, f"{module}.{key}")
    if not isinstance(data.get("data_acquisition"), Mapping):
        raise ValueError("data_acquisition must be an object")
    _data_readiness(data["data_acquisition"])
    _gap_controls(data["data_acquisition"])
    _validate_research_debt(data["data_acquisition"])
    _validate_gap_debt_links(data["data_acquisition"])
    blockers = data.get("blockers")
    if not isinstance(blockers, list):
        raise ValueError("blockers must be an array")
    _blocker_controls(blockers)


def score(data: Dict[str, Any]) -> Dict[str, Any]:
    validate_input_shape(data)
    module_results: Dict[str, Any] = {}
    for module in ["layer", "company_thesis", "evidence_confidence", "market_payoff", "technical_timing", "risk_controls"]:
        module_score, detail = _module_score(data[module], module)
        module_results[module] = detail
        module_results[module]["label"] = MODULES.get(module, {}).get("label", module.replace("_", " ").title())
        module_results[module]["score"] = round(module_score, 2)

    thesis_quality_score = sum(
        module_results[module]["score"] * info["weight"]
        for module, info in MODULES.items()
    )
    data_readiness = _data_readiness(data["data_acquisition"])
    gap_control = _gap_controls(data["data_acquisition"])
    blocker_control = _blocker_controls(data["blockers"])

    evidence_score = module_results["evidence_confidence"]["score"] * 0.70 + data_readiness["score"] * 0.30
    evidence_score *= float(gap_control["evidence_multiplier"])
    if _has_critical_debt(data["data_acquisition"]):
        evidence_score = min(evidence_score, 65.0)

    payoff_score = module_results["market_payoff"]["score"]
    technical_score = module_results["technical_timing"]["score"]
    risk_score = module_results["risk_controls"]["score"]
    action = _action_readiness(
        technical_score=technical_score,
        data_readiness_score=data_readiness["score"],
        risk_score=risk_score,
        gap_controls=gap_control,
        blocker_controls=blocker_control,
    )
    priority_score = (
        thesis_quality_score * PRIORITY_WEIGHTS["thesis_quality"]
        + evidence_score * PRIORITY_WEIGHTS["evidence_confidence"]
        + payoff_score * PRIORITY_WEIGHTS["market_payoff"]
        + action["score"] * PRIORITY_WEIGHTS["action_readiness"]
    )
    priority_score *= _payoff_multiplier(payoff_score)
    priority_score *= float(blocker_control["priority_multiplier"])
    priority_score = max(0.0, min(100.0, priority_score))

    raw_rating = _rating_from_score(thesis_quality_score)
    cap = stricter_cap(gap_control["rating_cap"], blocker_control["rating_cap"])
    if str(data.get("market")) in {Market.OTHER.value, Market.UNKNOWN.value}:
        cap = RatingCap.OBSERVE_ONLY
        action["state"] = "OBSERVE_ONLY"
        action["score"] = min(float(action["score"]), 30.0)
    final_rating = _apply_cap(raw_rating, cap)
    if blocker_control["eliminate"]:
        final_rating = Rating.D

    evidence_rating = _apply_cap(_rating_from_score(evidence_score), cap)
    if cap == RatingCap.B:
        priority_score = min(priority_score, 72.0)
    elif cap == RatingCap.C:
        priority_score = min(priority_score, 55.0)
    elif cap == RatingCap.D:
        priority_score = min(priority_score, 35.0)
    elif cap == RatingCap.OBSERVE_ONLY:
        priority_score = min(priority_score, 30.0)
    if action["state"] == "ELIMINATE":
        watchlist_bucket = "ELIMINATE"
    else:
        watchlist_bucket = _watchlist_bucket(priority_score, final_rating, action["state"])

    verdict_map = {
        Rating.S: "核心长线候选：层级、公司、证据、赔率和风险控制同时达标",
        Rating.A: "强候选：主线成立，等待关键证据、赔率或买点进一步确认",
        Rating.B: "候选池：存在明确价值线索，关键证据或行动条件仍需补齐",
        Rating.C: "线索跟踪：适合继续观察，不适合作为核心候选",
        Rating.D: "剔除或反面样本：风险、证据缺陷或商业逻辑压倒机会",
        Rating.OBSERVE_ONLY: "仅观察：市场解析或关键数据条件未达到研究要求",
    }

    decision_blockers = []
    decision_blockers.extend(gap_control["material_gaps"])
    decision_blockers.extend(blocker_control["active"])

    return {
        "ticker": data.get("ticker", ""),
        "company": data.get("company", ""),
        "market": data.get("market", ""),
        "as_of_date": data.get("as_of_date", ""),
        "score_purpose": "candidate_priority_and_action_readiness",
        "thesis_quality_score": round(thesis_quality_score, 2),
        "evidence_confidence_score": round(evidence_score, 2),
        "market_payoff_score": round(payoff_score, 2),
        "technical_timing_score": round(technical_score, 2),
        "data_readiness_score": data_readiness["score"],
        "action_readiness_score": action["score"],
        "candidate_priority_score": round(priority_score, 2),
        "raw_research_rating": raw_rating.value,
        "rating_cap": cap.value,
        "research_rating": final_rating.value,
        "final_rating": final_rating.value,
        "evidence_confidence_rating": evidence_rating.value,
        "action_readiness": action["state"],
        "watchlist_bucket": watchlist_bucket,
        "verdict": verdict_map[final_rating],
        "module_results": module_results,
        "data_readiness": data_readiness,
        "decision_blockers": decision_blockers,
        "evidence_notes": data.get("evidence_notes", []),
        "falsification_points": [x for x in data.get("falsification_points", []) if str(x).strip()],
    }


def to_markdown(result: Dict[str, Any]) -> str:
    title = f"{result.get('ticker') or 'UNKNOWN'}"
    if result.get("company"):
        title += f" ({result['company']})"
    lines = [
        f"# Serenity + Chan Decision Scorecard: {title}",
        "",
        f"- Market: {result.get('market', '')}",
        f"- As of: {result.get('as_of_date', '')}",
        f"- Score purpose: **{result.get('score_purpose', '')}**",
        f"- Candidate priority score: **{result['candidate_priority_score']} / 100**",
        f"- Research rating: **{result['research_rating']}**",
        f"- Evidence confidence rating: **{result['evidence_confidence_rating']}**",
        f"- Rating cap: **{result['rating_cap']}**",
        f"- Action readiness: **{result['action_readiness']}**",
        f"- Watchlist bucket: **{result['watchlist_bucket']}**",
        f"- Verdict: {result['verdict']}",
        "",
        "## Decision Scores",
        "| Dimension | Score |",
        "|---|---:|",
        f"| Thesis quality | {result['thesis_quality_score']} |",
        f"| Evidence confidence | {result['evidence_confidence_score']} |",
        f"| Market payoff | {result['market_payoff_score']} |",
        f"| Technical timing | {result['technical_timing_score']} |",
        f"| Data readiness | {result['data_readiness_score']} |",
        f"| Action readiness | {result['action_readiness_score']} |",
    ]
    if result.get("decision_blockers"):
        lines.extend(["", "## Decision Blockers"])
        for item in result["decision_blockers"]:
            if not isinstance(item, Mapping):
                continue
            label = item.get("name") or item.get("dataset") or "blocker"
            effect = item.get("effect") or item.get("gap_type") or ""
            lines.append(f"- {label}: {effect}")
    if result.get("falsification_points"):
        lines.extend(["", "## Falsification Points"])
        for point in result["falsification_points"]:
            lines.append(f"- {point}")
    lines.append("")
    return "\n".join(lines)


def load_json(path: str) -> Dict[str, Any]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise SystemExit("Input JSON must be an object.")
    return data


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Score a Serenity + Chan stock thesis")
    parser.add_argument("input", help="Scorecard JSON path or '-' for stdin")
    parser.add_argument("--format", choices=["json", "md", "both"], default="json")
    parser.add_argument("--validate-only", action="store_true", help="validate input shape and exit")
    args = parser.parse_args(argv)
    data = load_json(args.input)
    try:
        validate_input_shape(data)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.validate_only:
        print("OK: scorecard input")
        return 0
    result = score(data)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.format == "md":
        print(to_markdown(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("\n---\n")
        print(to_markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
