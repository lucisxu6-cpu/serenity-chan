#!/usr/bin/env python3
"""Fetch FX rates for cross-currency valuation normalization."""

from __future__ import annotations

import datetime as dt
import json
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional


KNOWN_CURRENCY_CODES: set[str] = {
    "AUD",
    "CAD",
    "CHF",
    "CNY",
    "EUR",
    "GBP",
    "HKD",
    "INR",
    "JPY",
    "KRW",
    "RUB",
    "SGD",
    "TWD",
    "USD",
}
CURRENCY_ALIASES: dict[str, str] = {
    "RMB": "CNY",
    "CNH": "CNY",
    "YUAN": "CNY",
    "人民币": "CNY",
    "元": "CNY",
    "HK$": "HKD",
    "港元": "HKD",
    "港币": "HKD",
    "US$": "USD",
    "美元": "USD",
}
AMOUNT_UNIT_MARKERS: tuple[str, ...] = (
    "000",
    "BILLION",
    "BILLIONS",
    "HUNDREDMILLION",
    "MILLION",
    "MILLIONS",
    "THOUSAND",
    "THOUSANDS",
    "百万元",
    "百万",
    "十亿元",
    "亿元",
    "千元",
    "万元",
)


@dataclass(frozen=True)
class FxRate:
    base_currency: str
    quote_currency: str
    rate: float
    as_of_date: str
    source_name: str
    source_level: str
    source_url: str


def normalize_currency_code(value: Any) -> str:
    text: str = str(value or "").strip().upper()
    compact: str = text.replace(" ", "").replace("_", "").replace("-", "")
    aliased: str = CURRENCY_ALIASES.get(text) or CURRENCY_ALIASES.get(compact) or ""
    if aliased:
        return aliased
    return text if text in KNOWN_CURRENCY_CODES else ""


def currency_code_from_unit(value: Any) -> str:
    text: str = str(value or "").strip()
    if not text:
        return ""
    code: str = normalize_currency_code(text)
    if code:
        return code

    normalized: str = (
        text.upper()
        .replace("（", " ")
        .replace("）", " ")
        .replace("(", " ")
        .replace(")", " ")
        .replace("[", " ")
        .replace("]", " ")
        .replace("/", " ")
        .replace(",", " ")
        .replace("，", " ")
        .replace("_", " ")
        .replace("-", " ")
    )
    tokens: list[str] = [token.strip(" .:;") for token in normalized.split() if token.strip(" .:;")]
    for token in tokens:
        token_code: str = normalize_currency_code(token)
        if token_code:
            return token_code

    compact: str = normalized.replace(" ", "")
    for alias, alias_code in sorted(CURRENCY_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if alias and alias in compact:
            return alias_code
    for currency_code in KNOWN_CURRENCY_CODES:
        suffix: str = compact[len(currency_code):] if compact.startswith(currency_code) else ""
        if suffix and any(marker in suffix for marker in AMOUNT_UNIT_MARKERS):
            return currency_code
    return ""


class YahooFxProvider:
    name: str = "Yahoo_FX_L2"
    level: str = "L2_FREE_API_OR_OPEN_SOURCE"

    def __init__(self, *, timeout_seconds: float = 6.0) -> None:
        self.timeout_seconds: float = timeout_seconds

    def fetch_rate(self, base_currency: str, quote_currency: str) -> FxRate:
        base: str = normalize_currency_code(base_currency)
        quote: str = normalize_currency_code(quote_currency)
        if not base or not quote:
            raise ValueError("base and quote currency are required")
        if base == quote:
            return FxRate(
                base_currency=base,
                quote_currency=quote,
                rate=1.0,
                as_of_date=dt.date.today().isoformat(),
                source_name=self.name,
                source_level=self.level,
                source_url="same-currency",
            )

        direct: Optional[FxRate] = self._try_pair(base, quote, inverse=False)
        if direct:
            return direct
        inverse: Optional[FxRate] = self._try_pair(quote, base, inverse=True)
        if inverse:
            return inverse
        raise RuntimeError(f"FX rate unavailable for {base}/{quote}")

    def _try_pair(self, base: str, quote: str, *, inverse: bool) -> Optional[FxRate]:
        pair: str = f"{base}{quote}=X"
        encoded_pair: str = urllib.parse.quote(pair, safe="=X")
        url: str = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_pair}?range=5d&interval=1d"
        try:
            request: urllib.request.Request = urllib.request.Request(
                url,
                headers={"User-Agent": "serenity-chan-stock-skill/1.0"},
            )
            try:
                import certifi  # type: ignore

                context: ssl.SSLContext = ssl.create_default_context(cafile=certifi.where())
            except Exception:
                context = ssl.create_default_context()
            with urllib.request.urlopen(request, timeout=self.timeout_seconds, context=context) as response:
                payload: Any = json.loads(response.read().decode("utf-8"))
        except Exception:
            return None

        try:
            result: Any = payload["chart"]["result"][0]
            meta: dict[str, Any] = result.get("meta", {})
            rate: Optional[float] = _as_float(meta.get("regularMarketPrice") or meta.get("previousClose"))
            timestamp: Any = meta.get("regularMarketTime")
            if rate is None:
                quote_rows: Any = result.get("indicators", {}).get("quote", [{}])[0]
                closes: list[Any] = quote_rows.get("close", []) if isinstance(quote_rows, dict) else []
                for close in reversed(closes):
                    rate = _as_float(close)
                    if rate is not None:
                        break
            if rate is None or rate <= 0:
                return None
            if inverse:
                rate = 1.0 / rate
                output_base: str = quote
                output_quote: str = base
            else:
                output_base = base
                output_quote = quote
            as_of: str = (
                dt.datetime.fromtimestamp(float(timestamp), tz=dt.timezone.utc).date().isoformat()
                if timestamp
                else dt.date.today().isoformat()
            )
            return FxRate(
                base_currency=output_base,
                quote_currency=output_quote,
                rate=float(rate),
                as_of_date=as_of,
                source_name=self.name,
                source_level=self.level,
                source_url=url,
            )
        except Exception:
            return None


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
