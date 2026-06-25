#!/usr/bin/env python3
"""Build a decision-grade candidate comparison report from fetch manifests."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from a_share_capital_actions import analyze_announcements
    from currency_normalizer import build_currency_normalization_row
    from data_consumption import financial_consumption_audit, ranking_validity_from_consumption, valuation_consumption_audit
    from financial_amounts import financial_unit_multiplier, normalize_financial_amount
    from financial_periods import latest_annual as select_latest_annual
    from financial_periods import latest_quarter as select_latest_quarter
    from financial_periods import normalize_financial_period
    from financial_periods import period_year as normalized_period_year
    from fx_provider import currency_code_from_unit, normalize_currency_code
    from report_labels import display_bool, display_label, display_list, display_mapping_pairs
    from technical_health import analyze_price_csv
    from validate_ai_overlay import validate_overlay
except ModuleNotFoundError:  # pragma: no cover - supports python -m scripts.build_comparison_report
    from scripts.a_share_capital_actions import analyze_announcements
    from scripts.currency_normalizer import build_currency_normalization_row
    from scripts.data_consumption import financial_consumption_audit, ranking_validity_from_consumption, valuation_consumption_audit
    from scripts.financial_amounts import financial_unit_multiplier, normalize_financial_amount
    from scripts.financial_periods import latest_annual as select_latest_annual
    from scripts.financial_periods import latest_quarter as select_latest_quarter
    from scripts.financial_periods import normalize_financial_period
    from scripts.financial_periods import period_year as normalized_period_year
    from scripts.fx_provider import currency_code_from_unit, normalize_currency_code
    from scripts.report_labels import display_bool, display_label, display_list, display_mapping_pairs
    from scripts.technical_health import analyze_price_csv
    from scripts.validate_ai_overlay import validate_overlay


RATING_SCORE_LIMIT: dict[str, float] = {"S": 100.0, "A": 84.0, "B": 72.0, "C": 55.0, "D": 35.0, "OBSERVE_ONLY": 25.0}
STATUS_SCORE: dict[str, float] = {"OK": 100.0, "PARTIAL": 70.0, "STALE": 55.0, "PENDING": 45.0, "NOT_REQUESTED": 35.0, "NOT_APPLICABLE": 35.0, "FAILED": 25.0}
CAPITAL_RISK_SCORE: dict[str, float] = {"none": 0.0, "supportive": -2.0, "low": 3.0, "medium": 8.0, "medium_high": 12.0, "high": 18.0}
GROWTH_ORDER: dict[str, int] = {"H0": 0, "H1": 1, "H2": 2, "H3": 3, "H4": 4, "H5": 5, "UNKNOWN": -1}
RATING_CAPS: set[str] = set(RATING_SCORE_LIMIT)
ACTION_READINESS: set[str] = {"CORE_CANDIDATE", "STRONG_OBSERVE", "CANDIDATE_POOL", "WAIT_FOR_BUY_POINT", "DATA_GATED", "RESEARCH_GATED", "LEAD_TRACKING", "ELIMINATE", "OBSERVE_ONLY"}
ACTION_GATE_TYPES: set[str] = {
    "NONE",
    "DATA_GATED",
    "EVIDENCE_GATED",
    "VALUATION_GATED",
    "AI_REVIEW_GATED",
    "BUY_POINT_GATED",
    "CAPITAL_ACTION_GATED",
}
ACTION_GATE_CLASSES: set[str] = {"NONE", "DATA_ACQUISITION", "EVIDENCE_VALIDATION", "RESEARCH_VALIDATION", "ACTION_TIMING"}
DECISION_MODES: set[str] = {"single_candidate", "clear_top_candidate", "tentative_top_candidate", "candidate_cluster", "comparison_not_decision_grade"}
RANKING_VALIDITY_STATUSES: set[str] = {"VALID", "PARTIAL", "INVALID"}
CONSUMPTION_AUDIT_DATASETS: set[str] = {"financials", "valuation_inputs"}
ACTION_BLOCKING_DEBT_DATASETS: set[str] = {
    "current_quote",
    "price_history_adjusted",
    "financials",
    "filings_announcements",
    "valuation",
    "valuation_currency",
    "valuation_growth",
    "share_capital",
    "valuation_inputs",
    "peer_valuation",
    "consensus_estimates",
    "serenity_layer",
}
VALUATION_DATA_DEBT_DATASETS: set[str] = {"valuation", "valuation_currency", "share_capital", "valuation_inputs", "peer_valuation", "consensus_estimates"}
VALUATION_RESEARCH_DEBT_DATASETS: set[str] = {"valuation_growth"}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_manifest(path: Path) -> Mapping[str, Any]:
    loaded: Any = _load_json(path)
    if not isinstance(loaded, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    manifest: Any = dict(loaded)
    manifest["_manifest_path"] = str(path.resolve())
    return manifest


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    return None if value is None else round(value, digits)


def _currency_code(value: Any) -> str:
    return normalize_currency_code(value)


def _currency_code_from_unit(value: Any) -> str:
    return currency_code_from_unit(value)


def _financial_currency(financials: Mapping[str, Any], latest_annual: Mapping[str, Any]) -> str:
    for value in [
        financials.get("currency"),
        financials.get("financial_currency"),
        financials.get("reporting_currency"),
        latest_annual.get("currency"),
        latest_annual.get("financial_currency"),
        latest_annual.get("reporting_currency"),
    ]:
        code: str = _currency_code(value) or _currency_code_from_unit(value)
        if code:
            return code
    for value in [
        latest_annual.get("revenue_unit"),
        latest_annual.get("net_income_unit"),
        financials.get("unit"),
        latest_annual.get("unit"),
    ]:
        code = _currency_code_from_unit(value)
        if code:
            return code
    return ""


def _financial_statement_unit(financials: Mapping[str, Any], latest_annual: Mapping[str, Any]) -> str:
    return str(
        latest_annual.get("unit")
        or financials.get("unit")
        or latest_annual.get("revenue_unit")
        or latest_annual.get("net_income_unit")
        or ""
    )


def _non_empty_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if item is not None and str(item).strip()]


def _display_cell(value: Any, default: str = "无") -> str:
    if value is None:
        return default
    text: str = str(value).strip()
    return text if text else default


def _gate_class_summary(value: Any) -> str:
    return display_mapping_pairs(value, empty="")


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number: Any = float(str(value).replace(",", ""))
    except Exception:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _pct_change(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous in (None, 0):
        return None
    return (current / previous - 1.0) * 100.0


def _ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator * 100.0


def _close_enough(actual: Optional[float], expected: Optional[float], *, tolerance_pct: float = 0.01) -> bool:
    if actual is None or expected is None:
        return False
    return abs(actual - expected) <= max(0.02, abs(expected) * tolerance_pct)


def _result_for_dataset(manifest: Mapping[str, Any], dataset: str) -> Mapping[str, Any]:
    for item in manifest.get("results", []) if isinstance(manifest.get("results"), list) else []:
        if isinstance(item, Mapping) and item.get("dataset") == dataset:
            return item
    return {}


def _path_from_result(manifest: Mapping[str, Any], result: Mapping[str, Any], key: str = "data_path") -> Optional[Path]:
    value: Any = result.get(key)
    if not value:
        return None
    path: Any = Path(str(value))
    if path.is_absolute() and path.exists():
        return path
    manifest_path: Any = manifest.get("_manifest_path")
    candidates: Any = [path]
    if manifest_path:
        manifest_file: Any = Path(str(manifest_path))
        candidates.append(manifest_file.parent / path)
        candidates.extend(parent / path for parent in manifest_file.parents)
    out_dir: Any = manifest.get("out_dir")
    if out_dir:
        candidates.append(Path(str(out_dir)) / path)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _load_result_json(manifest: Mapping[str, Any], dataset: str) -> Any:
    path: Any = _path_from_result(manifest, _result_for_dataset(manifest, dataset))
    if not path:
        return None
    return _load_json(path)


def _valuation_payload(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    acquisition: Any = manifest.get("data_acquisition") if isinstance(manifest.get("data_acquisition"), Mapping) else {}
    status_by_dataset: Any = acquisition.get("status_by_dataset") if isinstance(acquisition.get("status_by_dataset"), Mapping) else {}
    data_quality: Any = manifest.get("data_quality") if isinstance(manifest.get("data_quality"), Mapping) else {}
    valuation_status: Any = str(status_by_dataset.get("valuation_inputs") or data_quality.get("valuation_inputs") or "")
    if valuation_status != "OK":
        return {}
    payload: Any = _load_result_json(manifest, "valuation_inputs")
    if isinstance(payload, Mapping):
        return payload
    return {}


def _valuation_audit_payload(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    payload: Any = _load_result_json(manifest, "valuation_inputs")
    if isinstance(payload, Mapping):
        return payload
    return {}


def _dataset_status(manifest: Mapping[str, Any], dataset: str) -> str:
    acquisition: Any = manifest.get("data_acquisition") if isinstance(manifest.get("data_acquisition"), Mapping) else {}
    status_by_dataset: Any = acquisition.get("status_by_dataset") if isinstance(acquisition.get("status_by_dataset"), Mapping) else {}
    data_quality: Any = manifest.get("data_quality") if isinstance(manifest.get("data_quality"), Mapping) else {}
    result: Any = _result_for_dataset(manifest, dataset)
    return str(status_by_dataset.get(dataset) or data_quality.get(dataset) or result.get("status") or "NOT_REQUESTED")


def _list_field(payload: Mapping[str, Any], key: str) -> list[str]:
    value: Any = payload.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _valuation_input_row(manifest: Mapping[str, Any]) -> dict[str, Any]:
    symbol: Any = _symbol(manifest)
    result: Any = _result_for_dataset(manifest, "valuation_inputs")
    payload: Any = _valuation_audit_payload(manifest)
    status: Any = _dataset_status(manifest, "valuation_inputs")
    source_level: Any = str(result.get("source_level") or payload.get("source_level") or "")
    warnings: Any = _list_field(result, "warnings") + _list_field(payload, "warnings")
    errors: Any = _list_field(result, "errors") + _list_field(payload, "errors")
    verification_needed: Any = bool(
        payload.get("requires_l0_l1_verification")
        or (status != "OK")
        or (source_level and not source_level.startswith(("L0", "L1")))
    )
    source_basis: Any = str(payload.get("source_basis") or "")
    if status != "OK":
        valuation_stage: Any = "unavailable"
        valuation_confidence: Any = "blocked"
    elif source_basis == "quote_derived_preflight" or "quote" in source_basis.lower() or verification_needed:
        valuation_stage = "preflight"
        valuation_confidence = "medium" if source_level.startswith(("L0", "L1")) else "low"
    elif source_level.startswith("L0"):
        valuation_stage = "verified_l0"
        valuation_confidence = "high"
    elif source_level.startswith("L1"):
        valuation_stage = "verified_l1"
        valuation_confidence = "high"
    else:
        valuation_stage = "preflight"
        valuation_confidence = "low"
    return {
        "symbol": symbol,
        "valuation_input_ref": f"valuation_input_matrix:{symbol}",
        "status": status,
        "valuation_stage": valuation_stage,
        "valuation_confidence": valuation_confidence,
        "regular_market_price": _round(_as_float(payload.get("regular_market_price"))),
        "total_shares": _round(_as_float(payload.get("total_shares"))),
        "float_shares": _round(_as_float(payload.get("float_shares"))),
        "total_market_cap": _round(_as_float(payload.get("total_market_cap"))),
        "float_market_cap": _round(_as_float(payload.get("float_market_cap"))),
        "currency": _currency_code(payload.get("currency")),
        "as_of_date": str(payload.get("as_of_date") or result.get("as_of_date") or ""),
        "source_name": str(result.get("source") or payload.get("source") or ""),
        "source_level": source_level,
        "source_basis": source_basis,
        "share_count_basis": str(payload.get("share_count_basis") or ""),
        "market_cap_basis": str(payload.get("market_cap_basis") or ""),
        "verification_needed": verification_needed,
        "warnings": warnings,
        "errors": errors,
    }


def _currency_normalization_row(
    manifest: Mapping[str, Any],
    financial: Mapping[str, Any],
    valuation: Mapping[str, Any],
) -> dict[str, Any]:
    return build_currency_normalization_row(
        symbol=_symbol(manifest),
        valuation_currency=valuation.get("currency"),
        financial_currency=financial.get("financial_currency"),
        total_market_cap=valuation.get("total_market_cap"),
        as_of_date=valuation.get("as_of_date"),
        allow_network=True,
    )


def _symbol(manifest: Mapping[str, Any]) -> str:
    symbol: Any = manifest.get("symbol")
    if isinstance(symbol, Mapping):
        return str(symbol.get("symbol") or symbol.get("input_value") or "")
    return ""


def _market(manifest: Mapping[str, Any]) -> str:
    symbol: Any = manifest.get("symbol")
    if isinstance(symbol, Mapping):
        return str(symbol.get("market") or "UNKNOWN")
    return "UNKNOWN"


def _currency(manifest: Mapping[str, Any]) -> str:
    symbol: Any = manifest.get("symbol")
    if isinstance(symbol, Mapping):
        return str(symbol.get("currency") or "UNKNOWN")
    return "UNKNOWN"


def _candidate_name(manifest: Mapping[str, Any]) -> str:
    quote: Any = _load_result_json(manifest, "current_quote")
    if isinstance(quote, Mapping) and quote.get("name"):
        return str(quote.get("name"))
    filings: Any = _load_result_json(manifest, "filings_announcements")
    if isinstance(filings, Mapping) and filings.get("name"):
        return str(filings.get("name"))
    financials: Any = _load_result_json(manifest, "financials")
    if isinstance(financials, Mapping):
        periods: Any = financials.get("periods")
        if isinstance(periods, list):
            for row in reversed(periods):
                if isinstance(row, Mapping) and row.get("security_name"):
                    return str(row.get("security_name"))
    return ""


def _periods(financials: Any) -> list[Mapping[str, Any]]:
    if not isinstance(financials, Mapping):
        return []
    rows: Any = financials.get("periods", [])
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, Mapping) and row.get("period")]


def _period_year(period: Any) -> Optional[int]:
    return normalized_period_year(period)


def _latest_annual(
    rows: Sequence[Mapping[str, Any]],
    before_year: Optional[int] = None,
    *,
    before_period_end: Optional[str] = None,
    market: str = "",
    source: str = "",
) -> Optional[Mapping[str, Any]]:
    return select_latest_annual(rows, before_fiscal_year=before_year, before_period_end=before_period_end, market=market, source=source)


def _latest_quarter(
    rows: Sequence[Mapping[str, Any]],
    suffix: str,
    before_year: Optional[int] = None,
    *,
    before_period_end: Optional[str] = None,
    market: str = "",
    source: str = "",
) -> Optional[Mapping[str, Any]]:
    quarter: Any = "q1" if suffix in {"03-31", "q1", "Q1"} else str(suffix).lower()
    return select_latest_quarter(rows, quarter, before_fiscal_year=before_year, before_period_end=before_period_end, market=market, source=source)


def _financial_quality(manifest: Mapping[str, Any]) -> dict[str, Any]:
    financials: Any = _load_result_json(manifest, "financials")
    rows: Any = _periods(financials)
    source_level: Any = str((financials or {}).get("source_level") or _result_for_dataset(manifest, "financials").get("source_level") or "")
    source_name: Any = str((financials or {}).get("source") or _result_for_dataset(manifest, "financials").get("source") or "")
    source_usage: Mapping[str, Any] = (financials or {}).get("source_usage", {}) if isinstance((financials or {}).get("source_usage"), Mapping) else {}
    market: Any = _market(manifest)
    latest_annual: Any = _latest_annual(rows, market=market, source=source_name)
    latest_annual_meta: Any = normalize_financial_period(latest_annual, market=market, source=source_name) if latest_annual else {}
    latest_annual_year: Any = latest_annual_meta.get("fiscal_year") if isinstance(latest_annual_meta.get("fiscal_year"), int) else None
    previous_annual: Any = _latest_annual(rows, before_period_end=str(latest_annual_meta.get("period_end") or ""), market=market, source=source_name) if latest_annual else None
    latest_q1: Any = _latest_quarter(rows, "q1", market=market, source=source_name)
    latest_q1_meta: Any = normalize_financial_period(latest_q1, market=market, source=source_name) if latest_q1 else {}
    latest_q1_year: Any = latest_q1_meta.get("fiscal_year") if isinstance(latest_q1_meta.get("fiscal_year"), int) else None
    previous_q1: Any = _latest_quarter(rows, "q1", before_period_end=str(latest_q1_meta.get("period_end") or ""), market=market, source=source_name) if latest_q1 else None

    if not latest_annual:
        research_debt: Any = (
            "Financial statement period rows exist, but no annual period could be selected after fiscal-period normalization."
            if rows
            else "Financial statement rows are missing from the data package."
        )
        return {
            "symbol": _symbol(manifest),
            "status": "DATA_GATED",
            "score": 30.0,
            "source_level": source_level,
            "period_row_count": len(rows),
            "research_debt": research_debt,
        }

    revenue: Any = _as_float(latest_annual.get("revenue"))
    previous_revenue: Any = _as_float(previous_annual.get("revenue")) if previous_annual else None
    net_income: Any = _as_float(latest_annual.get("net_income") or latest_annual.get("net_profit"))
    previous_net_income: Any = _as_float(previous_annual.get("net_income") or previous_annual.get("net_profit")) if previous_annual else None
    statement_unit: str = _financial_statement_unit(financials or {}, latest_annual)
    unit_multiplier: float = financial_unit_multiplier(statement_unit)
    revenue_absolute: Any = normalize_financial_amount(revenue, statement_unit)
    net_income_absolute: Any = normalize_financial_amount(net_income, statement_unit)
    operating_cash_flow: Any = _as_float(latest_annual.get("operating_cash_flow"))
    operating_cost: Any = _as_float(latest_annual.get("operating_cost"))
    gross_profit: Any = (revenue - operating_cost) if revenue is not None and operating_cost is not None else _as_float(latest_annual.get("gross_profit"))
    assets: Any = _as_float(latest_annual.get("assets"))
    liabilities: Any = _as_float(latest_annual.get("liabilities"))
    receivables: Any = _as_float(latest_annual.get("accounts_receivable"))
    inventory: Any = _as_float(latest_annual.get("inventory"))
    rd_expense: Any = _as_float(latest_annual.get("research_expense"))
    valuation_payload: Any = _valuation_payload(manifest)
    quote: Any = _load_result_json(manifest, "current_quote")
    regular_market_price: Any = (
        _as_float(valuation_payload.get("regular_market_price"))
        or (_as_float(quote.get("regular_market_price")) if isinstance(quote, Mapping) else None)
    )
    total_market_cap: Any = _as_float(valuation_payload.get("total_market_cap"))
    float_market_cap: Any = _as_float(valuation_payload.get("float_market_cap"))
    total_shares: Any = _as_float(valuation_payload.get("total_shares"))
    financial_currency: Any = _financial_currency(financials or {}, latest_annual)
    valuation_currency: Any = _currency_code(
        valuation_payload.get("currency")
        or (quote.get("currency") if isinstance(quote, Mapping) else "")
    )
    valuation_currency_match: Any = bool(financial_currency and valuation_currency and financial_currency == valuation_currency)
    q1_revenue_growth: Any = _pct_change(_as_float(latest_q1.get("revenue")) if latest_q1 else None, _as_float(previous_q1.get("revenue")) if previous_q1 else None)
    q1_net_income_growth: Any = _pct_change(_as_float(latest_q1.get("net_income") or latest_q1.get("net_profit")) if latest_q1 else None, _as_float(previous_q1.get("net_income") or previous_q1.get("net_profit")) if previous_q1 else None)
    q1_ocf_to_ni: Any = _ratio(
        _as_float(latest_q1.get("operating_cash_flow")) if latest_q1 else None,
        _as_float(latest_q1.get("net_income") or latest_q1.get("net_profit")) if latest_q1 else None,
    )

    revenue_growth: Any = _pct_change(revenue, previous_revenue)
    net_income_growth: Any = _pct_change(net_income, previous_net_income)
    turned_profitable: Any = previous_net_income is not None and previous_net_income < 0 and (net_income or 0) > 0
    gross_margin: Any = _ratio(gross_profit, revenue)
    net_margin: Any = _ratio(net_income, revenue)
    ocf_to_ni: Any = _ratio(operating_cash_flow, net_income)
    receivables_to_revenue: Any = _ratio(receivables, revenue)
    inventory_to_revenue: Any = _ratio(inventory, revenue)
    debt_to_assets: Any = _ratio(liabilities, assets)
    rd_to_revenue: Any = _ratio(rd_expense, revenue)

    score: Any = 45.0
    if revenue_growth is not None:
        score += 14.0 if revenue_growth >= 30 else 9.0 if revenue_growth >= 15 else 4.0 if revenue_growth >= 5 else -4.0
    if turned_profitable:
        score += 7.0
    elif net_income_growth is not None:
        score += 14.0 if net_income_growth >= 25 else 8.0 if net_income_growth >= 10 else 2.0 if net_income_growth >= 0 else -8.0
    if gross_margin is not None:
        score += 10.0 if gross_margin >= 50 else 7.0 if gross_margin >= 40 else 3.0 if gross_margin >= 25 else -4.0
    if net_margin is not None:
        score += 10.0 if net_margin >= 25 else 6.0 if net_margin >= 12 else 2.0 if net_margin >= 5 else -5.0
    if ocf_to_ni is not None:
        score += 8.0 if ocf_to_ni >= 80 else 4.0 if ocf_to_ni >= 50 else -8.0 if ocf_to_ni < 0 else -3.0
    if debt_to_assets is not None:
        score += 5.0 if debt_to_assets <= 35 else 1.0 if debt_to_assets <= 55 else -5.0
    if q1_revenue_growth is not None and q1_revenue_growth < 10:
        score -= 6.0
    if q1_ocf_to_ni is not None and q1_ocf_to_ni < 0:
        score -= 10.0
    if source_level.startswith("L3"):
        score = min(score, 90.0)

    if score >= 76:
        label: Any = "strong_preflight"
    elif score >= 62:
        label = "constructive_preflight"
    elif score >= 48:
        label = "watch_preflight"
    else:
        label = "weak_preflight"

    return {
        "symbol": _symbol(manifest),
        "status": "OK",
        "source_level": source_level,
        "score": round(max(0.0, min(100.0, score)), 2),
        "label": label,
        "latest_annual_period": str(latest_annual.get("period")),
        "latest_annual_fiscal_year": latest_annual_year,
        "latest_annual_period_type": str(latest_annual_meta.get("period_type") or ""),
        "latest_annual_selection_rule": str(latest_annual_meta.get("selection_rule") or ""),
        "latest_q1_period": str(latest_q1.get("period")) if latest_q1 else "",
        "latest_q1_fiscal_year": latest_q1_year,
        "revenue": _round(revenue),
        "revenue_absolute": _round(revenue_absolute),
        "revenue_growth_pct": _round(revenue_growth),
        "net_income": _round(net_income),
        "net_income_absolute": _round(net_income_absolute),
        "net_income_growth_pct": None if turned_profitable else _round(net_income_growth),
        "turned_profitable": turned_profitable,
        "gross_margin_pct": _round(gross_margin),
        "net_margin_pct": _round(net_margin),
        "ocf_to_net_income_pct": _round(ocf_to_ni),
        "receivables_to_revenue_pct": _round(receivables_to_revenue),
        "inventory_to_revenue_pct": _round(inventory_to_revenue),
        "debt_to_assets_pct": _round(debt_to_assets),
        "rd_to_revenue_pct": _round(rd_to_revenue),
        "regular_market_price": _round(regular_market_price),
        "total_shares": _round(total_shares),
        "total_market_cap": _round(total_market_cap),
        "float_market_cap": _round(float_market_cap),
        "financial_currency": financial_currency,
        "financial_statement_unit": statement_unit,
        "financial_unit_multiplier": _round(unit_multiplier, 0),
        "valuation_currency": valuation_currency,
        "valuation_currency_match": valuation_currency_match,
        "valuation_source_basis": str(valuation_payload.get("source_basis") or ""),
        "share_count_basis": str(valuation_payload.get("share_count_basis") or ""),
        "market_cap_basis": str(valuation_payload.get("market_cap_basis") or ""),
        "financial_sector_profile_required": bool(source_usage.get("financial_sector_profile_required")),
        "financial_sector_profile_status": str(source_usage.get("financial_sector_profile_status") or ""),
        "financial_sector_profile_fallback": source_usage.get("financial_sector_profile_fallback", {}) if isinstance(source_usage.get("financial_sector_profile_fallback"), Mapping) else {},
        "q1_revenue_growth_pct": _round(q1_revenue_growth),
        "q1_net_income_growth_pct": _round(q1_net_income_growth),
        "q1_ocf_to_net_income_pct": _round(q1_ocf_to_ni),
        "research_debt": "Reconcile core financial lines with L0/L1 annual and quarterly reports before A/S rating." if source_level.startswith("L3") else "",
    }


def _data_summary(manifest: Mapping[str, Any]) -> dict[str, Any]:
    acquisition: Any = manifest.get("data_acquisition") if isinstance(manifest.get("data_acquisition"), Mapping) else {}
    quality: Any = manifest.get("data_quality") if isinstance(manifest.get("data_quality"), Mapping) else {}
    statuses: Any = acquisition.get("status_by_dataset") if isinstance(acquisition.get("status_by_dataset"), Mapping) else {}
    def count_field(count_key: str, list_key: str) -> int:
        items: Any = acquisition.get(list_key)
        item_count: Any = len(items) if isinstance(items, list) else 0
        value: Any = acquisition.get(count_key)
        if value is not None:
            try:
                return max(int(value), item_count)
            except Exception:
                pass
        return item_count

    return {
        "symbol": _symbol(manifest),
        "market": _market(manifest),
        "status_by_dataset": dict(statuses),
        "rating_cap": str(quality.get("rating_cap") or quality.get("full_research_rating_cap") or "OBSERVE_ONLY"),
        "attempt_count": count_field("attempt_count", "attempt_ledger"),
        "gap_count": count_field("gap_count", "data_gaps"),
        "research_debt_count": count_field("research_debt_count", "research_debt"),
        "manual_task_count": count_field("manual_task_count", "manual_retrieval_tasks"),
        "full_research_ready": bool(acquisition.get("full_research_ready")),
    }


def _data_readiness_score(summary: Mapping[str, Any]) -> float:
    statuses: Any = summary.get("status_by_dataset") if isinstance(summary.get("status_by_dataset"), Mapping) else {}
    if not statuses:
        return 25.0
    weights: Any = {
        "current_quote": 0.18,
        "price_history_adjusted": 0.22,
        "financials": 0.26,
        "filings_announcements": 0.22,
        "valuation_inputs": 0.12,
    }
    return sum(STATUS_SCORE.get(str(statuses.get(key) or "NOT_REQUESTED"), 25.0) * weight for key, weight in weights.items())


def _technical_summary(manifest: Mapping[str, Any]) -> dict[str, Any]:
    price_path: Any = _path_from_result(manifest, _result_for_dataset(manifest, "price_history_adjusted"))
    quote_path: Any = _path_from_result(manifest, _result_for_dataset(manifest, "current_quote"))
    if not price_path:
        return {
            "symbol": _symbol(manifest),
            "status": "DATA_GATED",
            "trend_state": "DATA_GATED",
            "chan_action": "DATA_REQUIRED",
            "buy_point_claim_allowed": False,
            "decision_note": "Adjusted price history is missing from the manifest.",
            "readiness_score": 25.0,
            "metrics": {"bars": 0},
        }
    result: Any = analyze_price_csv(price_path, quote_path)
    result["symbol"] = _symbol(manifest)
    return result


def _capital_summary(manifest: Mapping[str, Any]) -> dict[str, Any]:
    filings: Any = _load_result_json(manifest, "filings_announcements")
    if _market(manifest) != "CN_A" or filings is None:
        return {
            "symbol": _symbol(manifest),
            "summary": {
                "action_count": 0,
                "material_action_count": 0,
                "material_risk_level": "none",
                "action_types": [],
                "has_dilution_event": False,
            },
            "actions": [],
            "research_debt": [],
        }
    result: Any = analyze_announcements(filings)
    result["symbol"] = _symbol(manifest)
    return result


def _profile_from_overlay(symbol: str, overlay: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(overlay, Mapping):
        raise ValueError(f"{symbol} overlay must be a JSON object")
    validated: Any = validate_overlay(overlay)["normalized_overlay"]
    overlay_symbol: Any = str(validated.get("symbol") or "")
    if overlay_symbol != symbol:
        raise ValueError(f"overlay assignment {symbol} does not match overlay.symbol {overlay_symbol}")
    profile: Any = {
        "layer": validated["layer"],
        "bottleneck_reason": validated["bottleneck_reason"],
        "layer_score": validated["layer_score"],
        "company_fit": validated["company_fit"],
        "serenity_fit": validated["serenity_fit"],
        "revenue_transmission": validated["revenue_transmission"],
        "evidence_gap": "; ".join(validated.get("research_questions", [])) or "AI overlay supplied evidence-backed layer mapping.",
        "ai_confidence": validated["ai_confidence"],
        "key_evidence_refs": validated.get("key_evidence_refs", []),
        "contrary_evidence": validated.get("contrary_evidence", []),
        "research_questions": validated.get("research_questions", []),
    }
    for key in ["evidence_supported_growth", "required_next_evidence", "posterior_basis"]:
        if key in validated:
            profile[key] = validated[key]
    return profile


def _overlay_profiles(manifests: Sequence[Mapping[str, Any]], overlays: Optional[Mapping[str, Mapping[str, Any]]]) -> dict[str, dict[str, Any]]:
    if overlays is None:
        return {}
    if not isinstance(overlays, Mapping):
        raise ValueError("overlays must be a mapping from candidate symbol to overlay JSON object")
    candidate_symbols: Any = {_symbol(manifest) for manifest in manifests}
    normalized_overlays: Any = {str(symbol): overlay for symbol, overlay in overlays.items()}
    overlay_symbols: Any = set(normalized_overlays)
    unknown: Any = sorted(overlay_symbols - candidate_symbols)
    if unknown:
        raise ValueError(f"overlay supplied for non-candidate symbol(s): {', '.join(unknown)}")
    return {
        symbol: _profile_from_overlay(symbol, normalized_overlays[symbol])
        for symbol in sorted(overlay_symbols)
    }


def _serenity_layer(manifest: Mapping[str, Any], profile: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    profile = profile or {}
    layer: Any = str(profile.get("layer") or "AI_REVIEW_REQUIRED")
    serenity_fit: Any = _as_float(profile.get("serenity_fit"))
    layer_score: Any = _as_float(profile.get("layer_score"))
    company_fit: Any = _as_float(profile.get("company_fit"))
    if layer_score is None and serenity_fit is not None:
        layer_score = serenity_fit * 100.0 if serenity_fit <= 1.0 else serenity_fit
    if company_fit is None and serenity_fit is not None:
        company_fit = serenity_fit * 100.0 if serenity_fit <= 1.0 else serenity_fit
    return {
        "symbol": _symbol(manifest),
        "layer": layer,
        "bottleneck_reason": str(profile.get("bottleneck_reason") or "在正式评级前，需要把公司映射到具体价值链瓶颈。"),
        "layer_score": _round(layer_score),
        "company_fit": _round(company_fit),
        "revenue_transmission": str(profile.get("revenue_transmission") or "需要用公告、财报或公司披露把产品/客户映射到财务行项目。"),
        "evidence_gap": str(profile.get("evidence_gap") or "产业链层级映射需要 AI/行业研究复核，不能只从 ticker 数据推断。"),
        "ai_confidence": str(profile.get("ai_confidence") or "NOT_PROVIDED"),
        "key_evidence_refs": profile.get("key_evidence_refs", []) if isinstance(profile.get("key_evidence_refs", []), list) else [],
        "contrary_evidence": profile.get("contrary_evidence", []) if isinstance(profile.get("contrary_evidence", []), list) else [],
        "research_questions": profile.get("research_questions", []) if isinstance(profile.get("research_questions", []), list) else [],
    }


def _growth_level_from_valuation(pe: Optional[float], ps: Optional[float]) -> str:
    if pe is None and ps is None:
        return "UNKNOWN"
    pe_value: Any = pe if pe is not None and pe > 0 else None
    ps_value: Any = ps if ps is not None and ps > 0 else None
    if (pe_value is not None and pe_value >= 120) or (ps_value is not None and ps_value >= 35):
        return "H5"
    if (pe_value is not None and pe_value >= 60) or (ps_value is not None and ps_value >= 18):
        return "H4"
    if (pe_value is not None and pe_value >= 35) or (ps_value is not None and ps_value >= 10):
        return "H3"
    if (pe_value is not None and pe_value >= 20) or (ps_value is not None and ps_value >= 5):
        return "H2"
    return "H1"


def _growth_hypothesis(
    manifest: Mapping[str, Any],
    financial: Mapping[str, Any],
    profile: Optional[Mapping[str, Any]] = None,
    currency_normalization: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    profile = profile or {}
    score: Any = _as_float(financial.get("score")) or 0.0
    if score >= 76:
        supported: Any = "H3"
    elif score >= 62:
        supported = "H2"
    elif score >= 48:
        supported = "H1"
    elif financial.get("status") == "OK":
        supported = "H0"
    else:
        supported = "UNKNOWN"
    supported = str(profile.get("evidence_supported_growth") or supported)
    quote: Any = _load_result_json(manifest, "current_quote")
    valuation_payload: Any = _valuation_payload(manifest)
    valuation_row: Any = _valuation_input_row(manifest)
    price: Any = _as_float(quote.get("regular_market_price")) if isinstance(quote, Mapping) else None
    total_shares: Any = _as_float(valuation_payload.get("total_shares"))
    revenue: Any = _as_float(financial.get("revenue_absolute"))
    if revenue is None:
        revenue = _as_float(financial.get("revenue"))
    net_income: Any = _as_float(financial.get("net_income_absolute"))
    if net_income is None:
        net_income = _as_float(financial.get("net_income"))
    market_cap: Any = _as_float(valuation_payload.get("total_market_cap"))
    if valuation_payload and market_cap is None and price is not None and total_shares is not None:
        market_cap = price * total_shares
    financial_currency: Any = _currency_code(financial.get("financial_currency"))
    valuation_currency: Any = _currency_code(financial.get("valuation_currency") or valuation_payload.get("currency"))
    valuation_input_ready: bool = bool(valuation_payload) and market_cap is not None
    currency_ready: bool = bool(financial_currency and valuation_currency)
    currency_mismatch: bool = bool(currency_ready and financial_currency != valuation_currency)
    normalization_status: str = str((currency_normalization or {}).get("normalization_status") or "")
    normalized_market_cap: Optional[float] = _as_float((currency_normalization or {}).get("normalized_total_market_cap"))
    original_market_cap: Optional[float] = _as_float((currency_normalization or {}).get("original_total_market_cap")) or _as_float(market_cap)
    fx_rate: Optional[float] = _as_float((currency_normalization or {}).get("fx_rate"))
    if currency_mismatch and normalization_status == "OK" and normalized_market_cap is not None:
        market_cap = normalized_market_cap
        valuation_currency = financial_currency
        currency_mismatch = False
        currency_ready = True
    pe: Any = (
        None
        if not valuation_input_ready or not currency_ready or currency_mismatch
        else market_cap / net_income if market_cap is not None and net_income and net_income > 0 else None
    )
    ps: Any = (
        None
        if not valuation_input_ready or not currency_ready or currency_mismatch
        else market_cap / revenue if market_cap is not None and revenue and revenue > 0 else None
    )
    valuation_can_infer_growth: bool = valuation_input_ready and currency_ready and not currency_mismatch and (pe is not None or ps is not None)
    market_implied: Any = _growth_level_from_valuation(pe, ps) if valuation_can_infer_growth else "UNKNOWN"
    gap: Any
    required: Any
    if not valuation_input_ready:
        gap = "valuation_input_required"
        required = "补齐总股本、总市值、估值倍数和同业/DCF 依据后，才能推断市场隐含增长。"
    elif not currency_ready:
        gap = "valuation_currency_reconciliation_required"
        required = "补齐估值货币和财报货币后，才能计算 PE/PS 和市场隐含增长。"
    elif currency_mismatch:
        gap = "valuation_currency_reconciliation_required"
        required = f"把估值市值从 {valuation_currency} 归一到财报口径 {financial_currency}；若 FX 获取失败，则不能输出市场隐含增长。"
    elif market_implied == "UNKNOWN":
        gap = "valuation_input_required"
        required = "补齐总股本、总市值、估值倍数和同业/DCF 依据后，才能推断市场隐含增长。"
    else:
        implied_order: Any = GROWTH_ORDER.get(market_implied, -1)
        supported_order: Any = GROWTH_ORDER.get(supported, -1)
        if supported_order >= implied_order and implied_order >= 0:
            gap = "roughly_matched"
        elif implied_order >= 4 and supported_order < implied_order:
            gap = "market_ahead_of_evidence"
        else:
            gap = "requires_ai_review"
        required = str(profile.get("required_next_evidence") or "用 L0/L1 证据复核股本、分部收入、订单、产能和估值口径。")
    implied_order = GROWTH_ORDER.get(market_implied, -1)
    supported_order = GROWTH_ORDER.get(supported, -1)
    h4_h5_bar_met: bool = implied_order < 4 or supported_order >= implied_order
    valuation_stage: Any = str(valuation_row.get("valuation_stage") or "unavailable")
    valuation_confidence: Any = str(valuation_row.get("valuation_confidence") or "blocked")
    if valuation_can_infer_growth:
        posterior_basis: Any = f"{valuation_stage} 估值预检来自当前价、总股本、总市值、收入和净利润；PE/PS 仍需 L0/L1 财务与股本口径复核后才能视为正式估值。"
    elif not valuation_input_ready:
        posterior_basis = "市场隐含增长在估值输入完整前保持阻断。"
    elif not currency_ready or currency_mismatch:
        posterior_basis = "市场隐含增长在完成同币种财务口径前保持阻断。"
    else:
        posterior_basis = "市场隐含增长在收入、净利润和估值倍数可计算前保持阻断。"
    if normalization_status == "OK" and fx_rate is not None:
        posterior_basis = (
            f"{posterior_basis} 本轮已按 {currency_normalization.get('fx_rate_direction')}="
            f"{currency_normalization.get('fx_rate')} 将市值归一到 {financial_currency}。"
        )
    return {
        "symbol": str(financial.get("symbol") or ""),
        "valuation_input_ref": f"valuation_input_matrix:{financial.get('symbol') or ''}",
        "market_implied_growth": market_implied,
        "evidence_supported_growth": supported,
        "gap": gap,
        "h4_h5_evidence_bar_met": h4_h5_bar_met,
        "required_next_evidence": required,
        "posterior_basis": str(profile.get("posterior_basis") or posterior_basis),
        "total_market_cap": _round(market_cap),
        "original_total_market_cap": _round(original_market_cap),
        "normalized_total_market_cap": _round(normalized_market_cap),
        "total_shares": _round(total_shares),
        "revenue_amount": _round(revenue),
        "net_income_amount": _round(net_income),
        "financial_statement_unit": str(financial.get("financial_statement_unit") or ""),
        "financial_unit_multiplier": financial.get("financial_unit_multiplier"),
        "financial_currency": financial_currency,
        "valuation_currency": valuation_currency,
        "valuation_currency_match": not currency_mismatch if currency_ready else None,
        "currency_normalization_status": normalization_status,
        "fx_rate": _round(fx_rate, 6),
        "pe_preflight": _round(pe),
        "ps_preflight": _round(ps),
        "valuation_stage": valuation_stage,
        "valuation_confidence": valuation_confidence,
        "valuation_basis": str(
            valuation_payload.get("market_cap_basis")
            or valuation_payload.get("share_count_basis")
            or ""
        ),
    }


def _research_debt_rows(
    manifest: Mapping[str, Any],
    capital: Mapping[str, Any],
    financial: Mapping[str, Any],
    technical: Mapping[str, Any],
    layer: Mapping[str, Any],
    growth: Mapping[str, Any],
) -> list[dict[str, Any]]:
    symbol: Any = _symbol(manifest)
    rows: list[dict[str, Any]] = []
    acquisition: Any = manifest.get("data_acquisition") if isinstance(manifest.get("data_acquisition"), Mapping) else {}
    for item in acquisition.get("research_debt", []) if isinstance(acquisition.get("research_debt"), list) else []:
        if isinstance(item, Mapping):
            rows.append({"symbol": symbol, **dict(item)})
    for task in acquisition.get("manual_retrieval_tasks", []) if isinstance(acquisition.get("manual_retrieval_tasks"), list) else []:
        if isinstance(task, Mapping):
            rows.append({"symbol": symbol, "task_type": "manual_retrieval", **dict(task)})
    for item in capital.get("research_debt", []) if isinstance(capital.get("research_debt"), list) else []:
        rows.append({"symbol": symbol, "dataset": "capital_actions", "priority": "high", "next_action": str(item)})
    if financial.get("research_debt"):
        rows.append({"symbol": symbol, "dataset": "financials", "priority": "critical", "next_action": str(financial.get("research_debt"))})
    if technical.get("status") == "DATA_GATED" or technical.get("chan_action") == "DATA_REQUIRED":
        rows.append({
            "symbol": symbol,
            "dataset": "price_history_adjusted",
            "priority": "high",
            "next_action": "补齐足够长度的复权日线历史后，才能输出缠论时机或买点判断。",
        })
    if layer.get("layer") == "AI_REVIEW_REQUIRED":
        rows.append({"symbol": symbol, "dataset": "serenity_layer", "priority": "high", "next_action": str(layer.get("evidence_gap"))})
    if growth.get("gap") == "valuation_currency_reconciliation_required":
        rows.append({"symbol": symbol, "dataset": "valuation_currency", "priority": "high", "next_action": str(growth.get("required_next_evidence"))})
    elif growth.get("market_implied_growth") == "UNKNOWN":
        rows.append({"symbol": symbol, "dataset": "valuation", "priority": "high", "next_action": str(growth.get("required_next_evidence"))})
    elif growth.get("h4_h5_evidence_bar_met") is False or growth.get("gap") == "market_ahead_of_evidence":
        rows.append({
            "symbol": symbol,
            "dataset": "valuation_growth",
            "priority": "high",
            "next_action": str(growth.get("required_next_evidence")),
            "gap": str(growth.get("gap")),
            "market_implied_growth": str(growth.get("market_implied_growth")),
            "evidence_supported_growth": str(growth.get("evidence_supported_growth")),
        })
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key: Any = (str(row.get("symbol")), str(row.get("dataset")), str(row.get("next_action") or row.get("objective")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _research_debt_from_consumption(consumption_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in consumption_rows:
        if row.get("consumption_status") != "MISMATCH":
            continue
        dataset: Any = str(row.get("dataset") or "data_consumption")
        warnings: Any = row.get("warnings", [])
        rows.append({
            "symbol": str(row.get("symbol") or ""),
            "dataset": dataset,
            "priority": "critical",
            "gap_type": "CONFLICTING_SOURCES",
            "decision_impact": "EVIDENCE_IMPACT" if dataset == "financials" else "VALUATION_IMPACT",
            "next_action": "; ".join(str(item) for item in warnings if item) or "先修复下游数据消费错配，再使用候选排序。",
        })
    return rows


def _debt_gate_profile(debt_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    priorities_by_dataset: dict[str, set[str]] = {}
    for row in debt_rows:
        dataset: Any = str(row.get("dataset") or row.get("task_type") or "unknown")
        priority: Any = str(row.get("priority") or "").lower()
        priorities_by_dataset.setdefault(dataset, set()).add(priority)

    critical_datasets: Any = {dataset for dataset, priorities in priorities_by_dataset.items() if "critical" in priorities}
    high_datasets: Any = {dataset for dataset, priorities in priorities_by_dataset.items() if "high" in priorities}
    blocking_datasets: Any = set(critical_datasets) | (high_datasets & ACTION_BLOCKING_DEBT_DATASETS)

    drag: Any = 0.0
    if "financials" in critical_datasets:
        drag += 6.0
    elif critical_datasets:
        drag += 5.0
    valuation_high: Any = high_datasets & {"valuation", "share_capital", "valuation_inputs", "peer_valuation", "consensus_estimates"}
    if "valuation_growth" in high_datasets:
        drag += 6.0
    if valuation_high:
        drag += 4.0
    if "serenity_layer" in high_datasets:
        drag += 4.0
    if "capital_actions" in high_datasets:
        drag += 3.0
    if "current_quote" in high_datasets:
        drag += 4.0
    if "price_history_adjusted" in high_datasets:
        drag += 4.0
    other_high: Any = high_datasets - {"valuation_growth", "valuation", "share_capital", "valuation_inputs", "peer_valuation", "consensus_estimates", "serenity_layer", "capital_actions", "current_quote", "price_history_adjusted"}
    drag += min(4.0, 2.0 * len(other_high))

    return {
        "critical_datasets": sorted(critical_datasets),
        "high_datasets": sorted(high_datasets),
        "blocking_datasets": sorted(blocking_datasets),
        "debt_drag": min(18.0, drag),
    }


def _ranking_validity(
    consumption_rows: Sequence[Mapping[str, Any]],
    debt_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    validity: dict[str, Any] = ranking_validity_from_consumption(consumption_rows)
    if validity.get("status") == "INVALID":
        return validity

    debt_profile: dict[str, Any] = _debt_gate_profile(debt_rows)
    open_debt_axes: list[str] = sorted(
        set(debt_profile.get("critical_datasets") or [])
        | set(debt_profile.get("high_datasets") or [])
    )
    if not open_debt_axes:
        return validity

    partial_axes: list[str] = [str(axis) for axis in validity.get("partial_axes") or []]
    for dataset in open_debt_axes:
        axis: str = f"research_debt:{dataset}"
        if axis not in partial_axes:
            partial_axes.append(axis)

    reason: str = str(validity.get("reason") or "")
    if validity.get("status") == "VALID":
        reason = "排序可用于研究优先级，但仍存在高优先级或关键研究债务。"
    else:
        reason = f"{reason} 仍存在高优先级或关键研究债务。".strip()
    return {
        "status": "PARTIAL",
        "reason": reason,
        "blocked_by": list(validity.get("blocked_by") or []),
        "partial_axes": partial_axes,
    }


def _action_gate_profile(
    technical: Mapping[str, Any],
    capital: Mapping[str, Any],
    layer: Mapping[str, Any],
    growth: Mapping[str, Any],
    debt_profile: Mapping[str, Any],
) -> dict[str, Any]:
    gate_order: Any = [
        "DATA_GATED",
        "EVIDENCE_GATED",
        "VALUATION_GATED",
        "CAPITAL_ACTION_GATED",
        "AI_REVIEW_GATED",
        "BUY_POINT_GATED",
    ]
    gates: list[str] = []
    reasons: list[str] = []
    blocking_datasets: set[str] = set()
    gate_classes: dict[str, str] = {}

    def add(gate: str, reason: str, dataset: str = "", gate_class: str = "RESEARCH_VALIDATION") -> None:
        if gate not in gates:
            gates.append(gate)
            gate_classes[gate] = gate_class
        if reason and reason not in reasons:
            reasons.append(reason)
        if dataset:
            blocking_datasets.add(dataset)

    high_or_blocking: Any = set(debt_profile.get("blocking_datasets") or []) | set(debt_profile.get("high_datasets") or [])
    for dataset in sorted(high_or_blocking):
        if dataset in {"current_quote", "price_history_adjusted"}:
            add("DATA_GATED", f"{dataset} 不完整，当前估值或技术时机结论保持阻断。", dataset, "DATA_ACQUISITION")
        elif dataset in {"financials", "filings_announcements"}:
            add("EVIDENCE_GATED", f"{dataset} 证据仍需 L0/L1 复核，高置信研究结论保持阻断。", dataset, "EVIDENCE_VALIDATION")
        elif dataset in VALUATION_DATA_DEBT_DATASETS | VALUATION_RESEARCH_DEBT_DATASETS:
            if dataset in VALUATION_RESEARCH_DEBT_DATASETS:
                add("VALUATION_GATED", "市场隐含增长高于证据支持增长，需要 AI/行业研究复核。", dataset, "RESEARCH_VALIDATION")
            else:
                add("VALUATION_GATED", "估值输入不完整，市场隐含增长和赔率判断保持阻断。", dataset, "DATA_ACQUISITION")
        elif dataset == "serenity_layer":
            add("AI_REVIEW_GATED", "产业链层级、瓶颈位置和收入传导仍需 AI/行业研究复核。", dataset, "RESEARCH_VALIDATION")
        elif dataset == "capital_actions":
            add("CAPITAL_ACTION_GATED", "资本动作需要量化稀释、回购、上市或减持影响。", dataset, "RESEARCH_VALIDATION")

    if growth.get("market_implied_growth") == "UNKNOWN" or growth.get("gap") == "valuation_input_required":
        add("VALUATION_GATED", str(growth.get("required_next_evidence") or "Valuation inputs are required."), "valuation", "DATA_ACQUISITION")
    if layer.get("layer") == "AI_REVIEW_REQUIRED":
        add("AI_REVIEW_GATED", str(layer.get("evidence_gap") or "需要 AI/行业研究复核。"), "serenity_layer", "RESEARCH_VALIDATION")

    risk_level: Any = str((capital.get("summary") or {}).get("material_risk_level") or "none") if isinstance(capital.get("summary"), Mapping) else "none"
    has_dilution: Any = bool((capital.get("summary") or {}).get("has_dilution_event")) if isinstance(capital.get("summary"), Mapping) else False
    if risk_level in {"medium_high", "high"} or has_dilution:
        add("CAPITAL_ACTION_GATED", f"资本动作风险为 {risk_level}，需要量化稀释和流动性影响。", "capital_actions", "RESEARCH_VALIDATION")

    if technical.get("buy_point_claim_allowed") is not True:
        action: Any = str(technical.get("chan_action") or "")
        if action in {"WAIT_FOR_SECOND_BUY", "WAIT_FOR_THIRD_BUY", "WAIT_FOR_STRUCTURE_CONFIRMATION", "DATA_REQUIRED"}:
            add("BUY_POINT_GATED", str(technical.get("decision_note") or "当前没有确认的缠论买点。"), "price_history_adjusted", "ACTION_TIMING")

    ordered: Any = [gate for gate in gate_order if gate in gates]
    primary: Any = ordered[0] if ordered else "NONE"
    primary_class: Any = gate_classes.get(primary, "NONE") if primary != "NONE" else "NONE"
    return {
        "state": "ACTIONABLE_WATCH" if primary == "NONE" else "NOT_ACTIONABLE",
        "primary_gate": primary,
        "primary_gate_class": primary_class,
        "gate_classes": {gate: gate_classes.get(gate, "RESEARCH_VALIDATION") for gate in ordered},
        "secondary_gates": ordered[1:],
        "blocking_datasets": sorted(blocking_datasets),
        "blocking_reasons": reasons,
    }


def _readiness_from_gate(primary_gate: str, gate: Mapping[str, Any], action_score: float) -> str:
    gate_class: Any = str(gate.get("primary_gate_class") or "")
    if primary_gate == "DATA_GATED":
        return "DATA_GATED"
    if primary_gate == "EVIDENCE_GATED":
        return "RESEARCH_GATED"
    if primary_gate == "VALUATION_GATED":
        return "DATA_GATED" if gate_class == "DATA_ACQUISITION" else "RESEARCH_GATED"
    if primary_gate in {"CAPITAL_ACTION_GATED", "AI_REVIEW_GATED"}:
        return "RESEARCH_GATED"
    if primary_gate == "BUY_POINT_GATED":
        return "WAIT_FOR_BUY_POINT"
    return "STRONG_OBSERVE" if action_score >= 68 else "CANDIDATE_POOL" if action_score >= 55 else "LEAD_TRACKING"


def _priority_score(
    data_summary: Mapping[str, Any],
    financial: Mapping[str, Any],
    technical: Mapping[str, Any],
    capital: Mapping[str, Any],
    layer: Mapping[str, Any],
    growth: Mapping[str, Any],
    research_debt: Sequence[Mapping[str, Any]],
) -> tuple[float, float, float, str, str, dict[str, Any]]:
    financial_score: Any = _as_float(financial.get("score")) or 35.0
    data_score: Any = _data_readiness_score(data_summary)
    technical_score: Any = _as_float(technical.get("readiness_score")) or 25.0
    layer_score: Any = _as_float(layer.get("layer_score"))
    thesis_proxy: Any = financial_score if layer_score is None else (financial_score * 0.55 + layer_score * 0.45)
    risk_level: Any = str((capital.get("summary") or {}).get("material_risk_level") or "none") if isinstance(capital.get("summary"), Mapping) else "none"
    capital_drag: Any = CAPITAL_RISK_SCORE.get(risk_level, 0.0)
    debt_profile: Any = _debt_gate_profile(research_debt)
    debt_drag: Any = _as_float(debt_profile.get("debt_drag")) or 0.0
    research_score: Any = thesis_proxy * 0.48 + data_score * 0.18 + financial_score * 0.24 + 8.0
    research_score -= min(8.0, debt_drag * 0.45)
    action_score: Any = technical_score * 0.34 + data_score * 0.18 + financial_score * 0.12 + research_score * 0.16 + 18.0
    action_score -= capital_drag
    action_score -= debt_drag
    cap: Any = str(data_summary.get("rating_cap") or "OBSERVE_ONLY")
    if cap in {"C", "D", "OBSERVE_ONLY"}:
        research_score = min(research_score, RATING_SCORE_LIMIT.get(cap, 25.0))
        action_score = min(action_score, RATING_SCORE_LIMIT.get(cap, 25.0))
    gate: Any = _action_gate_profile(technical, capital, layer, growth, debt_profile)
    primary_gate: Any = str(gate.get("primary_gate") or "NONE")
    if primary_gate in {"DATA_GATED", "EVIDENCE_GATED"}:
        action_score = min(action_score, 48.0)
    elif primary_gate == "VALUATION_GATED":
        action_score = min(action_score, 58.0)
    elif primary_gate == "CAPITAL_ACTION_GATED":
        action_score = min(action_score, 55.0)
    elif primary_gate == "AI_REVIEW_GATED":
        action_score = min(action_score, 62.0)
    elif primary_gate == "BUY_POINT_GATED":
        action_score = min(action_score, 62.0)
    research_score = max(0.0, min(100.0, research_score))
    action_score = max(0.0, min(100.0, action_score))
    combined_score: Any = research_score * 0.72 + action_score * 0.28
    action: Any = _readiness_from_gate(primary_gate, gate, action_score)
    debt_items: Any = debt_profile.get("blocking_datasets") or debt_profile.get("high_datasets") or []
    debt_label: str = display_list(debt_items, empty="无")
    reason: Any = (
        f"研究={research_score:.1f}，行动={action_score:.1f}，财务={financial_score:.1f}，"
        f"数据={data_score:.1f}，技术={technical_score:.1f}，资本风险={display_label(risk_level)}，"
        f"主门控={display_label(primary_gate)}，研究债务={debt_label}，证据上限={cap}"
    )
    return round(combined_score, 2), round(research_score, 2), round(action_score, 2), action, reason, gate


def _final_decision(
    ranked: Sequence[Mapping[str, Any]],
    next_actions: Sequence[str],
    ranking_validity: Mapping[str, Any],
) -> dict[str, Any]:
    top: Any = ranked[0] if ranked else {}
    top_symbol: Any = str(top.get("symbol") or "")
    top_score: Any = _as_float(top.get("priority_score"))
    runner_up_score: Any = _as_float(ranked[1].get("priority_score")) if len(ranked) > 1 else None
    score_gap: Any = _round(top_score - runner_up_score) if top_score is not None and runner_up_score is not None else None
    validity_status: Any = str(ranking_validity.get("status") or "VALID")
    if validity_status == "INVALID":
        decision_mode: Any = "comparison_not_decision_grade"
        decision: Any = "在数据消费错配修复前不命名正式优先候选；当前排序只用于工程诊断和补数任务。"
        top_symbol = ""
        score_gap = None
    elif score_gap is None:
        decision_mode = "single_candidate"
        decision = f"将 {top_symbol} 作为研究对象推进，但行动结论必须受当前门控约束。"
    elif score_gap >= 10.0:
        if validity_status == "PARTIAL":
            decision_mode = "tentative_top_candidate"
            decision = f"优先跟进 {top_symbol}，但在部分可比维度补齐前，不把该排序视为正式结论。"
        else:
            decision_mode = "clear_top_candidate"
            decision = f"优先研究 {top_symbol}；当前优先级差距明确，但评级和行动仍受开放门控约束。"
    elif score_gap >= 5.0:
        decision_mode = "tentative_top_candidate"
        decision = f"先研究 {top_symbol}，同时补齐第二候选的关键证据差异后再确认排序稳定性。"
    else:
        decision_mode = "candidate_cluster"
        decision = "将领先候选视为同一候选簇，先处理区分度研究债务，再命名稳定优先候选。"
    candidate_count_warning: Any = "insufficient_universe_warning" if len(ranked) < 3 else ""
    return {
        "top_candidate": top_symbol,
        "decision_mode": decision_mode,
        "score_gap_to_runner_up": score_gap,
        "candidate_count_warning": candidate_count_warning,
        "ranking_validity": dict(ranking_validity),
        "decision": decision,
        "next_research_actions": list(next_actions)[:12],
    }


def _fetch_status_from_summary(summary: Mapping[str, Any]) -> str:
    statuses: Mapping[str, Any] = summary.get("status_by_dataset") if isinstance(summary.get("status_by_dataset"), Mapping) else {}
    if not statuses:
        return "FAILED"
    values: list[str] = [str(value) for value in statuses.values()]
    if all(value == "OK" for value in values):
        return "PASS"
    if any(value in {"FAILED", "PENDING"} for value in values):
        return "FAILED"
    return "PARTIAL"


def _readiness_matrix(
    *,
    data_rows: Sequence[Mapping[str, Any]],
    ranked_rows: Sequence[Mapping[str, Any]],
    consumption_rows: Sequence[Mapping[str, Any]],
    debt_rows: Sequence[Mapping[str, Any]],
    ranking_validity: Mapping[str, Any],
) -> list[dict[str, Any]]:
    ranked_by_symbol: dict[str, Mapping[str, Any]] = {str(row.get("symbol") or ""): row for row in ranked_rows}
    consumption_by_symbol: dict[str, list[Mapping[str, Any]]] = {}
    for row in consumption_rows:
        consumption_by_symbol.setdefault(str(row.get("symbol") or ""), []).append(row)
    debt_by_symbol: dict[str, list[Mapping[str, Any]]] = {}
    for row in debt_rows:
        debt_by_symbol.setdefault(str(row.get("symbol") or ""), []).append(row)

    validity_status: str = str(ranking_validity.get("status") or "")
    rows: list[dict[str, Any]] = []
    for data in data_rows:
        symbol: str = str(data.get("symbol") or "")
        ranking: Mapping[str, Any] = ranked_by_symbol.get(symbol, {})
        cap: str = str(data.get("rating_cap") or "OBSERVE_ONLY")
        symbol_debt: list[Mapping[str, Any]] = debt_by_symbol.get(symbol, [])
        reason_codes: list[str] = []
        for item in consumption_by_symbol.get(symbol, []):
            code: str = str(item.get("reason_code") or "")
            if code and code != "NONE" and code not in reason_codes:
                reason_codes.append(code)
        for item in symbol_debt:
            code = str(item.get("gap_type") or item.get("dataset") or "")
            if code and code not in reason_codes:
                reason_codes.append(code)

        fetch_status: str = _fetch_status_from_summary(data)
        has_blocking_debt: bool = any(str(item.get("priority") or "").lower() in {"critical", "high"} for item in symbol_debt)
        if fetch_status == "FAILED" or cap in {"C", "D", "OBSERVE_ONLY"}:
            research_readiness: str = "NOT_READY"
        elif has_blocking_debt or cap == "B" or validity_status in {"PARTIAL", "INVALID"}:
            research_readiness = "PARTIAL"
        else:
            research_readiness = "HIGH"

        action_gate: Mapping[str, Any] = ranking.get("action_gate") if isinstance(ranking.get("action_gate"), Mapping) else {}
        primary_gate: str = str(action_gate.get("primary_gate") or "NONE")
        action_readiness: str = str(ranking.get("action_readiness") or "")
        decision_grade: bool = validity_status == "VALID"
        rows.append({
            "symbol": symbol,
            "fetch_status": fetch_status,
            "research_readiness": research_readiness,
            "action_readiness": action_readiness,
            "primary_gate": primary_gate,
            "data_evidence_cap": cap,
            "decision_grade": decision_grade,
            "reason_codes": reason_codes,
        })
    return rows


def validate_comparison_report(report: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    required: Any = {
        "comparison_scope",
        "candidates",
        "data_acquisition_summary",
        "serenity_layer_matrix",
        "financial_quality_matrix",
        "valuation_input_matrix",
        "currency_normalization_matrix",
        "growth_hypothesis_matrix",
        "technical_timing_matrix",
        "capital_actions",
        "data_consumption_audit",
        "readiness_matrix",
        "research_debt",
        "candidate_priority_ranking",
        "final_decision",
    }
    missing: Any = sorted(required - set(report))
    if missing:
        errors.append(f"comparison report missing keys: {', '.join(missing)}")
    final_decision: Any = report.get("final_decision")
    if not isinstance(final_decision, Mapping):
        errors.append("final_decision must be an object")
    else:
        for field in ["top_candidate", "decision_mode", "score_gap_to_runner_up", "candidate_count_warning", "ranking_validity", "decision", "next_research_actions"]:
            if field not in final_decision:
                errors.append(f"final_decision missing {field}")
        if str(final_decision.get("decision_mode") or "") not in DECISION_MODES:
            errors.append("final_decision.decision_mode is unknown")
        gap: Any = final_decision.get("score_gap_to_runner_up")
        if gap is not None and _as_float(gap) is None:
            errors.append("final_decision.score_gap_to_runner_up must be numeric or null")
        if not isinstance(final_decision.get("next_research_actions", []), list):
            errors.append("final_decision.next_research_actions must be an array")
        ranking_validity: Any = final_decision.get("ranking_validity")
        if not isinstance(ranking_validity, Mapping):
            errors.append("final_decision.ranking_validity must be an object")
        else:
            validity_status: str = str(ranking_validity.get("status") or "")
            blocked_by: Any = ranking_validity.get("blocked_by")
            partial_axes: Any = ranking_validity.get("partial_axes")
            blocked_items: list[str] = _non_empty_strings(blocked_by)
            partial_items: list[str] = _non_empty_strings(partial_axes)
            if validity_status not in RANKING_VALIDITY_STATUSES:
                errors.append("final_decision.ranking_validity.status is unknown")
            if not isinstance(blocked_by, list):
                errors.append("final_decision.ranking_validity.blocked_by must be an array")
            if not isinstance(partial_axes, list):
                errors.append("final_decision.ranking_validity.partial_axes must be an array")
            if validity_status == "INVALID" and final_decision.get("decision_mode") != "comparison_not_decision_grade":
                errors.append("INVALID ranking_validity requires comparison_not_decision_grade decision_mode")
            if validity_status == "INVALID" and not blocked_items:
                errors.append("INVALID ranking_validity requires non-empty blocked_by")
            if validity_status == "PARTIAL" and final_decision.get("decision_mode") == "clear_top_candidate":
                errors.append("PARTIAL ranking_validity cannot use clear_top_candidate decision_mode")
            if validity_status == "PARTIAL" and not partial_items:
                errors.append("PARTIAL ranking_validity requires non-empty partial_axes")
            if validity_status == "VALID" and (blocked_items or partial_items):
                errors.append("VALID ranking_validity cannot carry blocked_by or partial_axes")
    candidates: Any = report.get("candidates", [])
    if not isinstance(candidates, list) or len(candidates) < 2:
        errors.append("comparison report requires at least two candidates")
    symbols: set[str] = {str(item.get("symbol")) for item in candidates if isinstance(item, Mapping)}
    for key in ["data_acquisition_summary", "serenity_layer_matrix", "financial_quality_matrix", "valuation_input_matrix", "currency_normalization_matrix", "growth_hypothesis_matrix", "technical_timing_matrix", "capital_actions", "readiness_matrix"]:
        rows: Any = report.get(key, [])
        if not isinstance(rows, list) or {str(item.get("symbol")) for item in rows if isinstance(item, Mapping)} != symbols:
            errors.append(f"{key} must contain one row per candidate")
    for row in report.get("currency_normalization_matrix", []) if isinstance(report.get("currency_normalization_matrix"), list) else []:
        if not isinstance(row, Mapping):
            continue
        status: str = str(row.get("normalization_status") or "")
        if status not in {"OK", "NOT_REQUIRED", "DATA_GATED", "FAILED"}:
            errors.append(f"{row.get('symbol')} currency_normalization_matrix.normalization_status is unknown")
        if status == "OK":
            for field in ["source_currency", "target_currency", "original_total_market_cap", "normalized_total_market_cap", "fx_rate", "fx_source", "fx_source_level"]:
                if row.get(field) in (None, ""):
                    errors.append(f"{row.get('symbol')} currency_normalization_matrix OK row missing {field}")
    financial_rows_for_validation: Any = report.get("financial_quality_matrix", [])
    financial_by_symbol: dict[str, Mapping[str, Any]] = {}
    if isinstance(financial_rows_for_validation, list):
        for row in financial_rows_for_validation:
            if not isinstance(row, Mapping):
                continue
            financial_by_symbol[str(row.get("symbol") or "")] = row
            if row.get("status") == "OK":
                for field in ["financial_statement_unit", "financial_unit_multiplier", "revenue_absolute", "net_income_absolute"]:
                    if row.get(field) in (None, ""):
                        errors.append(f"{row.get('symbol')} financial_quality_matrix OK row missing {field}")
                multiplier: Optional[float] = _as_float(row.get("financial_unit_multiplier"))
                revenue_reported: Optional[float] = _as_float(row.get("revenue"))
                revenue_absolute: Optional[float] = _as_float(row.get("revenue_absolute"))
                net_income_reported: Optional[float] = _as_float(row.get("net_income"))
                net_income_absolute: Optional[float] = _as_float(row.get("net_income_absolute"))
                if multiplier is not None and revenue_reported is not None and revenue_absolute is not None:
                    if not _close_enough(revenue_absolute, revenue_reported * multiplier):
                        errors.append(f"{row.get('symbol')} revenue_absolute must match reported revenue times financial_unit_multiplier")
                if multiplier is not None and net_income_reported is not None and net_income_absolute is not None:
                    if not _close_enough(net_income_absolute, net_income_reported * multiplier):
                        errors.append(f"{row.get('symbol')} net_income_absolute must match reported net_income times financial_unit_multiplier")
    consumption_rows: Any = report.get("data_consumption_audit", [])
    if not isinstance(consumption_rows, list):
        errors.append("data_consumption_audit must be an array")
    else:
        audited_symbols: set[str] = {str(item.get("symbol")) for item in consumption_rows if isinstance(item, Mapping)}
        if audited_symbols != symbols:
            errors.append("data_consumption_audit must contain audited rows for every candidate")
        audited_pairs: set[tuple[str, str]] = set()
        duplicate_pairs: set[tuple[str, str]] = set()
        for row in consumption_rows:
            if not isinstance(row, Mapping):
                errors.append("data_consumption_audit rows must be objects")
                continue
            pair: tuple[str, str] = (str(row.get("symbol") or ""), str(row.get("dataset") or ""))
            if pair in audited_pairs:
                duplicate_pairs.add(pair)
            audited_pairs.add(pair)
            if str(row.get("consumption_status") or "") not in {"OK", "PARTIAL", "DATA_GATED", "MISMATCH"}:
                errors.append(f"{row.get('symbol')} {row.get('dataset')} has unknown consumption_status")
            if str(row.get("reason_code") or "") == "":
                errors.append(f"{row.get('symbol')} {row.get('dataset')} missing reason_code")
        required_pairs: set[tuple[str, str]] = {(symbol, dataset) for symbol in symbols for dataset in CONSUMPTION_AUDIT_DATASETS}
        missing_pairs: list[tuple[str, str]] = sorted(required_pairs - audited_pairs)
        if missing_pairs:
            formatted: str = ", ".join(f"{symbol} {dataset}" for symbol, dataset in missing_pairs)
            errors.append(f"data_consumption_audit missing required dataset rows: {formatted}")
        if duplicate_pairs:
            formatted = ", ".join(f"{symbol} {dataset}" for symbol, dataset in sorted(duplicate_pairs))
            errors.append(f"data_consumption_audit has duplicate dataset rows: {formatted}")
        has_mismatch: bool = any(isinstance(row, Mapping) and row.get("consumption_status") == "MISMATCH" for row in consumption_rows)
        has_partial_consumption: bool = any(isinstance(row, Mapping) and row.get("consumption_status") in {"PARTIAL", "DATA_GATED"} for row in consumption_rows)
        final_validity: Any = final_decision.get("ranking_validity") if isinstance(final_decision, Mapping) else {}
        if has_mismatch and isinstance(final_validity, Mapping) and final_validity.get("status") != "INVALID":
            errors.append("data-consumption mismatch requires INVALID ranking_validity")
        if has_partial_consumption and isinstance(final_validity, Mapping) and final_validity.get("status") == "VALID":
            errors.append("partial or data-gated consumption requires PARTIAL ranking_validity")
    debt_rows: Any = report.get("research_debt", [])
    if isinstance(debt_rows, list):
        debt_profile: Any = _debt_gate_profile([row for row in debt_rows if isinstance(row, Mapping)])
        has_high_or_critical_debt: Any = bool(debt_profile.get("critical_datasets") or debt_profile.get("high_datasets"))
        final_validity = final_decision.get("ranking_validity") if isinstance(final_decision, Mapping) else {}
        if has_high_or_critical_debt and isinstance(final_validity, Mapping) and final_validity.get("status") == "VALID":
            errors.append("open high/critical research debt requires PARTIAL ranking_validity")
    for row in report.get("serenity_layer_matrix", []) if isinstance(report.get("serenity_layer_matrix"), list) else []:
        if not isinstance(row, Mapping):
            continue
        for field in ["layer_score", "company_fit"]:
            value: Any = row.get(field)
            if value is None:
                continue
            score: Any = _as_float(value)
            if score is None or score < 0 or score > 100:
                errors.append(f"{row.get('symbol')} serenity_layer_matrix.{field} must be 0-100 or null")
    for row in report.get("valuation_input_matrix", []) if isinstance(report.get("valuation_input_matrix"), list) else []:
        if not isinstance(row, Mapping):
            continue
        status: Any = str(row.get("status") or "")
        expected_ref: Any = f"valuation_input_matrix:{row.get('symbol')}"
        if row.get("valuation_input_ref") != expected_ref:
            errors.append(f"{row.get('symbol')} valuation input row requires valuation_input_ref={expected_ref}")
        if str(row.get("valuation_stage") or "") not in {"unavailable", "preflight", "verified_l0", "verified_l1", "deep_valuation"}:
            errors.append(f"{row.get('symbol')} valuation_input_matrix.valuation_stage is unknown")
        if status == "OK":
            for field in [
                "valuation_stage",
                "valuation_confidence",
                "regular_market_price",
                "total_shares",
                "total_market_cap",
                "currency",
                "as_of_date",
                "source_name",
                "source_level",
                "source_basis",
                "share_count_basis",
                "market_cap_basis",
            ]:
                if row.get(field) in (None, ""):
                    errors.append(f"{row.get('symbol')} valuation_input_matrix OK row missing {field}")
    ranking: Any = report.get("candidate_priority_ranking", [])
    if not isinstance(ranking, list) or len(ranking) != len(symbols):
        errors.append("candidate_priority_ranking must contain one row per candidate")
    else:
        debt_rows = report.get("research_debt", [])
        debt_by_symbol: dict[str, list[Mapping[str, Any]]] = {}
        if isinstance(debt_rows, list):
            for row in debt_rows:
                if isinstance(row, Mapping):
                    debt_by_symbol.setdefault(str(row.get("symbol")), []).append(row)
        last_score: Optional[float] = None
        for idx, item in enumerate(ranking, start=1):
            if not isinstance(item, Mapping):
                errors.append(f"candidate_priority_ranking[{idx - 1}] must be an object")
                continue
            if item.get("rank") != idx:
                errors.append(f"candidate_priority_ranking[{idx - 1}].rank must equal {idx}")
            score = _as_float(item.get("priority_score"))
            if score is None or score < 0 or score > 100:
                errors.append(f"candidate_priority_ranking[{idx - 1}].priority_score must be 0-100")
            elif last_score is not None and score > last_score:
                errors.append("candidate_priority_ranking must be sorted by descending priority_score")
            last_score = score
            for field in ["research_priority_score", "action_priority_score"]:
                field_score: Any = _as_float(item.get(field))
                if field_score is None or field_score < 0 or field_score > 100:
                    errors.append(f"candidate_priority_ranking[{idx - 1}].{field} must be 0-100")
            if str(item.get("rating_cap")) not in RATING_CAPS:
                errors.append(f"candidate_priority_ranking[{idx - 1}].rating_cap is unknown")
            if str(item.get("action_readiness")) not in ACTION_READINESS:
                errors.append(f"candidate_priority_ranking[{idx - 1}].action_readiness is unknown")
            if not isinstance(item.get("decision_grade"), bool):
                errors.append(f"candidate_priority_ranking[{idx - 1}].decision_grade must be boolean")
            action_gate: Any = item.get("action_gate")
            if not isinstance(action_gate, Mapping):
                errors.append(f"candidate_priority_ranking[{idx - 1}].action_gate must be an object")
            else:
                primary_gate: Any = str(action_gate.get("primary_gate") or "")
                if primary_gate not in ACTION_GATE_TYPES:
                    errors.append(f"candidate_priority_ranking[{idx - 1}].action_gate.primary_gate is unknown")
                if not isinstance(action_gate.get("secondary_gates", []), list):
                    errors.append(f"candidate_priority_ranking[{idx - 1}].action_gate.secondary_gates must be an array")
                if not isinstance(action_gate.get("blocking_datasets", []), list):
                    errors.append(f"candidate_priority_ranking[{idx - 1}].action_gate.blocking_datasets must be an array")
                if not isinstance(action_gate.get("blocking_reasons", []), list):
                    errors.append(f"candidate_priority_ranking[{idx - 1}].action_gate.blocking_reasons must be an array")
                readiness: Any = str(item.get("action_readiness") or "")
                primary_gate_class: Any = str(action_gate.get("primary_gate_class") or "")
                gate_classes: Any = action_gate.get("gate_classes")
                if primary_gate_class not in ACTION_GATE_CLASSES:
                    errors.append(f"candidate_priority_ranking[{idx - 1}].action_gate.primary_gate_class is unknown")
                if not isinstance(gate_classes, Mapping):
                    errors.append(f"candidate_priority_ranking[{idx - 1}].action_gate.gate_classes must be an object")
                elif primary_gate != "NONE" and gate_classes.get(primary_gate) != primary_gate_class:
                    errors.append(f"candidate_priority_ranking[{idx - 1}].action_gate.gate_classes must include the primary gate class")
                if primary_gate == "DATA_GATED" and readiness != "DATA_GATED":
                    errors.append(f"{item.get('symbol')} DATA_GATED requires DATA_GATED action_readiness")
                if primary_gate == "EVIDENCE_GATED" and readiness != "RESEARCH_GATED":
                    errors.append(f"{item.get('symbol')} EVIDENCE_GATED requires RESEARCH_GATED action_readiness")
                if primary_gate == "VALUATION_GATED":
                    expected: Any = "DATA_GATED" if primary_gate_class == "DATA_ACQUISITION" else "RESEARCH_GATED"
                    if readiness != expected:
                        errors.append(f"{item.get('symbol')} VALUATION_GATED requires {expected} action_readiness")
                if primary_gate in {"CAPITAL_ACTION_GATED", "AI_REVIEW_GATED"} and readiness != "RESEARCH_GATED":
                    errors.append(f"{item.get('symbol')} {primary_gate} requires RESEARCH_GATED action_readiness")
                if primary_gate == "BUY_POINT_GATED" and readiness != "WAIT_FOR_BUY_POINT":
                    errors.append(f"{item.get('symbol')} BUY_POINT_GATED requires WAIT_FOR_BUY_POINT action_readiness")
    for row in report.get("growth_hypothesis_matrix", []) if isinstance(report.get("growth_hypothesis_matrix"), list) else []:
        if not isinstance(row, Mapping):
            continue
        implied: Any = str(row.get("market_implied_growth"))
        supported: Any = str(row.get("evidence_supported_growth"))
        if implied not in GROWTH_ORDER:
            errors.append(f"{row.get('symbol')} market_implied_growth is unknown: {implied}")
        if supported not in GROWTH_ORDER:
            errors.append(f"{row.get('symbol')} evidence_supported_growth is unknown: {supported}")
        if row.get("valuation_input_ref") != f"valuation_input_matrix:{row.get('symbol')}":
            errors.append(f"{row.get('symbol')} growth row must reference valuation_input_matrix")
        valuation_rows: Any = report.get("valuation_input_matrix", [])
        valuation_row: Any = next(
            (
                item for item in valuation_rows
                if isinstance(item, Mapping) and item.get("symbol") == row.get("symbol")
            ),
            {},
        ) if isinstance(valuation_rows, list) else {}
        valuation_complete: Any = (
            isinstance(valuation_row, Mapping)
            and valuation_row.get("status") == "OK"
            and valuation_row.get("total_market_cap") is not None
            and bool(str(valuation_row.get("currency") or "").strip())
            and (row.get("pe_preflight") is not None or row.get("ps_preflight") is not None)
        )
        if implied != "UNKNOWN" and not valuation_complete:
            errors.append(f"{row.get('symbol')} market_implied_growth requires complete valuation inputs and computed PE/PS")
        expected_implied: Any = _growth_level_from_valuation(_as_float(row.get("pe_preflight")), _as_float(row.get("ps_preflight"))) if valuation_complete else "UNKNOWN"
        if implied != expected_implied:
            errors.append(f"{row.get('symbol')} market_implied_growth must match valuation-derived PE/PS tier {expected_implied}")
        financial_row: Mapping[str, Any] = financial_by_symbol.get(str(row.get("symbol") or ""), {})
        market_cap: Optional[float] = _as_float(row.get("total_market_cap"))
        revenue_absolute = _as_float(financial_row.get("revenue_absolute"))
        net_income_absolute = _as_float(financial_row.get("net_income_absolute"))
        expected_pe: Optional[float] = market_cap / net_income_absolute if market_cap is not None and net_income_absolute and net_income_absolute > 0 else None
        expected_ps: Optional[float] = market_cap / revenue_absolute if market_cap is not None and revenue_absolute and revenue_absolute > 0 else None
        if row.get("pe_preflight") is not None and not _close_enough(_as_float(row.get("pe_preflight")), expected_pe):
            errors.append(f"{row.get('symbol')} pe_preflight must use normalized absolute net income")
        if row.get("ps_preflight") is not None and not _close_enough(_as_float(row.get("ps_preflight")), expected_ps):
            errors.append(f"{row.get('symbol')} ps_preflight must use normalized absolute revenue")
        if not isinstance(row.get("h4_h5_evidence_bar_met"), bool):
            errors.append(f"{row.get('symbol')} h4_h5_evidence_bar_met must be boolean")
        else:
            expected_bar: Any = GROWTH_ORDER.get(implied, -1) < 4 or GROWTH_ORDER.get(supported, -1) >= GROWTH_ORDER.get(implied, -1)
            if row.get("h4_h5_evidence_bar_met") is not expected_bar:
                errors.append(f"{row.get('symbol')} h4_h5_evidence_bar_met must match market/evidence growth tiers")
        valuation_input_ready: bool = (
            isinstance(valuation_row, Mapping)
            and valuation_row.get("status") == "OK"
            and row.get("total_market_cap") is not None
        )
        if not valuation_input_ready:
            expected_gap: Any = "valuation_input_required"
        elif row.get("valuation_currency_match") is False or not str(row.get("financial_currency") or "").strip() or not str(row.get("valuation_currency") or "").strip():
            expected_gap = "valuation_currency_reconciliation_required"
        elif implied == "UNKNOWN":
            expected_gap = "valuation_input_required"
        elif GROWTH_ORDER.get(supported, -1) >= GROWTH_ORDER.get(implied, -1) and GROWTH_ORDER.get(implied, -1) >= 0:
            expected_gap = "roughly_matched"
        elif GROWTH_ORDER.get(implied, -1) >= 4 and GROWTH_ORDER.get(supported, -1) < GROWTH_ORDER.get(implied, -1):
            expected_gap = "market_ahead_of_evidence"
        else:
            expected_gap = "requires_ai_review"
        if row.get("gap") != expected_gap:
            errors.append(f"{row.get('symbol')} growth gap must be {expected_gap}")
        if implied in {"H4", "H5"} and GROWTH_ORDER.get(supported, -1) < GROWTH_ORDER[implied]:
            if row.get("h4_h5_evidence_bar_met") is not False:
                errors.append(f"{row.get('symbol')} H4/H5 valuation gap requires h4_h5_evidence_bar_met=false")
            debt_rows = report.get("research_debt", [])
            has_growth_debt: Any = any(
                isinstance(item, Mapping)
                and item.get("symbol") == row.get("symbol")
                and item.get("dataset") == "valuation_growth"
                for item in debt_rows
            ) if isinstance(debt_rows, list) else False
            if not has_growth_debt:
                errors.append(f"{row.get('symbol')} H4/H5 valuation gap requires valuation_growth research debt")
    for row in report.get("capital_actions", []) if isinstance(report.get("capital_actions"), list) else []:
        if not isinstance(row, Mapping):
            continue
        actions: Any = row.get("actions", [])
        if not isinstance(actions, list):
            errors.append("capital_actions.actions must be an array")
            continue
        for action in actions:
            if isinstance(action, Mapping) and action.get("action_type") == "private_placement" and not str(action.get("research_debt", "")).strip():
                errors.append(f"{row.get('symbol')} private placement requires dilution research debt")
    for row in report.get("technical_timing_matrix", []) if isinstance(report.get("technical_timing_matrix"), list) else []:
        if not isinstance(row, Mapping):
            continue
        if row.get("trend_state") == "CONSTRUCTIVE_PULLBACK_WATCH" and row.get("buy_point_claim_allowed") is not False:
            errors.append(f"{row.get('symbol')} short-average proximity cannot be marked as a confirmed buy point")
        if row.get("status") == "DATA_GATED" or row.get("chan_action") == "DATA_REQUIRED":
            debt_rows = report.get("research_debt", [])
            has_technical_debt: Any = any(
                isinstance(item, Mapping)
                and item.get("symbol") == row.get("symbol")
                and item.get("dataset") == "price_history_adjusted"
                for item in debt_rows
            ) if isinstance(debt_rows, list) else False
            if not has_technical_debt:
                errors.append(f"{row.get('symbol')} missing adjusted history requires price_history_adjusted research debt")
    return errors


def build_comparison_report(manifest_paths: Sequence[Path], overlays: Optional[Mapping[str, Mapping[str, Any]]] = None) -> dict[str, Any]:
    manifests: Any = [_load_manifest(path) for path in manifest_paths]
    if len(manifests) < 2:
        raise ValueError("comparison requires at least two manifest paths")
    profiles: Any = _overlay_profiles(manifests, overlays)

    candidates: Any = []
    data_rows: Any = []
    layer_rows: Any = []
    financial_rows: Any = []
    valuation_rows: Any = []
    currency_rows: Any = []
    growth_rows: Any = []
    technical_rows: Any = []
    capital_rows: Any = []
    consumption_rows: list[dict[str, Any]] = []
    debt_rows: list[dict[str, Any]] = []
    ranking_seed: Any = []

    for manifest, path in zip(manifests, manifest_paths):
        symbol: Any = _symbol(manifest)
        profile: Any = profiles.get(symbol, {})
        data_summary: Any = _data_summary(manifest)
        financial: Any = _financial_quality(manifest)
        valuation: Any = _valuation_input_row(manifest)
        currency_normalization: Any = _currency_normalization_row(manifest, financial, valuation)
        technical: Any = _technical_summary(manifest)
        capital: Any = _capital_summary(manifest)
        layer: Any = _serenity_layer(manifest, profile)
        growth: Any = _growth_hypothesis(manifest, financial, profile, currency_normalization)
        candidate_consumption: Any = [
            financial_consumption_audit(
                symbol=symbol,
                raw_status=_dataset_status(manifest, "financials"),
                financial_payload=_load_result_json(manifest, "financials"),
                financial_row=financial,
            ),
            valuation_consumption_audit(
                symbol=symbol,
                raw_status=_dataset_status(manifest, "valuation_inputs"),
                valuation_payload=_valuation_audit_payload(manifest),
                valuation_row=valuation,
                growth_row=growth,
                currency_normalization_row=currency_normalization,
            ),
        ]
        candidate_debt: Any = _research_debt_rows(manifest, capital, financial, technical, layer, growth)
        candidate_debt.extend(_research_debt_from_consumption(candidate_consumption))
        score: Any
        research_score: Any
        action_score: Any
        action_readiness: Any
        reason: Any
        action_gate: Any
        score, research_score, action_score, action_readiness, reason, action_gate = _priority_score(data_summary, financial, technical, capital, layer, growth, candidate_debt)

        candidates.append({
            "symbol": symbol,
            "name": _candidate_name(manifest),
            "market": _market(manifest),
            "currency": _currency(manifest),
            "rating_cap": data_summary["rating_cap"],
            "data_package_path": str(path),
        })
        data_rows.append(data_summary)
        layer_rows.append(layer)
        financial_rows.append(financial)
        valuation_rows.append(valuation)
        currency_rows.append(currency_normalization)
        growth_rows.append(growth)
        technical_rows.append(technical)
        capital_rows.append(capital)
        consumption_rows.extend(candidate_consumption)
        debt_rows.extend(candidate_debt)
        ranking_seed.append({
            "symbol": symbol,
            "priority_score": score,
            "research_priority_score": research_score,
            "action_priority_score": action_score,
            "rating_cap": data_summary["rating_cap"],
            "action_readiness": action_readiness,
            "action_gate": action_gate,
            "key_reason": reason,
        })

    ranked: Any = sorted(ranking_seed, key=lambda item: item["priority_score"], reverse=True)
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index

    next_actions: Any = []
    for row in debt_rows:
        action: Any = str(row.get("next_action") or row.get("objective") or "")
        if action and action not in next_actions:
            next_actions.append(action)

    ranking_validity: Any = _ranking_validity(consumption_rows, debt_rows)
    for item in ranked:
        item["decision_grade"] = ranking_validity.get("status") == "VALID"
    readiness_rows: list[dict[str, Any]] = _readiness_matrix(
        data_rows=data_rows,
        ranked_rows=ranked,
        consumption_rows=consumption_rows,
        debt_rows=debt_rows,
        ranking_validity=ranking_validity,
    )
    report: Any = {
        "comparison_scope": {
            "candidate_count": len(candidates),
            "as_of": dt.datetime.now(dt.timezone.utc).isoformat(),
            "basis": "fetch_manifest_plus_deterministic_decision_matrices",
        },
        "candidates": candidates,
        "data_acquisition_summary": data_rows,
        "serenity_layer_matrix": layer_rows,
        "financial_quality_matrix": financial_rows,
        "valuation_input_matrix": valuation_rows,
        "currency_normalization_matrix": currency_rows,
        "growth_hypothesis_matrix": growth_rows,
        "technical_timing_matrix": technical_rows,
        "capital_actions": capital_rows,
        "data_consumption_audit": consumption_rows,
        "readiness_matrix": readiness_rows,
        "research_debt": debt_rows,
        "candidate_priority_ranking": ranked,
        "final_decision": _final_decision(ranked, next_actions, ranking_validity),
    }
    errors: Any = validate_comparison_report(report)
    if errors:
        raise ValueError("; ".join(errors))
    return report


def to_markdown(report: Mapping[str, Any]) -> str:
    lines: Any = [
        "# 候选公司对比决策报告",
        "",
        "## 0. 结论先行",
    ]
    decision: Any = report.get("final_decision", {}) if isinstance(report.get("final_decision"), Mapping) else {}
    lines.append(f"- 优先候选：{decision.get('top_candidate', '')}")
    lines.append(f"- 决策模式：{display_label(decision.get('decision_mode', ''))}")
    lines.append(f"- 与第二名分差：{decision.get('score_gap_to_runner_up', '')}")
    if decision.get("candidate_count_warning"):
        lines.append(f"- 候选池提示：{display_label(decision.get('candidate_count_warning'))}")
    ranking_validity: Any = decision.get("ranking_validity") if isinstance(decision.get("ranking_validity"), Mapping) else {}
    lines.append(f"- 排序可信度：{display_label(ranking_validity.get('status', ''))}｜{ranking_validity.get('reason', '')}")
    lines.append(f"- 决策说明：{decision.get('decision', '')}")
    invalid_ranking: bool = ranking_validity.get("status") == "INVALID"
    if invalid_ranking:
        lines.extend([
            "",
            "> 本报告不产生正式优先候选。以下排序仅用于定位数据消费、研究债务和工程修复点，不代表投资研究排序。",
        ])
    ranking_title: str = "工程诊断排序｜非投资候选排序" if invalid_ranking else "候选优先级"
    lines.extend(["", f"## 1. {ranking_title}", "| 排名 | 标的 | 可形成结论 | 研究分 | 行动分 | 优先级 | 主门控 | 行动状态 | 理由 |", "|---:|---|---|---:|---:|---:|---|---|---|"])
    for row in report.get("candidate_priority_ranking", []):
        if isinstance(row, Mapping):
            gate: Any = row.get("action_gate") if isinstance(row.get("action_gate"), Mapping) else {}
            decision_grade: bool = bool(row.get("decision_grade"))
            lines.append(f"| {row.get('rank')} | {row.get('symbol')} | {display_bool(decision_grade)} | {row.get('research_priority_score')} | {row.get('action_priority_score')} | {row.get('priority_score')} | {display_label(gate.get('primary_gate', ''))} | {display_label(row.get('action_readiness'))} | {row.get('key_reason')} |")
    lines.extend(["", "## 1.2 三层状态", "| 标的 | 数据获取状态 | 研究状态 | 行动状态 | 数据证据上限 | 原因码 |", "|---|---|---|---|---|---|"])
    for row in report.get("readiness_matrix", []):
        if isinstance(row, Mapping):
            reasons: Any = display_list(row.get("reason_codes", []), empty="")
            lines.append(f"| {row.get('symbol')} | {display_label(row.get('fetch_status'))} | {display_label(row.get('research_readiness'))} | {display_label(row.get('action_readiness'))} | {row.get('data_evidence_cap')} | {reasons} |")
    lines.extend(["", "## 1.1 行动门控", "| 标的 | 主门控 | 门控类别 | 次级门控 | 门控类别明细 | 阻断数据集 | 阻断原因 |", "|---|---|---|---|---|---|---|"])
    for row in report.get("candidate_priority_ranking", []):
        if isinstance(row, Mapping):
            gate = row.get("action_gate") if isinstance(row.get("action_gate"), Mapping) else {}
            secondary: Any = display_list(gate.get("secondary_gates", []), empty="")
            gate_classes: str = _gate_class_summary(gate.get("gate_classes"))
            datasets: Any = display_list(gate.get("blocking_datasets", []), empty="")
            reasons: Any = "; ".join(str(item) for item in gate.get("blocking_reasons", []) if item) if isinstance(gate.get("blocking_reasons", []), list) else ""
            lines.append(f"| {row.get('symbol')} | {display_label(gate.get('primary_gate', ''))} | {display_label(gate.get('primary_gate_class', ''))} | {secondary} | {gate_classes} | {datasets} | {reasons} |")
    lines.extend(["", "## 2. 数据消费审计", "| 标的 | 数据集 | 原始状态 | 行数 | 消费状态 | 原因码 | 必需转换 | 阻断矩阵 | 选中期间 | 选择规则 | 警告 |", "|---|---|---|---:|---|---|---|---|---|---|---|"])
    for row in report.get("data_consumption_audit", []):
        if isinstance(row, Mapping):
            warnings: Any = "; ".join(str(item) for item in row.get("warnings", []) if item) if isinstance(row.get("warnings", []), list) else ""
            blocked: Any = display_list(row.get("blocked_matrices", []), empty="")
            lines.append(f"| {row.get('symbol')} | {row.get('dataset')} | {display_label(row.get('raw_status'))} | {row.get('row_count')} | {display_label(row.get('consumption_status'))} | {display_label(row.get('reason_code'))} | {row.get('required_transform', '')} | {blocked} | {row.get('selected_period')} | {row.get('selection_rule')} | {warnings} |")
    lines.extend(["", "## 3. 数据追索与研究债务", "| 标的 | 数据集 | 优先级 | 下一步动作 |", "|---|---|---|---|"])
    for row in report.get("research_debt", []):
        if isinstance(row, Mapping):
            lines.append(f"| {row.get('symbol')} | {display_label(row.get('dataset', ''))} | {display_label(row.get('priority', ''))} | {row.get('next_action') or row.get('objective', '')} |")
    lines.extend(["", "## 4. 财务质量矩阵", "| 标的 | 分数 | 年报期间 | 选择规则 | 金额单位 | 收入增速 | 净利率 | 经营现金流/净利润 | 负债/资产 | 预检标签 |", "|---|---:|---|---|---|---:|---:|---:|---:|---|"])
    for row in report.get("financial_quality_matrix", []):
        if isinstance(row, Mapping):
            amount_unit: str = f"{row.get('financial_statement_unit', '')} x{row.get('financial_unit_multiplier', '')}"
            lines.append(f"| {row.get('symbol')} | {row.get('score')} | {row.get('latest_annual_period', '')} | {row.get('latest_annual_selection_rule', '')} | {amount_unit} | {_display_cell(row.get('revenue_growth_pct'))} | {_display_cell(row.get('net_margin_pct'))} | {_display_cell(row.get('ocf_to_net_income_pct'))} | {_display_cell(row.get('debt_to_assets_pct'))} | {display_label(row.get('label', ''))} |")
    lines.extend(["", "## 5. 估值输入矩阵", "| 标的 | 状态 | 阶段 | 价格 | 股数 | 总市值 | 币种 | 来源 | 口径 | 需复核 |", "|---|---|---|---:|---:|---:|---|---|---|---|"])
    for row in report.get("valuation_input_matrix", []):
        if isinstance(row, Mapping):
            basis: Any = row.get("market_cap_basis") or row.get("share_count_basis") or row.get("source_basis")
            lines.append(f"| {row.get('symbol')} | {display_label(row.get('status'))} | {display_label(row.get('valuation_stage'))} | {row.get('regular_market_price')} | {row.get('total_shares')} | {row.get('total_market_cap')} | {row.get('currency')} | {row.get('source_name')} | {basis} | {display_bool(row.get('verification_needed'))} |")
    lines.extend(["", "## 5.1 币种归一矩阵", "| 标的 | 状态 | 估值币种 | 财报币种 | 原始总市值 | 汇率 | 归一后总市值 | 汇率来源 | 原因 |", "|---|---|---|---|---:|---:|---:|---|---|"])
    for row in report.get("currency_normalization_matrix", []):
        if isinstance(row, Mapping):
            lines.append(f"| {row.get('symbol')} | {display_label(row.get('normalization_status'))} | {row.get('source_currency')} | {row.get('target_currency')} | {row.get('original_total_market_cap')} | {row.get('fx_rate')} | {row.get('normalized_total_market_cap')} | {row.get('fx_source')} | {display_label(row.get('reason_code'))} |")
    lines.extend(["", "## 6. 市场隐含增长 vs 证据支持增长", "| 标的 | 估值引用 | 市场隐含增长 | 证据支持增长 | PE | PS | 财务金额口径 | 缺口 | 所需证据 |", "|---|---|---|---|---:|---:|---|---|---|"])
    for row in report.get("growth_hypothesis_matrix", []):
        if isinstance(row, Mapping):
            amount_basis: str = f"revenue={row.get('revenue_amount')} / net_income={row.get('net_income_amount')} / unit={row.get('financial_statement_unit')} x{row.get('financial_unit_multiplier')}"
            lines.append(f"| {row.get('symbol')} | {row.get('valuation_input_ref')} | {display_label(row.get('market_implied_growth'))} | {display_label(row.get('evidence_supported_growth'))} | {row.get('pe_preflight')} | {row.get('ps_preflight')} | {amount_basis} | {display_label(row.get('gap'))} | {row.get('required_next_evidence')} |")
    lines.extend(["", "## 7. 技术健康与缠论动作", "| 标的 | 历史深度 | 趋势状态 | 缠论动作 | 允许买点判断 | 说明 |", "|---|---|---|---|---|---|"])
    for row in report.get("technical_timing_matrix", []):
        if isinstance(row, Mapping):
            lines.append(f"| {row.get('symbol')} | {display_label(row.get('history_depth_status'))} | {display_label(row.get('trend_state'))} | {display_label(row.get('chan_action'))} | {display_bool(row.get('buy_point_claim_allowed'))} | {row.get('decision_note')} |")
    lines.extend(["", "## 8. A 股资本动作", "| 标的 | 风险 | 动作类型 | 研究债务 |", "|---|---|---|---|"])
    for row in report.get("capital_actions", []):
        if isinstance(row, Mapping):
            summary: Any = row.get("summary", {}) if isinstance(row.get("summary"), Mapping) else {}
            lines.append(f"| {row.get('symbol')} | {display_label(summary.get('material_risk_level'))} | {display_list(summary.get('action_types', []), empty='无')} | {'; '.join(row.get('research_debt', [])) if isinstance(row.get('research_debt'), list) else ''} |")
    lines.append("")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: Any = argparse.ArgumentParser(description="Build Serenity + Chan candidate comparison report")
    parser.add_argument("manifests", nargs="+", help="fetch manifest JSON paths")
    parser.add_argument("--format", choices=["json", "md", "both"], default="json")
    args: Any = parser.parse_args(argv)
    try:
        report: Any = build_comparison_report([Path(path) for path in args.manifests])
        if args.format == "json":
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif args.format == "md":
            print(to_markdown(report))
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            print("\n---\n")
            print(to_markdown(report))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
