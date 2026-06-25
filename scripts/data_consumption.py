#!/usr/bin/env python3
"""Audit whether fetched datasets were actually consumed by research matrices."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


AVAILABLE_STATUSES: set[str] = {"OK", "PARTIAL", "STALE"}


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

    if raw_status in AVAILABLE_STATUSES and rows and matrix_status == "DATA_GATED":
        consumption_status = "MISMATCH"
        warnings.append("financials dataset has period rows but financial_quality_matrix did not consume an annual row")
    elif raw_status in AVAILABLE_STATUSES and not rows:
        consumption_status = "DATA_GATED"
    elif matrix_status == "OK" and selected_period:
        consumption_status = "OK" if raw_status == "OK" else "PARTIAL"
        if consumption_status == "PARTIAL":
            warnings.append(f"financials raw_status={raw_status}; consumed annual row keeps ranking partial")
    elif raw_status in {"FAILED", "PENDING", "NOT_REQUESTED", "NOT_APPLICABLE"}:
        consumption_status = "DATA_GATED"
    else:
        consumption_status = "PARTIAL"

    return {
        "symbol": symbol,
        "dataset": "financials",
        "raw_status": raw_status,
        "row_count": len(rows),
        "consumed_by": ["financial_quality_matrix", "growth_hypothesis_matrix"] if matrix_status == "OK" else [],
        "consumption_status": consumption_status,
        "selected_period": selected_period,
        "selection_rule": str(financial_row.get("latest_annual_selection_rule") or ""),
        "warnings": warnings,
    }


def valuation_consumption_audit(
    *,
    symbol: str,
    raw_status: str,
    valuation_payload: Any,
    valuation_row: Mapping[str, Any],
    growth_row: Mapping[str, Any],
) -> dict[str, Any]:
    has_payload: bool = isinstance(valuation_payload, Mapping) and bool(valuation_payload)
    market_cap: Any = valuation_row.get("total_market_cap")
    growth: str = str(growth_row.get("market_implied_growth") or "UNKNOWN")
    warnings: list[str] = []
    consumption_status: str

    if raw_status in AVAILABLE_STATUSES and has_payload and market_cap is not None and growth == "UNKNOWN":
        consumption_status = "MISMATCH"
        warnings.append("valuation inputs are available but growth_hypothesis_matrix did not derive market-implied growth")
    elif raw_status == "OK" and growth != "UNKNOWN":
        consumption_status = "OK"
    elif raw_status in {"FAILED", "PENDING", "NOT_REQUESTED", "NOT_APPLICABLE"}:
        consumption_status = "DATA_GATED"
    else:
        consumption_status = "PARTIAL"

    consumed_by: list[str] = []
    if has_payload:
        consumed_by.append("valuation_input_matrix")
    if growth != "UNKNOWN":
        consumed_by.append("growth_hypothesis_matrix")

    return {
        "symbol": symbol,
        "dataset": "valuation_inputs",
        "raw_status": raw_status,
        "row_count": 1 if has_payload else 0,
        "consumed_by": consumed_by,
        "consumption_status": consumption_status,
        "selected_period": str(valuation_row.get("as_of_date") or ""),
        "selection_rule": str(valuation_row.get("source_basis") or valuation_row.get("market_cap_basis") or ""),
        "warnings": warnings,
    }


def ranking_validity_from_consumption(consumption_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    mismatches: list[str] = [
        f"{row.get('symbol')} {row.get('dataset')}: {', '.join(str(item) for item in row.get('warnings', []) if item)}"
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
        reason = "At least one fetched dataset was not correctly consumed by the comparison matrices."
    elif gated or partial:
        status = "PARTIAL"
        reason = "Ranking can guide research priority, but at least one comparable axis is partially consumed or data-gated."
    else:
        status = "VALID"
        reason = "All audited fetched datasets were consumed by their downstream matrices."
    return {
        "status": status,
        "reason": reason,
        "blocked_by": mismatches,
        "partial_axes": gated + partial,
    }
