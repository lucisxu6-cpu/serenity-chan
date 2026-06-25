#!/usr/bin/env python3
"""Audit whether fetched datasets were actually consumed by research matrices."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence


AVAILABLE_STATUSES: set[str] = {"OK", "PARTIAL", "STALE"}


def _audit_row(
    *,
    symbol: str,
    dataset: str,
    raw_status: str,
    row_count: int,
    consumed_by: Sequence[str],
    consumption_status: str,
    selected_period: str,
    selection_rule: str,
    warnings: Sequence[str],
    reason_code: str = "NONE",
    required_transform: str = "",
    blocked_matrices: Sequence[str] = (),
    ranking_impact: str = "",
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "dataset": dataset,
        "raw_status": raw_status,
        "row_count": row_count,
        "consumed_by": list(consumed_by),
        "consumption_status": consumption_status,
        "selected_period": selected_period,
        "selection_rule": selection_rule,
        "warnings": list(warnings),
        "reason_code": reason_code,
        "required_transform": required_transform,
        "blocked_matrices": list(blocked_matrices),
        "ranking_impact": ranking_impact,
    }


def _periods(payload: Any) -> list[Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    rows: Any = payload.get("periods")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def _status(value: Any) -> str:
    return str(value or "NOT_REQUESTED")


def financial_consumption_audit(
    *,
    symbol: str,
    raw_status: str,
    financial_payload: Any,
    financial_row: Mapping[str, Any],
) -> dict[str, Any]:
    rows: list[Mapping[str, Any]] = _periods(financial_payload)
    matrix_status: str = _status(financial_row.get("status"))
    selected_period: str = str(financial_row.get("latest_annual_period") or "")
    warnings: list[str] = []
    consumption_status: str
    reason_code: str = "NONE"
    blocked_matrices: list[str] = []
    ranking_impact: str = ""

    if raw_status in AVAILABLE_STATUSES and rows and matrix_status == "DATA_GATED":
        consumption_status = "MISMATCH"
        warnings.append("financials dataset has period rows but financial_quality_matrix did not consume an annual row")
        reason_code = "PERIOD_SELECTION_MISMATCH"
        blocked_matrices = ["financial_quality_matrix", "growth_hypothesis_matrix"]
        ranking_impact = "INVALID_UNTIL_CONSUMED"
    elif raw_status in AVAILABLE_STATUSES and not rows:
        consumption_status = "DATA_GATED"
        reason_code = "FINANCIAL_ROWS_NOT_CONSUMED"
        blocked_matrices = ["financial_quality_matrix", "growth_hypothesis_matrix"]
        ranking_impact = "PARTIAL_UNTIL_ROWS_AVAILABLE"
    elif matrix_status == "OK" and selected_period:
        consumption_status = "OK" if raw_status == "OK" else "PARTIAL"
        if consumption_status == "PARTIAL":
            warnings.append(f"financials raw_status={raw_status}; consumed annual row keeps ranking partial")
            source_usage: Any = financial_payload.get("source_usage", {}) if isinstance(financial_payload, Mapping) else {}
            reason_code = (
                "INDUSTRY_PROFILE_PARTIAL"
                if isinstance(source_usage, Mapping)
                and source_usage.get("financial_sector_profile_required")
                and source_usage.get("financial_sector_profile_status") != "OK"
                else "RAW_STATUS_PARTIAL"
            )
            ranking_impact = "PARTIAL_RESEARCH_ONLY"
    elif raw_status in {"FAILED", "PENDING", "NOT_REQUESTED", "NOT_APPLICABLE"}:
        consumption_status = "DATA_GATED"
        reason_code = "SOURCE_DATA_UNAVAILABLE"
        blocked_matrices = ["financial_quality_matrix", "growth_hypothesis_matrix"]
        ranking_impact = "PARTIAL_UNTIL_FETCHED"
    else:
        consumption_status = "PARTIAL"
        reason_code = "FINANCIAL_CONSUMPTION_PARTIAL"
        ranking_impact = "PARTIAL_RESEARCH_ONLY"

    return _audit_row(
        symbol=symbol,
        dataset="financials",
        raw_status=raw_status,
        row_count=len(rows),
        consumed_by=["financial_quality_matrix", "growth_hypothesis_matrix"] if matrix_status == "OK" else [],
        consumption_status=consumption_status,
        selected_period=selected_period,
        selection_rule=str(financial_row.get("latest_annual_selection_rule") or ""),
        warnings=warnings,
        reason_code=reason_code,
        blocked_matrices=blocked_matrices,
        ranking_impact=ranking_impact,
    )


def valuation_consumption_audit(
    *,
    symbol: str,
    raw_status: str,
    valuation_payload: Any,
    valuation_row: Mapping[str, Any],
    growth_row: Mapping[str, Any],
    currency_normalization_row: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    has_payload: bool = isinstance(valuation_payload, Mapping) and bool(valuation_payload)
    market_cap: Any = valuation_row.get("total_market_cap")
    growth: str = str(growth_row.get("market_implied_growth") or "UNKNOWN")
    warnings: list[str] = []
    consumption_status: str
    reason_code: str = "NONE"
    required_transform: str = ""
    blocked_matrices: list[str] = []
    ranking_impact: str = ""
    normalization_status: str = str((currency_normalization_row or {}).get("normalization_status") or "")
    normalization_reason: str = str((currency_normalization_row or {}).get("reason_code") or "")

    if raw_status in AVAILABLE_STATUSES and has_payload and market_cap is not None and growth == "UNKNOWN":
        consumption_status = "MISMATCH"
        if normalization_status in {"FAILED", "DATA_GATED"} or normalization_reason in {"CURRENCY_MISMATCH", "FX_RATE_UNAVAILABLE", "CURRENCY_MISSING"}:
            warnings.append("valuation inputs are available, but cross-currency normalization is required before growth_hypothesis_matrix can derive market-implied growth")
            reason_code = "CURRENCY_MISMATCH"
            required_transform = "FX_NORMALIZATION"
            blocked_matrices = ["growth_hypothesis_matrix"]
            ranking_impact = "INVALID_UNTIL_NORMALIZED"
        else:
            warnings.append("valuation inputs are available but growth_hypothesis_matrix did not derive market-implied growth")
            reason_code = "VALUATION_INPUT_NOT_CONSUMED"
            blocked_matrices = ["growth_hypothesis_matrix"]
            ranking_impact = "INVALID_UNTIL_CONSUMED"
    elif raw_status in AVAILABLE_STATUSES and has_payload and market_cap is None:
        consumption_status = "DATA_GATED"
        warnings.append("valuation inputs payload is present, but total_market_cap is missing")
        reason_code = "MARKET_CAP_MISSING"
        blocked_matrices = ["valuation_input_matrix", "growth_hypothesis_matrix"]
        ranking_impact = "PARTIAL_UNTIL_FETCHED"
    elif raw_status == "OK" and growth != "UNKNOWN":
        consumption_status = "OK"
    elif raw_status in {"FAILED", "PENDING", "NOT_REQUESTED", "NOT_APPLICABLE"}:
        consumption_status = "DATA_GATED"
        reason_code = "SOURCE_DATA_UNAVAILABLE"
        blocked_matrices = ["valuation_input_matrix", "growth_hypothesis_matrix"]
        ranking_impact = "PARTIAL_UNTIL_FETCHED"
    else:
        consumption_status = "PARTIAL"
        reason_code = "VALUATION_CONSUMPTION_PARTIAL"
        ranking_impact = "PARTIAL_RESEARCH_ONLY"

    consumed_by: list[str] = []
    if has_payload:
        consumed_by.append("valuation_input_matrix")
    if growth != "UNKNOWN":
        consumed_by.append("growth_hypothesis_matrix")

    return _audit_row(
        symbol=symbol,
        dataset="valuation_inputs",
        raw_status=raw_status,
        row_count=1 if has_payload else 0,
        consumed_by=consumed_by,
        consumption_status=consumption_status,
        selected_period=str(valuation_row.get("as_of_date") or ""),
        selection_rule=str(valuation_row.get("source_basis") or valuation_row.get("market_cap_basis") or ""),
        warnings=warnings,
        reason_code=reason_code,
        required_transform=required_transform,
        blocked_matrices=blocked_matrices,
        ranking_impact=ranking_impact,
    )


def ranking_validity_from_consumption(consumption_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    mismatches: list[str] = [
        f"{row.get('symbol')} {row.get('dataset')} {row.get('reason_code')}: {', '.join(str(item) for item in row.get('warnings', []) if item)}"
        for row in consumption_rows
        if isinstance(row, Mapping) and row.get("consumption_status") == "MISMATCH"
    ]
    gated: list[str] = [
        f"{row.get('symbol')} {row.get('dataset')}"
        for row in consumption_rows
        if isinstance(row, Mapping) and row.get("consumption_status") == "DATA_GATED"
    ]
    partial: list[str] = [
        f"{row.get('symbol')} {row.get('dataset')}"
        for row in consumption_rows
        if isinstance(row, Mapping) and row.get("consumption_status") == "PARTIAL"
    ]
    status: str
    reason: str
    if mismatches:
        status = "INVALID"
        reason = "至少一个已获取数据集没有被候选对比矩阵正确消费。"
    elif gated or partial:
        status = "PARTIAL"
        reason = "排序可用于研究优先级，但至少一个可比维度仍处于部分消费或数据门控。"
    else:
        status = "VALID"
        reason = "已审计数据集均被下游矩阵正确消费。"
    return {
        "status": status,
        "reason": reason,
        "blocked_by": mismatches,
        "partial_axes": gated + partial,
    }
