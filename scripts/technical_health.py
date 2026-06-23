#!/usr/bin/env python3
"""Compute deterministic technical-health fields from adjusted daily prices."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence


def _parse_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(str(value).replace(",", ""))
    except Exception:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _parse_date(value: Any) -> Optional[dt.date]:
    if value is None:
        return None
    text = str(value).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    return None if value is None else round(value, digits)


def _pct(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator in (None, 0):
        return None
    return (numerator / denominator - 1.0) * 100.0


def _sma(values: Sequence[float], window: int) -> Optional[float]:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def _atr(rows: Sequence[Mapping[str, Any]], window: int = 20) -> Optional[float]:
    if len(rows) < 2:
        return None
    true_ranges: list[float] = []
    previous_close: Optional[float] = None
    for row in rows:
        high = _parse_float(row.get("high"))
        low = _parse_float(row.get("low"))
        close = _parse_float(row.get("close") or row.get("adj_close"))
        if high is None or low is None or close is None:
            continue
        if previous_close is None:
            true_range = high - low
        else:
            true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
        true_ranges.append(true_range)
        previous_close = close
    if not true_ranges:
        return None
    sample = true_ranges[-window:]
    return sum(sample) / len(sample)


def read_price_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _clean_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        date_value = _parse_date(row.get("date"))
        close = _parse_float(row.get("close") or row.get("adj_close"))
        high = _parse_float(row.get("high") or close)
        low = _parse_float(row.get("low") or close)
        if date_value is None or close is None or high is None or low is None:
            continue
        cleaned.append({
            "date": date_value.isoformat(),
            "close": close,
            "high": high,
            "low": low,
            "volume": _parse_float(row.get("volume")),
        })
    cleaned.sort(key=lambda item: item["date"])
    return cleaned


def analyze_price_rows(rows: Iterable[Mapping[str, Any]], quote: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    cleaned = _clean_rows(rows)
    closes = [float(row["close"]) for row in cleaned]
    latest_close = closes[-1] if closes else _parse_float((quote or {}).get("regular_market_price"))
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, 200)
    recent = cleaned[-252:] if cleaned else []
    high_52w = max((_parse_float(row.get("high")) or 0.0) for row in recent) if recent else _parse_float((quote or {}).get("fifty_two_week_high"))
    low_52w_values = [(_parse_float(row.get("low")) or 0.0) for row in recent if (_parse_float(row.get("low")) or 0.0) > 0]
    low_52w = min(low_52w_values) if low_52w_values else _parse_float((quote or {}).get("fifty_two_week_low"))
    atr20 = _atr(cleaned, 20)

    metrics = {
        "bars": len(cleaned),
        "sma20": _round(sma20),
        "sma50": _round(sma50),
        "sma200": _round(sma200),
        "atr20": _round(atr20),
        "distance_to_sma20_pct": _round(_pct(latest_close, sma20)),
        "distance_to_sma50_pct": _round(_pct(latest_close, sma50)),
        "distance_to_sma200_pct": _round(_pct(latest_close, sma200)),
        "distance_to_52w_high_pct": _round(_pct(latest_close, high_52w)),
        "distance_to_52w_low_pct": _round(_pct(latest_close, low_52w)),
    }

    status = "OK" if len(cleaned) >= 200 else "PARTIAL" if len(cleaned) >= 60 else "DATA_GATED"
    if latest_close is None or status == "DATA_GATED":
        return {
            "status": "DATA_GATED",
            "trend_state": "DATA_GATED",
            "chan_action": "DATA_REQUIRED",
            "buy_point_claim_allowed": False,
            "latest_close": _round(latest_close),
            "metrics": metrics,
            "decision_note": "Adjusted daily history is insufficient for a technical timing claim.",
            "readiness_score": 25.0,
        }

    distance20 = metrics["distance_to_sma20_pct"]
    distance_high = metrics["distance_to_52w_high_pct"]
    if sma20 and sma50 and sma200 and latest_close > sma20 > sma50 > sma200:
        if (distance20 is not None and distance20 >= 8.0) or (distance_high is not None and distance_high >= -5.0):
            trend_state = "STRONG_EXTENDED_WATCH"
            chan_action = "WAIT_FOR_SECOND_BUY"
            note = "Strong trend is extended; wait for a second-buy pullback or a third-buy retest."
            readiness = 62.0
        else:
            trend_state = "TREND_PULLBACK_WATCH"
            chan_action = "WAIT_FOR_STRUCTURE_CONFIRMATION"
            note = "Trend remains constructive, yet DMA proximity alone does not confirm a Chan buy point."
            readiness = 66.0
    elif sma20 and sma50 and latest_close >= sma50 and (distance20 is not None and abs(distance20) <= 4.0):
        trend_state = "CONSTRUCTIVE_PULLBACK_WATCH"
        chan_action = "WAIT_FOR_STRUCTURE_CONFIRMATION"
        note = "Price is near the short moving average; require structure confirmation before calling a buy point."
        readiness = 58.0
    elif sma200 and latest_close < sma200:
        trend_state = "WEAK_OR_DOWNTREND"
        chan_action = "NO_BUY_POINT"
        note = "Price is below the long moving average; do not use a rebound as a long-term buy point."
        readiness = 38.0
    else:
        trend_state = "BASE_BUILDING_WATCH"
        chan_action = "WAIT_FOR_STRUCTURE_CONFIRMATION"
        note = "Structure is watchable but needs multi-level confirmation."
        readiness = 50.0

    return {
        "status": status,
        "trend_state": trend_state,
        "chan_action": chan_action,
        "buy_point_claim_allowed": False,
        "latest_close": _round(latest_close),
        "metrics": metrics,
        "decision_note": note,
        "readiness_score": readiness,
    }


def analyze_price_csv(path: Path, quote_path: Optional[Path] = None) -> dict[str, Any]:
    quote: Optional[Mapping[str, Any]] = None
    if quote_path and quote_path.exists():
        loaded = json.loads(quote_path.read_text(encoding="utf-8"))
        if isinstance(loaded, Mapping):
            quote = loaded
    return analyze_price_rows(read_price_csv(path), quote=quote)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Compute Serenity + Chan technical-health fields")
    parser.add_argument("price_history_csv")
    parser.add_argument("--quote", help="Optional current quote JSON")
    args = parser.parse_args(argv)
    try:
        result = analyze_price_csv(
            Path(args.price_history_csv),
            Path(args.quote) if args.quote else None,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
