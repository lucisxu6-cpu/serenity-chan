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
    from technical_health import analyze_price_csv
except ModuleNotFoundError:  # pragma: no cover - supports python -m scripts.build_comparison_report
    from scripts.a_share_capital_actions import analyze_announcements
    from scripts.technical_health import analyze_price_csv


RATING_SCORE_LIMIT = {"S": 100.0, "A": 84.0, "B": 72.0, "C": 55.0, "D": 35.0, "OBSERVE_ONLY": 25.0}
STATUS_SCORE = {"OK": 100.0, "PARTIAL": 70.0, "STALE": 55.0, "PENDING": 45.0, "NOT_REQUESTED": 35.0, "NOT_APPLICABLE": 35.0, "FAILED": 25.0}
CAPITAL_RISK_SCORE = {"none": 0.0, "supportive": -2.0, "low": 3.0, "medium": 8.0, "medium_high": 12.0, "high": 18.0}
GROWTH_ORDER = {"H0": 0, "H1": 1, "H2": 2, "H3": 3, "H4": 4, "H5": 5, "UNKNOWN": -1}
RATING_CAPS = set(RATING_SCORE_LIMIT)
ACTION_READINESS = {"CORE_CANDIDATE", "STRONG_OBSERVE", "CANDIDATE_POOL", "WAIT_FOR_BUY_POINT", "DATA_GATED", "LEAD_TRACKING", "ELIMINATE", "OBSERVE_ONLY"}
ACTION_BLOCKING_DEBT_DATASETS = {
    "current_quote",
    "price_history_adjusted",
    "financials",
    "valuation",
    "valuation_growth",
    "serenity_layer",
}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_manifest(path: Path) -> Mapping[str, Any]:
    loaded = _load_json(path)
    if not isinstance(loaded, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    manifest = dict(loaded)
    manifest["_manifest_path"] = str(path.resolve())
    return manifest


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    return None if value is None else round(value, digits)


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(str(value).replace(",", ""))
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


def _result_for_dataset(manifest: Mapping[str, Any], dataset: str) -> Mapping[str, Any]:
    for item in manifest.get("results", []) if isinstance(manifest.get("results"), list) else []:
        if isinstance(item, Mapping) and item.get("dataset") == dataset:
            return item
    return {}


def _path_from_result(manifest: Mapping[str, Any], result: Mapping[str, Any], key: str = "data_path") -> Optional[Path]:
    value = result.get(key)
    if not value:
        return None
    path = Path(str(value))
    if path.is_absolute() and path.exists():
        return path
    manifest_path = manifest.get("_manifest_path")
    candidates = [path]
    if manifest_path:
        manifest_file = Path(str(manifest_path))
        candidates.append(manifest_file.parent / path)
        candidates.extend(parent / path for parent in manifest_file.parents)
    out_dir = manifest.get("out_dir")
    if out_dir:
        candidates.append(Path(str(out_dir)) / path)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _load_result_json(manifest: Mapping[str, Any], dataset: str) -> Any:
    path = _path_from_result(manifest, _result_for_dataset(manifest, dataset))
    if not path:
        return None
    return _load_json(path)


def _symbol(manifest: Mapping[str, Any]) -> str:
    symbol = manifest.get("symbol")
    if isinstance(symbol, Mapping):
        return str(symbol.get("symbol") or symbol.get("input_value") or "")
    return ""


def _market(manifest: Mapping[str, Any]) -> str:
    symbol = manifest.get("symbol")
    if isinstance(symbol, Mapping):
        return str(symbol.get("market") or "UNKNOWN")
    return "UNKNOWN"


def _currency(manifest: Mapping[str, Any]) -> str:
    symbol = manifest.get("symbol")
    if isinstance(symbol, Mapping):
        return str(symbol.get("currency") or "UNKNOWN")
    return "UNKNOWN"


def _candidate_name(manifest: Mapping[str, Any]) -> str:
    quote = _load_result_json(manifest, "current_quote")
    if isinstance(quote, Mapping) and quote.get("name"):
        return str(quote.get("name"))
    filings = _load_result_json(manifest, "filings_announcements")
    if isinstance(filings, Mapping) and filings.get("name"):
        return str(filings.get("name"))
    financials = _load_result_json(manifest, "financials")
    if isinstance(financials, Mapping):
        periods = financials.get("periods")
        if isinstance(periods, list):
            for row in reversed(periods):
                if isinstance(row, Mapping) and row.get("security_name"):
                    return str(row.get("security_name"))
    return ""


def _periods(financials: Any) -> list[Mapping[str, Any]]:
    if not isinstance(financials, Mapping):
        return []
    rows = financials.get("periods", [])
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, Mapping) and row.get("period")]


def _period_year(period: Any) -> Optional[int]:
    text = str(period or "")
    try:
        return int(text[:4])
    except Exception:
        return None


def _latest_annual(rows: Sequence[Mapping[str, Any]], before_year: Optional[int] = None) -> Optional[Mapping[str, Any]]:
    candidates = []
    for row in rows:
        period = str(row.get("period") or "")
        year = _period_year(period)
        if not period.endswith("12-31") or year is None:
            continue
        if before_year is not None and year >= before_year:
            continue
        candidates.append(row)
    return max(candidates, key=lambda row: str(row.get("period"))) if candidates else None


def _latest_quarter(rows: Sequence[Mapping[str, Any]], suffix: str, before_year: Optional[int] = None) -> Optional[Mapping[str, Any]]:
    candidates = []
    for row in rows:
        period = str(row.get("period") or "")
        year = _period_year(period)
        if not period.endswith(suffix) or year is None:
            continue
        if before_year is not None and year >= before_year:
            continue
        candidates.append(row)
    return max(candidates, key=lambda row: str(row.get("period"))) if candidates else None


def _financial_quality(manifest: Mapping[str, Any]) -> dict[str, Any]:
    financials = _load_result_json(manifest, "financials")
    rows = _periods(financials)
    source_level = str((financials or {}).get("source_level") or _result_for_dataset(manifest, "financials").get("source_level") or "")
    latest_annual = _latest_annual(rows)
    latest_annual_year = _period_year(latest_annual.get("period")) if latest_annual else None
    previous_annual = _latest_annual(rows, before_year=latest_annual_year) if latest_annual_year else None
    latest_q1 = _latest_quarter(rows, "03-31")
    latest_q1_year = _period_year(latest_q1.get("period")) if latest_q1 else None
    previous_q1 = _latest_quarter(rows, "03-31", before_year=latest_q1_year) if latest_q1_year else None

    if not latest_annual:
        return {
            "symbol": _symbol(manifest),
            "status": "DATA_GATED",
            "score": 30.0,
            "source_level": source_level,
            "research_debt": "Financial statement rows are missing from the data package.",
        }

    revenue = _as_float(latest_annual.get("revenue"))
    previous_revenue = _as_float(previous_annual.get("revenue")) if previous_annual else None
    net_income = _as_float(latest_annual.get("net_income") or latest_annual.get("net_profit"))
    previous_net_income = _as_float(previous_annual.get("net_income") or previous_annual.get("net_profit")) if previous_annual else None
    operating_cash_flow = _as_float(latest_annual.get("operating_cash_flow"))
    operating_cost = _as_float(latest_annual.get("operating_cost"))
    gross_profit = (revenue - operating_cost) if revenue is not None and operating_cost is not None else _as_float(latest_annual.get("gross_profit"))
    assets = _as_float(latest_annual.get("assets"))
    liabilities = _as_float(latest_annual.get("liabilities"))
    receivables = _as_float(latest_annual.get("accounts_receivable"))
    inventory = _as_float(latest_annual.get("inventory"))
    rd_expense = _as_float(latest_annual.get("research_expense"))
    latest_row = max(rows, key=lambda row: str(row.get("period"))) if rows else latest_annual
    share_capital = _as_float(latest_row.get("share_capital")) or _as_float(latest_annual.get("share_capital"))
    q1_revenue_growth = _pct_change(_as_float(latest_q1.get("revenue")) if latest_q1 else None, _as_float(previous_q1.get("revenue")) if previous_q1 else None)
    q1_net_income_growth = _pct_change(_as_float(latest_q1.get("net_income") or latest_q1.get("net_profit")) if latest_q1 else None, _as_float(previous_q1.get("net_income") or previous_q1.get("net_profit")) if previous_q1 else None)
    q1_ocf_to_ni = _ratio(
        _as_float(latest_q1.get("operating_cash_flow")) if latest_q1 else None,
        _as_float(latest_q1.get("net_income") or latest_q1.get("net_profit")) if latest_q1 else None,
    )

    revenue_growth = _pct_change(revenue, previous_revenue)
    net_income_growth = _pct_change(net_income, previous_net_income)
    turned_profitable = previous_net_income is not None and previous_net_income < 0 and (net_income or 0) > 0
    gross_margin = _ratio(gross_profit, revenue)
    net_margin = _ratio(net_income, revenue)
    ocf_to_ni = _ratio(operating_cash_flow, net_income)
    receivables_to_revenue = _ratio(receivables, revenue)
    inventory_to_revenue = _ratio(inventory, revenue)
    debt_to_assets = _ratio(liabilities, assets)
    rd_to_revenue = _ratio(rd_expense, revenue)

    score = 45.0
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
        label = "strong_preflight"
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
        "latest_q1_period": str(latest_q1.get("period")) if latest_q1 else "",
        "revenue": _round(revenue),
        "revenue_growth_pct": _round(revenue_growth),
        "net_income": _round(net_income),
        "net_income_growth_pct": None if turned_profitable else _round(net_income_growth),
        "turned_profitable": turned_profitable,
        "gross_margin_pct": _round(gross_margin),
        "net_margin_pct": _round(net_margin),
        "ocf_to_net_income_pct": _round(ocf_to_ni),
        "receivables_to_revenue_pct": _round(receivables_to_revenue),
        "inventory_to_revenue_pct": _round(inventory_to_revenue),
        "debt_to_assets_pct": _round(debt_to_assets),
        "rd_to_revenue_pct": _round(rd_to_revenue),
        "share_capital": _round(share_capital),
        "q1_revenue_growth_pct": _round(q1_revenue_growth),
        "q1_net_income_growth_pct": _round(q1_net_income_growth),
        "q1_ocf_to_net_income_pct": _round(q1_ocf_to_ni),
        "research_debt": "Reconcile core financial lines with L0/L1 annual and quarterly reports before A/S rating." if source_level.startswith("L3") else "",
    }


def _data_summary(manifest: Mapping[str, Any]) -> dict[str, Any]:
    acquisition = manifest.get("data_acquisition") if isinstance(manifest.get("data_acquisition"), Mapping) else {}
    quality = manifest.get("data_quality") if isinstance(manifest.get("data_quality"), Mapping) else {}
    statuses = acquisition.get("status_by_dataset") if isinstance(acquisition.get("status_by_dataset"), Mapping) else {}
    def count_field(count_key: str, list_key: str) -> int:
        items = acquisition.get(list_key)
        item_count = len(items) if isinstance(items, list) else 0
        value = acquisition.get(count_key)
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
    statuses = summary.get("status_by_dataset") if isinstance(summary.get("status_by_dataset"), Mapping) else {}
    if not statuses:
        return 25.0
    weights = {
        "current_quote": 0.20,
        "price_history_adjusted": 0.25,
        "financials": 0.30,
        "filings_announcements": 0.25,
    }
    return sum(STATUS_SCORE.get(str(statuses.get(key) or "NOT_REQUESTED"), 25.0) * weight for key, weight in weights.items())


def _technical_summary(manifest: Mapping[str, Any]) -> dict[str, Any]:
    price_path = _path_from_result(manifest, _result_for_dataset(manifest, "price_history_adjusted"))
    quote_path = _path_from_result(manifest, _result_for_dataset(manifest, "current_quote"))
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
    result = analyze_price_csv(price_path, quote_path)
    result["symbol"] = _symbol(manifest)
    return result


def _capital_summary(manifest: Mapping[str, Any]) -> dict[str, Any]:
    filings = _load_result_json(manifest, "filings_announcements")
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
    result = analyze_announcements(filings)
    result["symbol"] = _symbol(manifest)
    return result


def _serenity_layer(manifest: Mapping[str, Any], profile: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    profile = profile or {}
    layer = str(profile.get("layer") or "AI_REVIEW_REQUIRED")
    return {
        "symbol": _symbol(manifest),
        "layer": layer,
        "bottleneck_reason": str(profile.get("bottleneck_reason") or "Map the company to a value-chain bottleneck before final thesis grading."),
        "layer_score": _as_float(profile.get("layer_score")),
        "company_fit": _as_float(profile.get("company_fit")),
        "revenue_transmission": str(profile.get("revenue_transmission") or "Requires product-to-financial-line mapping from filings or company disclosure."),
        "evidence_gap": str(profile.get("evidence_gap") or "Layer mapping is an AI/domain-review task; do not infer it from ticker data alone."),
    }


def _growth_level_from_valuation(pe: Optional[float], ps: Optional[float]) -> str:
    if pe is None and ps is None:
        return "UNKNOWN"
    pe_value = pe if pe is not None and pe > 0 else None
    ps_value = ps if ps is not None and ps > 0 else None
    if (pe_value is not None and pe_value >= 120) or (ps_value is not None and ps_value >= 35):
        return "H5"
    if (pe_value is not None and pe_value >= 60) or (ps_value is not None and ps_value >= 18):
        return "H4"
    if (pe_value is not None and pe_value >= 35) or (ps_value is not None and ps_value >= 10):
        return "H3"
    if (pe_value is not None and pe_value >= 20) or (ps_value is not None and ps_value >= 5):
        return "H2"
    return "H1"


def _growth_hypothesis(manifest: Mapping[str, Any], financial: Mapping[str, Any], profile: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    profile = profile or {}
    score = _as_float(financial.get("score")) or 0.0
    if score >= 76:
        supported = "H3"
    elif score >= 62:
        supported = "H2"
    elif score >= 48:
        supported = "H1"
    elif financial.get("status") == "OK":
        supported = "H0"
    else:
        supported = "UNKNOWN"
    quote = _load_result_json(manifest, "current_quote")
    price = _as_float(quote.get("regular_market_price")) if isinstance(quote, Mapping) else None
    share_capital = _as_float(financial.get("share_capital"))
    revenue = _as_float(financial.get("revenue"))
    net_income = _as_float(financial.get("net_income"))
    market_cap = price * share_capital if price is not None and share_capital is not None else None
    pe = market_cap / net_income if market_cap is not None and net_income and net_income > 0 else None
    ps = market_cap / revenue if market_cap is not None and revenue and revenue > 0 else None
    market_implied = str(profile.get("market_implied_growth") or _growth_level_from_valuation(pe, ps))
    if market_implied == "UNKNOWN":
        gap = "valuation_input_required"
        required = "Add share capital, market cap, valuation multiples, and peer/DCF basis to infer market-implied growth."
    else:
        implied_order = GROWTH_ORDER.get(market_implied, -1)
        supported_order = GROWTH_ORDER.get(supported, -1)
        if supported_order >= implied_order and implied_order >= 0:
            gap = str(profile.get("growth_gap") or "roughly_matched")
        elif implied_order >= 4 and supported_order < implied_order:
            gap = str(profile.get("growth_gap") or "market_ahead_of_evidence")
        else:
            gap = str(profile.get("growth_gap") or "requires_ai_review")
        required = str(profile.get("required_next_evidence") or "Verify share capital, segment revenue, orders, capacity, and valuation with L0/L1 evidence.")
    implied_order = GROWTH_ORDER.get(market_implied, -1)
    supported_order = GROWTH_ORDER.get(supported, -1)
    h4_h5_bar_met = bool(profile.get("h4_h5_evidence_bar_met", implied_order < 4 or supported_order >= implied_order))
    return {
        "symbol": str(financial.get("symbol") or ""),
        "market_implied_growth": market_implied,
        "evidence_supported_growth": supported,
        "gap": gap,
        "h4_h5_evidence_bar_met": h4_h5_bar_met,
        "required_next_evidence": required,
        "posterior_basis": "Preliminary valuation from current quote, share capital, revenue, and net income; verify share capital and financial lines with L0/L1 evidence.",
        "market_cap": _round(market_cap),
        "pe_preflight": _round(pe),
        "ps_preflight": _round(ps),
    }


def _research_debt_rows(
    manifest: Mapping[str, Any],
    capital: Mapping[str, Any],
    financial: Mapping[str, Any],
    technical: Mapping[str, Any],
    layer: Mapping[str, Any],
    growth: Mapping[str, Any],
) -> list[dict[str, Any]]:
    symbol = _symbol(manifest)
    rows: list[dict[str, Any]] = []
    acquisition = manifest.get("data_acquisition") if isinstance(manifest.get("data_acquisition"), Mapping) else {}
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
            "next_action": "Fetch adjusted daily history with enough bars before making Chan timing or buy-point claims.",
        })
    if layer.get("layer") == "AI_REVIEW_REQUIRED":
        rows.append({"symbol": symbol, "dataset": "serenity_layer", "priority": "high", "next_action": str(layer.get("evidence_gap"))})
    if growth.get("market_implied_growth") == "UNKNOWN":
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
        key = (str(row.get("symbol")), str(row.get("dataset")), str(row.get("next_action") or row.get("objective")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _debt_gate_profile(debt_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    priorities_by_dataset: dict[str, set[str]] = {}
    for row in debt_rows:
        dataset = str(row.get("dataset") or row.get("task_type") or "unknown")
        priority = str(row.get("priority") or "").lower()
        priorities_by_dataset.setdefault(dataset, set()).add(priority)

    critical_datasets = {dataset for dataset, priorities in priorities_by_dataset.items() if "critical" in priorities}
    high_datasets = {dataset for dataset, priorities in priorities_by_dataset.items() if "high" in priorities}
    blocking_datasets = set(critical_datasets) | (high_datasets & ACTION_BLOCKING_DEBT_DATASETS)

    drag = 0.0
    if "financials" in critical_datasets:
        drag += 6.0
    elif critical_datasets:
        drag += 5.0
    if "valuation_growth" in high_datasets:
        drag += 6.0
    if "valuation" in high_datasets:
        drag += 4.0
    if "serenity_layer" in high_datasets:
        drag += 4.0
    if "capital_actions" in high_datasets:
        drag += 3.0
    if "current_quote" in high_datasets:
        drag += 4.0
    if "price_history_adjusted" in high_datasets:
        drag += 4.0
    other_high = high_datasets - {"valuation_growth", "valuation", "serenity_layer", "capital_actions", "current_quote", "price_history_adjusted"}
    drag += min(4.0, 2.0 * len(other_high))

    return {
        "critical_datasets": sorted(critical_datasets),
        "high_datasets": sorted(high_datasets),
        "blocking_datasets": sorted(blocking_datasets),
        "debt_drag": min(18.0, drag),
    }


def _priority_score(
    data_summary: Mapping[str, Any],
    financial: Mapping[str, Any],
    technical: Mapping[str, Any],
    capital: Mapping[str, Any],
    layer: Mapping[str, Any],
    research_debt: Sequence[Mapping[str, Any]],
) -> tuple[float, str, str]:
    financial_score = _as_float(financial.get("score")) or 35.0
    data_score = _data_readiness_score(data_summary)
    technical_score = _as_float(technical.get("readiness_score")) or 25.0
    layer_score = _as_float(layer.get("layer_score"))
    thesis_proxy = financial_score if layer_score is None else (financial_score * 0.55 + layer_score * 0.45)
    risk_level = str((capital.get("summary") or {}).get("material_risk_level") or "none") if isinstance(capital.get("summary"), Mapping) else "none"
    capital_drag = CAPITAL_RISK_SCORE.get(risk_level, 0.0)
    debt_profile = _debt_gate_profile(research_debt)
    debt_drag = _as_float(debt_profile.get("debt_drag")) or 0.0
    score = thesis_proxy * 0.42 + data_score * 0.24 + financial_score * 0.18 + technical_score * 0.12 + 8.0
    score -= capital_drag
    score -= debt_drag
    cap = str(data_summary.get("rating_cap") or "OBSERVE_ONLY")
    if cap in {"C", "D", "OBSERVE_ONLY"}:
        score = min(score, RATING_SCORE_LIMIT.get(cap, 25.0))
    score = max(0.0, min(100.0, score))
    has_blocking_debt = bool(debt_profile.get("blocking_datasets"))
    has_high_debt = bool(debt_profile.get("high_datasets"))
    if data_summary.get("research_debt_count") or has_blocking_debt:
        action = "DATA_GATED"
    elif technical.get("chan_action") in {"WAIT_FOR_SECOND_BUY", "WAIT_FOR_THIRD_BUY", "WAIT_FOR_STRUCTURE_CONFIRMATION"}:
        action = "WAIT_FOR_BUY_POINT"
    elif has_high_debt:
        action = "CANDIDATE_POOL"
    else:
        action = "STRONG_OBSERVE" if score >= 68 else "CANDIDATE_POOL" if score >= 55 else "LEAD_TRACKING"
    debt_label = ",".join(debt_profile.get("blocking_datasets") or debt_profile.get("high_datasets") or [])
    reason = f"financial={financial_score:.1f}, data={data_score:.1f}, technical={technical_score:.1f}, capital_risk={risk_level}, debt_drag={debt_drag:.1f}, debt={debt_label or 'none'}, cap={cap}"
    return round(score, 2), action, reason


def validate_comparison_report(report: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    required = {
        "comparison_scope",
        "candidates",
        "data_acquisition_summary",
        "serenity_layer_matrix",
        "financial_quality_matrix",
        "growth_hypothesis_matrix",
        "technical_timing_matrix",
        "capital_actions",
        "research_debt",
        "candidate_priority_ranking",
        "final_decision",
    }
    missing = sorted(required - set(report))
    if missing:
        errors.append(f"comparison report missing keys: {', '.join(missing)}")
    candidates = report.get("candidates", [])
    if not isinstance(candidates, list) or len(candidates) < 2:
        errors.append("comparison report requires at least two candidates")
    symbols = {str(item.get("symbol")) for item in candidates if isinstance(item, Mapping)}
    for key in ["data_acquisition_summary", "serenity_layer_matrix", "financial_quality_matrix", "growth_hypothesis_matrix", "technical_timing_matrix", "capital_actions"]:
        rows = report.get(key, [])
        if not isinstance(rows, list) or {str(item.get("symbol")) for item in rows if isinstance(item, Mapping)} != symbols:
            errors.append(f"{key} must contain one row per candidate")
    ranking = report.get("candidate_priority_ranking", [])
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
            if str(item.get("rating_cap")) not in RATING_CAPS:
                errors.append(f"candidate_priority_ranking[{idx - 1}].rating_cap is unknown")
            if str(item.get("action_readiness")) not in ACTION_READINESS:
                errors.append(f"candidate_priority_ranking[{idx - 1}].action_readiness is unknown")
            debt_profile = _debt_gate_profile(debt_by_symbol.get(str(item.get("symbol")), []))
            if debt_profile.get("blocking_datasets") and item.get("action_readiness") != "DATA_GATED":
                errors.append(f"{item.get('symbol')} blocking research debt requires DATA_GATED action_readiness")
    for row in report.get("growth_hypothesis_matrix", []) if isinstance(report.get("growth_hypothesis_matrix"), list) else []:
        if not isinstance(row, Mapping):
            continue
        implied = str(row.get("market_implied_growth"))
        supported = str(row.get("evidence_supported_growth"))
        if implied not in GROWTH_ORDER:
            errors.append(f"{row.get('symbol')} market_implied_growth is unknown: {implied}")
        if supported not in GROWTH_ORDER:
            errors.append(f"{row.get('symbol')} evidence_supported_growth is unknown: {supported}")
        if implied in {"H4", "H5"} and GROWTH_ORDER.get(supported, -1) < GROWTH_ORDER[implied]:
            if row.get("h4_h5_evidence_bar_met") is not False:
                errors.append(f"{row.get('symbol')} H4/H5 valuation gap requires h4_h5_evidence_bar_met=false")
            debt_rows = report.get("research_debt", [])
            has_growth_debt = any(
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
        actions = row.get("actions", [])
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
            has_technical_debt = any(
                isinstance(item, Mapping)
                and item.get("symbol") == row.get("symbol")
                and item.get("dataset") == "price_history_adjusted"
                for item in debt_rows
            ) if isinstance(debt_rows, list) else False
            if not has_technical_debt:
                errors.append(f"{row.get('symbol')} missing adjusted history requires price_history_adjusted research debt")
    return errors


def build_comparison_report(manifest_paths: Sequence[Path], profiles: Optional[Mapping[str, Mapping[str, Any]]] = None) -> dict[str, Any]:
    manifests = [_load_manifest(path) for path in manifest_paths]
    if len(manifests) < 2:
        raise ValueError("comparison requires at least two manifest paths")
    profiles = profiles or {}

    candidates = []
    data_rows = []
    layer_rows = []
    financial_rows = []
    growth_rows = []
    technical_rows = []
    capital_rows = []
    debt_rows: list[dict[str, Any]] = []
    ranking_seed = []

    for manifest, path in zip(manifests, manifest_paths):
        symbol = _symbol(manifest)
        profile = profiles.get(symbol, {})
        data_summary = _data_summary(manifest)
        financial = _financial_quality(manifest)
        technical = _technical_summary(manifest)
        capital = _capital_summary(manifest)
        layer = _serenity_layer(manifest, profile)
        growth = _growth_hypothesis(manifest, financial, profile)
        candidate_debt = _research_debt_rows(manifest, capital, financial, technical, layer, growth)
        score, action_readiness, reason = _priority_score(data_summary, financial, technical, capital, layer, candidate_debt)

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
        growth_rows.append(growth)
        technical_rows.append(technical)
        capital_rows.append(capital)
        debt_rows.extend(candidate_debt)
        ranking_seed.append({
            "symbol": symbol,
            "priority_score": score,
            "rating_cap": data_summary["rating_cap"],
            "action_readiness": action_readiness,
            "key_reason": reason,
        })

    ranked = sorted(ranking_seed, key=lambda item: item["priority_score"], reverse=True)
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index

    next_actions = []
    for row in debt_rows:
        action = str(row.get("next_action") or row.get("objective") or "")
        if action and action not in next_actions:
            next_actions.append(action)

    report = {
        "comparison_scope": {
            "candidate_count": len(candidates),
            "as_of": dt.datetime.now(dt.timezone.utc).isoformat(),
            "basis": "fetch_manifest_plus_deterministic_decision_matrices",
        },
        "candidates": candidates,
        "data_acquisition_summary": data_rows,
        "serenity_layer_matrix": layer_rows,
        "financial_quality_matrix": financial_rows,
        "growth_hypothesis_matrix": growth_rows,
        "technical_timing_matrix": technical_rows,
        "capital_actions": capital_rows,
        "research_debt": debt_rows,
        "candidate_priority_ranking": ranked,
        "final_decision": {
            "top_candidate": ranked[0]["symbol"],
            "decision": "Use the top-ranked candidate as the first research object, while keeping rating and action constrained by open research debt.",
            "next_research_actions": next_actions[:12],
        },
    }
    errors = validate_comparison_report(report)
    if errors:
        raise ValueError("; ".join(errors))
    return report


def to_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# 候选公司对比决策报告",
        "",
        "## 0. 结论先行",
    ]
    decision = report.get("final_decision", {}) if isinstance(report.get("final_decision"), Mapping) else {}
    lines.append(f"- 优先候选：{decision.get('top_candidate', '')}")
    lines.append(f"- 决策说明：{decision.get('decision', '')}")
    lines.extend(["", "## 1. 候选优先级", "| Rank | Symbol | Priority | Rating Cap | Action | Reason |", "|---:|---|---:|---|---|---|"])
    for row in report.get("candidate_priority_ranking", []):
        if isinstance(row, Mapping):
            lines.append(f"| {row.get('rank')} | {row.get('symbol')} | {row.get('priority_score')} | {row.get('rating_cap')} | {row.get('action_readiness')} | {row.get('key_reason')} |")
    lines.extend(["", "## 2. 数据追索与研究债务", "| Symbol | Dataset | Priority | Next Action |", "|---|---|---|---|"])
    for row in report.get("research_debt", []):
        if isinstance(row, Mapping):
            lines.append(f"| {row.get('symbol')} | {row.get('dataset', '')} | {row.get('priority', '')} | {row.get('next_action') or row.get('objective', '')} |")
    lines.extend(["", "## 3. 财务质量矩阵", "| Symbol | Score | Revenue Growth | Net Margin | OCF/NI | Debt/Assets | Label |", "|---|---:|---:|---:|---:|---:|---|"])
    for row in report.get("financial_quality_matrix", []):
        if isinstance(row, Mapping):
            lines.append(f"| {row.get('symbol')} | {row.get('score')} | {row.get('revenue_growth_pct')} | {row.get('net_margin_pct')} | {row.get('ocf_to_net_income_pct')} | {row.get('debt_to_assets_pct')} | {row.get('label', '')} |")
    lines.extend(["", "## 4. 市场隐含增长 vs 证据支持增长", "| Symbol | Market Implied | Evidence Supported | Gap | Required Evidence |", "|---|---|---|---|---|"])
    for row in report.get("growth_hypothesis_matrix", []):
        if isinstance(row, Mapping):
            lines.append(f"| {row.get('symbol')} | {row.get('market_implied_growth')} | {row.get('evidence_supported_growth')} | {row.get('gap')} | {row.get('required_next_evidence')} |")
    lines.extend(["", "## 5. 技术健康与缠论动作", "| Symbol | Trend State | Chan Action | Buy Point Claim | Note |", "|---|---|---|---|---|"])
    for row in report.get("technical_timing_matrix", []):
        if isinstance(row, Mapping):
            lines.append(f"| {row.get('symbol')} | {row.get('trend_state')} | {row.get('chan_action')} | {row.get('buy_point_claim_allowed')} | {row.get('decision_note')} |")
    lines.extend(["", "## 6. A 股资本动作", "| Symbol | Risk | Action Types | Research Debt |", "|---|---|---|---|"])
    for row in report.get("capital_actions", []):
        if isinstance(row, Mapping):
            summary = row.get("summary", {}) if isinstance(row.get("summary"), Mapping) else {}
            lines.append(f"| {row.get('symbol')} | {summary.get('material_risk_level')} | {', '.join(summary.get('action_types', [])) if isinstance(summary.get('action_types'), list) else ''} | {'; '.join(row.get('research_debt', [])) if isinstance(row.get('research_debt'), list) else ''} |")
    lines.append("")
    return "\n".join(lines)


def _load_profiles(values: Sequence[str]) -> dict[str, Mapping[str, Any]]:
    profiles: dict[str, Mapping[str, Any]] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--profile must use SYMBOL=path")
        symbol, path_text = value.split("=", 1)
        loaded = _load_json(Path(path_text))
        if not isinstance(loaded, Mapping):
            raise ValueError(f"profile for {symbol} must be a JSON object")
        profiles[symbol] = loaded
    return profiles


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build Serenity + Chan candidate comparison report")
    parser.add_argument("manifests", nargs="+", help="fetch manifest JSON paths")
    parser.add_argument("--profile", action="append", default=[], help="Optional SYMBOL=profile.json qualitative layer/growth inputs")
    parser.add_argument("--format", choices=["json", "md", "both"], default="json")
    args = parser.parse_args(argv)
    try:
        report = build_comparison_report([Path(path) for path in args.manifests], _load_profiles(args.profile))
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
