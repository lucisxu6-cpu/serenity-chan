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
from contextlib import contextmanager
import csv
import datetime as dt
import json
import math
import os
import signal
import statistics
import sys
import threading
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

try:
    from data_contracts import (
        AcquisitionStage,
        DataGap,
        DataGapType,
        DataStatus,
        DecisionImpact,
        FetchAttempt,
        ManualRetrievalTask,
        Market,
        RatingCap,
        SourceLevel,
        stricter_cap,
    )
    from data_layer import build_data_fetch_plan
    from data_layer import DataProvider
    from data_layer import DataResult
    from data_layer import Dataset as CanonicalDataset
    from data_layer import Market as CanonicalMarket
    from data_layer import default_real_providers
    from data_layer import provider_is_allowed
    from data_layer import resolve_symbol as canonical_resolve_symbol
    from financial_periods import normalize_financial_period
except ModuleNotFoundError:  # pragma: no cover - supports python -m scripts.data_router
    from scripts.data_contracts import (
        AcquisitionStage,
        DataGap,
        DataGapType,
        DataStatus,
        DecisionImpact,
        FetchAttempt,
        ManualRetrievalTask,
        Market,
        RatingCap,
        SourceLevel,
        stricter_cap,
    )
    from scripts.data_layer import build_data_fetch_plan
    from scripts.data_layer import DataProvider
    from scripts.data_layer import DataResult
    from scripts.data_layer import Dataset as CanonicalDataset
    from scripts.data_layer import Market as CanonicalMarket
    from scripts.data_layer import default_real_providers
    from scripts.data_layer import provider_is_allowed
    from scripts.data_layer import resolve_symbol as canonical_resolve_symbol
    from scripts.financial_periods import normalize_financial_period


DEFAULT_PROVIDER_TIMEOUT_SECONDS: Any = 45


class ProviderTimeoutError(RuntimeError):
    """Raised when one data provider exceeds the router execution budget."""


def _provider_timeout_seconds(value: Any = None) -> int:
    raw_value: Any = value if value is not None else os.getenv("SERENITY_PROVIDER_TIMEOUT_SECONDS", str(DEFAULT_PROVIDER_TIMEOUT_SECONDS))
    try:
        parsed: Any = int(float(str(raw_value)))
    except (TypeError, ValueError):
        parsed = DEFAULT_PROVIDER_TIMEOUT_SECONDS
    return max(0, parsed)


@contextmanager
def _provider_timeout(seconds: int, *, provider_name: str, dataset: CanonicalDataset):
    if (
        seconds <= 0
        or threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "SIGALRM")
        or not hasattr(signal, "setitimer")
    ):
        yield
        return

    previous_handler: Any = signal.getsignal(signal.SIGALRM)
    previous_timer: Any = signal.getitimer(signal.ITIMER_REAL)

    def _raise_timeout(signum: int, frame: Any) -> None:
        raise ProviderTimeoutError(f"{provider_name} exceeded {seconds}s while fetching {dataset.value}")

    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, float(seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])


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
        order: Any = [RatingCap.OBSERVE_ONLY, RatingCap.C, RatingCap.B, RatingCap.A, RatingCap.S]
        if order.index(cap) < order.index(self.rating_cap):
            self.rating_cap = cap
        self.warnings.append(reason)


def _source_pack(market: Market) -> Tuple[List[str], List[str], List[str]]:
    if market == Market.CN_A:
        disclosure: Any = ["CNINFO", "SSE", "SZSE", "BSE", "Company IR"]
        price: Any = ["Wind", "Choice", "CSMAR", "Tushare Pro", "Eastmoney", "Tencent", "AKShare", "BaoStock", "yfinance auxiliary"]
        financial: Any = ["CNINFO filings", "SSE/SZSE/BSE filings", "Wind/Choice/CSMAR", "Tushare Pro", "Eastmoney F10 L3 structured preflight", "Company IR"]
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
    canonical: Any = canonical_resolve_symbol(value)
    warnings: List[str] = []

    if canonical.market == CanonicalMarket.CN_A:
        market: Any = Market.CN_A
    elif canonical.market == CanonicalMarket.US:
        market = Market.US
    elif canonical.market == CanonicalMarket.HK:
        market = Market.HK
    else:
        market = Market.OTHER

    normalized: Any = canonical.symbol
    exchange: Any = canonical.exchange or None
    aliases: Dict[str, str] = {}

    if market == Market.CN_A:
        aliases = {"tushare": normalized, "wind": normalized}
        code: Any = normalized.split(".", 1)[0]
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

    raw_token: Any = canonical.input_value.upper().replace(" ", "")
    if raw_token.endswith(".SS") and normalized.endswith(".SH"):
        warnings.append("Input used Yahoo-style .SS; normalized to A-share .SH.")
    if raw_token.isdigit() and market == Market.CN_A:
        warnings.append(f"No suffix provided; inferred {normalized}. Confirm if ambiguity matters.")
    if market == Market.OTHER:
        warnings.append("Could not confidently resolve market. Ask user or provide suffix.")

    disclosure: Any
    price: Any
    financial: Any
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
        f: Any = float(str(value).replace(",", ""))
    except Exception:
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _parse_date(value: Any) -> Optional[dt.date]:
    if value is None:
        return None
    s: Any = str(value).strip()[:10]
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
    report: Any = ValidationReport(dataset="price_history", status=DataStatus.OK, stats={"path": str(path), "adjust": adjust})
    if not path.exists():
        report.status = DataStatus.FAILED
        report.errors.append(f"File not found: {path}")
        report.rating_cap = RatingCap.B
        return report
    rows: Any = read_csv_rows(path)
    required: Any = ["date", "open", "high", "low", "close", "volume"]
    if not rows:
        report.status = DataStatus.FAILED
        report.errors.append("CSV is empty.")
        report.rating_cap = RatingCap.B
        return report
    missing_cols: Any = [c for c in required if c not in rows[0]]
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
    invalid_count: Any = 0
    for i, row in enumerate(rows, start=2):
        d: Any = _parse_date(row.get("date"))
        o: Any = _parse_float(row.get("open"))
        h: Any = _parse_float(row.get("high"))
        l: Any = _parse_float(row.get("low"))
        c: Any = _parse_float(row.get("close"))
        v: Any = _parse_float(row.get("volume"))
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
    adjust_normalized: Any = adjust.lower()
    if adjust_normalized not in {"qfq", "forward", "hfq", "backward", "adjusted", "none", "unadjusted", "unknown"} and not adjust_normalized.startswith("qfq_"):
        report.warnings.append(f"Unknown adjustment flag: {adjust}")
        report.rating_cap = RatingCap.B
    if adjust_normalized in {"unknown", "none", "unadjusted"}:
        report.warnings.append("Historical price adjustment is not confirmed; Chan/DMA conclusions may be capped.")
        report.rating_cap = RatingCap.B

    if dates:
        latest: Any = dates[-1]
        today: Any = dt.datetime.now().date()
        calendar_days_old: Any = (today - latest).days
        stale_threshold: Any = 7 if market in {Market.CN_A, Market.HK, Market.US} else 14
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
            trs: Any = []
            for j in range(len(closes) - 20, len(closes)):
                prev_close: Any = closes[j - 1]
                tr: Any = max(highs[j] - lows[j], abs(highs[j] - prev_close), abs(lows[j] - prev_close))
                trs.append(tr)
            report.stats["atr20"] = sum(trs) / len(trs)

    if invalid_count:
        report.status = DataStatus.FAILED if invalid_count > 5 else DataStatus.PARTIAL
        report.rating_cap = RatingCap.C if invalid_count > 5 else min_cap(report.rating_cap, RatingCap.B)
    return report


def min_cap(a: RatingCap, b: RatingCap) -> RatingCap:
    return stricter_cap(a, b)


def compare_quotes(prices: Sequence[float]) -> Dict[str, Any]:
    valid: Any = [float(p) for p in prices if p and p > 0]
    if len(valid) < 2:
        return {"status": "PARTIAL", "warning": "Need at least two valid prices for cross-source comparison."}
    med: Any = statistics.median(valid)
    max_diff: Any = max(abs(p - med) / med for p in valid)
    if max_diff <= 0.005:
        status: Any = "OK"
    elif max_diff <= 0.02:
        status = "PARTIAL"
    else:
        status = "FAILED"
    return {"status": status, "prices": valid, "median": med, "max_diff_pct": round(max_diff * 100, 4)}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def validate_financials(path: Path) -> ValidationReport:
    report: Any = ValidationReport(dataset="financials", status=DataStatus.OK, stats={"path": str(path)})
    if not path.exists():
        report.status = DataStatus.FAILED
        report.errors.append(f"File not found: {path}")
        report.rating_cap = RatingCap.B
        return report
    data: Any = load_json(path)
    if isinstance(data, list):
        rows: Any = data
    elif isinstance(data, Mapping):
        rows = data.get("periods", [])
    else:
        rows = []
    if not isinstance(rows, list) or not rows:
        report.status = DataStatus.FAILED
        report.errors.append("Financial JSON must contain a non-empty list or {'periods': [...]}.")
        report.rating_cap = RatingCap.B
        return report
    if not all(isinstance(row, Mapping) for row in rows):
        report.status = DataStatus.FAILED
        report.errors.append("Financial rows must be JSON objects.")
        report.rating_cap = RatingCap.B
        return report

    core_statement_fields: Any = ["revenue", "net_income", "operating_cash_flow", "assets", "liabilities", "equity"]
    required_any: Any = ["period", *core_statement_fields]
    bad: Any = 0
    missing_counts: Dict[str, int] = {k: 0 for k in required_any}
    for idx, row in enumerate(rows):
        for k in required_any:
            if k not in row:
                missing_counts[k] += 1
        assets: Any = _parse_float(row.get("assets"))
        liabilities: Any = _parse_float(row.get("liabilities"))
        equity: Any = _parse_float(row.get("equity"))
        if assets and liabilities is not None and equity is not None:
            denom: Any = max(abs(assets), 1.0)
            diff: Any = abs(assets - liabilities - equity) / denom
            if diff > 0.01:
                report.warnings.append(f"Balance sheet identity differs by {diff:.2%} in period {row.get('period')}.")
                bad += 1
        revenue: Any = _parse_float(row.get("revenue"))
        ocf: Any = _parse_float(row.get("operating_cash_flow"))
        net_income: Any = _parse_float(row.get("net_income"))
        if revenue is not None and revenue < 0:
            report.warnings.append(f"Negative revenue in period {row.get('period')}; confirm business context.")
        if net_income and ocf is not None and abs(ocf / net_income) < 0.3:
            report.warnings.append(f"OCF/net income is low in period {row.get('period')}; explain working-capital quality.")

    report.stats["period_count"] = len(rows)
    for key, count in missing_counts.items():
        if count:
            report.warnings.append(f"{count}/{len(rows)} periods missing {key}.")

    period_identity_counts: Dict[Tuple[str, str], int] = {}
    duplicate_period_identities: List[str] = []
    for row in rows:
        normalized: Any = normalize_financial_period(row)
        period_end: Any = str(normalized.get("period_end") or row.get("period") or "")
        period_type: Any = str(normalized.get("period_type") or "unknown")
        if not period_end or period_type == "unknown":
            continue
        identity: Any = (period_end, period_type)
        period_identity_counts[identity] = period_identity_counts.get(identity, 0) + 1
    for (period_end, period_type), count in sorted(period_identity_counts.items()):
        if count > 1:
            duplicate_period_identities.append(f"{period_end}/{period_type} x{count}")
    report.stats["unique_period_identity_count"] = len(period_identity_counts)
    if duplicate_period_identities:
        report.stats["duplicate_period_identities"] = duplicate_period_identities

    def has_core_statement(row: Mapping[str, Any]) -> bool:
        return all(_parse_float(row.get(key)) is not None for key in core_statement_fields)

    sorted_rows: Any = sorted(
        [row for row in rows if isinstance(row, Mapping)],
        key=lambda row: str(row.get("period") or ""),
    )
    latest_row: Any = sorted_rows[-1] if sorted_rows else {}
    latest_period: Any = str(latest_row.get("period") or "") if latest_row else ""
    latest_missing_core_fields: Any = [
        key for key in core_statement_fields
        if not latest_row or _parse_float(latest_row.get(key)) is None
    ]
    core_complete_rows: Any = [
        row for row in rows
        if isinstance(row, Mapping) and has_core_statement(row)
    ]
    latest_core_period: Any = max((str(row.get("period", "")) for row in core_complete_rows), default="")
    report.stats["core_complete_period_count"] = len(core_complete_rows)
    report.stats["core_statement_fields"] = core_statement_fields
    if latest_period:
        report.stats["latest_period"] = latest_period
    report.stats["latest_core_statement_complete"] = not latest_missing_core_fields
    if latest_missing_core_fields:
        report.stats["latest_core_statement_missing_fields"] = latest_missing_core_fields
    if latest_core_period:
        report.stats["latest_core_complete_period"] = latest_core_period

    if latest_missing_core_fields:
        report.status = DataStatus.PARTIAL
        report.rating_cap = RatingCap.B
        period_label: Any = latest_period or "latest retained period"
        report.warnings.append(
            f"Latest financial period {period_label} is missing core statement fields: {', '.join(latest_missing_core_fields)}."
        )
    elif not core_complete_rows:
        report.status = DataStatus.PARTIAL
        report.rating_cap = RatingCap.B
        report.warnings.append("No retained period has all core statement fields: revenue, net_income, operating_cash_flow, assets, liabilities, and equity.")
    elif len(core_complete_rows) < 2:
        report.status = DataStatus.PARTIAL
        report.rating_cap = min_cap(report.rating_cap, RatingCap.A)
        report.warnings.append("Only one retained period has all core statement fields; trend analysis is partial.")
    if duplicate_period_identities:
        report.status = DataStatus.PARTIAL
        report.rating_cap = min_cap(report.rating_cap, RatingCap.B)
        report.warnings.append("Financial rows contain duplicate normalized reporting periods: " + ", ".join(duplicate_period_identities) + ".")
    if bad:
        report.status = DataStatus.PARTIAL
        report.rating_cap = RatingCap.B
    return report


def validate_valuation_inputs(path: Path) -> ValidationReport:
    report: Any = ValidationReport(dataset="valuation_inputs", status=DataStatus.OK, stats={"path": str(path)})
    if not path.exists():
        report.status = DataStatus.FAILED
        report.errors.append(f"File not found: {path}")
        return report
    data: Any = load_json(path)
    if not isinstance(data, Mapping):
        report.status = DataStatus.FAILED
        report.errors.append("Valuation inputs JSON must be an object.")
        return report

    price: Any = _parse_float(data.get("regular_market_price"))
    total_shares: Any = _parse_float(data.get("total_shares"))
    total_market_cap: Any = _parse_float(data.get("total_market_cap"))
    float_shares: Any = _parse_float(data.get("float_shares"))
    float_market_cap: Any = _parse_float(data.get("float_market_cap"))
    currency: Any = str(data.get("currency") or "").strip()
    as_of_date: Any = str(data.get("as_of_date") or "").strip()
    source_basis: Any = str(data.get("source_basis") or "").strip()
    share_count_basis: Any = str(data.get("share_count_basis") or "").strip()
    market_cap_basis: Any = str(data.get("market_cap_basis") or "").strip()

    missing: List[str] = []
    if price is None or price <= 0:
        missing.append("regular_market_price")
    if total_shares is None or total_shares <= 0:
        missing.append("total_shares")
    if total_market_cap is None or total_market_cap <= 0:
        missing.append("total_market_cap")
    for field_name, value in {
        "currency": currency,
        "as_of_date": as_of_date,
        "source_basis": source_basis,
        "share_count_basis": share_count_basis,
        "market_cap_basis": market_cap_basis,
    }.items():
        if not value:
            missing.append(field_name)

    report.stats.update({
        "has_regular_market_price": price is not None and price > 0,
        "has_total_shares": total_shares is not None and total_shares > 0,
        "has_total_market_cap": total_market_cap is not None and total_market_cap > 0,
        "has_float_shares": float_shares is not None and float_shares > 0,
        "has_float_market_cap": float_market_cap is not None and float_market_cap > 0,
        "source_basis": source_basis,
    })
    if price and total_shares and total_market_cap:
        implied_market_cap: Any = price * total_shares
        diff: Any = abs(implied_market_cap - total_market_cap) / max(abs(total_market_cap), 1.0)
        report.stats["implied_total_market_cap"] = implied_market_cap
        report.stats["market_cap_diff_ratio"] = diff
        if diff > 0.08:
            report.status = DataStatus.PARTIAL
            report.warnings.append(f"total_market_cap differs from regular_market_price * total_shares by {diff:.2%}; verify valuation basis.")
    if missing:
        report.status = DataStatus.PARTIAL
        report.warnings.append("Valuation inputs are incomplete: " + ", ".join(missing) + ".")
    if (float_shares is None or float_shares <= 0) or (float_market_cap is None or float_market_cap <= 0):
        report.warnings.append("Float-share or float-market-cap basis is unavailable; liquidity/free-float analysis must stay explicit.")
    return report


def _default_fetch_dir(symbol: str) -> Path:
    stamp: Any = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root: Any = Path(os.getenv("SERENITY_DATA_DIR", "/tmp/serenity-chan-data"))
    safe_symbol: Any = "".join(ch if ch.isalnum() or ch in {"-", ".", "_"} else "_" for ch in symbol)
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
    fieldnames: Any = ["date", "open", "high", "low", "close", "volume", "adj_close", "raw_close"]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer: Any = csv.DictWriter(f, fieldnames=fieldnames)
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
    cap: Any = RatingCap.S

    def downgrade(target: RatingCap) -> None:
        nonlocal cap
        cap = stricter_cap(cap, target)

    keys: Any = list(required_datasets) if required_datasets is not None else list(statuses)
    for key in keys:
        status: Any = statuses.get(key, DataStatus.NOT_REQUESTED.value)
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
    source_usage: Any = result_data.get("source_usage")
    if isinstance(source_usage, Mapping):
        return dict(source_usage)
    return None


def _build_source_integrity_summary(result_items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    l3_structured_datasets: List[str] = []
    preferred_source_gaps: List[str] = []
    for item in result_items:
        source_usage: Any = item.get("source_usage") if isinstance(item.get("source_usage"), dict) else None
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


def _stage_for_source_level(level: SourceLevel | str) -> AcquisitionStage:
    value: Any = getattr(level, "value", str(level))
    if value.startswith("L0_"):
        return AcquisitionStage.PRIMARY_DISCLOSURE
    if value.startswith("L1_"):
        return AcquisitionStage.LICENSED_STRUCTURED
    if value.startswith("L2_"):
        return AcquisitionStage.OPEN_STRUCTURED
    if value.startswith("L3_"):
        return AcquisitionStage.STRUCTURED_PREFLIGHT
    return AcquisitionStage.MANUAL_RETRIEVAL


def _decision_impact_for_dataset(dataset: CanonicalDataset | str) -> DecisionImpact:
    value: Any = getattr(dataset, "value", str(dataset))
    if value in {CanonicalDataset.CURRENT_QUOTE.value, CanonicalDataset.PRICE_HISTORY_ADJUSTED.value, CanonicalDataset.PRICE_HISTORY_RAW.value}:
        return DecisionImpact.ACTION_IMPACT
    if value in {CanonicalDataset.FINANCIALS.value, CanonicalDataset.FILINGS.value, CanonicalDataset.CUSTOMER_EVIDENCE.value}:
        return DecisionImpact.EVIDENCE_IMPACT
    if value in {CanonicalDataset.SHARE_CAPITAL.value, CanonicalDataset.VALUATION_INPUTS.value, CanonicalDataset.PEER_VALUATION.value, CanonicalDataset.ESTIMATES.value}:
        return DecisionImpact.VALUATION_IMPACT
    return DecisionImpact.NO_IMPACT


_PRICE_HISTORY_DATASETS: Any = {
    CanonicalDataset.PRICE_HISTORY_ADJUSTED.value,
    CanonicalDataset.PRICE_HISTORY_RAW.value,
}

_ADJUSTMENT_BASIS_TOKENS: Any = [
    "adjustment is not confirmed",
    "unknown adjustment",
    "source adjustment basis",
    "adjustment basis",
    "unadjusted",
]

_EVIDENCE_DEPTH_TOKENS: Any = [
    "200dma",
    "bar count",
    "evidence window",
    "history depth",
    "medium-term structure",
    "minimum bar",
    "minimum bars",
]


def _is_adjustment_basis_gap(dataset: str, reason: str) -> bool:
    if dataset not in _PRICE_HISTORY_DATASETS:
        return False
    text: Any = reason.lower()
    return any(token in text for token in _ADJUSTMENT_BASIS_TOKENS)


def _is_evidence_depth_gap(dataset: str, reason: str) -> bool:
    if dataset not in _PRICE_HISTORY_DATASETS:
        return False
    text: Any = reason.lower()
    return any(token in text for token in _EVIDENCE_DEPTH_TOKENS)


def _validation_cap_gap_types(dataset: str, reason: str) -> List[DataGapType]:
    gap_types: List[DataGapType] = []
    if _is_adjustment_basis_gap(dataset, reason):
        gap_types.append(DataGapType.ADJUSTMENT_BASIS_UNVERIFIED)
    if _is_evidence_depth_gap(dataset, reason):
        gap_types.append(DataGapType.EVIDENCE_DEPTH_LIMIT)
    return gap_types or [DataGapType.EVIDENCE_DEPTH_LIMIT]


def _classify_gap(status: str, reason: str, *, dataset: str = "", source_level: str = "") -> DataGapType:
    text: Any = reason.lower()
    if status == DataStatus.STALE.value:
        return DataGapType.STALE_DATA
    if _is_adjustment_basis_gap(dataset, reason):
        return DataGapType.ADJUSTMENT_BASIS_UNVERIFIED
    if "does not support dataset" in text or "unsupported dataset" in text:
        return DataGapType.SOURCE_NOT_IMPLEMENTED
    if "does not support market" in text or "market is unknown" in text:
        return DataGapType.POLICY_BLOCKED
    if any(token in text for token in ["403", "forbidden", "429", "timeout", "timed out", "exceeded", "ssl", "certificate", "network", "urlopen", "connection", "jsondecodeerror", "json parse failed", "malformed json", "parse failed"]):
        return DataGapType.ACCESS_FAILURE
    if any(token in text for token in ["ohlc consistency", "price difference", "cross-source", "difference"]):
        return DataGapType.CONFLICTING_SOURCES
    if any(token in text for token in ["not machine-readable", "line extraction", "line-item", "line item"]):
        return DataGapType.NOT_MACHINE_READABLE
    if any(token in text for token in ["financial-sector", "industry-specific", "bank/insurance/securities"]):
        return DataGapType.SOURCE_NOT_IMPLEMENTED
    if any(token in text for token in ["no announcements", "no filings", "not disclosed", "issuer", "annual report not found"]):
        return DataGapType.ISSUER_NON_DISCLOSURE
    if source_level.startswith("L3_") and dataset == CanonicalDataset.FINANCIALS.value:
        return DataGapType.NOT_MACHINE_READABLE
    return DataGapType.SOURCE_UNAVAILABLE


def _attempt_from_provider(
    *,
    dataset: CanonicalDataset,
    provider_name: str,
    source_level: SourceLevel | str,
    status: DataStatus,
    attempted_at: str,
    reason: str = "",
) -> Dict[str, Any]:
    level_value: Any = getattr(source_level, "value", str(source_level))
    gap_type: Any = None
    decision_impact: Any = None
    if status != DataStatus.OK:
        gap_type = _classify_gap(status.value, reason, dataset=dataset.value, source_level=level_value).value
        decision_impact = _decision_impact_for_dataset(dataset).value
    return FetchAttempt(
        dataset=dataset.value,
        source_name=provider_name,
        source_level=level_value,
        stage=_stage_for_source_level(source_level).value,
        status=status.value,
        attempted_at=attempted_at,
        gap_type=gap_type,
        decision_impact=decision_impact,
        reason=reason,
    ).to_dict()


def _fetch_with_attempt_ledger(
    providers: Iterable[DataProvider],
    symbol: Any,
    dataset: CanonicalDataset,
    **kwargs: Any,
) -> Tuple[DataResult, List[Dict[str, Any]]]:
    provider_kwargs: Any = dict(kwargs)
    provider_timeout_seconds: Any = _provider_timeout_seconds(provider_kwargs.pop("provider_timeout_seconds", None))
    attempts: List[Dict[str, Any]] = []
    if symbol.market == CanonicalMarket.UNKNOWN:
        result: Any = DataResult.failed(dataset, symbol.symbol, "symbol_resolver", SourceLevel.L4, "market is UNKNOWN; cannot route data safely")
        attempts.append(_attempt_from_provider(
            dataset=dataset,
            provider_name="symbol_resolver",
            source_level=SourceLevel.L4,
            status=DataStatus.FAILED,
            attempted_at=result.retrieved_at,
            reason="market is UNKNOWN; cannot route data safely",
        ))
        return result, attempts

    failures: List[str] = []
    for provider in providers:
        allowed: Any
        reason: Any
        allowed, reason = provider_is_allowed(provider, symbol, dataset)
        source_level: Any = getattr(provider, "level", SourceLevel.L4)
        if not allowed:
            attempts.append(_attempt_from_provider(
                dataset=dataset,
                provider_name=getattr(provider, "name", provider.__class__.__name__),
                source_level=source_level,
                status=DataStatus.NOT_APPLICABLE,
                attempted_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                reason=reason,
            ))
            continue

        provider_name: Any = getattr(provider, "name", provider.__class__.__name__)
        try:
            with _provider_timeout(provider_timeout_seconds, provider_name=provider_name, dataset=dataset):
                result = provider.fetch(symbol, dataset, **provider_kwargs)
        except ProviderTimeoutError as exc:
            reason = str(exc)
            failures.append(f"{provider_name}: {reason}")
            attempts.append(_attempt_from_provider(
                dataset=dataset,
                provider_name=provider_name,
                source_level=source_level,
                status=DataStatus.FAILED,
                attempted_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                reason=reason,
            ))
            continue
        except Exception as exc:  # defensive; provider errors must not crash whole agent
            reason = f"{type(exc).__name__}: {exc}"
            failures.append(f"{provider_name}: {reason}")
            attempts.append(_attempt_from_provider(
                dataset=dataset,
                provider_name=provider_name,
                source_level=source_level,
                status=DataStatus.FAILED,
                attempted_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                reason=reason,
            ))
            continue

        if result.ok:
            attempts.append(_attempt_from_provider(
                dataset=dataset,
                provider_name=result.source_name,
                source_level=result.source_level,
                status=DataStatus.OK,
                attempted_at=result.retrieved_at,
                reason="",
            ))
            return result, attempts

        reason = "; ".join(result.errors) or "not ok"
        failures.append(f"{provider_name}: {reason}")
        attempts.append(_attempt_from_provider(
            dataset=dataset,
            provider_name=result.source_name,
            source_level=result.source_level,
            status=DataStatus.FAILED,
            attempted_at=result.retrieved_at,
            reason=reason,
        ))

    result = DataResult.failed(dataset, symbol.symbol, "provider_chain", SourceLevel.L4, "All providers failed or incompatible: " + " | ".join(failures))
    if not attempts:
        attempts.append(_attempt_from_provider(
            dataset=dataset,
            provider_name="provider_chain",
            source_level=SourceLevel.L4,
            status=DataStatus.FAILED,
            attempted_at=result.retrieved_at,
            reason="no providers configured",
        ))
    return result, attempts


def _rating_impact_for_gap(dataset: str, status: str, gap_type: str) -> str:
    if gap_type == DataGapType.NOT_MACHINE_READABLE.value and dataset == CanonicalDataset.FINANCIALS.value:
        return "S/A research ratings require L0/L1 verification of key financial statements."
    if dataset in {CanonicalDataset.SHARE_CAPITAL.value, CanonicalDataset.VALUATION_INPUTS.value, CanonicalDataset.PEER_VALUATION.value, CanonicalDataset.ESTIMATES.value}:
        return "Market payoff, valuation multiples, market-implied growth, and action priority are gated until valuation inputs are complete."
    if gap_type == DataGapType.ADJUSTMENT_BASIS_UNVERIFIED.value:
        return "Price history exists, but the adjustment basis is unverified; Chan, moving-average, and current action conclusions are capped."
    if gap_type == DataGapType.EVIDENCE_DEPTH_LIMIT.value:
        if dataset in {CanonicalDataset.CURRENT_QUOTE.value, CanonicalDataset.PRICE_HISTORY_ADJUSTED.value, CanonicalDataset.PRICE_HISTORY_RAW.value}:
            return "Data is usable, but the validated history depth or evidence window caps current action and technical conviction."
        return "Data is usable, but validation limits evidence depth before high-conviction ratings are allowed."
    if dataset in {CanonicalDataset.CURRENT_QUOTE.value, CanonicalDataset.PRICE_HISTORY_ADJUSTED.value}:
        return "Current action, valuation reference, and buy-point conclusions are capped at B."
    if dataset in {CanonicalDataset.FINANCIALS.value, CanonicalDataset.FILINGS.value}:
        return "High-conviction long-term ratings are capped at B until primary or licensed evidence is complete."
    if status in {DataStatus.PARTIAL.value, DataStatus.STALE.value}:
        return "Evidence confidence is capped at A until the gap is cleared."
    return "No rating lift is allowed from this dataset until the gap is cleared."


def _next_action_for_gap(dataset: str, gap_type: str) -> str:
    if dataset == CanonicalDataset.FINANCIALS.value and gap_type == DataGapType.NOT_MACHINE_READABLE.value:
        return "Reconcile revenue, profit, cash flow, assets, liabilities, and equity against official filings or an L1 database export."
    if dataset == CanonicalDataset.FINANCIALS.value and gap_type == DataGapType.SOURCE_NOT_IMPLEMENTED.value:
        return "Use the issuer's industry-specific official statement metrics, such as bank NIM/NPL/provision/capital ratios or insurance/ securities equivalents, before scoring fundamentals."
    if dataset == CanonicalDataset.FINANCIALS.value:
        return "Fetch latest official or licensed financial statements and run financial validation before scoring fundamentals."
    if dataset == CanonicalDataset.FILINGS.value:
        return "Fetch official filings or announcement metadata from the market-primary disclosure venue."
    if dataset == CanonicalDataset.CURRENT_QUOTE.value:
        return "Fetch a current market quote from a market-appropriate source before making current price or valuation claims."
    if dataset in {CanonicalDataset.SHARE_CAPITAL.value, CanonicalDataset.VALUATION_INPUTS.value}:
        return "Fetch total shares, float shares, total market cap, and float market cap from a market-appropriate source before inferring valuation or market-implied growth."
    if dataset in {CanonicalDataset.PEER_VALUATION.value, CanonicalDataset.ESTIMATES.value}:
        return "Fetch market-appropriate valuation comparables or estimates before upgrading payoff claims."
    if dataset == CanonicalDataset.PRICE_HISTORY_ADJUSTED.value:
        if gap_type == DataGapType.ADJUSTMENT_BASIS_UNVERIFIED.value:
            return "Fetch or reconstruct adjusted daily history with explicit forward/backward adjustment basis and corporate-action evidence."
        if gap_type == DataGapType.EVIDENCE_DEPTH_LIMIT.value:
            return "Extend adjusted daily history to the configured minimum bar count, or keep action timing and technical claims capped."
        if gap_type == DataGapType.CONFLICTING_SOURCES.value:
            return "Cross-check adjusted history with another market-appropriate source before making Chan or moving-average claims."
        return "Fetch adjusted daily history with enough bars for Chan and moving-average analysis."
    if gap_type == DataGapType.ACCESS_FAILURE.value:
        return "Fix access prerequisites, identity headers, network, or rate-limit handling, then retry the same source route."
    return "Open the data bundle, inspect source errors, and fetch the missing market-appropriate evidence."


def _manual_task_for_gap(gap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    dataset: Any = str(gap.get("dataset") or "")
    impact: Any = str(gap.get("decision_impact") or "")
    gap_type: Any = str(gap.get("gap_type") or "")
    if impact == DecisionImpact.NO_IMPACT.value:
        return None
    if gap_type in {
        DataGapType.SCOPE_NOT_REQUESTED.value,
        DataGapType.SOURCE_NOT_IMPLEMENTED.value,
        DataGapType.SOURCE_UNAVAILABLE.value,
        DataGapType.ACCESS_FAILURE.value,
        DataGapType.STALE_DATA.value,
    } and dataset in {
        CanonicalDataset.SHARE_CAPITAL.value,
        CanonicalDataset.VALUATION_INPUTS.value,
        CanonicalDataset.PEER_VALUATION.value,
        CanonicalDataset.ESTIMATES.value,
    }:
        return ManualRetrievalTask(
            dataset=dataset,
            priority="high",
            target_source="Exchange, official filings, licensed vendor, or validated quote-derived market-cap source",
            objective="Recover valuation inputs before inferring market-implied growth, payoff quality, or action priority.",
            acceptance_criteria=[
                "Total shares and float shares are explicit, or market cap and latest price can derive the share basis.",
                "Total market cap, float market cap, currency, and as-of date are recorded.",
                "Source basis is labeled as official, licensed, or quote-derived preflight.",
                "Stale or conflicting share-count bases remain valuation-gated until reconciled.",
            ],
        ).to_dict()
    if gap_type == DataGapType.ADJUSTMENT_BASIS_UNVERIFIED.value and dataset in _PRICE_HISTORY_DATASETS:
        return ManualRetrievalTask(
            dataset=dataset,
            priority="high",
            target_source="Exchange, licensed vendor, or validated open market-data source",
            objective="Verify the adjusted-history basis before making Chan, moving-average, or action-timing claims.",
            acceptance_criteria=[
                "Forward/backward/unadjusted basis is explicit.",
                "Corporate-action evidence or vendor adjustment method is recorded.",
                "Latest trading day and enough bars for the intended technical window are present.",
            ],
        ).to_dict()
    if gap_type == DataGapType.EVIDENCE_DEPTH_LIMIT.value and dataset == CanonicalDataset.PRICE_HISTORY_ADJUSTED.value:
        return ManualRetrievalTask(
            dataset=dataset,
            priority="high",
            target_source="Exchange, licensed vendor, or validated open market-data source",
            objective="Extend adjusted daily history to the configured minimum bar count before making high-conviction timing or technical-structure claims.",
            acceptance_criteria=[
                "Latest trading day is present.",
                "Adjustment basis is stated.",
                "At least the configured minimum bars are available, or the shorter listing/history window is explicitly treated as an action-timing cap.",
            ],
        ).to_dict()
    if dataset == CanonicalDataset.FINANCIALS.value:
        return ManualRetrievalTask(
            dataset=dataset,
            priority="critical",
            target_source="Official filing PDF/XBRL or L1 financial database",
            objective="Verify the latest core financial statement lines before issuing an S/A research rating.",
            acceptance_criteria=[
                "Latest reporting period is identified.",
                "Revenue, profit, operating cash flow, assets, liabilities, and equity are reconciled.",
                "Units, currency, and reporting standard are explicit.",
                "Financial-sector issuers include bank, insurance, or securities-specific risk, capital, and profitability metrics.",
            ],
        ).to_dict()
    if dataset == CanonicalDataset.FILINGS.value:
        return ManualRetrievalTask(
            dataset=dataset,
            priority="critical",
            target_source="Market-primary disclosure venue",
            objective="Verify announcements, annual reports, interim reports, risk events, and customer/order claims.",
            acceptance_criteria=[
                "Source venue matches the resolved market.",
                "Filing title, date, URL/path, and source level are recorded.",
                "Claims used in the thesis are linked to a concrete filing or announcement.",
            ],
        ).to_dict()
    if dataset in {CanonicalDataset.CURRENT_QUOTE.value, CanonicalDataset.PRICE_HISTORY_ADJUSTED.value}:
        return ManualRetrievalTask(
            dataset=dataset,
            priority="high",
            target_source="Exchange, licensed vendor, or validated open market-data source",
            objective="Recover current quote and adjusted history needed for valuation reference and action timing.",
            acceptance_criteria=[
                "Latest trading day is present.",
                "Adjustment basis is stated.",
                "At least the configured minimum bars are available for technical analysis.",
            ],
        ).to_dict()
    return None


def _build_data_gaps(
    result_items: Sequence[Dict[str, Any]],
    statuses: Mapping[str, str],
    *,
    requested_dataset_values: Sequence[str],
    critical_datasets: Sequence[str],
) -> List[Dict[str, Any]]:
    gaps: List[Dict[str, Any]] = []
    seen_keys: set[Tuple[str, str, str]] = set()
    requested_set: Any = set(requested_dataset_values)

    for item in result_items:
        dataset: Any = str(item.get("dataset") or "")
        status: Any = str(item.get("status") or DataStatus.FAILED.value)
        source_level: Any = str(item.get("source_level") or "")
        source_name: Any = str(item.get("source") or "")
        reason_items: List[str] = []
        reason_items.extend(str(x) for x in (item.get("errors") or []))
        reason_items.extend(str(x) for x in (item.get("warnings") or []))
        validation: Any = item.get("validation") if isinstance(item.get("validation"), dict) else {}
        if isinstance(validation, dict):
            reason_items.extend(str(x) for x in (validation.get("errors") or []))
            reason_items.extend(str(x) for x in (validation.get("warnings") or []))
        reason: Any = "; ".join(reason_items)

        gap_entries: List[Tuple[DataGapType, str]] = []

        def add_gap(gap_type: DataGapType, gap_status: str = status) -> None:
            if (gap_type, gap_status) not in gap_entries:
                gap_entries.append((gap_type, gap_status))

        if status in {DataStatus.FAILED.value, DataStatus.PARTIAL.value, DataStatus.STALE.value, DataStatus.PENDING.value}:
            add_gap(_classify_gap(status, reason, dataset=dataset, source_level=source_level))
        if dataset == CanonicalDataset.FINANCIALS.value and source_level.startswith("L3_"):
            add_gap(
                DataGapType.NOT_MACHINE_READABLE,
                DataStatus.PARTIAL.value if status == DataStatus.OK.value else status,
            )
        if isinstance(validation, dict):
            validation_cap: Any = validation.get("rating_cap")
            if isinstance(validation_cap, str):
                try:
                    if stricter_cap(RatingCap.S, RatingCap(validation_cap)) != RatingCap.S:
                        for validation_gap_type in _validation_cap_gap_types(dataset, reason):
                            add_gap(validation_gap_type)
                except ValueError:
                    pass

        if not gap_entries:
            continue
        for gap_type, gap_status in gap_entries:
            key: Any = (dataset, gap_status, gap_type.value)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            gap: Any = DataGap(
                dataset=dataset,
                status=gap_status,
                gap_type=gap_type.value,
                decision_impact=_decision_impact_for_dataset(dataset).value,
                rating_impact=_rating_impact_for_gap(dataset, gap_status, gap_type.value),
                next_action=_next_action_for_gap(dataset, gap_type.value),
                source_name=source_name,
                source_level=source_level,
                evidence_path=item.get("raw_path") or item.get("data_path"),
            ).to_dict()
            gaps.append(gap)

    for dataset in critical_datasets:
        if dataset in requested_set:
            continue
        if statuses.get(dataset) == DataStatus.OK.value:
            continue
        key = (dataset, DataStatus.NOT_REQUESTED.value, DataGapType.SCOPE_NOT_REQUESTED.value)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        gap = DataGap(
            dataset=dataset,
            status=DataStatus.NOT_REQUESTED.value,
            gap_type=DataGapType.SCOPE_NOT_REQUESTED.value,
            decision_impact=_decision_impact_for_dataset(dataset).value,
            rating_impact=_rating_impact_for_gap(dataset, DataStatus.NOT_REQUESTED.value, DataGapType.SCOPE_NOT_REQUESTED.value),
            next_action=_next_action_for_gap(dataset, DataGapType.SCOPE_NOT_REQUESTED.value),
        ).to_dict()
        gaps.append(gap)

    return gaps


def _build_research_debt(data_gaps: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    debt: List[Dict[str, Any]] = []
    for gap in data_gaps:
        impact: Any = str(gap.get("decision_impact") or "")
        dataset: Any = str(gap.get("dataset") or "")
        if impact == DecisionImpact.NO_IMPACT.value:
            continue
        priority: Any = "critical" if dataset in {CanonicalDataset.FINANCIALS.value, CanonicalDataset.FILINGS.value} else "high"
        if str(gap.get("gap_type")) == DataGapType.SCOPE_NOT_REQUESTED.value:
            priority = "high"
        debt.append({
            "dataset": dataset,
            "priority": priority,
            "gap_type": gap.get("gap_type"),
            "decision_impact": impact,
            "rating_impact": gap.get("rating_impact"),
            "next_action": gap.get("next_action"),
            "source_name": gap.get("source_name", ""),
            "source_level": gap.get("source_level", ""),
            **_research_debt_runbook_fields(gap, priority=priority),
        })
    return debt


def _research_debt_runbook_fields(gap: Mapping[str, Any], *, priority: str) -> Dict[str, Any]:
    dataset: Any = str(gap.get("dataset") or "")
    impact: Any = str(gap.get("decision_impact") or "")
    source: Any = str(gap.get("source_name") or "")

    if impact == DecisionImpact.VALUATION_IMPACT.value:
        axis: Any = "market_payoff"
        blocking_level: Any = "rating_and_action"
    elif impact == DecisionImpact.ACTION_IMPACT.value:
        axis = "action_readiness"
        blocking_level = "action"
    elif impact == DecisionImpact.EVIDENCE_IMPACT.value:
        axis = "evidence_confidence"
        blocking_level = "rating_and_action" if priority == "critical" else "rating"
    else:
        axis = "source_quality"
        blocking_level = "research_priority"

    if dataset == CanonicalDataset.FILINGS.value:
        if "HKEX" in source:
            preferred: Any = ["HKEXnews stock-code/date-range search", "HKEX issuer filings page"]
            fallback: Any = ["Company IR announcements", "Company annual/interim report page", "Licensed disclosure database"]
        elif "CNINFO" in source or not source:
            preferred = ["CNINFO stock-specific announcement query", "Listing exchange disclosure page"]
            fallback = ["SSE/SZSE/BSE announcement search", "Company IR announcements", "Eastmoney announcements as L3 lead"]
        else:
            preferred = ["Market-primary disclosure venue", "Company IR"]
            fallback = ["Licensed disclosure database", "Validated issuer PDF archive"]
        validation: Any = [
            "latest annual/interim/quarterly report",
            "major contracts or bid-win announcements",
            "capacity expansion and capex disclosures",
            "customer/order claims",
            "capital actions and regulatory inquiry letters",
        ]
        expected: Any = "Evidence confidence can be upgraded only after thesis claims link to concrete filings or announcements."
    elif dataset == CanonicalDataset.FINANCIALS.value:
        preferred = ["Official filing XBRL/PDF", "L1 financial database export"]
        fallback = ["Issuer IR financial reports", "Exchange filing archive", "L3 structured preflight only as a lead"]
        validation = [
            "latest reporting period",
            "revenue",
            "net income",
            "operating cash flow",
            "assets",
            "liabilities",
            "equity",
            "unit and currency",
        ]
        expected = "Financial quality and rating cap can improve after core statement lines are reconciled with L0/L1 evidence."
    elif dataset in {CanonicalDataset.VALUATION_INPUTS.value, CanonicalDataset.SHARE_CAPITAL.value}:
        preferred = ["Exchange share-capital disclosure", "Official filing share count", "Licensed market data"]
        fallback = ["Validated quote-derived market cap", "Issuer IR capital structure disclosure"]
        validation = ["current price", "total shares", "float shares when available", "total market cap", "currency", "as-of date", "share-count basis"]
        expected = "Market-implied growth and valuation gates can be recomputed with an explicit preflight or verified valuation stage."
    elif dataset in _PRICE_HISTORY_DATASETS:
        preferred = ["Exchange or licensed adjusted history", "Validated open market-data source"]
        fallback = ["Corporate-action-adjusted reconstruction", "Secondary quote provider cross-check"]
        validation = ["latest trading day", "adjustment basis", "corporate-action evidence", "minimum bar count"]
        expected = "Chan timing and action-readiness gates can be evaluated after adjusted history is complete."
    else:
        preferred = ["Market-appropriate primary source"]
        fallback = ["Licensed vendor", "Issuer IR", "Validated secondary source"]
        validation = ["source identity", "as-of date", "field completeness", "claim linkage"]
        expected = "Research priority can be upgraded only after the missing evidence is linked to the affected decision axis."

    return {
        "axis": axis,
        "blocking_level": blocking_level,
        "preferred_sources": preferred,
        "fallback_sources": fallback,
        "validation_target": validation,
        "expected_effect_if_resolved": expected,
    }


def _build_ai_review_guidance(
    result_items: Sequence[Dict[str, Any]],
    data_quality: Dict[str, str],
    data_gaps: Optional[Sequence[Dict[str, Any]]] = None,
    research_debt: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
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
        dataset: Any = str(item.get("dataset") or "")
        status: Any = str(item.get("status") or "")
        source_level: Any = str(item.get("source_level") or "")
        source: Any = str(item.get("source") or "")
        source_usage: Any = item.get("source_usage") if isinstance(item.get("source_usage"), dict) else None
        validation: Any = item.get("validation") if isinstance(item.get("validation"), dict) else {}
        validation_warnings: Any = validation.get("warnings") if isinstance(validation, dict) else []

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
            if (
                source_usage
                and source_usage.get("financial_sector_profile_required")
                and source_usage.get("financial_sector_profile_status") != "OK"
            ):
                blockers.append(
                    "Financial-sector issuer requires bank/insurance/securities-specific statement metrics; ordinary operating-company financial fields are not sufficient for S/A ratings."
                )
                upgrade_requirements.extend([
                    "For banks, extract net interest income, net interest margin, non-performing loan ratio, provision coverage, capital adequacy, deposits, loans, and risk-weighted asset context from official reports.",
                    "For insurers or securities firms, extract the equivalent industry-specific solvency, underwriting, investment, brokerage, capital, and risk-quality metrics before scoring fundamentals.",
                ])
            if source_usage and source_usage.get("report_pdf_evidence_used") and not source_usage.get("report_line_items_extracted"):
                blockers.append(
                    f"Financials use official report PDF evidence from {source}; line items must be extracted or reconciled before S/A ratings."
                )
                upgrade_requirements.extend([
                    "Extract and reconcile revenue, profit, operating cash flow, assets, liabilities, equity, reporting currency, and share-count basis from the official report PDFs or an L1 database export.",
                    "Explain whether the issuer reports annual, interim, or quarterly periods and avoid mixing standalone and cumulative figures.",
                ])
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
                if not (source_usage and source_usage.get("report_pdf_evidence_used")):
                    upgrade_requirements.append("Fetch market-primary financial statements before upgrading: SEC XBRL/company filings for US, CNINFO/exchange reports or L1 data for A-share, HKEX/company reports for HK.")
            if validation_warnings:
                checks.append("Financial validation warnings require explicit AI explanation, not mechanical promotion or downgrade.")

        if dataset in {CanonicalDataset.CURRENT_QUOTE.value, CanonicalDataset.PRICE_HISTORY_ADJUSTED.value} and status in {DataStatus.PARTIAL.value, DataStatus.FAILED.value, DataStatus.STALE.value}:
            blockers.append(f"{dataset} status is {status}; do not issue a current entry/buy-point conclusion.")
            upgrade_requirements.append("Fetch market-appropriate current quote and adjusted history before making current price, valuation, or buy-point claims.")

        if dataset == CanonicalDataset.VALUATION_INPUTS.value and status != DataStatus.OK.value:
            blockers.append(f"valuation_inputs status is {status}; do not upgrade payoff, market-implied growth, or action priority.")
            upgrade_requirements.append("Fetch a complete valuation input set with current price, total shares, total market cap, currency, as-of date, share-count basis, and market-cap basis.")

        if dataset == CanonicalDataset.FILINGS.value and status != DataStatus.OK.value:
            blockers.append(f"Filing/announcement status is {status}; customer/order/capacity claims remain unverified leads.")
            upgrade_requirements.append("Fetch market-primary filings or announcements before treating customer, order, capacity, risk, or governance claims as verified.")

    if data_quality.get("rating_cap") in {RatingCap.B.value, RatingCap.C.value, RatingCap.OBSERVE_ONLY.value}:
        checks.append("When the cap is B or lower, the output must frame the result as observation/pre-research unless stronger primary evidence is added.")

    if data_gaps:
        checks.append("Read data_gaps before scoring; each material gap must map to a rating limit, action limit, or manual retrieval task.")
    if research_debt:
        blockers.append("Research debt is open; do not upgrade action readiness until the debt items are cleared or explicitly scoped out.")
        upgrade_requirements.extend(
            str(item.get("next_action"))
            for item in research_debt
            if isinstance(item, Mapping) and item.get("next_action")
        )

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
    symbol: Any = canonical_resolve_symbol(symbol_value)
    destination: Any = Path(out_dir) if out_dir else _default_fetch_dir(symbol.symbol)
    destination.mkdir(parents=True, exist_ok=True)

    providers: Any = default_real_providers(symbol)
    result_items: List[Dict[str, Any]] = []
    attempt_ledger: List[Dict[str, Any]] = []
    statuses: Dict[str, str] = {}
    fetched_payloads: Dict[str, Dict[str, Any]] = {}
    validation_caps: List[str] = []
    router_market: Any = _market_for_router(symbol.market)
    requested_dataset_values: Any = [CanonicalDataset(dataset_name).value for dataset_name in datasets]

    for dataset_name in requested_dataset_values:
        dataset: Any = CanonicalDataset(dataset_name)
        provider_kwargs: Dict[str, Any] = {"raw_dir": destination / "raw"}
        if dataset in {CanonicalDataset.PRICE_HISTORY_RAW, CanonicalDataset.PRICE_HISTORY_ADJUSTED}:
            provider_kwargs.update({"range": chart_range, "interval": interval})
        if dataset in {CanonicalDataset.SHARE_CAPITAL, CanonicalDataset.VALUATION_INPUTS} and CanonicalDataset.CURRENT_QUOTE.value in fetched_payloads:
            provider_kwargs["current_quote_result"] = fetched_payloads[CanonicalDataset.CURRENT_QUOTE.value]
        result: Any
        attempts: Any
        result, attempts = _fetch_with_attempt_ledger(
            providers,
            symbol,
            dataset,
            **provider_kwargs,
        )
        attempt_ledger.extend(attempts)
        source_level_value: Any = getattr(result.source_level, "value", str(result.source_level))
        source_usage: Any = _source_usage_from_result(result.data)
        data_path: Optional[str] = None
        validation_payload: Optional[Dict[str, Any]] = None
        status: Any = "OK" if result.ok else "FAILED"

        if result.ok:
            if dataset in {CanonicalDataset.PRICE_HISTORY_RAW, CanonicalDataset.PRICE_HISTORY_ADJUSTED}:
                csv_path: Any = destination / f"{symbol.symbol}_{dataset.value}.csv"
                _write_price_csv(csv_path, list(result.data or []))
                data_path = str(csv_path)
                adjust_basis: Any = result.adjust or ("adjusted" if dataset == CanonicalDataset.PRICE_HISTORY_ADJUSTED else "unadjusted")
                validation: Any = validate_price_history(
                    csv_path,
                    router_market,
                    adjust_basis,
                    min_bars,
                )
                adjust_basis_normalized: Any = adjust_basis.lower()
                if dataset == CanonicalDataset.PRICE_HISTORY_ADJUSTED and adjust_basis_normalized not in {"qfq", "forward", "hfq", "backward", "adjusted"} and not adjust_basis_normalized.startswith("qfq_"):
                    validation.warnings.append(f"Requested adjusted history but source adjustment basis is {adjust_basis}.")
                    if validation.status == DataStatus.OK:
                        validation.status = DataStatus.PARTIAL
                    validation.rating_cap = stricter_cap(validation.rating_cap, RatingCap.B)
                status = validation.status.value
                validation_payload = json.loads(json.dumps(validation, ensure_ascii=False, default=lambda o: o.value if isinstance(o, Enum) else asdict(o)))
                validation_caps.append(validation.rating_cap.value)
            else:
                json_path: Any = destination / f"{symbol.symbol}_{dataset.value}.json"
                _write_json(json_path, result.data)
                data_path = str(json_path)
                if dataset == CanonicalDataset.FINANCIALS:
                    report_evidence: Any = result.data.get("official_report_evidence") if isinstance(result.data, Mapping) else None
                    has_period_rows: Any = isinstance(result.data, Mapping) and isinstance(result.data.get("periods"), list) and bool(result.data.get("periods"))
                    if isinstance(report_evidence, Mapping) and not has_period_rows:
                        validation = ValidationReport(
                            dataset="financials",
                            status=DataStatus.PARTIAL,
                            warnings=[
                                "Official financial report PDFs were fetched, but core statement lines were not extracted from the available PDF text.",
                                "Reconcile revenue, profit, cash flow, assets, liabilities, equity, currency, and share-count basis before S/A ratings.",
                            ],
                            stats={
                                "official_report_status": report_evidence.get("status"),
                                "report_count": len(report_evidence.get("reports", []) or []),
                                "downloaded_report_count": len(report_evidence.get("downloaded_reports", []) or []),
                            },
                            rating_cap=RatingCap.B,
                        )
                        status = validation.status.value
                        validation_payload = json.loads(json.dumps(validation, ensure_ascii=False, default=lambda o: o.value if isinstance(o, Enum) else asdict(o)))
                        validation_caps.append(validation.rating_cap.value)
                        result.warnings.append("Financials are official report PDF evidence; final S/A research ratings require line-item extraction or L1 reconciliation.")
                    else:
                        validation = validate_financials(json_path)
                        status = validation.status.value
                        if (
                            source_usage
                            and source_usage.get("financial_sector_profile_required")
                            and source_usage.get("financial_sector_profile_status") != "OK"
                        ):
                            validation.status = DataStatus.PARTIAL
                            validation.rating_cap = stricter_cap(validation.rating_cap, RatingCap.B)
                            validation.warnings.append(
                                "Financial-sector issuer requires industry-specific bank/insurance/securities metrics; ordinary operating-company financial fields are not sufficient for S/A ratings."
                            )
                            status = validation.status.value
                        validation_payload = json.loads(json.dumps(validation, ensure_ascii=False, default=lambda o: o.value if isinstance(o, Enum) else asdict(o)))
                        validation_caps.append(validation.rating_cap.value)
                    if source_level_value.startswith("L3_"):
                        validation_caps.append(RatingCap.B.value)
                        result.warnings.append("Financials use L3/F10 structured preflight data; final S/A research ratings require L0/L1 verification.")
                    elif source_level_value.startswith("L4_"):
                        validation_caps.append(RatingCap.B.value)
                        result.warnings.append("Financials are from unverified L4 source; high-conviction conclusions are not allowed.")
                elif dataset == CanonicalDataset.VALUATION_INPUTS:
                    validation = validate_valuation_inputs(json_path)
                    status = validation.status.value
                    validation_payload = json.loads(json.dumps(validation, ensure_ascii=False, default=lambda o: o.value if isinstance(o, Enum) else asdict(o)))

            fetched_payloads[dataset.value] = {
                "data": result.data,
                "as_of_date": result.as_of_date,
                "source_name": result.source_name,
                "source_level": source_level_value,
                "currency": result.currency,
            }

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

    effective_statuses: Any = dict(statuses)

    rating_critical_datasets: Any = [
        CanonicalDataset.CURRENT_QUOTE.value,
        CanonicalDataset.PRICE_HISTORY_ADJUSTED.value,
        CanonicalDataset.FINANCIALS.value,
        CanonicalDataset.FILINGS.value,
    ]
    full_research_required_datasets: Any = [
        *rating_critical_datasets,
        CanonicalDataset.VALUATION_INPUTS.value,
    ]
    rating_cap_exempt_datasets: Any = {
        CanonicalDataset.SHARE_CAPITAL.value,
        CanonicalDataset.VALUATION_INPUTS.value,
    }
    rating_requested_dataset_values: Any = [
        dataset for dataset in requested_dataset_values
        if dataset not in rating_cap_exempt_datasets
    ]
    requested_cap: Any = _cap_for_statuses(
        effective_statuses,
        validation_caps,
        required_datasets=rating_requested_dataset_values,
        downgrade_not_requested=False,
    )
    full_research_cap: Any = _cap_for_statuses(
        effective_statuses,
        validation_caps,
        required_datasets=rating_critical_datasets,
        downgrade_not_requested=True,
    )
    data_quality: Any = {
        "market_resolution": "OK" if symbol.market != CanonicalMarket.UNKNOWN else "FAILED",
        "current_price": effective_statuses.get(CanonicalDataset.CURRENT_QUOTE.value, DataStatus.NOT_REQUESTED.value),
        "adjusted_history": effective_statuses.get(CanonicalDataset.PRICE_HISTORY_ADJUSTED.value, DataStatus.NOT_REQUESTED.value),
        "valuation_inputs": effective_statuses.get(CanonicalDataset.VALUATION_INPUTS.value, DataStatus.NOT_REQUESTED.value),
        "financials": effective_statuses.get(CanonicalDataset.FINANCIALS.value, DataStatus.NOT_REQUESTED.value),
        "filings": effective_statuses.get(CanonicalDataset.FILINGS.value, DataStatus.NOT_REQUESTED.value),
        "requested_data_rating_cap": requested_cap.value,
        "full_research_rating_cap": full_research_cap.value,
        "rating_cap": full_research_cap.value,
    }
    data_gaps: Any = _build_data_gaps(
        result_items,
        effective_statuses,
        requested_dataset_values=requested_dataset_values,
        critical_datasets=full_research_required_datasets,
    )
    research_debt: Any = _build_research_debt(data_gaps)
    manual_retrieval_tasks: List[Dict[str, Any]] = []
    seen_tasks: set[Tuple[str, str]] = set()
    for gap in data_gaps:
        task: Any = _manual_task_for_gap(gap)
        if not task:
            continue
        key: Any = (str(task.get("dataset") or ""), str(task.get("objective") or ""))
        if key in seen_tasks:
            continue
        seen_tasks.add(key)
        manual_retrieval_tasks.append(task)

    data_acquisition: Any = {
        "policy": "assets/data_acquisition_policy.json",
        "status_by_dataset": {
            dataset: effective_statuses.get(dataset, DataStatus.NOT_REQUESTED.value)
            for dataset in full_research_required_datasets
        },
        "requested_dataset_statuses": dict(statuses),
        "attempt_ledger": attempt_ledger,
        "data_gaps": data_gaps,
        "research_debt": research_debt,
        "manual_retrieval_tasks": manual_retrieval_tasks,
        "attempt_ledger_path": str(destination / "attempt_ledger.json"),
        "data_gaps_path": str(destination / "data_gaps.json"),
        "research_debt_path": str(destination / "research_debt.json"),
        "manual_retrieval_tasks_path": str(destination / "manual_retrieval_tasks.json"),
        "attempt_count": len(attempt_ledger),
        "gap_count": len(data_gaps),
        "research_debt_count": len(research_debt),
        "manual_task_count": len(manual_retrieval_tasks),
        "full_research_ready": full_research_cap == RatingCap.S and not research_debt,
    }
    source_integrity: Any = _build_source_integrity_summary(result_items)
    ai_review: Any = _build_ai_review_guidance(result_items, data_quality, data_gaps, research_debt)
    manifest: Any = {
        "symbol": symbol.__dict__,
        "requested_datasets": requested_dataset_values,
        "full_research_required_datasets": full_research_required_datasets,
        "out_dir": str(destination),
        "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "data_acquisition": data_acquisition,
        "data_quality": data_quality,
        "source_integrity": source_integrity,
        "ai_review": ai_review,
        "results": result_items,
    }
    _write_json(destination / "attempt_ledger.json", attempt_ledger)
    _write_json(destination / "data_gaps.json", data_gaps)
    _write_json(destination / "research_debt.json", research_debt)
    _write_json(destination / "manual_retrieval_tasks.json", manual_retrieval_tasks)
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
    parser: Any = argparse.ArgumentParser(description="Serenity-Chan market data router and validator")
    sub: Any = parser.add_subparsers(dest="cmd", required=True)

    p_resolve: Any = sub.add_parser("resolve", help="Resolve ticker/stock code into market-aware symbol info")
    p_resolve.add_argument("symbol")

    p_plan: Any = sub.add_parser("plan", help="Build a market-aware data fetch plan")
    p_plan.add_argument("symbol")
    p_plan.add_argument("--horizon", default="12M")

    p_price: Any = sub.add_parser("validate-price", help="Validate OHLCV CSV")
    p_price.add_argument("csv_path")
    p_price.add_argument("--market", choices=[m.value for m in Market], default="OTHER")
    p_price.add_argument("--adjust", default="unknown")
    p_price.add_argument("--min-bars", type=int, default=250)

    p_fin: Any = sub.add_parser("validate-financial", help="Validate financial JSON")
    p_fin.add_argument("json_path")

    p_quotes: Any = sub.add_parser("compare-quotes", help="Compare multiple quotes")
    p_quotes.add_argument("prices", nargs="+")

    p_fetch: Any = sub.add_parser("fetch", help="Fetch real preflight data into an auditable local bundle")
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
            CanonicalDataset.VALUATION_INPUTS.value,
        ],
    )
    p_fetch.add_argument("--out-dir", help="output directory; defaults to /tmp/serenity-chan-data/<symbol>/<timestamp>")
    p_fetch.add_argument("--range", dest="chart_range", default="2y", help="chart range for price history")
    p_fetch.add_argument("--interval", default="1d", help="chart interval")
    p_fetch.add_argument("--min-bars", type=int, default=250)
    p_fetch.add_argument("--sec-user-agent", help="SEC-compliant User-Agent, e.g. 'Your Name your.email@example.com'")

    args: Any = parser.parse_args(argv)
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
