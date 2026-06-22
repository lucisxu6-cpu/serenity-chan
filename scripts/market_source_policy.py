#!/usr/bin/env python3
"""Shared market/source isolation rules for Serenity + Chan validators."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Sequence


MARKETS = {"CN_A", "US", "HK"}

A_SHARE_CONTEXT_RE = re.compile(r"(A\s*股|A股|CN_A|\b\d{6}\.(?:SH|SZ|BJ)\b)", re.I)
HK_CONTEXT_RE = re.compile(r"(港股|HK market|Hong Kong|\b\d{4,5}\.HK\b)", re.I)
US_MARKET_WORD_RE = re.compile(r"(美股|US ticker|US market|NASDAQ|NYSE|NYSEARCA|AMEX|ADR)", re.I)
US_TICKER_RE = re.compile(r"\b[A-Z]{1,5}(?:[.-][A-Z])?\b")

A_SHARE_SOURCE_RE = re.compile(
    r"(巨潮|CNINFO|上交所|深交所|北交所|东方财富|同花顺|A\s*股\s*F10|F10|\b(?:SSE|SZSE|BSE)\b)",
    re.I,
)
US_SOURCE_RE = re.compile(
    r"\bSEC\s+EDGAR\b|\bSEC\s+Companyfacts\b|\b10-K\b|\b10-Q\b|\b8-K\b|\bS-1\b|\bS-3\b|\b20-F\b|\b6-K\b|\bForm\s+4\b",
    re.I,
)
HK_SOURCE_RE = re.compile(r"(HKEXnews|HKEX|港交所|联交所|港股公告|香港交易所)", re.I)

FORBIDDEN_CONTEXT_RE = re.compile(
    r"(forbidden|禁止|不得|不要|不可|不能|do not use|not use|不得使用|错误源)",
    re.I,
)

COMMON_NON_TICKERS = {
    "A", "AI", "API", "ADR", "ATR", "B", "BSE", "C", "CEO", "CFO", "CN", "CN_A",
    "CNY", "CPU", "D", "DMA", "EDGAR", "EPS", "ETF", "FOMO", "GAAP", "GPU", "HK",
    "HKD", "HPC", "IR", "IPO", "KOL", "LLM", "LTM", "NPU", "NYSE", "OCF", "OK",
    "PEG", "QOQ", "RMB", "SEC", "SSE", "SZSE", "TAM", "TTM", "US", "USD", "YOY",
}


@dataclass(frozen=True)
class SourceMismatch:
    source_market: str
    expected_market: str
    text: str


def is_forbidden_context(text: str) -> bool:
    return bool(FORBIDDEN_CONTEXT_RE.search(text))


def _looks_like_us_ticker(token: str) -> bool:
    normalized = token.upper().replace(".", "-")
    if normalized in COMMON_NON_TICKERS:
        return False
    if len(normalized.replace("-", "")) < 2:
        return False
    if normalized.startswith(("L0", "L1", "L2", "L3", "L4", "H0", "H1", "H2", "H3", "H4", "H5")):
        return False
    return bool(re.fullmatch(r"[A-Z]{1,5}(?:-[A-Z])?", normalized))


def context_markets(text: str) -> set[str]:
    markets: set[str] = set()
    if A_SHARE_CONTEXT_RE.search(text):
        markets.add("CN_A")
    if HK_CONTEXT_RE.search(text):
        markets.add("HK")
    if US_MARKET_WORD_RE.search(text):
        markets.add("US")
    elif any(_looks_like_us_ticker(token) for token in US_TICKER_RE.findall(text)):
        markets.add("US")
    return markets


def source_markets(text: str) -> set[str]:
    markets: set[str] = set()
    if A_SHARE_SOURCE_RE.search(text):
        markets.add("CN_A")
    if US_SOURCE_RE.search(text):
        markets.add("US")
    if HK_SOURCE_RE.search(text):
        markets.add("HK")
    return markets


def wrong_source_lines(text: str) -> list[str]:
    """Return Markdown lines whose source market conflicts with local/document context."""
    bad: list[str] = []
    document_markets = context_markets(text)
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or is_forbidden_context(line):
            continue
        line_sources = source_markets(line)
        if not line_sources:
            continue
        line_contexts = context_markets(line)
        for source_market in line_sources:
            if line_contexts:
                if source_market not in line_contexts and len(line_contexts) == 1:
                    bad.append(line)
                    break
                continue
            if len(document_markets) == 1 and source_market not in document_markets:
                bad.append(line)
                break
    return bad


def mismatched_sources_for_market(market: str, source_texts: Iterable[str]) -> list[SourceMismatch]:
    """Validate structured source fields against a resolved single market."""
    if market not in MARKETS:
        return []
    mismatches: list[SourceMismatch] = []
    for source in source_texts:
        text = str(source).strip()
        if not text or is_forbidden_context(text):
            continue
        for source_market in sorted(source_markets(text)):
            if source_market != market:
                mismatches.append(SourceMismatch(source_market=source_market, expected_market=market, text=text))
    return mismatches


def flatten_sources(values: Sequence[object]) -> list[str]:
    return [str(value) for value in values if str(value).strip()]
