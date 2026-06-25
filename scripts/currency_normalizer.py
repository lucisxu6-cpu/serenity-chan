#!/usr/bin/env python3
"""Normalize valuation inputs to the financial reporting currency."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from fx_provider import FxRate, YahooFxProvider, currency_code_from_unit, normalize_currency_code
except ModuleNotFoundError:  # pragma: no cover
    from scripts.fx_provider import FxRate, YahooFxProvider, currency_code_from_unit, normalize_currency_code


NORMALIZATION_STATUSES: set[str] = {"OK", "NOT_REQUIRED", "DATA_GATED", "FAILED"}


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number: float = float(str(value).replace(",", ""))
    except Exception:
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def build_currency_normalization_row(
    *,
    symbol: str,
    valuation_currency: Any,
    financial_currency: Any,
    total_market_cap: Any,
    as_of_date: Any = "",
    allow_network: bool = True,
    provider: Optional[YahooFxProvider] = None,
) -> dict[str, Any]:
    source_currency: str = normalize_currency_code(valuation_currency)
    target_currency: str = normalize_currency_code(financial_currency)
    market_cap: Optional[float] = _as_float(total_market_cap)
    date_text: str = str(as_of_date or dt.date.today().isoformat())

    base_row: dict[str, Any] = {
        "symbol": symbol,
        "source_currency": source_currency,
        "target_currency": target_currency,
        "original_total_market_cap": market_cap,
        "normalized_total_market_cap": market_cap if source_currency and source_currency == target_currency else None,
        "fx_rate": 1.0 if source_currency and source_currency == target_currency else None,
        "fx_rate_direction": f"{source_currency}->{target_currency}" if source_currency and target_currency else "",
        "fx_as_of_date": date_text,
        "fx_source": "same-currency" if source_currency and source_currency == target_currency else "",
        "fx_source_level": "NOT_APPLICABLE" if source_currency and source_currency == target_currency else "",
        "normalization_stage": "same_currency" if source_currency and source_currency == target_currency else "preflight_fx",
        "normalization_status": "NOT_REQUIRED" if source_currency and source_currency == target_currency else "FAILED",
        "reason_code": "NONE" if source_currency and source_currency == target_currency else "CURRENCY_MISMATCH",
        "warnings": [],
        "errors": [],
    }

    if not source_currency or not target_currency:
        base_row.update({
            "normalization_status": "DATA_GATED",
            "reason_code": "CURRENCY_MISSING",
            "errors": ["valuation currency or financial reporting currency is missing"],
        })
        return base_row
    if market_cap is None:
        base_row.update({
            "normalization_status": "DATA_GATED",
            "reason_code": "MARKET_CAP_MISSING",
            "errors": ["total_market_cap is missing, so FX normalization cannot produce a normalized market cap"],
        })
        return base_row
    if source_currency == target_currency:
        return base_row
    if not allow_network:
        base_row.update({
            "normalization_status": "FAILED",
            "reason_code": "FX_RATE_UNAVAILABLE",
            "errors": ["FX provider was not allowed to fetch a live rate"],
        })
        return base_row

    fx_provider: YahooFxProvider = provider or YahooFxProvider()
    try:
        rate: FxRate = fx_provider.fetch_rate(source_currency, target_currency)
    except Exception as exc:
        base_row.update({
            "normalization_status": "FAILED",
            "reason_code": "FX_RATE_UNAVAILABLE",
            "errors": [f"{type(exc).__name__}: {exc}"],
        })
        return base_row

    converted: float = market_cap * rate.rate
    base_row.update({
        "normalized_total_market_cap": round(converted, 2),
        "fx_rate": round(rate.rate, 8),
        "fx_rate_direction": f"{rate.base_currency}->{rate.quote_currency}",
        "fx_as_of_date": rate.as_of_date,
        "fx_source": rate.source_name,
        "fx_source_level": rate.source_level,
        "normalization_status": "OK",
        "reason_code": "NONE",
        "errors": [],
    })
    return base_row


def _financial_currency_from_payload(financial_payload: Mapping[str, Any]) -> str:
    for key in ["currency", "financial_currency", "reporting_currency"]:
        code: str = normalize_currency_code(financial_payload.get(key)) or currency_code_from_unit(financial_payload.get(key))
        if code:
            return code
    for key in ["unit", "revenue_unit", "net_income_unit"]:
        code = currency_code_from_unit(financial_payload.get(key))
        if code:
            return code
    return ""


def normalize_valuation_payload(
    *,
    symbol: str,
    valuation_payload: Mapping[str, Any],
    financial_payload: Mapping[str, Any],
    allow_network: bool = True,
) -> dict[str, Any]:
    return build_currency_normalization_row(
        symbol=symbol,
        valuation_currency=valuation_payload.get("currency"),
        financial_currency=_financial_currency_from_payload(financial_payload),
        total_market_cap=valuation_payload.get("total_market_cap"),
        as_of_date=valuation_payload.get("as_of_date"),
        allow_network=allow_network,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Normalize valuation market cap to reporting currency")
    parser.add_argument("valuation_json")
    parser.add_argument("financial_json")
    parser.add_argument("--symbol", default="")
    parser.add_argument("--no-network", action="store_true")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        valuation: Any = json.loads(Path(args.valuation_json).read_text(encoding="utf-8"))
        financial: Any = json.loads(Path(args.financial_json).read_text(encoding="utf-8"))
        if not isinstance(valuation, Mapping) or not isinstance(financial, Mapping):
            raise ValueError("valuation_json and financial_json must be JSON objects")
        row: dict[str, Any] = normalize_valuation_payload(
            symbol=args.symbol or str(valuation.get("symbol") or ""),
            valuation_payload=valuation,
            financial_payload=financial,
            allow_network=not args.no_network,
        )
        print(json.dumps(row, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
