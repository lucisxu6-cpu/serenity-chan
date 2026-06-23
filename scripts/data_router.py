#!/usr/bin/env python3
"""
Data Router for serenity-chan-stock-skill.

This script provides market-aware symbol resolution and local data validation.
It intentionally avoids guessing and does not require paid vendor credentials.
Provider adapters can be added around these contracts.

Examples:
  python scripts/data_router.py resolve 688019
  python scripts/data_router.py resolve AAPL
  python scripts/data_router.py validate-price prices.csv --market CN_A --adjust qfq
  python scripts/data_router.py validate-financial financials.json
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import statistics
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    from data_layer import build_data_fetch_plan
    from data_layer import Dataset as CanonicalDataset
    from data_layer import Market as CanonicalMarket
    from data_layer import default_real_providers
    from data_layer import fetch_with_provider_chain
    from data_layer import resolve_symbol as canonical_resolve_symbol
except ModuleNotFoundError:  # pragma: no cover - supports python -m scripts.data_router
    from scripts.data_layer import build_data_fetch_plan
    from scripts.data_layer import Dataset as CanonicalDataset
    from scripts.data_layer import Market as CanonicalMarket
    from scripts.data_layer import default_real_providers
    from scripts.data_layer import fetch_with_provider_chain
    from scripts.data_layer import resolve_symbol as canonical_resolve_symbol


class Market(str, Enum):
    CN_A = "CN_A"
    US = "US"
    HK = "HK"
    OTHER = "OTHER"


class DataStatus(str, Enum):
    OK = "OK"
    PARTIAL = "PARTIAL"
    STALE = "STALE"
    FAILED = "FAILED"
    PENDING = "PENDING"
    NOT_REQUESTED = "NOT_REQUESTED"


class RatingCap(str, Enum):
    S = "S"
    A = "A"
    B = "B"
    C = "C"
    OBSERVE_ONLY = "OBSERVE_ONLY"


@dataclass
class SymbolInfo:
    input_value: str
    normalized: str
    market: Market
    exchange: Optional[str]
    currency: str
    provider_aliases: Dict[str, str] = field(default_factory=dict)
    disclosure_sources: List[str] = field(default_factory=list)
    price_source_priority: List[str] = field(default_factory=list)
    financial_source_priority: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    dataset: str
    status: DataStatus
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    rating_cap: RatingCap = RatingCap.S

    def downgrade(self, cap: RatingCap, reason: str) -> None:
        order = [RatingCap.OBSERVE_ONLY, RatingCap.C, RatingCap.B, RatingCap.A, RatingCap.S]
        if order.index(cap) < order.index(self.rating_cap):
            self.rating_cap = cap
        self.warnings.append(reason)


def _source_pack(market: Market) -> Tuple[List[str], List[str], List[str]]:
    if market == Market.CN_A:
        disclosure = ["CNINFO", "SSE", "SZSE", "BSE", "Company IR"]
        price = ["Wind", "Choice", "CSMAR", "Tushare Pro", "AKShare", "BaoStock", "yfinance auxiliary"]
        financial = ["CNINFO filings", "SSE/SZSE/BSE filings", "Wind/Choice/CSMAR", "Tushare Pro", "Eastmoney F10 L3 structured preflight", "Company IR"]
    elif market == Market.US:
        disclosure = ["SEC EDGAR", "Company IR", "Earnings releases", "Investor presentations"]
        price = ["Polygon", "IEX/Tiingo", "Nasdaq Data Link", "Bloomberg/FactSet/Koyfin", "yfinance auxiliary", "Stooq auxiliary"]
        financial = ["SEC Companyfacts/XBRL", "10-K/10-Q/8-K", "Company IR", "FactSet/Bloomberg/Koyfin"]
    elif market == Market.HK:
        disclosure = ["HKEXnews", "Company IR", "Annual/interim reports"]
        price = ["Wind", "Choice", "HKEX", "Bloomberg/FactSet", "yfinance auxiliary"]
        financial = ["HKEX filings", "Company annual/interim reports", "Wind/Choice"]
    else:
        disclosure = []
        price = []
        financial = []
    return disclosure, price, financial


def resolve_symbol(value: str) -> SymbolInfo:
    """Resolve symbols via the canonical data layer and adapt to this CLI contract."""
    canonical = canonical_resolve_symbol(value)
    warnings: List[str] = []

    if canonical.market == CanonicalMarket.CN_A:
        market = Market.CN_A
    elif canonical.market == CanonicalMarket.US:
        market = Market.US
    elif canonical.market == CanonicalMarket.HK:
        market = Market.HK
    else:
        market = Market.OTHER

    normalized = canonical.symbol
    exchange = canonical.exchange or None
    aliases: Dict[str, str] = {}

    if market == Market.CN_A:
        aliases = {"tushare": normalized, "wind": normalized}
        code = normalized.split(".", 1)[0]
        if exchange == "SH":
            aliases["yfinance"] = f"{code}.SS"
        elif exchange == "SZ":
            aliases["yfinance"] = f"{code}.SZ"
        elif exchange == "BJ":
            aliases["yfinance"] = f"{code}.BJ"
    elif market == Market.HK:
        aliases = {"yfinance": normalized}
    elif market == Market.US:
        aliases = {"sec_ticker": normalized, "yfinance": normalized}

    raw_token = canonical.input_value.upper().replace(" ", "")
    if raw_token.endswith(".SS") and normalized.endswith(".SH"):
        warnings.append("Input used Yahoo-style .SS; normalized to A-share .SH.")
    if raw_token.isdigit() and market == Market.CN_A:
        warnings.append(f"No suffix provided; inferred {normalized}. Confirm if ambiguity matters.")
    if market == Market.OTHER:
        warnings.append("Could not confidently resolve market. Ask user or provide suffix.")

    disclosure, price, financial = _source_pack(market)
    return SymbolInfo(
        canonical.input_value,
        normalized,
        market,
        exchange,
        canonical.currency or "UNKNOWN",
        aliases,
        disclosure,
        price,
        financial,
        warnings,
    )


def _parse_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        f = float(str(value).replace(",", ""))
    except Exception:
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _parse_date(value: Any) -> Optional[dt.date]:
    if value is None:
        return None
    s = str(value).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def read_csv_rows(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def validate_price_history(path: Path, market: Market = Market.OTHER, adjust: str = "unknown", min_bars: int = 250) -> ValidationReport:
    report = ValidationReport(dataset="price_history", status=DataStatus.OK, stats={"path": str(path), "adjust": adjust})
    if not path.exists():
        report.status = DataStatus.FAILED
        report.errors.append(f"File not found: {path}")
        report.rating_cap = RatingCap.B
        return report
    rows = read_csv_rows(path)
    required = ["date", "open", "high", "low", "close", "volume"]
    if not rows:
        report.status = DataStatus.FAILED
        report.errors.append("CSV is empty.")
        report.rating_cap = RatingCap.B
        return report
    missing_cols = [c for c in required if c not in rows[0]]
    if missing_cols:
        report.status = DataStatus.FAILED
        report.errors.append(f"Missing required columns: {missing_cols}")
        report.rating_cap = RatingCap.B
        return report

    seen_dates: set[dt.date] = set()
    dates: List[dt.date] = []
    closes: List[float] = []
    highs: List[float] = []
    lows: List[float] = []
    invalid_count = 0
    for i, row in enumerate(rows, start=2):
        d = _parse_date(row.get("date"))
        o = _parse_float(row.get("open"))
        h = _parse_float(row.get("high"))
        l = _parse_float(row.get("low"))
        c = _parse_float(row.get("close"))
        v = _parse_float(row.get("volume"))
        if d is None:
            report.errors.append(f"Invalid date at row {i}: {row.get('date')}")
            invalid_count += 1
            continue
        if d in seen_dates:
            report.errors.append(f"Duplicate date: {d}")
            invalid_count += 1
            continue
        seen_dates.add(d)
        if any(x is None for x in (o, h, l, c, v)):
            report.errors.append(f"Invalid OHLCV number at row {i}")
            invalid_count += 1
            continue
        assert o is not None and h is not None and l is not None and c is not None and v is not None
        if c <= 0 or o <= 0 or h <= 0 or l <= 0:
            report.errors.append(f"Non-positive OHLC at row {i}")
            invalid_count += 1
        if h < max(o, c) or l > min(o, c) or h < l:
            report.errors.append(f"OHLC consistency failure at row {i}")
            invalid_count += 1
        if v < 0:
            report.errors.append(f"Negative volume at row {i}")
            invalid_count += 1
        dates.append(d)
        closes.append(c)
        highs.append(h)
        lows.append(l)

    if dates != sorted(dates):
        report.errors.append("Dates are not sorted ascending.")
        invalid_count += 1
    if len(dates) < min_bars:
        report.warnings.append(f"Only {len(dates)} bars; {min_bars}+ preferred for 200DMA and medium-term structure.")
        report.rating_cap = RatingCap.B
    if adjust.lower() not in {"qfq", "forward", "hfq", "backward", "adjusted", "none", "unadjusted", "unknown"}:
        report.warnings.append(f"Unknown adjustment flag: {adjust}")
    if adjust.lower() in {"unknown", "none", "unadjusted"}:
        report.warnings.append("Historical price adjustment is not confirmed; Chan/DMA conclusions may be capped.")
        report.rating_cap = RatingCap.B

    if dates:
        latest = dates[-1]
        today = dt.datetime.now().date()
        calendar_days_old = (today - latest).days
        stale_threshold = 7 if market in {Market.CN_A, Market.HK, Market.US} else 14
        report.stats.update({
            "bars": len(dates),
            "start_date": str(dates[0]),
            "end_date": str(latest),
            "latest_close": closes[-1],
            "calendar_days_old": calendar_days_old,
        })
        if calendar_days_old > stale_threshold:
            report.status = DataStatus.STALE
            report.warnings.append(f"Latest bar is {calendar_days_old} calendar days old; verify trading calendar.")
            report.rating_cap = RatingCap.B
        if len(closes) >= 20:
            report.stats["sma20"] = sum(closes[-20:]) / 20
        if len(closes) >= 50:
            report.stats["sma50"] = sum(closes[-50:]) / 50
        if len(closes) >= 100:
            report.stats["sma100"] = sum(closes[-100:]) / 100
        if len(closes) >= 200:
            report.stats["sma200"] = sum(closes[-200:]) / 200
        if len(closes) >= 21:
            trs = []
            for j in range(len(closes) - 20, len(closes)):
                prev_close = closes[j - 1]
                tr = max(highs[j] - lows[j], abs(highs[j] - prev_close), abs(lows[j] - prev_close))
                trs.append(tr)
            report.stats["atr20"] = sum(trs) / len(trs)

    if invalid_count:
        report.status = DataStatus.FAILED if invalid_count > 5 else DataStatus.PARTIAL
        report.rating_cap = RatingCap.C if invalid_count > 5 else min_cap(report.rating_cap, RatingCap.B)
    return report


def min_cap(a: RatingCap, b: RatingCap) -> RatingCap:
    order = [RatingCap.OBSERVE_ONLY, RatingCap.C, RatingCap.B, RatingCap.A, RatingCap.S]
    return a if order.index(a) < order.index(b) else b


def compare_quotes(prices: Sequence[float]) -> Dict[str, Any]:
    valid = [float(p) for p in prices if p and p > 0]
    if len(valid) < 2:
        return {"status": "PARTIAL", "warning": "Need at least two valid prices for cross-source comparison."}
    med = statistics.median(valid)
    max_diff = max(abs(p - med) / med for p in valid)
    if max_diff <= 0.005:
        status = "OK"
    elif max_diff <= 0.02:
        status = "PARTIAL"
    else:
        status = "FAILED"
    return {"status": status, "prices": valid, "median": med, "max_diff_pct": round(max_diff * 100, 4)}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def validate_financials(path: Path) -> ValidationReport:
    report = ValidationReport(dataset="financials", status=DataStatus.OK, stats={"path": str(path)})
    if not path.exists():
        report.status = DataStatus.FAILED
        report.errors.append(f"File not found: {path}")
        report.rating_cap = RatingCap.B
        return report
    data = load_json(path)
    rows = data.get("periods", data if isinstance(data, list) else [])
    if not isinstance(rows, list) or not rows:
        report.status = DataStatus.FAILED
        report.errors.append("Financial JSON must contain a non-empty list or {'periods': [...]}.")
        report.rating_cap = RatingCap.B
        return report

    required_any = ["period", "revenue", "net_income", "operating_cash_flow", "assets", "liabilities", "equity"]
    bad = 0
    missing_counts: Dict[str, int] = {k: 0 for k in required_any}
    for idx, row in enumerate(rows):
        for k in required_any:
            if k not in row:
                missing_counts[k] += 1
        assets = _parse_float(row.get("assets"))
        liabilities = _parse_float(row.get("liabilities"))
        equity = _parse_float(row.get("equity"))
        if assets and liabilities is not None and equity is not None:
            denom = max(abs(assets), 1.0)
            diff = abs(assets - liabilities - equity) / denom
            if diff > 0.01:
                report.warnings.append(f"Balance sheet identity differs by {diff:.2%} in period {row.get('period')}.")
                bad += 1
        revenue = _parse_float(row.get("revenue"))
        ocf = _parse_float(row.get("operating_cash_flow"))
        net_income = _parse_float(row.get("net_income"))
        if revenue is not None and revenue < 0:
            report.warnings.append(f"Negative revenue in period {row.get('period')}; confirm business context.")
        if net_income and ocf is not None and abs(ocf / net_income) < 0.3:
            report.warnings.append(f"OCF/net income is low in period {row.get('period')}; explain working-capital quality.")

    report.stats["period_count"] = len(rows)
    for key, count in missing_counts.items():
        if count:
            report.warnings.append(f"{count}/{len(rows)} periods missing {key}.")
    core_fields = ["revenue", "net_income", "operating_cash_flow"]
    core_complete_rows = [
        row for row in rows
        if all(row.get(key) is not None for key in core_fields)
    ]
    latest_core_period = max((str(row.get("period", "")) for row in core_complete_rows), default="")
    report.stats["core_complete_period_count"] = len(core_complete_rows)
    if latest_core_period:
        report.stats["latest_core_complete_period"] = latest_core_period

    if not core_complete_rows:
        report.status = DataStatus.PARTIAL
        report.rating_cap = RatingCap.B
        report.warnings.append("No retained period has all core financial fields: revenue, net_income, and operating_cash_flow.")
    elif len(core_complete_rows) < 2:
        report.status = DataStatus.PARTIAL
        report.rating_cap = min_cap(report.rating_cap, RatingCap.A)
        report.warnings.append("Only one retained period has all core financial fields; trend analysis is partial.")
    if bad:
        report.status = DataStatus.PARTIAL
        report.rating_cap = RatingCap.B
    return report


def _default_fetch_dir(symbol: str) -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = Path(os.getenv("SERENITY_DATA_DIR", "/tmp/serenity-chan-data"))
    safe_symbol = "".join(ch if ch.isalnum() or ch in {"-", ".", "_"} else "_" for ch in symbol)
    return root / safe_symbol / stamp


def _market_for_router(market: CanonicalMarket) -> Market:
    if market == CanonicalMarket.CN_A:
        return Market.CN_A
    if market == CanonicalMarket.US:
        return Market.US
    if market == CanonicalMarket.HK:
        return Market.HK
    return Market.OTHER


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _write_price_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["date", "open", "high", "low", "close", "volume", "adj_close", "raw_close"]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "date": row.get("trade_date"),
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
                "volume": row.get("volume"),
                "adj_close": row.get("adj_close"),
                "raw_close": row.get("raw_close"),
            })


def _cap_for_statuses(
    statuses: Dict[str, str],
    validation_caps: Optional[Sequence[str]] = None,
    *,
    required_datasets: Optional[Sequence[str]] = None,
    downgrade_not_requested: bool = True,
) -> RatingCap:
    cap = RatingCap.S
    order = [RatingCap.OBSERVE_ONLY, RatingCap.C, RatingCap.B, RatingCap.A, RatingCap.S]

    def downgrade(target: RatingCap) -> None:
        nonlocal cap
        if order.index(target) < order.index(cap):
            cap = target

    keys = list(required_datasets) if required_datasets is not None else list(statuses)
    for key in keys:
        status = statuses.get(key, DataStatus.NOT_REQUESTED.value)
        if status in {"FAILED", "PENDING"}:
            downgrade(RatingCap.B)
        elif status == "STALE":
            downgrade(RatingCap.B)
        elif status == "NOT_REQUESTED" and downgrade_not_requested:
            downgrade(RatingCap.B)
        elif status == "PARTIAL":
            downgrade(RatingCap.A)
    for raw_cap in validation_caps or []:
        try:
            downgrade(RatingCap(raw_cap))
        except ValueError:
            continue
    return cap


def _source_usage_from_result(result_data: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(result_data, Mapping):
        return None
    source_usage = result_data.get("source_usage")
    if isinstance(source_usage, Mapping):
        return dict(source_usage)
    return None


def _build_source_integrity_summary(result_items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    l3_structured_datasets: List[str] = []
    preferred_source_gaps: List[str] = []
    for item in result_items:
        source_usage = item.get("source_usage") if isinstance(item.get("source_usage"), dict) else None
        if not source_usage:
            continue
        if source_usage.get("structured_preflight_used"):
            l3_structured_datasets.append(str(item.get("dataset") or ""))
        if source_usage.get("preferred_source_status") not in {None, "OK"}:
            preferred_source_gaps.append(
                f"{item.get('dataset')}: preferred={source_usage.get('preferred_source')} status={source_usage.get('preferred_source_status')}"
            )
    return {
        "l3_structured_preflight_used": bool(l3_structured_datasets),
        "l3_structured_datasets": l3_structured_datasets,
        "preferred_source_gaps": preferred_source_gaps,
    }


def _build_ai_review_guidance(result_items: Sequence[Dict[str, Any]], data_quality: Dict[str, str]) -> Dict[str, Any]:
    """Add explicit AI adjudication prompts to the data bundle.

    Deterministic fetch/validation can say what was available. It cannot decide
    whether a source is strong enough for a long-term research rating without
    market, industry, and evidence-context reasoning.
    """
    checks: List[str] = [
        "data_quality.rating_cap defines the maximum permitted rating ceiling.",
        "Rate evidence strength after reviewing data availability, source level, validation warnings, and raw artifacts.",
        "Inspect each result's source_level, warnings, validation warnings, and raw_path before upgrading conviction.",
    ]
    blockers: List[str] = []
    upgrade_requirements: List[str] = []

    for item in result_items:
        dataset = str(item.get("dataset") or "")
        status = str(item.get("status") or "")
        source_level = str(item.get("source_level") or "")
        source = str(item.get("source") or "")
        source_usage = item.get("source_usage") if isinstance(item.get("source_usage"), dict) else None
        validation = item.get("validation") if isinstance(item.get("validation"), dict) else {}
        validation_warnings = validation.get("warnings") if isinstance(validation, dict) else []

        if source_usage and source_usage.get("structured_preflight_used"):
            checks.append(
                f"{dataset} source chain: preferred={source_usage.get('preferred_source')} "
                f"status={source_usage.get('preferred_source_status')}; "
                f"structured={source_usage.get('structured_source')} level={source_usage.get('structured_source_level')}"
            )
            if source_usage.get("preferred_source_status") != "OK":
                blockers.append(
                    f"{dataset} preferred source status is {source_usage.get('preferred_source_status')}; apply the source-policy cap."
                )

        if dataset == CanonicalDataset.FINANCIALS.value:
            if source_level.startswith("L3_"):
                blockers.append(
                    f"Financials use {source} ({source_level}); L3 structured evidence caps final research rating at B until L0/L1 verification."
                )
                upgrade_requirements.extend([
                    "Verify key financial lines against CNINFO/exchange annual or quarterly report PDFs, or an L1 database export.",
                    "For A-share financial or insurance companies, use industry-specific statement analysis instead of ordinary operating-company three-statement shortcuts.",
                    "Explain whether cash-flow warnings are seasonal/interim-period effects or structural quality issues before scoring fundamentals.",
                ])
            if status in {DataStatus.PARTIAL.value, DataStatus.FAILED.value}:
                blockers.append(f"Financials status is {status}; do not issue S/A long-term ratings.")
                upgrade_requirements.append("Fetch market-primary financial statements before upgrading: SEC XBRL/company filings for US, CNINFO/exchange reports or L1 data for A-share, HKEX/company reports for HK.")
            if validation_warnings:
                checks.append("Financial validation warnings require explicit AI explanation, not mechanical promotion or downgrade.")

        if dataset in {CanonicalDataset.CURRENT_QUOTE.value, CanonicalDataset.PRICE_HISTORY_ADJUSTED.value} and status in {DataStatus.PARTIAL.value, DataStatus.FAILED.value, DataStatus.STALE.value}:
            blockers.append(f"{dataset} status is {status}; do not issue a current entry/buy-point conclusion.")
            upgrade_requirements.append("Fetch market-appropriate current quote and adjusted history before making current price, valuation, or buy-point claims.")

        if dataset == CanonicalDataset.FILINGS.value and status != DataStatus.OK.value:
            blockers.append(f"Filing/announcement status is {status}; customer/order/capacity claims remain unverified leads.")
            upgrade_requirements.append("Fetch market-primary filings or announcements before treating customer, order, capacity, risk, or governance claims as verified.")

    if data_quality.get("rating_cap") in {RatingCap.B.value, RatingCap.C.value, RatingCap.OBSERVE_ONLY.value}:
        checks.append("When the cap is B or lower, the output must frame the result as observation/pre-research unless stronger primary evidence is added.")

    return {
        "required": True,
        "checks": checks,
        "blockers": blockers,
        "upgrade_requirements": list(dict.fromkeys(upgrade_requirements)),
    }


def fetch_real_data(
    symbol_value: str,
    *,
    datasets: Sequence[str],
    out_dir: Optional[str] = None,
    chart_range: str = "2y",
    interval: str = "1d",
    min_bars: int = 250,
) -> Dict[str, Any]:
    symbol = canonical_resolve_symbol(symbol_value)
    destination = Path(out_dir) if out_dir else _default_fetch_dir(symbol.symbol)
    destination.mkdir(parents=True, exist_ok=True)

    providers = default_real_providers(symbol)
    result_items: List[Dict[str, Any]] = []
    statuses: Dict[str, str] = {}
    validation_caps: List[str] = []
    router_market = _market_for_router(symbol.market)
    requested_dataset_values = [CanonicalDataset(dataset_name).value for dataset_name in datasets]

    for dataset_name in requested_dataset_values:
        dataset = CanonicalDataset(dataset_name)
        provider_kwargs: Dict[str, Any] = {"raw_dir": destination / "raw"}
        if dataset in {CanonicalDataset.PRICE_HISTORY_RAW, CanonicalDataset.PRICE_HISTORY_ADJUSTED}:
            provider_kwargs.update({"range": chart_range, "interval": interval})
        result = fetch_with_provider_chain(
            providers,
            symbol,
            dataset,
            **provider_kwargs,
        )
        source_level_value = getattr(result.source_level, "value", str(result.source_level))
        source_usage = _source_usage_from_result(result.data)
        data_path: Optional[str] = None
        validation_payload: Optional[Dict[str, Any]] = None
        status = "OK" if result.ok else "FAILED"

        if result.ok:
            if dataset in {CanonicalDataset.PRICE_HISTORY_RAW, CanonicalDataset.PRICE_HISTORY_ADJUSTED}:
                csv_path = destination / f"{symbol.symbol}_{dataset.value}.csv"
                _write_price_csv(csv_path, list(result.data or []))
                data_path = str(csv_path)
                validation = validate_price_history(
                    csv_path,
                    router_market,
                    "adjusted" if dataset == CanonicalDataset.PRICE_HISTORY_ADJUSTED else "unadjusted",
                    min_bars,
                )
                status = validation.status.value
                validation_payload = json.loads(json.dumps(validation, ensure_ascii=False, default=lambda o: o.value if isinstance(o, Enum) else asdict(o)))
                validation_caps.append(validation.rating_cap.value)
            else:
                json_path = destination / f"{symbol.symbol}_{dataset.value}.json"
                _write_json(json_path, result.data)
                data_path = str(json_path)
                if dataset == CanonicalDataset.FINANCIALS:
                    validation = validate_financials(json_path)
                    status = validation.status.value
                    validation_payload = json.loads(json.dumps(validation, ensure_ascii=False, default=lambda o: o.value if isinstance(o, Enum) else asdict(o)))
                    validation_caps.append(validation.rating_cap.value)
                    if source_level_value.startswith("L3_"):
                        validation_caps.append(RatingCap.B.value)
                        result.warnings.append("Financials use L3/F10 structured preflight data; final S/A research ratings require L0/L1 verification.")
                    elif source_level_value.startswith("L4_"):
                        validation_caps.append(RatingCap.B.value)
                        result.warnings.append("Financials are from unverified L4 source; high-conviction conclusions are not allowed.")

        statuses[dataset.value] = status
        result_items.append({
            "dataset": dataset.value,
            "status": status,
            "source": result.source_name,
            "source_level": source_level_value,
            "retrieved_at": result.retrieved_at,
            "as_of_date": result.as_of_date,
            "currency": result.currency,
            "adjust": result.adjust,
            "data_path": data_path,
            "raw_path": result.raw_path,
            "raw_hash": result.raw_hash,
            "source_usage": source_usage,
            "warnings": result.warnings,
            "errors": result.errors,
            "validation": validation_payload,
        })

    critical_datasets = [
        CanonicalDataset.CURRENT_QUOTE.value,
        CanonicalDataset.PRICE_HISTORY_ADJUSTED.value,
        CanonicalDataset.FINANCIALS.value,
        CanonicalDataset.FILINGS.value,
    ]
    requested_cap = _cap_for_statuses(
        statuses,
        validation_caps,
        required_datasets=requested_dataset_values,
        downgrade_not_requested=False,
    )
    full_research_cap = _cap_for_statuses(
        statuses,
        validation_caps,
        required_datasets=critical_datasets,
        downgrade_not_requested=True,
    )
    data_quality = {
        "market_resolution": "OK" if symbol.market != CanonicalMarket.UNKNOWN else "FAILED",
        "current_price": statuses.get(CanonicalDataset.CURRENT_QUOTE.value, DataStatus.NOT_REQUESTED.value),
        "adjusted_history": statuses.get(CanonicalDataset.PRICE_HISTORY_ADJUSTED.value, DataStatus.NOT_REQUESTED.value),
        "financials": statuses.get(CanonicalDataset.FINANCIALS.value, DataStatus.NOT_REQUESTED.value),
        "filings": statuses.get(CanonicalDataset.FILINGS.value, DataStatus.NOT_REQUESTED.value),
        "requested_data_rating_cap": requested_cap.value,
        "full_research_rating_cap": full_research_cap.value,
        "rating_cap": full_research_cap.value,
    }
    source_integrity = _build_source_integrity_summary(result_items)
    ai_review = _build_ai_review_guidance(result_items, data_quality)
    manifest = {
        "symbol": symbol.__dict__,
        "requested_datasets": requested_dataset_values,
        "full_research_required_datasets": critical_datasets,
        "out_dir": str(destination),
        "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "data_quality": data_quality,
        "source_integrity": source_integrity,
        "ai_review": ai_review,
        "results": result_items,
    }
    _write_json(destination / "manifest.json", manifest)
    return manifest


def emit(obj: Any) -> None:
    def default(o: Any) -> Any:
        if isinstance(o, Enum):
            return o.value
        if hasattr(o, "__dict__"):
            return asdict(o)
        return str(o)
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=default))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Serenity-Chan market data router and validator")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_resolve = sub.add_parser("resolve", help="Resolve ticker/stock code into market-aware symbol info")
    p_resolve.add_argument("symbol")

    p_plan = sub.add_parser("plan", help="Build a market-aware data fetch plan")
    p_plan.add_argument("symbol")
    p_plan.add_argument("--horizon", default="12M")

    p_price = sub.add_parser("validate-price", help="Validate OHLCV CSV")
    p_price.add_argument("csv_path")
    p_price.add_argument("--market", choices=[m.value for m in Market], default="OTHER")
    p_price.add_argument("--adjust", default="unknown")
    p_price.add_argument("--min-bars", type=int, default=250)

    p_fin = sub.add_parser("validate-financial", help="Validate financial JSON")
    p_fin.add_argument("json_path")

    p_quotes = sub.add_parser("compare-quotes", help="Compare multiple quotes")
    p_quotes.add_argument("prices", nargs="+")

    p_fetch = sub.add_parser("fetch", help="Fetch real preflight data into an auditable local bundle")
    p_fetch.add_argument("symbol")
    p_fetch.add_argument(
        "--datasets",
        nargs="+",
        choices=[d.value for d in CanonicalDataset],
        default=[
            CanonicalDataset.CURRENT_QUOTE.value,
            CanonicalDataset.PRICE_HISTORY_ADJUSTED.value,
            CanonicalDataset.FINANCIALS.value,
            CanonicalDataset.FILINGS.value,
        ],
    )
    p_fetch.add_argument("--out-dir", help="output directory; defaults to /tmp/serenity-chan-data/<symbol>/<timestamp>")
    p_fetch.add_argument("--range", dest="chart_range", default="2y", help="Yahoo chart range for price history")
    p_fetch.add_argument("--interval", default="1d", help="Yahoo chart interval")
    p_fetch.add_argument("--min-bars", type=int, default=250)
    p_fetch.add_argument("--sec-user-agent", help="SEC-compliant User-Agent, e.g. 'Your Name your.email@example.com'")

    args = parser.parse_args(argv)
    if args.cmd == "resolve":
        emit(resolve_symbol(args.symbol))
    elif args.cmd == "plan":
        emit(build_data_fetch_plan(args.symbol, horizon=args.horizon))
    elif args.cmd == "validate-price":
        emit(validate_price_history(Path(args.csv_path), Market(args.market), args.adjust, args.min_bars))
    elif args.cmd == "validate-financial":
        emit(validate_financials(Path(args.json_path)))
    elif args.cmd == "compare-quotes":
        emit(compare_quotes([_parse_float(x) or 0.0 for x in args.prices]))
    elif args.cmd == "fetch":
        if args.sec_user_agent:
            os.environ["SEC_USER_AGENT"] = args.sec_user_agent
        emit(fetch_real_data(
            args.symbol,
            datasets=args.datasets,
            out_dir=args.out_dir,
            chart_range=args.chart_range,
            interval=args.interval,
            min_bars=args.min_bars,
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
