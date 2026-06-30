#!/usr/bin/env python3
"""Build a Laplace strategy input from a Serenity comparison report."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


CONTRACT_TYPE: str = "serenity_laplace_strategy_input"
SCHEMA_VERSION: str = "1.0"
STRATEGY_READY_AI_STATUSES: set[str] = {"COMPLETED", "FAILED_INSUFFICIENT_EVIDENCE", "CONFLICT_WITH_DATA"}


def _load_json(path: Path) -> Mapping[str, Any]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _by_symbol(rows: Any) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in _as_list(rows):
        if not isinstance(row, Mapping):
            continue
        symbol: str = str(row.get("symbol") or "").strip()
        if symbol:
            result[symbol] = row
    return result


def _safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _research_debt_count(symbol: str, rows: Sequence[Any]) -> int:
    count: int = 0
    for row in rows:
        if isinstance(row, Mapping) and _text(row.get("symbol")) == symbol:
            count += 1
    return count


def _ai_review_statuses(report: Mapping[str, Any]) -> list[str]:
    statuses: list[str] = []
    for row in _as_list(report.get("ai_review_status_matrix")):
        if not isinstance(row, Mapping):
            continue
        status: str = _text(row.get("ai_review_status"))
        if status:
            statuses.append(status)
    return statuses


def _ensure_strategy_ready(report: Mapping[str, Any], source_report_path: Path) -> dict[str, Any]:
    source_name: str = source_report_path.name
    errors: list[str] = []
    if source_name in {"comparison_baseline.json", "comparison_internal_baseline.json", "comparison_diagnostic_baseline.json"}:
        errors.append(f"{source_name} cannot be used for Laplace strategy input")
    readiness: Mapping[str, Any] = _as_mapping(report.get("report_readiness"))
    if readiness.get("stage") != "FINAL_REPORT_READY":
        errors.append("report_readiness.stage must be FINAL_REPORT_READY before strategy handoff")
    if readiness.get("delivery_allowed") is not True:
        errors.append("report_readiness.delivery_allowed must be true before strategy handoff")
    statuses: list[str] = _ai_review_statuses(report)
    if not statuses:
        errors.append("ai_review_status_matrix must be present before strategy handoff")
    blocked_statuses: list[str] = sorted({status for status in statuses if status not in STRATEGY_READY_AI_STATUSES})
    if blocked_statuses:
        errors.append(f"AI research is not strategy-ready: {blocked_statuses}")
    final_decision: Mapping[str, Any] = _as_mapping(report.get("final_decision"))
    decision_mode: str = _text(final_decision.get("decision_mode"))
    if not decision_mode:
        errors.append("final_decision.decision_mode is required before strategy handoff")
    if errors:
        raise ValueError("; ".join(errors))
    return {
        "status": "READY",
        "source_report_type": "completed_ai_research",
        "report_readiness": dict(readiness),
        "ai_review_statuses": sorted(set(statuses)),
        "policy": "Strategy input is built only after every candidate has a completed AI overlay or a validated AI outcome.",
    }


def _candidate_name(symbol: str, candidates: Mapping[str, Mapping[str, Any]]) -> str:
    candidate: Mapping[str, Any] = candidates.get(symbol, {})
    return _text(candidate.get("name"))


def _gate(ranking: Mapping[str, Any]) -> Mapping[str, Any]:
    gate: Any = ranking.get("action_gate")
    return gate if isinstance(gate, Mapping) else {}


def _candidate_row(
    ranking: Mapping[str, Any],
    *,
    candidates: Mapping[str, Mapping[str, Any]],
    data_rows: Mapping[str, Mapping[str, Any]],
    layer_rows: Mapping[str, Mapping[str, Any]],
    ai_rows: Mapping[str, Mapping[str, Any]],
    financial_rows: Mapping[str, Mapping[str, Any]],
    customer_rows: Mapping[str, Mapping[str, Any]],
    valuation_rows: Mapping[str, Mapping[str, Any]],
    growth_rows: Mapping[str, Mapping[str, Any]],
    technical_rows: Mapping[str, Mapping[str, Any]],
    readiness_rows: Mapping[str, Mapping[str, Any]],
    research_debt_rows: Sequence[Any],
) -> dict[str, Any]:
    symbol: str = _text(ranking.get("symbol"))
    candidate: Mapping[str, Any] = candidates.get(symbol, {})
    data: Mapping[str, Any] = data_rows.get(symbol, {})
    layer: Mapping[str, Any] = layer_rows.get(symbol, {})
    ai: Mapping[str, Any] = ai_rows.get(symbol, {})
    financial: Mapping[str, Any] = financial_rows.get(symbol, {})
    customer: Mapping[str, Any] = customer_rows.get(symbol, {})
    valuation: Mapping[str, Any] = valuation_rows.get(symbol, {})
    growth: Mapping[str, Any] = growth_rows.get(symbol, {})
    technical: Mapping[str, Any] = technical_rows.get(symbol, {})
    readiness: Mapping[str, Any] = readiness_rows.get(symbol, {})
    gate: Mapping[str, Any] = _gate(ranking)
    return {
        "symbol": symbol,
        "name": _candidate_name(symbol, candidates),
        "market": _text(candidate.get("market")),
        "currency": _text(candidate.get("currency")),
        "rating_cap": _text(candidate.get("rating_cap") or readiness.get("data_evidence_cap")),
        "research_priority_score": _safe_float(ranking.get("research_priority_score")),
        "action_priority_score": _safe_float(ranking.get("action_priority_score")),
        "priority_score": _safe_float(ranking.get("priority_score")),
        "action_readiness": _text(ranking.get("action_readiness")),
        "primary_gate": _text(gate.get("primary_gate")),
        "primary_gate_class": _text(gate.get("primary_gate_class")),
        "primary_gate_reason": _text(gate.get("reason")),
        "data_evidence_cap": _text(readiness.get("data_evidence_cap")),
        "fetch_status": _text(readiness.get("fetch_status")),
        "research_readiness": _text(readiness.get("research_readiness")),
        "ai_review_status": _text(ai.get("ai_review_status")),
        "layer": _text(layer.get("layer")),
        "bottleneck_reason": _text(layer.get("bottleneck_reason")),
        "revenue_transmission": _text(layer.get("revenue_transmission")),
        "layer_score": _safe_float(layer.get("layer_score")),
        "company_fit": _safe_float(layer.get("company_fit")),
        "financial_quality": dict(financial),
        "customer_order_capacity_evidence": dict(customer),
        "valuation": {
            "regular_market_price": _safe_float(valuation.get("regular_market_price")),
            "total_market_cap": _safe_float(valuation.get("total_market_cap")),
            "currency": _text(valuation.get("currency")),
            "valuation_stage": _text(valuation.get("valuation_stage")),
            "valuation_confidence": _text(valuation.get("valuation_confidence")),
            "verification_needed": bool(valuation.get("verification_needed")),
        },
        "market_implied_growth": _text(growth.get("market_implied_growth") or "UNKNOWN"),
        "evidence_supported_growth": _text(growth.get("evidence_supported_growth") or "UNKNOWN"),
        "growth_gap": _text(growth.get("gap")),
        "required_next_evidence": _text(growth.get("required_next_evidence")),
        "technical_timing": dict(technical),
        "data_acquisition": dict(data),
        "research_debt_count": _research_debt_count(symbol, research_debt_rows),
    }


def _forecast_variables(report: Mapping[str, Any]) -> list[dict[str, str]]:
    final_decision: Mapping[str, Any] = _as_mapping(report.get("final_decision"))
    ranking_validity: Mapping[str, Any] = _as_mapping(final_decision.get("ranking_validity"))
    coherence: Mapping[str, Any] = _as_mapping(report.get("candidate_pool_semantic_coherence"))
    return [
        {
            "variable": "候选池语义一致性",
            "role": "veto",
            "direction": _text(coherence.get("status") or "UNKNOWN"),
            "observability": "high",
            "change_speed": "medium",
            "dominant_lens": "operations",
            "confidence": "high",
            "why": "候选池是否同层决定排序能否转化为正式优先级或组合动作。",
        },
        {
            "variable": "排序有效性",
            "role": "veto",
            "direction": _text(ranking_validity.get("status") or "UNKNOWN"),
            "observability": "high",
            "change_speed": "fast",
            "dominant_lens": "evidence",
            "confidence": "high",
            "why": "数据消费、研究债务和候选一致性共同决定报告能否进入决策级输出。",
        },
        {
            "variable": "研究债务密度",
            "role": "bottleneck",
            "direction": "higher debt weakens action readiness",
            "observability": "high",
            "change_speed": "medium",
            "dominant_lens": "evidence",
            "confidence": "high",
            "why": "高优先级研究债务会压低行动状态，并决定下一步补证顺序。",
        },
        {
            "variable": "市场隐含增长与证据支持增长差距",
            "role": "tripwire",
            "direction": "market ahead of evidence is negative for action",
            "observability": "medium",
            "change_speed": "fast",
            "dominant_lens": "economics",
            "confidence": "medium",
            "why": "估值预期高于证据支持增长时，主题热度会转化为赔率和回撤风险。",
        },
        {
            "variable": "技术位置与行动门控",
            "role": "timing",
            "direction": "buy point gates control execution timing",
            "observability": "medium",
            "change_speed": "fast",
            "dominant_lens": "market behavior",
            "confidence": "medium",
            "why": "基本面候选仍需通过价格结构、趋势健康和买点纪律转成行动。",
        },
    ]


def _strategy_questions(theme: str) -> list[str]:
    subject: str = theme or "当前候选池"
    return [
        f"{subject} 在当前证据和估值约束下应该进入观察、等待、试错还是回避？",
        "哪些候选的研究优先级可以转化为行动优先级，哪些只能保留研究跟踪？",
        "30/90/180 天内哪些数据、价格结构或公告会改变判断？",
        "哪些反证会推翻当前主题或候选排序？",
        "如果需要组合化处理，核心、卫星、现金和剔除桶如何划分？",
    ]


def infer_geography(report: Mapping[str, Any]) -> str:
    markets: set[str] = {
        _text(row.get("market"))
        for row in _as_list(report.get("candidates"))
        if isinstance(row, Mapping) and _text(row.get("market"))
    }
    if not markets:
        return "cross-market equities"
    if markets == {"CN_A"}:
        return "China A-share"
    if markets == {"US"}:
        return "US equities"
    if markets == {"HK"}:
        return "Hong Kong equities"
    return "cross-market equities: " + ", ".join(sorted(markets))


def build_strategy_input(
    report: Mapping[str, Any],
    *,
    source_report_path: Path,
    theme: str,
    horizon: str,
    geography: str,
    decision_use: str,
    default_profile: str,
) -> dict[str, Any]:
    readiness: dict[str, Any] = _ensure_strategy_ready(report, source_report_path)
    comparison_scope: Mapping[str, Any] = _as_mapping(report.get("comparison_scope"))
    final_decision: Mapping[str, Any] = _as_mapping(report.get("final_decision"))
    ranking_validity: Mapping[str, Any] = _as_mapping(final_decision.get("ranking_validity"))
    coherence: Mapping[str, Any] = _as_mapping(report.get("candidate_pool_semantic_coherence"))
    candidate_rows: Mapping[str, Mapping[str, Any]] = _by_symbol(report.get("candidates"))
    data_rows: Mapping[str, Mapping[str, Any]] = _by_symbol(report.get("data_acquisition_summary"))
    layer_rows: Mapping[str, Mapping[str, Any]] = _by_symbol(report.get("serenity_layer_matrix"))
    ai_rows: Mapping[str, Mapping[str, Any]] = _by_symbol(report.get("ai_review_status_matrix"))
    financial_rows: Mapping[str, Mapping[str, Any]] = _by_symbol(report.get("financial_quality_matrix"))
    customer_rows: Mapping[str, Mapping[str, Any]] = _by_symbol(report.get("customer_evidence_matrix"))
    valuation_rows: Mapping[str, Mapping[str, Any]] = _by_symbol(report.get("valuation_input_matrix"))
    growth_rows: Mapping[str, Mapping[str, Any]] = _by_symbol(report.get("growth_hypothesis_matrix"))
    technical_rows: Mapping[str, Mapping[str, Any]] = _by_symbol(report.get("technical_timing_matrix"))
    readiness_rows: Mapping[str, Mapping[str, Any]] = _by_symbol(report.get("readiness_matrix"))
    research_debt_rows: list[Any] = _as_list(report.get("research_debt"))
    ranking_rows: list[Any] = _as_list(report.get("candidate_priority_ranking"))
    resolved_geography: str = geography or infer_geography(report)
    candidates: list[dict[str, Any]] = [
        _candidate_row(
            row,
            candidates=candidate_rows,
            data_rows=data_rows,
            layer_rows=layer_rows,
            ai_rows=ai_rows,
            financial_rows=financial_rows,
            customer_rows=customer_rows,
            valuation_rows=valuation_rows,
            growth_rows=growth_rows,
            technical_rows=technical_rows,
            readiness_rows=readiness_rows,
            research_debt_rows=research_debt_rows,
        )
        for row in ranking_rows
        if isinstance(row, Mapping)
    ]
    object_name: str = theme or _text(comparison_scope.get("basis") or "Serenity candidate comparison")
    return {
        "contract_type": CONTRACT_TYPE,
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_report_path": str(source_report_path.resolve()),
        "strategy_input_ready": readiness,
        "companion_skill": {
            "name": "laplace-forecast",
            "path": "companion-skills/laplace-forecast",
            "entrypoint": "companion-skills/laplace-forecast/SKILL.md",
        },
        "decision_context": {
            "object": object_name,
            "horizon": horizon,
            "geography": resolved_geography,
            "decision_use": decision_use,
            "default_profile": default_profile,
        },
        "comparison_summary": {
            "candidate_count": _safe_int(comparison_scope.get("candidate_count") or len(candidates)),
            "as_of": _text(comparison_scope.get("as_of")),
            "semantic_coherence": _text(coherence.get("status") or "UNREVIEWED"),
            "semantic_coherence_reason": _text(coherence.get("reason")),
            "decision_mode": _text(final_decision.get("decision_mode")),
            "ranking_validity": _text(ranking_validity.get("status")),
            "score_gap_to_runner_up": _safe_float(final_decision.get("score_gap_to_runner_up")),
        },
        "observed_facts": {
            "candidates": candidates,
            "research_debt": research_debt_rows,
            "readiness_matrix": _as_list(report.get("readiness_matrix")),
            "research_debt_runbook": _as_list(report.get("research_debt_runbook")),
            "data_consumption_audit": _as_list(report.get("data_consumption_audit")),
            "customer_evidence_matrix": _as_list(report.get("customer_evidence_matrix")),
        },
        "forecast_variables": _forecast_variables(report),
        "strategy_questions": _strategy_questions(object_name),
        "laplace_execution": {
            "must_read": [
                "companion-skills/laplace-forecast/SKILL.md",
                "companion-skills/laplace-forecast/references/first-order-lenses.md",
                "companion-skills/laplace-forecast/references/evidence-loop.md",
            ],
            "required_output_sections": [
                "Forecast",
                "Decision",
                "Decision model",
                "Observed",
                "Inferred",
                "Judgment",
                "Dominant variables",
                "Scenarios",
                "Triggers",
                "Invalidation",
                "Next evidence",
                "Action plan",
            ],
            "claim_policy": "Create or update a ledger record when the result is a reusable theme view, watchlist, or medium-term strategy.",
        },
        "ledger_seed": {
            "question": f"{object_name} 在 {horizon} 维度下是否具备决策级机会？",
            "horizon": horizon,
            "object": object_name,
            "geography": resolved_geography,
            "decision_use": decision_use,
            "claims": [],
        },
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Build Laplace strategy input from a Serenity comparison report")
    parser.add_argument("comparison_report", help="completed comparison_final.json with AI overlay/outcome merged")
    parser.add_argument("--theme", default="", help="theme or strategy object")
    parser.add_argument("--horizon", default="3-6个月")
    parser.add_argument("--geography", default="", help="geography; defaults to inferred markets from comparison report")
    parser.add_argument("--decision-use", default="watchlist allocation, action triggers, and invalidation")
    parser.add_argument("--default-profile", default="balanced")
    parser.add_argument("--out", help="write strategy input JSON")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        source_path: Path = Path(args.comparison_report)
        payload: dict[str, Any] = build_strategy_input(
            _load_json(source_path),
            source_report_path=source_path,
            theme=args.theme,
            horizon=args.horizon,
            geography=args.geography,
            decision_use=args.decision_use,
            default_profile=args.default_profile,
        )
        text: str = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
