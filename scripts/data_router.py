#!/usr/bin/env python3
"""
Data Router for serenity-chan-stock-skill v3.

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
import re
import statistics
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


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
        financial = ["CNINFO filings", "SSE/SZSE/BSE filings", "Wind/Choice/CSMAR", "Tushare Pro", "Company IR"]
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
    raw = value.strip()
    token = raw.upper().replace(" ", "")
    warnings: List[str] = []

    # A-share explicit suffixes
    m = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ|SS)", token)
    if m:
        code, suffix = m.groups()
        if suffix == "SS":
            suffix = "SH"
            warnings.append("Input used Yahoo-style .SS; normalized to A-share .SH.")
        market = Market.CN_A
        exchange = suffix
        normalized = f"{code}.{suffix}"
        currency = "CNY"
        aliases: Dict[str, str] = {"tushare": normalized, "wind": normalized}
        if suffix == "SH":
            aliases["yfinance"] = f"{code}.SS"
        elif suffix == "SZ":
            aliases["yfinance"] = f"{code}.SZ"
        elif suffix == "BJ":
            aliases["yfinance"] = f"{code}.BJ"
        disclosure, price, financial = _source_pack(market)
        return SymbolInfo(raw, normalized, market, exchange, currency, aliases, disclosure, price, financial, warnings)

    # A-share six-digit no suffix inference
    if re.fullmatch(r"\d{6}", token):
        code = token
        if code.startswith(("600", "601", "603", "605", "688")):
            suffix = "SH"
        elif code.startswith(("000", "001", "002", "003", "300", "301")):
            suffix = "SZ"
        elif code.startswith(("43", "83", "87", "92")):
            suffix = "BJ"
        else:
            suffix = "UNKNOWN"
            warnings.append("Six-digit code does not match common A-share prefixes; confirm market manually.")
        if suffix != "UNKNOWN":
            normalized = f"{code}.{suffix}"
            market = Market.CN_A
            aliases = {"tushare": normalized, "wind": normalized}
            if suffix == "SH":
                aliases["yfinance"] = f"{code}.SS"
            elif suffix == "SZ":
                aliases["yfinance"] = f"{code}.SZ"
            disclosure, price, financial = _source_pack(market)
            warnings.append(f"No suffix provided; inferred {normalized}. Confirm if ambiguity matters.")
            return SymbolInfo(raw, normalized, market, suffix, "CNY", aliases, disclosure, price, financial, warnings)

    # HK explicit or numeric with HK suffix
    m = re.fullmatch(r"(\d{1,5})\.HK", token)
    if m:
        code = m.group(1).zfill(4)
        normalized = f"{code}.HK"
        market = Market.HK
        disclosure, price, financial = _source_pack(market)
        return SymbolInfo(raw, normalized, market, "HKEX", "HKD", {"yfinance": normalized}, disclosure, price, financial, warnings)

    # US ticker, allow class dots/dashes
    if re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", token) or re.fullmatch(r"[A-Z]{1,5}-[A-Z]", token):
        market = Market.US
        normalized = token.replace("-", ".")
        disclosure, price, financial = _source_pack(market)
        return SymbolInfo(raw, normalized, market, None, "USD", {"sec_ticker": normalized, "yfinance": normalized}, disclosure, price, financial, warnings)

    disclosure, price, financial = _source_pack(Market.OTHER)
    return SymbolInfo(raw, raw, Market.OTHER, None, "UNKNOWN", {}, disclosure, price, financial, ["Could not confidently resolve market. Ask user or provide suffix."])


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
    if adjust.lower() not in {"qfq", "forward", "hfq", "backward", "none", "unadjusted", "unknown"}:
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
    for idx, row in enumerate(rows):
        for k in required_any:
            if k not in row:
                report.warnings.append(f"Period index {idx} missing {k}.")
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
    if bad:
        report.status = DataStatus.PARTIAL
        report.rating_cap = RatingCap.B
    return report


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

    p_price = sub.add_parser("validate-price", help="Validate OHLCV CSV")
    p_price.add_argument("csv_path")
    p_price.add_argument("--market", choices=[m.value for m in Market], default="OTHER")
    p_price.add_argument("--adjust", default="unknown")
    p_price.add_argument("--min-bars", type=int, default=250)

    p_fin = sub.add_parser("validate-financial", help="Validate financial JSON")
    p_fin.add_argument("json_path")

    p_quotes = sub.add_parser("compare-quotes", help="Compare multiple quotes")
    p_quotes.add_argument("prices", nargs="+")

    args = parser.parse_args(argv)
    if args.cmd == "resolve":
        emit(resolve_symbol(args.symbol))
    elif args.cmd == "validate-price":
        emit(validate_price_history(Path(args.csv_path), Market(args.market), args.adjust, args.min_bars))
    elif args.cmd == "validate-financial":
        emit(validate_financials(Path(args.json_path)))
    elif args.cmd == "compare-quotes":
        emit(compare_quotes([_parse_float(x) or 0.0 for x in args.prices]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
