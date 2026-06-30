#!/usr/bin/env python3
"""
Serenity + Chan Data-First Equity Skill - Data Layer

This module is a robust data-access contract for Codex / Claude Code / Python agents.
It deliberately separates:

1. symbol resolution
2. market-specific source routing
3. provider adapters
4. validation and cross-checks
5. rating caps when data is incomplete

It does not include vendor credentials and does not pretend that all data is available.
If a critical dataset fails, the agent must cap the conclusion instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import gzip
import html
import http.client
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple
import datetime as dt
import json
import math
import os
import re
import subprocess
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    import pandas as pd  # type: ignore
except Exception:  # pragma: no cover
    pd: Any = None  # type: ignore

try:
    from data_contracts import DataStatus, Dataset, Market, RatingCap, SourceLevel
except ModuleNotFoundError:  # pragma: no cover - supports python -m scripts.data_layer
    from scripts.data_contracts import DataStatus, Dataset, Market, RatingCap, SourceLevel


OFFICIAL_REPORT_DOWNLOAD_LIMIT_DEFAULT: int = 4
HK_VALUATION_REPORT_DOWNLOAD_LIMIT_DEFAULT: int = OFFICIAL_REPORT_DOWNLOAD_LIMIT_DEFAULT
CNINFO_REPORT_SCAN_PAGE_LIMIT_DEFAULT: int = 16


@dataclass(frozen=True)
class SymbolInfo:
    input_value: str
    symbol: str
    market: Market
    exchange: str
    name: Optional[str] = None
    currency: str = ""
    cik: Optional[str] = None
    isin: Optional[str] = None


@dataclass
class DataResult:
    ok: bool
    dataset: Dataset | str
    symbol: str
    source_name: str
    source_level: SourceLevel
    retrieved_at: str
    as_of_date: Optional[str] = None
    data: Any = None
    raw_path: Optional[str] = None
    raw_hash: Optional[str] = None
    unit: Optional[str] = None
    currency: Optional[str] = None
    adjust: str = "not_applicable"  # none / qfq / hfq / adjusted / not_applicable
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @classmethod
    def failed(
        cls,
        dataset: Dataset | str,
        symbol: str,
        source_name: str,
        source_level: SourceLevel,
        error: str,
    ) -> "DataResult":
        return cls(
            ok=False,
            dataset=dataset,
            symbol=symbol,
            source_name=source_name,
            source_level=source_level,
            retrieved_at=utc_now(),
            errors=[error],
        )


@dataclass
class ValidationReport:
    dataset: Dataset | str
    status: DataStatus
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DataQualityScore:
    price: DataStatus
    financials: DataStatus
    filings: DataStatus
    technical: DataStatus
    cross_validation: DataStatus
    max_rating_allowed: RatingCap
    missing_critical_fields: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class DataProvider(Protocol):
    name: str
    level: SourceLevel
    markets: Sequence[Market]
    datasets: Sequence[Dataset]

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        ...


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

RATING_ORDER: Any = [RatingCap.S, RatingCap.A, RatingCap.B, RatingCap.C, RatingCap.D, RatingCap.OBSERVE_ONLY]


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def file_sha256(path: str | Path) -> str:
    p: Any = Path(path)
    h: Any = sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def save_raw_json(obj: Any, raw_dir: str | Path, name: str) -> Tuple[str, str]:
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    path: Any = raw_dir / name
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return str(path), file_sha256(path)


def save_raw_bytes(data: bytes, raw_dir: str | Path, name: str) -> Tuple[str, str]:
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    path: Any = raw_dir / name
    path.write_bytes(data)
    return str(path), file_sha256(path)


def save_raw_text(text: str, raw_dir: str | Path, name: str) -> Tuple[str, str]:
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    path: Any = raw_dir / name
    path.write_text(text, encoding="utf-8")
    return str(path), file_sha256(path)


def _safe_artifact_name(value: str, *, max_length: int = 120) -> str:
    cleaned: Any = re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("._-")
    return (cleaned or "artifact")[:max_length]


def https_json(
    url: str,
    *,
    user_agent: str,
    timeout: int = 30,
    headers: Optional[Mapping[str, str]] = None,
    retries: int = 2,
) -> Any:
    """Fetch HTTPS JSON with certificate validation.

    Use certifi when available because local Python installs, especially
    Homebrew Python, may not have OpenSSL CA paths configured. Do not fall back
    to an unverified SSL context; failed TLS means failed data.
    """
    merged_headers: Any = {
        "User-Agent": user_agent,
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    }
    if headers:
        merged_headers.update(headers)

    try:
        import certifi  # type: ignore

        context: Any = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        context = ssl.create_default_context()

    last_error: Optional[BaseException] = None
    for attempt in range(retries + 1):
        request: Any = urllib.request.Request(url, headers=merged_headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                raw: Any = response.read()
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code != 429 or attempt >= retries:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionResetError, http.client.IncompleteRead, http.client.RemoteDisconnected) as exc:
            last_error = exc
            if attempt >= retries:
                raise
        time.sleep(0.75 * (attempt + 1))
    if last_error:
        raise last_error
    raise RuntimeError("HTTPS JSON fetch failed without a captured exception")


def https_text(
    url: str,
    *,
    user_agent: str,
    timeout: int = 30,
    headers: Optional[Mapping[str, str]] = None,
    retries: int = 2,
) -> str:
    """Fetch HTTPS text with the same TLS and retry policy as JSON fetches."""
    merged_headers: Any = {
        "User-Agent": user_agent,
        "Accept": "text/plain,*/*",
        "Accept-Encoding": "gzip",
    }
    if headers:
        merged_headers.update(headers)

    try:
        import certifi  # type: ignore

        context: Any = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        context = ssl.create_default_context()

    last_error: Optional[BaseException] = None
    for attempt in range(retries + 1):
        request: Any = urllib.request.Request(url, headers=merged_headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                raw: Any = response.read()
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8-sig")
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code != 429 or attempt >= retries:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionResetError, http.client.IncompleteRead, http.client.RemoteDisconnected) as exc:
            last_error = exc
            if attempt >= retries:
                raise
        time.sleep(0.75 * (attempt + 1))
    if last_error:
        raise last_error
    raise RuntimeError("HTTPS text fetch failed without a captured exception")


def https_bytes(
    url: str,
    *,
    user_agent: str,
    timeout: int = 30,
    headers: Optional[Mapping[str, str]] = None,
    retries: int = 2,
    max_bytes: int = 80 * 1024 * 1024,
) -> bytes:
    """Fetch binary source artifacts with TLS verification and a size guard."""
    merged_headers: Any = {
        "User-Agent": user_agent,
        "Accept": "application/pdf,*/*",
        "Accept-Encoding": "gzip",
    }
    if headers:
        merged_headers.update(headers)

    try:
        import certifi  # type: ignore

        context: Any = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        context = ssl.create_default_context()

    last_error: Optional[BaseException] = None
    for attempt in range(retries + 1):
        request: Any = urllib.request.Request(url, headers=merged_headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                content_length: Any = response.headers.get("Content-Length")
                if content_length and int(content_length) > max_bytes:
                    raise RuntimeError(f"artifact is too large: {content_length} bytes > {max_bytes}")
                raw: Any = response.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    raise RuntimeError(f"artifact exceeds max_bytes={max_bytes}")
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                return raw
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code != 429 or attempt >= retries:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionResetError, http.client.IncompleteRead, http.client.RemoteDisconnected) as exc:
            last_error = exc
            if attempt >= retries:
                raise
        time.sleep(0.75 * (attempt + 1))
    if last_error:
        raise last_error
    raise RuntimeError("HTTPS binary fetch failed without a captured exception")


def form_json(
    url: str,
    form: Mapping[str, str],
    *,
    user_agent: str,
    timeout: int = 30,
    headers: Optional[Mapping[str, str]] = None,
    retries: int = 2,
) -> Any:
    """POST form data and parse JSON without disabling TLS verification."""
    merged_headers: Any = {
        "User-Agent": user_agent,
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if headers:
        merged_headers.update(headers)

    body: Any = urllib.parse.urlencode(form).encode("utf-8")
    context: Any = None
    if url.lower().startswith("https://"):
        try:
            import certifi  # type: ignore

            context = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            context = ssl.create_default_context()

    last_error: Optional[BaseException] = None
    for attempt in range(retries + 1):
        request: Any = urllib.request.Request(url, data=body, headers=merged_headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                raw: Any = response.read()
                if response.headers.get("Content-Encoding", "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8-sig"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code != 429 or attempt >= retries:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionResetError, http.client.IncompleteRead, http.client.RemoteDisconnected) as exc:
            last_error = exc
            if attempt >= retries:
                raise
        time.sleep(0.75 * (attempt + 1))
    if last_error:
        raise last_error
    raise RuntimeError("Form JSON fetch failed without a captured exception")


def cap_rating(current: RatingCap, cap: RatingCap) -> RatingCap:
    return RATING_ORDER[max(RATING_ORDER.index(current), RATING_ORDER.index(cap))]


# ---------------------------------------------------------------------------
# Symbol resolution and market routing
# ---------------------------------------------------------------------------

CN_A_SH_PREFIX: Any = ("600", "601", "603", "605", "688", "689")
CN_A_SZ_PREFIX: Any = ("000", "001", "002", "003", "300", "301")
CN_A_BJ_PREFIX: Any = ("430", "830", "831", "832", "833", "834", "835", "836", "837", "838", "839", "870", "871", "872", "873", "920")


def resolve_symbol(input_value: str, *, master_table: Optional[Mapping[str, Mapping[str, Any]]] = None) -> SymbolInfo:
    """Resolve a ticker-like input into a market-aware SymbolInfo.

    This resolver is conservative. It handles common forms and allows an optional
    master table to override/confirm exchange/name/currency. For production,
    use a licensed or official security master and keep the result traceable.
    """
    raw: Any = input_value.strip()
    token: Any = raw.upper().replace(" ", "")

    if master_table and token in master_table:
        row: Any = master_table[token]
        return SymbolInfo(
            input_value=raw,
            symbol=str(row.get("symbol", token)),
            market=Market(str(row.get("market", "UNKNOWN"))),
            exchange=str(row.get("exchange", "")),
            name=row.get("name"),
            currency=str(row.get("currency", "")),
            cik=row.get("cik"),
            isin=row.get("isin"),
        )

    # Explicit A-share suffix. Accept Yahoo-style .SS, but normalize to .SH.
    m: Any = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ|SS)", token)
    if m:
        code: Any
        exch: Any
        code, exch = m.groups()
        if exch == "SS":
            exch = "SH"
        return SymbolInfo(raw, f"{code}.{exch}", Market.CN_A, exch, currency="CNY")

    # Prefix forms SH688019 / SZ300750 / BJ920593
    m = re.fullmatch(r"(SH|SZ|BJ)(\d{6})", token)
    if m:
        exch, code = m.groups()
        return SymbolInfo(raw, f"{code}.{exch}", Market.CN_A, exch, currency="CNY")

    # Bare six-digit A-share-like code
    if re.fullmatch(r"\d{6}", token):
        if token.startswith(CN_A_SH_PREFIX):
            return SymbolInfo(raw, f"{token}.SH", Market.CN_A, "SH", currency="CNY")
        if token.startswith(CN_A_SZ_PREFIX):
            return SymbolInfo(raw, f"{token}.SZ", Market.CN_A, "SZ", currency="CNY")
        if token.startswith(CN_A_BJ_PREFIX):
            return SymbolInfo(raw, f"{token}.BJ", Market.CN_A, "BJ", currency="CNY")
        return SymbolInfo(raw, token, Market.UNKNOWN, "", currency="")

    # Hong Kong
    m = re.fullmatch(r"0*(\d{1,5})\.HK", token)
    if m:
        code = m.group(1).zfill(4)
        return SymbolInfo(raw, f"{code}.HK", Market.HK, "HKEX", currency="HKD")

    # US common ticker, including BRK.B/BRK-B normalization kept as supplied style
    if re.fullmatch(r"[A-Z]{1,5}([.-][A-Z])?", token):
        return SymbolInfo(raw, token.replace(".", "-"), Market.US, "US", currency="USD")

    return SymbolInfo(raw, token, Market.UNKNOWN, "", currency="")


@dataclass(frozen=True)
class SourcePolicy:
    market: Market
    dataset: Dataset
    primary: List[str]
    structured: List[str]
    auxiliary: List[str]
    forbidden: List[str]
    notes: str = ""


def source_policy(market: Market, dataset: Dataset) -> SourcePolicy:
    """Return preferred source route for a market/dataset pair."""
    if market == Market.CN_A:
        policies: Dict[Dataset, SourcePolicy] = {
            Dataset.FILINGS: SourcePolicy(market, dataset, ["CNINFO", "SSE", "SZSE", "BSE"], ["Wind", "Choice", "CSMAR"], ["Eastmoney/F10"], ["SEC EDGAR"], "Official PDFs/HTML are required for S/A evidence."),
            Dataset.FINANCIALS: SourcePolicy(market, dataset, ["Annual/Quarterly Report PDF"], ["Wind", "Choice", "CSMAR", "Tushare Pro"], ["AKShare", "BaoStock", "Eastmoney F10 L3 structured preflight"], ["SEC EDGAR"], "Units must be normalized."),
            Dataset.CURRENT_QUOTE: SourcePolicy(market, dataset, ["Exchange/vendor"], ["Wind", "Choice", "Tushare Pro"], ["Eastmoney", "Tencent", "AKShare", "Sina"], ["SEC EDGAR"], "Latest A-share trading day required."),
            Dataset.PRICE_HISTORY_ADJUSTED: SourcePolicy(market, dataset, ["licensed vendor"], ["Tushare Pro", "BaoStock"], ["Eastmoney", "Tencent", "AKShare"], ["SEC EDGAR"], "Use qfq/front-adjusted for technical."),
            Dataset.PRICE_HISTORY_RAW: SourcePolicy(market, dataset, ["licensed vendor"], ["Tushare Pro", "BaoStock"], ["Eastmoney", "Tencent", "AKShare"], ["SEC EDGAR"], "Use raw for actual current/reference price."),
            Dataset.SHARE_CAPITAL: SourcePolicy(market, dataset, ["CNINFO/exchange capital disclosures"], ["Wind", "Choice", "CSMAR", "Tushare Pro"], ["Tencent quote market-cap fields", "Eastmoney capital structure"], ["SEC EDGAR"], "Total and float shares are required for valuation and market-implied growth."),
            Dataset.VALUATION_INPUTS: SourcePolicy(market, dataset, ["CNINFO/exchange capital disclosures"], ["Wind", "Choice", "CSMAR", "Tushare Pro"], ["Tencent quote market-cap fields", "Eastmoney capital structure"], ["SEC EDGAR"], "Market cap, float market cap, and share count are required for valuation preflight."),
            Dataset.CUSTOMER_EVIDENCE: SourcePolicy(market, dataset, ["CNINFO/SSE/SZSE/BSE announcements and investor relations records"], ["Wind", "Choice", "CSMAR"], ["Eastmoney/THS investor relations as leads"], ["SEC EDGAR"], "Customer, order, bid-win, capacity, and investor-relation evidence must stay separate from valuation facts."),
        }
        return policies.get(dataset, SourcePolicy(market, dataset, ["CNINFO/SSE/SZSE/BSE as applicable"], ["Wind/Choice/CSMAR/Tushare"], ["AKShare/Eastmoney"], ["SEC EDGAR"]))

    if market == Market.US:
        policies = {
            Dataset.FILINGS: SourcePolicy(market, dataset, ["SEC EDGAR", "Company IR"], ["edgartools", "licensed vendor"], ["company website"], ["CNINFO"], "10-K/10-Q/8-K/S-1/S-3/20-F/6-K as applicable."),
            Dataset.FINANCIALS: SourcePolicy(market, dataset, ["SEC XBRL", "SEC filings"], ["FactSet", "Koyfin", "TIKR", "Visible Alpha"], ["yfinance", "FMP"], ["CNINFO"], "Separate reported facts from estimates."),
            Dataset.CURRENT_QUOTE: SourcePolicy(market, dataset, ["exchange/vendor"], ["FactSet", "Koyfin", "Bloomberg"], ["yfinance", "Nasdaq/Yahoo"], ["CNINFO"], "Check split/dividend adjustments."),
            Dataset.PRICE_HISTORY_ADJUSTED: SourcePolicy(market, dataset, ["exchange/vendor"], ["FactSet", "Koyfin", "Bloomberg"], ["yfinance", "Stooq"], ["CNINFO"], "Use adjusted series consistently."),
            Dataset.SHARE_CAPITAL: SourcePolicy(market, dataset, ["SEC companyfacts / company filings"], ["FactSet", "Koyfin", "TIKR"], ["company IR"], ["CNINFO"], "Share count basis must be explicit before market-cap-derived valuation."),
            Dataset.VALUATION_INPUTS: SourcePolicy(market, dataset, ["SEC companyfacts / company filings"], ["FactSet", "Koyfin", "TIKR"], ["current quote plus SEC share count"], ["CNINFO"], "Market cap can be derived from current quote and SEC share count when direct market cap is unavailable."),
            Dataset.ESTIMATES: SourcePolicy(market, dataset, ["company guidance"], ["FactSet", "Visible Alpha", "Koyfin", "TIKR"], ["SeekingAlpha", "Yahoo Analysis"], ["CNINFO"], "Consensus is not reported fact."),
            Dataset.CUSTOMER_EVIDENCE: SourcePolicy(market, dataset, ["SEC filings and company IR"], ["FactSet", "Visible Alpha", "Koyfin", "TIKR"], ["company website"], ["CNINFO"], "Customer concentration, backlog, purchase obligations, and capacity claims require filing or IR evidence."),
        }
        return policies.get(dataset, SourcePolicy(market, dataset, ["SEC/Company IR"], ["FactSet/Koyfin/TIKR"], ["yfinance"], ["CNINFO"]))

    if market == Market.HK:
        policies = {
            Dataset.FILINGS: SourcePolicy(market, dataset, ["HKEXnews", "Company IR"], ["Wind", "Choice", "Bloomberg"], ["Company website"], ["SEC EDGAR unless ADR/dual-listed"], "Watch placings, connected transactions, and circulars."),
            Dataset.FINANCIALS: SourcePolicy(market, dataset, ["HKEX annual/interim reports", "Company IR"], ["Wind", "Choice", "Bloomberg"], ["AAStocks", "company website"], ["SEC EDGAR unless ADR/dual-listed"], "Keep HKD/reporting-currency and share-count basis explicit."),
            Dataset.CURRENT_QUOTE: SourcePolicy(market, dataset, ["HKEX market data", "licensed vendor"], ["Wind", "Choice", "Bloomberg"], ["yfinance", "AAStocks"], ["SEC EDGAR unless ADR/dual-listed"], "HK quote must use HK ticker, currency, lot size, and latest trading day."),
            Dataset.PRICE_HISTORY_RAW: SourcePolicy(market, dataset, ["HKEX market data", "licensed vendor"], ["Wind", "Choice", "Bloomberg"], ["yfinance", "AAStocks"], ["SEC EDGAR unless ADR/dual-listed"], "Use HK ticker and HKD history; do not substitute ADR history."),
            Dataset.PRICE_HISTORY_ADJUSTED: SourcePolicy(market, dataset, ["licensed vendor"], ["Wind", "Choice", "Bloomberg"], ["yfinance", "AAStocks"], ["SEC EDGAR unless ADR/dual-listed"], "Use adjusted HK series consistently for Chan/GF-DMA."),
            Dataset.SHARE_CAPITAL: SourcePolicy(market, dataset, ["HKEX announcements / annual reports"], ["Wind", "Choice", "Bloomberg"], ["company IR"], ["SEC EDGAR unless ADR/dual-listed"], "Share count and currency basis must follow HK line-item disclosures."),
            Dataset.VALUATION_INPUTS: SourcePolicy(market, dataset, ["HKEX announcements / annual reports"], ["Wind", "Choice", "Bloomberg"], ["HK quote plus official share count"], ["SEC EDGAR unless ADR/dual-listed"], "HKD market-cap and share-count basis must be explicit."),
            Dataset.CUSTOMER_EVIDENCE: SourcePolicy(market, dataset, ["HKEXnews announcements and company IR"], ["Wind", "Choice", "Bloomberg"], ["company website"], ["SEC EDGAR unless ADR/dual-listed"], "Customer, order, placing, and capacity evidence must be tied to HKEX or issuer disclosures."),
        }
        return policies.get(dataset, SourcePolicy(market, dataset, ["HKEXnews", "Company IR"], ["Wind", "Choice", "Bloomberg"], ["yfinance", "AAStocks"], ["SEC EDGAR unless ADR/dual-listed"], "Watch liquidity, placing, connected transactions."))

    return SourcePolicy(market, dataset, [], [], [], [], "Unknown market: resolve before analysis.")


def provider_is_allowed(provider: DataProvider, symbol: SymbolInfo, dataset: Dataset) -> Tuple[bool, str]:
    if symbol.market not in provider.markets and Market.GLOBAL not in provider.markets:
        return False, f"provider {provider.name} does not support market {symbol.market.value}"
    if dataset not in provider.datasets:
        return False, f"provider {provider.name} does not support dataset {dataset.value}"
    return True, ""


def fetch_with_provider_chain(providers: Iterable[DataProvider], symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
    failures: List[str] = []
    if symbol.market == Market.UNKNOWN:
        return DataResult.failed(dataset, symbol.symbol, "symbol_resolver", SourceLevel.L4, "market is UNKNOWN; cannot route data safely")
    for provider in providers:
        allowed: Any
        reason: Any
        allowed, reason = provider_is_allowed(provider, symbol, dataset)
        if not allowed:
            failures.append(reason)
            continue
        try:
            result: Any = provider.fetch(symbol, dataset, **kwargs)
            if result.ok:
                return result
            failures.append(f"{provider.name}: {'; '.join(result.errors) or 'not ok'}")
        except Exception as exc:  # defensive; provider errors must not crash whole agent
            failures.append(f"{provider.name}: {type(exc).__name__}: {exc}")
    return DataResult.failed(dataset, symbol.symbol, "provider_chain", SourceLevel.L4, "All providers failed or incompatible: " + " | ".join(failures))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

REQUIRED_PRICE_COLUMNS: Any = ["trade_date", "open", "high", "low", "close", "volume"]


def validate_price_frame(
    result: DataResult,
    *,
    require_adjusted: bool = False,
    max_stale_calendar_days: int = 10,
) -> ValidationReport:
    if not result.ok:
        return ValidationReport(result.dataset, DataStatus.FAILED, errors=result.errors)
    if pd is None:
        return ValidationReport(result.dataset, DataStatus.FAILED, errors=["pandas is not installed"])
    df: Any = result.data
    if df is None or not hasattr(df, "columns"):
        return ValidationReport(result.dataset, DataStatus.FAILED, errors=["price data is not a DataFrame"])
    if df.empty:
        return ValidationReport(result.dataset, DataStatus.FAILED, errors=["empty price frame"])

    errors: List[str] = []
    warnings: List[str] = []
    missing: Any = [c for c in REQUIRED_PRICE_COLUMNS if c not in df.columns]
    if missing:
        errors.append(f"missing columns: {missing}")
        return ValidationReport(result.dataset, DataStatus.FAILED, errors=errors)

    if require_adjusted and result.adjust not in {"qfq", "hfq", "adjusted"}:
        errors.append(f"adjusted price required, got adjust={result.adjust}")

    numeric_cols: Any = ["open", "high", "low", "close", "volume"]
    for col in numeric_cols:
        series: Any = pd.to_numeric(df[col], errors="coerce")
        if series.isna().any():
            warnings.append(f"{col} contains NaN or non-numeric values")
        if col != "volume" and (series <= 0).any():
            errors.append(f"{col} contains non-positive values")
        if col == "volume" and (series < 0).any():
            errors.append("volume contains negative values")

    high: Any = pd.to_numeric(df["high"], errors="coerce")
    low: Any = pd.to_numeric(df["low"], errors="coerce")
    open_: Any = pd.to_numeric(df["open"], errors="coerce")
    close: Any = pd.to_numeric(df["close"], errors="coerce")
    if (high < pd.concat([open_, close], axis=1).max(axis=1)).any():
        errors.append("high < max(open, close) on one or more rows")
    if (low > pd.concat([open_, close], axis=1).min(axis=1)).any():
        errors.append("low > min(open, close) on one or more rows")

    dates: Any = pd.to_datetime(df["trade_date"], errors="coerce")
    if dates.isna().any():
        errors.append("trade_date cannot be parsed")
    else:
        if dates.duplicated().any():
            errors.append("duplicated trade_date values")
        if not dates.is_monotonic_increasing:
            warnings.append("trade_date is not monotonically increasing")
        last_date: Any = dates.max().date()
        age: Any = (dt.datetime.now().date() - last_date).days
        if age > max_stale_calendar_days:
            warnings.append(f"price history appears stale: last_date={last_date}, age={age} days")
            status: Any = DataStatus.STALE if not errors else DataStatus.FAILED
        else:
            status = DataStatus.OK if not errors else DataStatus.FAILED

    if errors:
        status = DataStatus.FAILED
    elif not warnings:
        status = DataStatus.OK
    else:
        status = locals().get("status", DataStatus.PARTIAL)

    return ValidationReport(
        result.dataset,
        status,
        errors=errors,
        warnings=warnings,
        stats={"rows": len(df), "first_date": str(df["trade_date"].iloc[0]), "last_date": str(df["trade_date"].iloc[-1])},
    )


def compare_latest_closes(results: List[DataResult], *, warn_pct: float = 0.5, block_pct: float = 2.0) -> ValidationReport:
    if pd is None:
        return ValidationReport("latest_close_crosscheck", DataStatus.FAILED, errors=["pandas is not installed"])
    closes: List[Tuple[str, float, str]] = []
    warnings: List[str] = []
    for r in results:
        if not r.ok or r.data is None or not hasattr(r.data, "iloc"):
            warnings.append(f"skip {r.source_name}: no usable data")
            continue
        df: Any = r.data
        if "close" not in df.columns or df.empty:
            warnings.append(f"skip {r.source_name}: no close")
            continue
        latest: Any = df.iloc[-1]
        closes.append((r.source_name, float(latest["close"]), str(latest.get("trade_date", r.as_of_date))))
    if len(closes) < 2:
        return ValidationReport("latest_close_crosscheck", DataStatus.PARTIAL, warnings=["fewer than two usable sources"] + warnings)
    values: Any = [x[1] for x in closes]
    min_v: Any
    max_v: Any
    min_v, max_v = min(values), max(values)
    diff_pct: Any = (max_v - min_v) / min_v * 100 if min_v > 0 else math.inf
    if diff_pct > block_pct:
        return ValidationReport("latest_close_crosscheck", DataStatus.FAILED, errors=[f"latest close difference {diff_pct:.2f}% > {block_pct:.2f}%"], warnings=warnings, stats={"closes": closes, "diff_pct": diff_pct})
    if diff_pct > warn_pct:
        return ValidationReport("latest_close_crosscheck", DataStatus.PARTIAL, warnings=warnings + [f"latest close difference {diff_pct:.2f}% > {warn_pct:.2f}%"], stats={"closes": closes, "diff_pct": diff_pct})
    return ValidationReport("latest_close_crosscheck", DataStatus.OK, warnings=warnings, stats={"closes": closes, "diff_pct": diff_pct})


REQUIRED_FINANCIAL_FIELDS: Any = ["period", "revenue", "gross_profit", "net_profit", "operating_cash_flow", "total_assets", "total_liabilities", "total_equity"]


def validate_financial_frame(result: DataResult, *, max_stale_days: int = 240) -> ValidationReport:
    if not result.ok:
        return ValidationReport(result.dataset, DataStatus.FAILED, errors=result.errors)
    if pd is None:
        return ValidationReport(result.dataset, DataStatus.FAILED, errors=["pandas is not installed"])
    df: Any = result.data
    if df is None or not hasattr(df, "columns") or df.empty:
        return ValidationReport(result.dataset, DataStatus.FAILED, errors=["financial data is empty or not DataFrame"])

    errors: List[str] = []
    warnings: List[str] = []
    missing: Any = [c for c in REQUIRED_FINANCIAL_FIELDS if c not in df.columns]
    if missing:
        warnings.append(f"missing recommended fields: {missing}")

    if {"total_assets", "total_liabilities", "total_equity"}.issubset(df.columns):
        lhs: Any = pd.to_numeric(df["total_assets"], errors="coerce")
        rhs: Any = pd.to_numeric(df["total_liabilities"], errors="coerce") + pd.to_numeric(df["total_equity"], errors="coerce")
        denom: Any = lhs.abs().replace(0, math.nan)
        diff: Any = ((lhs - rhs).abs() / denom).fillna(0)
        if (diff > 0.01).any():
            warnings.append(f"assets != liabilities + equity by >1% on {int((diff > 0.01).sum())} rows")

    if {"revenue", "accounts_receivable"}.issubset(df.columns):
        # Warn if receivables grow much faster than revenue in the latest period.
        if len(df) >= 2:
            rev_growth: Any = _safe_growth(df["revenue"].iloc[-2], df["revenue"].iloc[-1])
            ar_growth: Any = _safe_growth(df["accounts_receivable"].iloc[-2], df["accounts_receivable"].iloc[-1])
            if ar_growth is not None and rev_growth is not None and ar_growth > rev_growth + 0.30:
                warnings.append(f"receivables growth exceeds revenue growth by >30ppt: AR={ar_growth:.1%}, revenue={rev_growth:.1%}")

    if {"net_profit", "operating_cash_flow"}.issubset(df.columns):
        np: Any = pd.to_numeric(df["net_profit"], errors="coerce")
        ocf: Any = pd.to_numeric(df["operating_cash_flow"], errors="coerce")
        if ((np > 0) & (ocf < 0)).sum() >= 2:
            warnings.append("positive net profit with negative operating cash flow in multiple periods")

    if "period" in df.columns:
        dates: Any = pd.to_datetime(df["period"], errors="coerce")
        if not dates.isna().all():
            last_period: Any = dates.max().date()
            age: Any = (dt.datetime.now().date() - last_period).days
            if age > max_stale_days:
                warnings.append(f"financial data may be stale: last_period={last_period}, age={age} days")
                status: Any = DataStatus.STALE if not errors else DataStatus.FAILED
            else:
                status = DataStatus.OK if not errors else DataStatus.FAILED
        else:
            status = DataStatus.PARTIAL if not errors else DataStatus.FAILED
    else:
        status = DataStatus.PARTIAL if not errors else DataStatus.FAILED

    if errors:
        status = DataStatus.FAILED
    elif warnings and status == DataStatus.OK:
        status = DataStatus.PARTIAL

    return ValidationReport(result.dataset, status, errors=errors, warnings=warnings, stats={"rows": len(df)})


def _safe_growth(old: Any, new: Any) -> Optional[float]:
    try:
        old_f: Any
        new_f: Any
        old_f, new_f = float(old), float(new)
        if old_f == 0 or math.isnan(old_f) or math.isnan(new_f):
            return None
        return new_f / old_f - 1
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Data quality and rating cap
# ---------------------------------------------------------------------------


def compute_quality_score(
    *,
    price_report: Optional[ValidationReport] = None,
    financial_report: Optional[ValidationReport] = None,
    filings_status: DataStatus = DataStatus.PARTIAL,
    technical_report: Optional[ValidationReport] = None,
    cross_validation_report: Optional[ValidationReport] = None,
) -> DataQualityScore:
    price: Any = price_report.status if price_report else DataStatus.FAILED
    financials: Any = financial_report.status if financial_report else DataStatus.FAILED
    technical: Any = technical_report.status if technical_report else DataStatus.FAILED
    cross: Any = cross_validation_report.status if cross_validation_report else DataStatus.PARTIAL

    cap: Any = RatingCap.S
    missing: List[str] = []
    warnings: List[str] = []

    if price == DataStatus.FAILED:
        cap = cap_rating(cap, RatingCap.B)
        missing.append("current_or_historical_price")
    if technical == DataStatus.FAILED:
        cap = cap_rating(cap, RatingCap.B)
        missing.append("adjusted_history_for_chan_dma")
    if financials == DataStatus.FAILED:
        cap = cap_rating(cap, RatingCap.B)
        missing.append("latest_financials")
    if filings_status == DataStatus.FAILED:
        cap = cap_rating(cap, RatingCap.B)
        missing.append("official_filings_announcements")
    if cross == DataStatus.FAILED:
        cap = cap_rating(cap, RatingCap.C)
        warnings.append("cross-source validation failed")

    for status in [price, financials, filings_status, technical, cross]:
        if status == DataStatus.STALE:
            cap = cap_rating(cap, RatingCap.B)
            warnings.append("one or more datasets are stale")
        elif status == DataStatus.PARTIAL:
            cap = cap_rating(cap, RatingCap.A)

    return DataQualityScore(price, financials, filings_status, technical, cross, cap, missing, warnings)


def quality_summary_markdown(score: DataQualityScore) -> str:
    return (
        "## Data Quality And Limits\n"
        f"- Price data: {score.price.value}\n"
        f"- Financial data: {score.financials.value}\n"
        f"- Filing/announcement data: {score.filings.value}\n"
        f"- Technical data: {score.technical.value}\n"
        f"- Cross-validation: {score.cross_validation.value}\n"
        f"- Max rating allowed: {score.max_rating_allowed.value}\n"
        f"- Missing critical fields: {', '.join(score.missing_critical_fields) or 'None'}\n"
        f"- Warnings: {'; '.join(score.warnings) or 'None'}\n"
    )


# ---------------------------------------------------------------------------
# Data Acquisition Plan
# ---------------------------------------------------------------------------


def build_data_fetch_plan(symbol_or_theme: str, *, horizon: str = "12M") -> Dict[str, Any]:
    symbol: Any = resolve_symbol(symbol_or_theme)
    datasets: Any = [
        Dataset.CURRENT_QUOTE,
        Dataset.PRICE_HISTORY_RAW,
        Dataset.PRICE_HISTORY_ADJUSTED,
        Dataset.SHARE_CAPITAL,
        Dataset.VALUATION_INPUTS,
        Dataset.FINANCIALS,
        Dataset.FILINGS,
        Dataset.CUSTOMER_EVIDENCE,
        Dataset.PEER_VALUATION,
        Dataset.ESTIMATES,
        Dataset.TRADING_CALENDAR,
    ]
    policies: Any = {d.value: source_policy(symbol.market, d).__dict__ for d in datasets}
    return {
        "research_object": symbol_or_theme,
        "resolved_symbol": symbol.__dict__,
        "analysis_date": dt.date.today().isoformat(),
        "horizon": horizon,
        "required_datasets": [d.value for d in datasets],
        "source_route": policies,
        "failure_handling": {
            "missing_current_quote": "cap B, no current entry/valuation conclusion",
            "missing_adjusted_history": "cap B, no Chan/GF-DMA buy point",
            "missing_latest_financials": "cap B, no long-term high-conviction rating",
            "weak_customer_evidence_only": "cap C unless verified by official/cross-company source",
        },
    }


# ---------------------------------------------------------------------------
# Provider skeletons
# ---------------------------------------------------------------------------

class DummyProvider:
    """For smoke tests. Replace with real adapters in production."""

    def __init__(self, name: str, level: SourceLevel, markets: Sequence[Market], datasets: Sequence[Dataset], payloads: Mapping[str, Any]):
        self.name = name
        self.level = level
        self.markets = list(markets)
        self.datasets = list(datasets)
        self.payloads = dict(payloads)

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        key: Any = dataset.value
        if key not in self.payloads:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "dataset not available")
        return DataResult(
            ok=True,
            dataset=dataset,
            symbol=symbol.symbol,
            source_name=self.name,
            source_level=self.level,
            retrieved_at=utc_now(),
            data=self.payloads[key],
            currency=symbol.currency,
            adjust=kwargs.get("adjust", "not_applicable"),
        )


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        number: Any = float(value)
    except Exception:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _yahoo_symbol(symbol: SymbolInfo) -> str:
    if symbol.market == Market.CN_A:
        code: Any
        _: Any
        suffix: Any
        code, _, suffix = symbol.symbol.partition(".")
        if suffix == "SH":
            return f"{code}.SS"
        if suffix in {"SZ", "BJ"}:
            return f"{code}.{suffix}"
    return symbol.symbol


def _eastmoney_secid(symbol: SymbolInfo) -> Optional[str]:
    if symbol.market != Market.CN_A:
        return None
    code: Any
    _: Any
    suffix: Any
    code, _, suffix = symbol.symbol.partition(".")
    if not re.fullmatch(r"\d{6}", code):
        return None
    if suffix == "SH":
        return f"1.{code}"
    if suffix in {"SZ", "BJ"}:
        return f"0.{code}"
    return None


def _eastmoney_price(value: Any) -> Optional[float]:
    number: Any = _safe_float(value)
    if number is None or number <= 0:
        return None
    return round(number / 100, 4)


def _eastmoney_history_begin(chart_range: str) -> str:
    token: Any = chart_range.strip().lower()
    today: Any = dt.datetime.now().date()
    if token in {"max", "all"}:
        return "19900101"
    if token == "ytd":
        return f"{today.year}0101"
    match: Any = re.fullmatch(r"(\d+)(d|mo|y)", token)
    if not match:
        return "19900101"
    amount: Any = int(match.group(1))
    unit: Any = match.group(2)
    if unit == "d":
        days: Any = amount
    elif unit == "mo":
        days = amount * 31
    else:
        days = amount * 366
    return (today - dt.timedelta(days=days)).strftime("%Y%m%d")


def _tencent_quote_alias(symbol: SymbolInfo) -> Optional[str]:
    if symbol.market != Market.CN_A:
        return None
    code: Any
    _: Any
    suffix: Any
    code, _, suffix = symbol.symbol.partition(".")
    if not re.fullmatch(r"\d{6}", code):
        return None
    if suffix == "SH":
        return f"sh{code}"
    if suffix == "SZ":
        return f"sz{code}"
    if suffix == "BJ":
        return f"bj{code}"
    return None


def _tencent_kline_alias(symbol: SymbolInfo) -> Optional[str]:
    quote_alias: Any = _tencent_quote_alias(symbol)
    if quote_alias and quote_alias.startswith("bj"):
        return "nq" + quote_alias[2:]
    return quote_alias


def _tencent_timestamp_to_date(value: Any) -> Optional[str]:
    token: Any = str(value or "")
    if not re.fullmatch(r"\d{14}", token):
        return None
    return f"{token[:4]}-{token[4:6]}-{token[6:8]}"


def _epoch_to_date(timestamp: int, gmtoffset: int = 0) -> str:
    shifted: Any = dt.datetime.fromtimestamp(timestamp + gmtoffset, dt.timezone.utc)
    return shifted.date().isoformat()


def _millis_to_date(value: Any) -> Optional[str]:
    millis: Any = _safe_int(value)
    if millis is None:
        return None
    return dt.datetime.fromtimestamp(millis / 1000, dt.timezone.utc).date().isoformat()


class YahooChartProvider:
    """Free Yahoo chart adapter for quote and historical OHLCV auxiliary data.

    This is an L2 auxiliary source. It is useful for automated preflight, but it
    must not replace market-specific official filings or licensed/pro databases.
    """

    name: Any = "Yahoo_Chart_L2"
    level: Any = SourceLevel.L2
    markets: Any = [Market.US, Market.HK, Market.CN_A]
    datasets: Any = [Dataset.CURRENT_QUOTE, Dataset.PRICE_HISTORY_RAW, Dataset.PRICE_HISTORY_ADJUSTED]
    user_agent: Any = "Mozilla/5.0 serenity-chan-stock-skill/0.1"

    def __init__(self, *, name: str = "Yahoo_Chart_L2", host: str = "query1.finance.yahoo.com") -> None:
        self.name = name
        self.host = host

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        yahoo_symbol: Any = _yahoo_symbol(symbol)
        chart_range: Any = str(kwargs.get("range", "5d" if dataset == Dataset.CURRENT_QUOTE else "1y"))
        interval: Any = str(kwargs.get("interval", "1d"))
        params: Any = urllib.parse.urlencode({
            "range": chart_range,
            "interval": interval,
            "events": "history|div|split",
            "includeAdjustedClose": "true",
        })
        url: Any = f"https://{self.host}/v8/finance/chart/{urllib.parse.quote(yahoo_symbol)}?{params}"
        try:
            payload: Any = https_json(url, user_agent=self.user_agent)
        except Exception as exc:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"https fetch failed: {type(exc).__name__}: {exc}")

        chart: Any = payload.get("chart", {}) if isinstance(payload, Mapping) else {}
        if chart.get("error"):
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"Yahoo chart error: {chart['error']}")
        results: Any = chart.get("result") or []
        if not results:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Yahoo chart returned no result")

        result: Any = results[0]
        meta: Any = result.get("meta", {}) if isinstance(result, Mapping) else {}
        raw_path: Any
        raw_hash: Any
        raw_path = raw_hash = None
        raw_dir: Any = kwargs.get("raw_dir")
        if raw_dir:
            raw_path, raw_hash = save_raw_json(payload, raw_dir, f"{symbol.symbol}_{dataset.value}_yahoo_chart_raw.json")

        if dataset == Dataset.CURRENT_QUOTE:
            market_time: Any = _safe_int(meta.get("regularMarketTime"))
            as_of_date: Any = _epoch_to_date(market_time, int(meta.get("gmtoffset", 0) or 0)) if market_time else None
            data: Any = {
                "symbol": meta.get("symbol", yahoo_symbol),
                "name": meta.get("longName") or meta.get("shortName"),
                "currency": meta.get("currency") or symbol.currency,
                "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
                "regular_market_price": _safe_float(meta.get("regularMarketPrice")),
                "regular_market_time": market_time,
                "regular_market_day_high": _safe_float(meta.get("regularMarketDayHigh")),
                "regular_market_day_low": _safe_float(meta.get("regularMarketDayLow")),
                "regular_market_volume": _safe_int(meta.get("regularMarketVolume")),
                "market_cap": _safe_float(meta.get("marketCap")),
                "previous_close": _safe_float(meta.get("chartPreviousClose")),
                "fifty_two_week_high": _safe_float(meta.get("fiftyTwoWeekHigh")),
                "fifty_two_week_low": _safe_float(meta.get("fiftyTwoWeekLow")),
                "timezone": meta.get("exchangeTimezoneName") or meta.get("timezone"),
            }
            if data["regular_market_price"] is None:
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "regularMarketPrice missing")
            return DataResult(
                True,
                dataset,
                symbol.symbol,
                self.name,
                self.level,
                utc_now(),
                as_of_date=as_of_date,
                data=data,
                raw_path=raw_path,
                raw_hash=raw_hash,
                currency=data["currency"],
            )

        rows: Any = self._price_rows(result, adjusted=(dataset == Dataset.PRICE_HISTORY_ADJUSTED))
        if not rows:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Yahoo chart returned no usable OHLCV rows")
        return DataResult(
            True,
            dataset,
            symbol.symbol,
            self.name,
            self.level,
            utc_now(),
            as_of_date=rows[-1]["trade_date"],
            data=rows,
            raw_path=raw_path,
            raw_hash=raw_hash,
            currency=meta.get("currency") or symbol.currency,
            adjust="adjusted" if dataset == Dataset.PRICE_HISTORY_ADJUSTED else "none",
        )

    @staticmethod
    def _price_rows(result: Mapping[str, Any], *, adjusted: bool) -> List[Dict[str, Any]]:
        timestamps: Any = result.get("timestamp") or []
        meta: Any = result.get("meta", {}) if isinstance(result.get("meta"), Mapping) else {}
        gmtoffset: Any = int(meta.get("gmtoffset", 0) or 0)
        indicators: Any = result.get("indicators", {}) if isinstance(result.get("indicators"), Mapping) else {}
        quotes: Any = indicators.get("quote") or []
        if not quotes:
            return []
        quote: Any = quotes[0]
        adj: Any = (indicators.get("adjclose") or [{}])[0].get("adjclose") or []
        rows: List[Dict[str, Any]] = []
        for idx, ts in enumerate(timestamps):
            open_: Any = _safe_float((quote.get("open") or [None])[idx] if idx < len(quote.get("open") or []) else None)
            high: Any = _safe_float((quote.get("high") or [None])[idx] if idx < len(quote.get("high") or []) else None)
            low: Any = _safe_float((quote.get("low") or [None])[idx] if idx < len(quote.get("low") or []) else None)
            close: Any = _safe_float((quote.get("close") or [None])[idx] if idx < len(quote.get("close") or []) else None)
            volume: Any = _safe_int((quote.get("volume") or [None])[idx] if idx < len(quote.get("volume") or []) else None)
            adj_close: Any = _safe_float(adj[idx] if idx < len(adj) else None)
            if open_ is None or high is None or low is None or close is None or volume is None:
                continue
            raw_close: Any = close
            if adjusted and adj_close is not None and close:
                ratio: Any = adj_close / close
                open_, high, low, close = open_ * ratio, high * ratio, low * ratio, adj_close
            rows.append({
                "trade_date": _epoch_to_date(int(ts), gmtoffset),
                "open": round(open_, 6),
                "high": round(high, 6),
                "low": round(low, 6),
                "close": round(close, 6),
                "volume": volume,
                "adj_close": round(adj_close, 6) if adj_close is not None else None,
                "raw_close": round(raw_close, 6),
            })
        return rows


class TencentQuoteKlineProvider:
    """Tencent L2 quote/K-line adapter for A-share market data.

    BJ current quotes use the BJ alias while BJ daily history uses Tencent's NQ
    alias. The alias mapping stays inside the provider so the rest of the skill
    continues to use the canonical .SH/.SZ/.BJ symbol format.
    """

    name: Any = "Tencent_Quote_Kline_L2"
    level: Any = SourceLevel.L2
    markets: Any = [Market.CN_A]
    datasets: Any = [Dataset.CURRENT_QUOTE, Dataset.PRICE_HISTORY_ADJUSTED, Dataset.SHARE_CAPITAL, Dataset.VALUATION_INPUTS]
    user_agent: Any = "Mozilla/5.0"
    quote_url: Any = "https://qt.gtimg.cn/q="
    kline_url: Any = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        if dataset == Dataset.CURRENT_QUOTE:
            alias: Any = _tencent_quote_alias(symbol)
            if not alias:
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported A-share exchange for {symbol.symbol}")
            return self._fetch_quote(symbol, dataset, alias, raw_dir=kwargs.get("raw_dir"))
        if dataset in {Dataset.SHARE_CAPITAL, Dataset.VALUATION_INPUTS}:
            alias = _tencent_quote_alias(symbol)
            if not alias:
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported A-share exchange for {symbol.symbol}")
            return self._fetch_valuation_inputs(symbol, dataset, alias, raw_dir=kwargs.get("raw_dir"))
        if dataset == Dataset.PRICE_HISTORY_ADJUSTED:
            alias = _tencent_kline_alias(symbol)
            if not alias:
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported A-share exchange for {symbol.symbol}")
            return self._fetch_kline(
                symbol,
                dataset,
                alias,
                chart_range=str(kwargs.get("range", "2y")),
                interval=str(kwargs.get("interval", "1d")),
                raw_dir=kwargs.get("raw_dir"),
            )
        return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported dataset {dataset.value}")

    def _read_bytes(self, url: str, *, retries: int = 2, timeout: int = 30) -> bytes:
        try:
            import certifi  # type: ignore

            context: Any = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            context = ssl.create_default_context()

        last_error: Optional[BaseException] = None
        for attempt in range(retries + 1):
            request: Any = urllib.request.Request(
                url,
                headers={
                    "User-Agent": self.user_agent,
                    "Referer": "https://gu.qq.com/",
                    "Accept": "*/*",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code != 429 or attempt >= retries:
                    raise
            except (urllib.error.URLError, TimeoutError, ConnectionResetError, http.client.IncompleteRead, http.client.RemoteDisconnected) as exc:
                last_error = exc
                if attempt >= retries:
                    raise
            time.sleep(0.75 * (attempt + 1))
        if last_error:
            raise last_error
        raise RuntimeError("Tencent fetch failed without a captured exception")

    def _fetch_quote(self, symbol: SymbolInfo, dataset: Dataset, alias: str, *, raw_dir: Optional[str | Path]) -> DataResult:
        url: Any = self.quote_url + urllib.parse.quote(alias)
        try:
            raw: Any = self._read_bytes(url)
            text: Any = raw.decode("gb18030", errors="replace")
        except Exception as exc:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"https fetch failed: {type(exc).__name__}: {exc}")

        match: Any = re.search(rf"v_{re.escape(alias)}=\"([^\"]*)\"", text)
        if not match:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Tencent quote returned no matching symbol data")
        fields: Any = match.group(1).split("~")
        if len(fields) < 35:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"Tencent quote returned too few fields: {len(fields)}")
        price: Any = _safe_float(fields[3])
        if price is None or price <= 0:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Tencent quote missing regular market price")

        raw_path: Any
        raw_hash: Any
        raw_path = raw_hash = None
        if raw_dir:
            raw_path, raw_hash = save_raw_json({"alias": alias, "raw_text": text}, raw_dir, f"{symbol.symbol}_{dataset.value}_tencent_quote_raw.json")

        market_time: Any = fields[30] if len(fields) > 30 else ""
        data: Any = {
            "symbol": fields[2] if len(fields) > 2 else symbol.symbol.partition(".")[0],
            "name": fields[1] if len(fields) > 1 else symbol.name,
            "currency": symbol.currency or "CNY",
            "exchange": symbol.exchange,
            "regular_market_price": price,
            "regular_market_time": market_time,
            "regular_market_open": _safe_float(fields[5] if len(fields) > 5 else None),
            "regular_market_day_high": _safe_float(fields[33] if len(fields) > 33 else None),
            "regular_market_day_low": _safe_float(fields[34] if len(fields) > 34 else None),
            "regular_market_volume": _safe_int(fields[6] if len(fields) > 6 else None),
            "regular_market_turnover": _safe_float(fields[37] if len(fields) > 37 else None),
            "previous_close": _safe_float(fields[4] if len(fields) > 4 else None),
            "alias": alias,
        }
        return DataResult(
            True,
            dataset,
            symbol.symbol,
            self.name,
            self.level,
            utc_now(),
            as_of_date=_tencent_timestamp_to_date(market_time),
            data=data,
            raw_path=raw_path,
            raw_hash=raw_hash,
            currency=data["currency"],
        )

    def _fetch_valuation_inputs(self, symbol: SymbolInfo, dataset: Dataset, alias: str, *, raw_dir: Optional[str | Path]) -> DataResult:
        url: Any = self.quote_url + urllib.parse.quote(alias)
        try:
            raw: Any = self._read_bytes(url)
            text: Any = raw.decode("gb18030", errors="replace")
        except Exception as exc:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"https fetch failed: {type(exc).__name__}: {exc}")

        match: Any = re.search(rf"v_{re.escape(alias)}=\"([^\"]*)\"", text)
        if not match:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Tencent quote returned no matching symbol data")
        fields: Any = match.group(1).split("~")
        if len(fields) < 46:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"Tencent quote returned too few fields for valuation inputs: {len(fields)}")

        price: Any = _safe_float(fields[3])
        float_market_cap: Any = self._tencent_market_cap(fields, 44)
        total_market_cap: Any = self._tencent_market_cap(fields, 45)
        float_shares: Any = _safe_float(fields[72] if len(fields) > 72 else None)
        total_shares: Any = _safe_float(fields[73] if len(fields) > 73 else None)
        if total_shares is None and total_market_cap is not None and price and price > 0:
            total_shares = total_market_cap / price
        if float_shares is None and float_market_cap is not None and price and price > 0:
            float_shares = float_market_cap / price

        if total_shares is None and total_market_cap is None:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Tencent quote missing share count and market-cap fields")
        if total_market_cap is not None and float_market_cap is not None and float_market_cap > total_market_cap * 1.02:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Tencent valuation fields are internally inconsistent: float market cap exceeds total market cap")
        if total_shares is not None and float_shares is not None and float_shares > total_shares * 1.02:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Tencent valuation fields are internally inconsistent: float shares exceed total shares")

        warnings: List[str] = []
        if price and price > 0 and total_market_cap is not None and total_shares:
            implied_market_cap: Any = price * total_shares
            if implied_market_cap:
                diff: Any = abs(implied_market_cap - total_market_cap) / max(total_market_cap, 1.0)
                if diff > 0.05:
                    warnings.append(f"Total market cap differs from price * total shares by {diff:.2%}; verify share basis.")
        if float_market_cap is not None and float_shares is None:
            warnings.append("Float market cap is present, but float share count could not be derived.")

        raw_path: Any
        raw_hash: Any
        raw_path = raw_hash = None
        if raw_dir:
            raw_path, raw_hash = save_raw_json({"alias": alias, "raw_text": text}, raw_dir, f"{symbol.symbol}_{dataset.value}_tencent_quote_raw.json")

        market_time: Any = fields[30] if len(fields) > 30 else ""
        data: Any = {
            "symbol": fields[2] if len(fields) > 2 else symbol.symbol.partition(".")[0],
            "name": fields[1] if len(fields) > 1 else symbol.name,
            "currency": symbol.currency or "CNY",
            "exchange": symbol.exchange,
            "as_of_date": _tencent_timestamp_to_date(market_time),
            "regular_market_price": price,
            "regular_market_time": market_time,
            "total_shares": round(total_shares, 6) if total_shares is not None else None,
            "float_shares": round(float_shares, 6) if float_shares is not None else None,
            "total_market_cap": round(total_market_cap, 6) if total_market_cap is not None else None,
            "float_market_cap": round(float_market_cap, 6) if float_market_cap is not None else None,
            "source_basis": "quote_derived_preflight",
            "share_count_basis": "Tencent quote fields 73 total shares and 72 float shares, with market-cap-derived secondary basis when share fields are absent.",
            "market_cap_basis": "Tencent quote fields 45 total market cap and 44 float market cap in CNY hundred-millions, normalized to CNY.",
            "requires_l0_l1_verification": True,
            "raw_field_indices": {
                "price": 3,
                "float_market_cap_100m": 44,
                "total_market_cap_100m": 45,
                "float_shares": 72,
                "total_shares": 73,
            },
        }
        return DataResult(
            True,
            dataset,
            symbol.symbol,
            self.name,
            self.level,
            utc_now(),
            as_of_date=data["as_of_date"],
            data=data,
            raw_path=raw_path,
            raw_hash=raw_hash,
            currency=data["currency"],
            warnings=warnings,
        )

    @staticmethod
    def _tencent_market_cap(fields: Sequence[str], index: int) -> Optional[float]:
        value: Any = _safe_float(fields[index] if len(fields) > index else None)
        if value is None or value <= 0:
            return None
        return value * 100_000_000

    def _fetch_kline(
        self,
        symbol: SymbolInfo,
        dataset: Dataset,
        alias: str,
        *,
        chart_range: str,
        interval: str,
        raw_dir: Optional[str | Path],
    ) -> DataResult:
        if interval.strip().lower() not in {"1d", "day", "daily"}:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported Tencent interval {interval}")
        limit: Any = self._history_limit(chart_range)
        params: Any = urllib.parse.urlencode({"param": f"{alias},day,,,{limit},qfq"})
        url: Any = f"{self.kline_url}?{params}"
        try:
            raw: Any = self._read_bytes(url)
            payload: Any = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"https fetch failed: {type(exc).__name__}: {exc}")
        data: Any = payload.get("data") if isinstance(payload, Mapping) else None
        stock_payload: Any = data.get(alias) if isinstance(data, Mapping) else None
        if not isinstance(stock_payload, Mapping):
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Tencent kline returned no matching symbol data")

        qfq_rows: Any = stock_payload.get("qfqday")
        day_rows: Any = stock_payload.get("day")
        rows_source: Any = qfq_rows if isinstance(qfq_rows, list) and qfq_rows else day_rows
        adjust: Any = "qfq" if isinstance(qfq_rows, list) and qfq_rows else "unknown"
        rows: Any = self._kline_rows(rows_source if isinstance(rows_source, list) else [])
        if not rows:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Tencent kline returned no usable OHLCV rows")

        raw_path: Any
        raw_hash: Any
        raw_path = raw_hash = None
        if raw_dir:
            raw_path, raw_hash = save_raw_json(payload, raw_dir, f"{symbol.symbol}_{dataset.value}_tencent_kline_raw.json")
        warnings: List[str] = []
        if adjust == "unknown":
            warnings.append("Tencent returned daily history without a separate qfqday array; adjustment basis is unconfirmed.")
        return DataResult(
            True,
            dataset,
            symbol.symbol,
            self.name,
            self.level,
            utc_now(),
            as_of_date=rows[-1]["trade_date"],
            data=rows,
            raw_path=raw_path,
            raw_hash=raw_hash,
            currency=symbol.currency or "CNY",
            adjust=adjust,
            warnings=warnings,
        )

    @staticmethod
    def _history_limit(chart_range: str) -> int:
        token: Any = chart_range.strip().lower()
        if token in {"max", "all"}:
            return 10000
        match: Any = re.fullmatch(r"(\d+)(d|mo|y)", token)
        if not match:
            return 800
        amount: Any = int(match.group(1))
        unit: Any = match.group(2)
        if unit == "d":
            return max(amount, 1)
        if unit == "mo":
            return max(amount * 23, 1)
        return max(amount * 260, 1)

    @staticmethod
    def _kline_rows(raw_rows: Sequence[Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for item in raw_rows:
            if not isinstance(item, Sequence) or isinstance(item, (str, bytes)) or len(item) < 6:
                continue
            trade_date: Any = str(item[0])
            open_: Any = _safe_float(item[1])
            close: Any = _safe_float(item[2])
            high: Any = _safe_float(item[3])
            low: Any = _safe_float(item[4])
            volume: Any = _safe_int(float(item[5])) if _safe_float(item[5]) is not None else None
            if not trade_date or open_ is None or high is None or low is None or close is None or volume is None:
                continue
            rows.append({
                "trade_date": trade_date,
                "open": round(open_, 6),
                "high": round(high, 6),
                "low": round(low, 6),
                "close": round(close, 6),
                "volume": volume,
                "adj_close": round(close, 6),
                "raw_close": round(close, 6),
            })
        return rows


class EastmoneyQuoteKlineProvider:
    """Eastmoney L2 quote/K-line adapter for A-share SH/SZ/BJ market data."""

    name: Any = "Eastmoney_Quote_Kline_L2"
    level: Any = SourceLevel.L2
    markets: Any = [Market.CN_A]
    datasets: Any = [Dataset.CURRENT_QUOTE, Dataset.PRICE_HISTORY_RAW, Dataset.PRICE_HISTORY_ADJUSTED]
    user_agent: Any = "Mozilla/5.0 serenity-chan-stock-skill/0.1"
    quote_url: Any = "https://push2.eastmoney.com/api/qt/stock/get"
    kline_url: Any = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        secid: Any = _eastmoney_secid(symbol)
        if not secid:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported A-share exchange for {symbol.symbol}")
        if dataset == Dataset.CURRENT_QUOTE:
            return self._fetch_quote(symbol, dataset, secid, raw_dir=kwargs.get("raw_dir"))
        if dataset in {Dataset.PRICE_HISTORY_RAW, Dataset.PRICE_HISTORY_ADJUSTED}:
            return self._fetch_kline(
                symbol,
                dataset,
                secid,
                chart_range=str(kwargs.get("range", "2y")),
                interval=str(kwargs.get("interval", "1d")),
                raw_dir=kwargs.get("raw_dir"),
            )
        return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported dataset {dataset.value}")

    def _headers(self) -> Dict[str, str]:
        return {
            "Referer": "https://quote.eastmoney.com/",
            "Origin": "https://quote.eastmoney.com",
        }

    def _fetch_quote(self, symbol: SymbolInfo, dataset: Dataset, secid: str, *, raw_dir: Optional[str | Path]) -> DataResult:
        fields: Any = ",".join([
            "f43",  # latest price, scaled by 100
            "f44",  # day high, scaled by 100
            "f45",  # day low, scaled by 100
            "f46",  # open, scaled by 100
            "f47",  # volume
            "f48",  # turnover amount
            "f57",  # code
            "f58",  # name
            "f60",  # previous close, scaled by 100
            "f86",  # market timestamp
            "f107",  # market id
        ])
        params: Any = urllib.parse.urlencode({"secid": secid, "fields": fields})
        url: Any = f"{self.quote_url}?{params}"
        try:
            payload: Any = https_json(url, user_agent=self.user_agent, headers=self._headers())
        except Exception as exc:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"https fetch failed: {type(exc).__name__}: {exc}")
        data: Any = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(data, Mapping):
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Eastmoney quote returned no data")

        market_price: Any = _eastmoney_price(data.get("f43"))
        if market_price is None:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Eastmoney quote missing regular market price")
        market_time: Any = _safe_int(data.get("f86"))
        raw_path: Any
        raw_hash: Any
        raw_path = raw_hash = None
        if raw_dir:
            raw_path, raw_hash = save_raw_json(payload, raw_dir, f"{symbol.symbol}_{dataset.value}_eastmoney_quote_raw.json")

        quote: Any = {
            "symbol": str(data.get("f57") or symbol.symbol.partition(".")[0]),
            "name": data.get("f58") or symbol.name,
            "currency": symbol.currency or "CNY",
            "exchange": symbol.exchange,
            "regular_market_price": market_price,
            "regular_market_time": market_time,
            "regular_market_day_high": _eastmoney_price(data.get("f44")),
            "regular_market_day_low": _eastmoney_price(data.get("f45")),
            "regular_market_open": _eastmoney_price(data.get("f46")),
            "regular_market_volume": _safe_int(data.get("f47")),
            "regular_market_turnover": _safe_float(data.get("f48")),
            "previous_close": _eastmoney_price(data.get("f60")),
            "market_id": _safe_int(data.get("f107")),
            "secid": secid,
        }
        return DataResult(
            True,
            dataset,
            symbol.symbol,
            self.name,
            self.level,
            utc_now(),
            as_of_date=_epoch_to_date(market_time) if market_time else None,
            data=quote,
            raw_path=raw_path,
            raw_hash=raw_hash,
            currency=quote["currency"],
        )

    def _fetch_kline(
        self,
        symbol: SymbolInfo,
        dataset: Dataset,
        secid: str,
        *,
        chart_range: str,
        interval: str,
        raw_dir: Optional[str | Path],
    ) -> DataResult:
        klt: Any = self._kline_interval(interval)
        if not klt:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported Eastmoney interval {interval}")
        params: Any = urllib.parse.urlencode({
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": klt,
            "fqt": "1" if dataset == Dataset.PRICE_HISTORY_ADJUSTED else "0",
            "beg": _eastmoney_history_begin(chart_range),
            "end": dt.datetime.now().date().strftime("%Y%m%d"),
        })
        url: Any = f"{self.kline_url}?{params}"
        try:
            payload: Any = https_json(url, user_agent=self.user_agent, headers=self._headers())
        except Exception as exc:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"https fetch failed: {type(exc).__name__}: {exc}")
        data: Any = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(data, Mapping):
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Eastmoney kline returned no data")
        rows: Any = self._kline_rows(data.get("klines") or [])
        if not rows:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Eastmoney kline returned no usable OHLCV rows")
        raw_path: Any
        raw_hash: Any
        raw_path = raw_hash = None
        if raw_dir:
            raw_path, raw_hash = save_raw_json(payload, raw_dir, f"{symbol.symbol}_{dataset.value}_eastmoney_kline_raw.json")
        return DataResult(
            True,
            dataset,
            symbol.symbol,
            self.name,
            self.level,
            utc_now(),
            as_of_date=rows[-1]["trade_date"],
            data=rows,
            raw_path=raw_path,
            raw_hash=raw_hash,
            currency=symbol.currency or "CNY",
            adjust="qfq" if dataset == Dataset.PRICE_HISTORY_ADJUSTED else "none",
        )

    @staticmethod
    def _kline_interval(interval: str) -> Optional[str]:
        normalized: Any = interval.strip().lower()
        mapping: Any = {
            "1d": "101",
            "day": "101",
            "daily": "101",
            "1wk": "102",
            "1w": "102",
            "week": "102",
            "weekly": "102",
            "1mo": "103",
            "1m": "103",
            "month": "103",
            "monthly": "103",
        }
        return mapping.get(normalized)

    @staticmethod
    def _kline_rows(klines: Sequence[Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for item in klines:
            if not isinstance(item, str):
                continue
            parts: Any = item.split(",")
            if len(parts) < 6:
                continue
            trade_date: Any = parts[0]
            open_: Any = _safe_float(parts[1])
            close: Any = _safe_float(parts[2])
            high: Any = _safe_float(parts[3])
            low: Any = _safe_float(parts[4])
            volume: Any = _safe_int(parts[5])
            amount: Any = _safe_float(parts[6]) if len(parts) > 6 else None
            if not trade_date or open_ is None or high is None or low is None or close is None or volume is None:
                continue
            rows.append({
                "trade_date": trade_date,
                "open": round(open_, 6),
                "high": round(high, 6),
                "low": round(low, 6),
                "close": round(close, 6),
                "volume": volume,
                "amount": amount,
                "adj_close": round(close, 6),
                "raw_close": round(close, 6),
            })
        return rows


class CninfoAnnouncementsProvider:
    """Official CNINFO announcement metadata adapter for A-share filings.

    This adapter fetches announcement metadata and PDF links. It deliberately
    does not parse financial statements out of PDFs; structured A-share
    financials should be provided by an official/licensed data adapter.
    """

    name: Any = "CNINFO_Announcements_L0"
    level: Any = SourceLevel.L0
    markets: Any = [Market.CN_A]
    datasets: Any = [Dataset.FILINGS]
    user_agent: Any = "Mozilla/5.0 serenity-chan-stock-skill/0.1"
    top_search_url: Any = "http://www.cninfo.com.cn/new/information/topSearch/query"
    announcement_url: Any = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
    referer: Any = "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search"

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        if symbol.market != Market.CN_A:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "CNINFO announcements only support A-share symbols")
        if dataset != Dataset.FILINGS:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported dataset {dataset.value}")
        try:
            code: Any
            _: Any
            suffix: Any
            code, _, suffix = symbol.symbol.partition(".")
            listing: Any = self._lookup_listing(code)
            if not listing:
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"could not resolve CNINFO orgId for {symbol.symbol}")
            payload: Any = self._query_announcements(code, str(listing.get("orgId") or ""), suffix)
            announcements: Any = payload.get("announcements") or []
            if not isinstance(announcements, list) or not announcements:
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "CNINFO returned no announcements")

            raw_path: Any
            raw_hash: Any
            raw_path = raw_hash = None
            raw_dir: Any = kwargs.get("raw_dir")
            if raw_dir:
                raw_path, raw_hash = save_raw_json(
                    {"lookup": listing, "announcements": payload},
                    raw_dir,
                    f"{symbol.symbol}_cninfo_announcements_raw.json",
                )

            records: Any = [self._normalize_announcement(item) for item in announcements[:80] if isinstance(item, Mapping)]
            records = [record for record in records if record]
            if not records:
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "CNINFO announcements could not be normalized")
            as_of: Any = records[0].get("announcement_date")
            return DataResult(
                True,
                Dataset.FILINGS,
                symbol.symbol,
                self.name,
                self.level,
                utc_now(),
                as_of_date=as_of,
                data={
                    "code": code,
                    "org_id": listing.get("orgId"),
                    "name": listing.get("zwjc"),
                    "total_announcement": payload.get("totalAnnouncement"),
                    "recent_announcements": records,
                },
                raw_path=raw_path,
                raw_hash=raw_hash,
                currency=symbol.currency,
                warnings=["CNINFO metadata/PDF links fetched; financial statement tables are not parsed by this adapter."],
            )
        except Exception as exc:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"CNINFO fetch failed: {type(exc).__name__}: {exc}")

    def _lookup_listing(self, code: str) -> Optional[Mapping[str, Any]]:
        payload: Any = form_json(
            self.top_search_url,
            {"keyWord": code, "maxNum": "10"},
            user_agent=self.user_agent,
            headers={"Referer": self.referer},
        )
        if not isinstance(payload, list):
            return None
        for item in payload:
            if not isinstance(item, Mapping):
                continue
            if str(item.get("code") or "") == code and str(item.get("category") or "") == "A股":
                return item
        return None

    def _query_announcements(self, code: str, org_id: str, suffix: str, *, page_num: int = 1, page_size: int = 30) -> Mapping[str, Any]:
        column: Any = "sse" if suffix == "SH" else "szse" if suffix == "SZ" else "bj"
        payload: Any = form_json(
            self.announcement_url,
            {
                "stock": f"{code},{org_id}",
                "tabName": "fulltext",
                "pageSize": str(page_size),
                "pageNum": str(page_num),
                "column": column,
                "plate": column,
                "seDate": "",
            },
            user_agent=self.user_agent,
            headers={"Referer": self.referer},
        )
        return payload if isinstance(payload, Mapping) else {}

    @staticmethod
    def _normalize_announcement(item: Mapping[str, Any]) -> Dict[str, Any]:
        adjunct: Any = str(item.get("adjunctUrl") or "")
        pdf_url: Any = f"https://static.cninfo.com.cn/{adjunct}" if adjunct else None
        title: Any = str(item.get("announcementTitle") or item.get("shortTitle") or "")
        return {
            "sec_code": item.get("secCode"),
            "sec_name": item.get("secName"),
            "announcement_id": item.get("announcementId"),
            "title": item.get("announcementTitle"),
            "short_title": item.get("shortTitle"),
            "announcement_date": _millis_to_date(item.get("announcementTime")),
            "pdf_url": pdf_url,
            "document_type": item.get("adjunctType"),
            "document_size_kb": item.get("adjunctSize"),
            "announcement_type": item.get("announcementType"),
            "document_category": CninfoAnnouncementsProvider._classify_announcement_title(title),
            "page_column": item.get("pageColumn"),
        }

    @staticmethod
    def _classify_announcement_title(title: str) -> str:
        compact: Any = re.sub(r"\s+", "", str(title or ""))
        if "半年度报告" in compact and "摘要" not in compact:
            return "interim_report"
        if "年度报告" in compact and "摘要" not in compact:
            return "annual_report"
        if any(token in compact for token in ["第一季度报告", "一季度报告", "第三季度报告", "三季度报告", "季度报告"]) and "摘要" not in compact:
            return "quarterly_report"
        if any(token in compact for token in ["业绩预告", "业绩快报", "盈利预告"]):
            return "earnings_preannouncement"
        if any(token in compact for token in ["向特定对象发行", "非公开发行", "定增", "募集说明书"]):
            return "private_placement"
        if any(token in compact for token in ["可转换公司债券", "可转债"]):
            return "convertible_bond"
        if any(token in compact for token in ["回购股份", "股份回购"]):
            return "share_repurchase"
        if any(token in compact for token in ["重大合同", "日常经营重大合同", "合同公告"]):
            return "major_contract"
        if any(token in compact for token in ["中标", "项目预中标", "收到中标通知书"]):
            return "bid_win"
        if any(token in compact for token in ["扩产", "产能", "建设项目", "投资项目", "募投项目"]):
            return "capacity_expansion"
        if any(token in compact for token in ["问询函", "监管工作函", "关注函", "回复"]):
            return "regulatory_inquiry"
        if any(token in compact for token in ["投资者关系活动", "调研活动", "业绩说明会"]):
            return "investor_relations_activity"
        if any(token in compact for token in ["减持", "权益变动"]):
            return "shareholding_reduction"
        if any(token in compact for token in ["限售股上市流通", "解除限售"]):
            return "lockup_release"
        if any(token in compact for token in ["股权激励", "员工持股计划"]):
            return "equity_incentive"
        return "other"


class DisclosureCustomerEvidenceProvider:
    """Build customer, order, bid-win, and capacity evidence from official disclosure metadata."""

    name: str = "Disclosure_Customer_Order_Capacity_Evidence_L0"
    level: SourceLevel = SourceLevel.L0
    markets: list[Market] = [Market.CN_A, Market.HK, Market.US]
    datasets: list[Dataset] = [Dataset.CUSTOMER_EVIDENCE]
    direct_categories: set[str] = {"major_contract", "bid_win", "capacity_expansion"}
    lead_categories: set[str] = {
        "investor_relations_activity",
        "earnings_preannouncement",
        "regulatory_inquiry",
        "annual_report",
        "interim_report",
        "quarterly_report",
    }
    keyword_axes: tuple[tuple[str, str, str], ...] = (
        ("客户", "customer_validation", "DISCLOSURE_LEAD"),
        ("customer", "customer_validation", "DISCLOSURE_LEAD"),
        ("订单", "order_backlog", "DIRECT_DISCLOSURE"),
        ("order", "order_backlog", "DIRECT_DISCLOSURE"),
        ("合同", "order_backlog", "DIRECT_DISCLOSURE"),
        ("contract", "order_backlog", "DIRECT_DISCLOSURE"),
        ("中标", "bid_win", "DIRECT_DISCLOSURE"),
        ("bid", "bid_win", "DIRECT_DISCLOSURE"),
        ("产能", "capacity_expansion", "DIRECT_DISCLOSURE"),
        ("capacity", "capacity_expansion", "DIRECT_DISCLOSURE"),
        ("扩产", "capacity_expansion", "DIRECT_DISCLOSURE"),
        ("investor relations", "investor_relations", "DISCLOSURE_LEAD"),
        ("调研", "investor_relations", "DISCLOSURE_LEAD"),
        ("业绩说明会", "investor_relations", "DISCLOSURE_LEAD"),
    )

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        if dataset != Dataset.CUSTOMER_EVIDENCE:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported dataset {dataset.value}")
        if symbol.market not in self.markets:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported market {symbol.market.value}")
        try:
            records: list[dict[str, Any]]
            source_name: str
            raw_payload: Mapping[str, Any]
            records, source_name, raw_payload = self._load_disclosure_records(symbol, kwargs)
            data: dict[str, Any] = self._build_payload(symbol, records, source_name=source_name)
            raw_path: Any
            raw_hash: Any
            raw_path = raw_hash = None
            raw_dir: Any = kwargs.get("raw_dir")
            if raw_dir:
                raw_path, raw_hash = save_raw_json(
                    {"source": source_name, "records": records, "raw_payload": raw_payload, "customer_evidence": data},
                    raw_dir,
                    f"{symbol.symbol}_{Dataset.CUSTOMER_EVIDENCE.value}_raw.json",
                )
            return DataResult(
                True,
                Dataset.CUSTOMER_EVIDENCE,
                symbol.symbol,
                self.name,
                self.level,
                utc_now(),
                as_of_date=data.get("as_of_date"),
                data=data,
                raw_path=raw_path,
                raw_hash=raw_hash,
                currency=symbol.currency,
                warnings=list(data.get("warnings", [])),
            )
        except Exception as exc:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"customer evidence fetch failed: {type(exc).__name__}: {exc}")

    def _load_disclosure_records(self, symbol: SymbolInfo, kwargs: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str, Mapping[str, Any]]:
        supplied: Any = kwargs.get("filings_result")
        supplied_data: Any = supplied.get("data") if isinstance(supplied, Mapping) else supplied
        if isinstance(supplied_data, Mapping):
            records: list[dict[str, Any]] = self._records_from_payload(symbol.market, supplied_data)
            if records:
                return records, str(supplied.get("source_name") or "filings_result") if isinstance(supplied, Mapping) else "filings_result", supplied_data

        if symbol.market == Market.CN_A:
            provider: Any = CninfoAnnouncementsProvider()
            result: Any = provider.fetch(symbol, Dataset.FILINGS, raw_dir=kwargs.get("raw_dir"))
            if not result.ok:
                raise RuntimeError("; ".join(result.errors) or "CNINFO announcements unavailable")
            payload: Mapping[str, Any] = result.data if isinstance(result.data, Mapping) else {}
            return self._records_from_payload(symbol.market, payload), provider.name, payload
        if symbol.market == Market.HK:
            provider = HkexAnnouncementsProvider()
            result = provider.fetch(symbol, Dataset.FILINGS, raw_dir=kwargs.get("raw_dir"))
            if not result.ok:
                raise RuntimeError("; ".join(result.errors) or "HKEX announcements unavailable")
            payload = result.data if isinstance(result.data, Mapping) else {}
            return self._records_from_payload(symbol.market, payload), provider.name, payload
        if symbol.market == Market.US:
            provider = SecCompanyFactsProvider()
            result = provider.fetch(symbol, Dataset.FILINGS, raw_dir=kwargs.get("raw_dir"))
            if not result.ok:
                raise RuntimeError("; ".join(result.errors) or "SEC submissions unavailable")
            payload = result.data if isinstance(result.data, Mapping) else {}
            return self._records_from_payload(symbol.market, payload), provider.name, payload
        return [], "unsupported_market", {}

    @staticmethod
    def _records_from_payload(market: Market, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        source_rows: Any = []
        if market == Market.CN_A:
            source_rows = payload.get("recent_announcements") or payload.get("announcements") or []
        elif market == Market.HK:
            source_rows = payload.get("announcements") or payload.get("recent_announcements") or []
        elif market == Market.US:
            source_rows = payload.get("recent_filings") or []
        records: list[dict[str, Any]] = []
        for row in source_rows if isinstance(source_rows, list) else []:
            if not isinstance(row, Mapping):
                continue
            title: str = str(row.get("title") or row.get("short_title") or row.get("form") or row.get("primary_document") or "")
            date: str = str(row.get("announcement_date") or row.get("announcement_datetime") or row.get("filing_date") or row.get("report_date") or "")
            category: str = str(row.get("document_category") or row.get("form") or "")
            url: str = str(row.get("pdf_url") or row.get("file_link") or row.get("primary_document") or "")
            record_id: str = str(row.get("announcement_id") or row.get("news_id") or row.get("accession_number") or title)
            records.append({
                "record_id": record_id,
                "title": title,
                "date": date[:10],
                "category": category,
                "url": url,
                "raw": dict(row),
            })
        return records

    def _evidence_axis(self, title: str, category: str) -> tuple[str, str]:
        category_text: str = category.lower()
        form_text: str = re.sub(r"\s+", "", category_text)
        sec_periodic_or_registration_form: bool = form_text.startswith(("10-k", "10-q", "20-f", "40-f", "6-k", "s-1", "s-3", "f-1", "424b"))
        sec_current_report_form: bool = form_text.startswith("8-k")
        if category_text == "major_contract":
            return "order_backlog", "DIRECT_DISCLOSURE"
        if category_text == "bid_win":
            return "bid_win", "DIRECT_DISCLOSURE"
        if category_text == "capacity_expansion":
            return "capacity_expansion", "DIRECT_DISCLOSURE"
        if sec_periodic_or_registration_form:
            return "disclosure_review", "DISCLOSURE_LEAD"
        if category_text in self.lead_categories:
            return "disclosure_review", "DISCLOSURE_LEAD"
        text: str = title.lower()
        for keyword, axis, strength in self.keyword_axes:
            if keyword.lower() in text:
                return axis, strength
        if sec_current_report_form:
            return "disclosure_review", "DISCLOSURE_LEAD"
        return "disclosure_review", "REVIEW_QUEUE"

    def _build_payload(self, symbol: SymbolInfo, records: Sequence[Mapping[str, Any]], *, source_name: str) -> dict[str, Any]:
        evidence_items: list[dict[str, Any]] = []
        review_queue: list[dict[str, Any]] = []
        for record in records[:120]:
            title: str = str(record.get("title") or "")
            category: str = str(record.get("category") or "")
            axis: str
            strength: str
            axis, strength = self._evidence_axis(title, category)
            item: dict[str, Any] = {
                "symbol": symbol.symbol,
                "source_ref": f"{Dataset.CUSTOMER_EVIDENCE.value}:{symbol.symbol}:{record.get('record_id') or title}",
                "source_name": source_name,
                "source_level": "L0",
                "title": title,
                "date": str(record.get("date") or ""),
                "category": category,
                "evidence_axis": axis,
                "evidence_strength": strength,
                "url": str(record.get("url") or ""),
                "claim_boundary": "该条目是披露索引；只有 AI 审阅者读取对应公告或 filing 正文后，才能作为客户、订单、产能或收入传导 claim 的支持证据。",
            }
            if strength in {"DIRECT_DISCLOSURE", "DISCLOSURE_LEAD"} or category in self.direct_categories or category in self.lead_categories:
                evidence_items.append(item)
            else:
                review_queue.append(item)

        direct_count: int = sum(1 for item in evidence_items if item.get("evidence_strength") == "DIRECT_DISCLOSURE")
        lead_count: int = sum(1 for item in evidence_items if item.get("evidence_strength") == "DISCLOSURE_LEAD")
        if direct_count:
            evidence_status: str = "DIRECT_EVIDENCE_FOUND"
            score: float = min(92.0, 72.0 + direct_count * 6.0 + lead_count * 2.0)
        elif lead_count:
            evidence_status = "DISCLOSURE_LEADS_ONLY"
            score = min(68.0, 50.0 + lead_count * 4.0)
        else:
            evidence_status = "NO_DIRECT_CUSTOMER_ORDER_CAPACITY_DISCLOSURE"
            score = 38.0
        warnings: list[str] = []
        if evidence_status != "DIRECT_EVIDENCE_FOUND":
            warnings.append("客户/订单/产能证据 lane 未在已扫描披露中找到直接 L0 客户、订单、招投标或产能证据。")
        return {
            "contract_type": "serenity_customer_order_capacity_evidence",
            "schema_version": "1.0",
            "symbol": symbol.symbol,
            "market": symbol.market.value,
            "source_name": source_name,
            "source_level": self.level.value,
            "as_of_date": next((str(item.get("date")) for item in evidence_items if item.get("date")), ""),
            "summary": {
                "evidence_status": evidence_status,
                "score": round(score, 2),
                "direct_evidence_count": direct_count,
                "lead_evidence_count": lead_count,
                "review_queue_count": len(review_queue),
                "loaded_record_count": len(records),
            },
            "evidence_items": evidence_items[:30],
            "review_queue": review_queue[:20],
            "required_next_evidence": [
                "读取 direct 或 lead 证据对应公告正文，确认客户、订单、产能或分部收入是否能映射到收入传导。",
                "若市场隐含增长为 H4/H5，必须用 L0/L1 客户、订单、产能、分部收入或财务兑现证据闭合增长假设。",
            ],
            "warnings": warnings,
        }


class PdfTextExtractionMixin:
    """Shared PDF text extraction utilities for official report adapters."""

    @staticmethod
    def _pdf_python_candidates() -> List[str]:
        candidates: List[str] = []
        env_python: Any = os.getenv("SERENITY_PDF_PYTHON")
        if env_python:
            candidates.append(env_python)
        candidates.append(sys.executable)
        runtime_python: Any = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
        candidates.append(str(runtime_python))
        output: List[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            if Path(candidate).exists():
                output.append(candidate)
        return output

    @staticmethod
    def _pdfplumber_extract_script() -> str:
        return r'''
import json
import sys

import pdfplumber

path = sys.argv[1]
max_pages = int(sys.argv[2])
pages = []
with pdfplumber.open(path) as pdf:
    total_pages = len(pdf.pages)
    for index, page in enumerate(pdf.pages[:max_pages], start=1):
        text = page.extract_text() or ""
        if text.strip():
            pages.append({"page_number": index, "text": text})
print(json.dumps({"parser": "pdfplumber", "page_count": total_pages, "pages": pages}, ensure_ascii=False))
'''

    def _extract_pdf_pages(self, pdf_path: str | Path, *, max_pages: int = 220, timeout: int = 60) -> Dict[str, Any]:
        path: Any = Path(pdf_path)
        errors: List[str] = []
        script: Any = self._pdfplumber_extract_script()
        deadline: Any = time.monotonic() + max(1, int(timeout))
        for python_exe in self._pdf_python_candidates():
            remaining: Any = int(deadline - time.monotonic())
            if remaining <= 0:
                errors.append(f"PDF extraction exceeded {timeout}s before trying {python_exe}")
                break
            try:
                completed: Any = subprocess.run(
                    [python_exe, "-", str(path), str(max_pages)],
                    input=script.encode("utf-8"),
                    capture_output=True,
                    timeout=max(1, remaining),
                    check=True,
                )
                payload: Any = json.loads(completed.stdout.decode("utf-8"))
                payload["ok"] = True
                payload["python"] = python_exe
                payload.setdefault("errors", [])
                return payload
            except Exception as exc:
                stderr: Any = ""
                if isinstance(exc, subprocess.CalledProcessError):
                    stderr = exc.stderr.decode("utf-8", errors="replace")[:500]
                errors.append(f"{python_exe}: {type(exc).__name__}: {exc} {stderr}".strip())

        return {"ok": False, "parser": "none", "page_count": 0, "pages": [], "errors": errors}

    @staticmethod
    def _normalize_pdf_line(line: str) -> str:
        line = line.replace("\u2019", "'").replace("\u2013", "-").replace("\u2014", "-")
        return re.sub(r"\s+", " ", line).strip()

    @classmethod
    def _pdf_number_tokens(cls, line: str) -> List[str]:
        normalized: Any = cls._normalize_pdf_line(line)
        return re.findall(r"\(?-?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?|\(?-?\d+(?:\.\d+)?\)?|(?<!\w)[-–](?!\w)", normalized)

    @staticmethod
    def _parse_pdf_number(token: str) -> Optional[float]:
        text: Any = token.strip()
        if text in {"-", "–", ""}:
            return None
        negative: Any = text.startswith("(") and text.endswith(")")
        text = text.strip("()").replace(",", "")
        try:
            value: Any = float(text)
        except Exception:
            return None
        return -value if negative else value

    @classmethod
    def _line_values(cls, line: str, *, expected_columns: int) -> List[Optional[float]]:
        tokens: Any = cls._pdf_number_tokens(line)
        while len(tokens) > expected_columns:
            first: Any = tokens[0].strip("()")
            if "," not in first and "." not in first and first.lstrip("-").isdigit() and abs(int(first)) <= 80:
                tokens.pop(0)
                continue
            break
        if len(tokens) > expected_columns:
            tokens = tokens[-expected_columns:]
        return [cls._parse_pdf_number(token) for token in tokens]

    @staticmethod
    def _page_texts_containing(pages: Sequence[Mapping[str, Any]], *needles: str) -> List[Mapping[str, Any]]:
        lower_needles: Any = [needle.lower() for needle in needles]
        output: List[Mapping[str, Any]] = []
        for page in pages:
            text: Any = str(page.get("text") or "")
            lower_text: Any = text.lower()
            if all(needle in lower_text for needle in lower_needles):
                output.append(page)
        return output

    @classmethod
    def _extract_label_value(
        cls,
        pages: Sequence[Mapping[str, Any]],
        labels: Sequence[str],
        *,
        expected_columns: int,
        value_index: int,
    ) -> Tuple[Optional[float], Optional[Dict[str, Any]]]:
        lower_labels: Any = [label.lower() for label in labels]
        for page in pages:
            text: Any = str(page.get("text") or "")
            for line in text.splitlines():
                clean: Any = cls._normalize_pdf_line(line)
                clean_lower: Any = clean.lower()
                if not any(clean_lower.startswith(label) or label in clean_lower for label in lower_labels):
                    continue
                values: Any = cls._line_values(clean, expected_columns=expected_columns)
                if len(values) <= value_index or values[value_index] is None:
                    continue
                return values[value_index], {
                    "page_number": page.get("page_number"),
                    "line": clean,
                    "value_index": value_index,
                }
        return None, None

    @classmethod
    def _extract_revenue_value(
        cls,
        pages: Sequence[Mapping[str, Any]],
        *,
        expected_columns: int,
        value_index: int,
    ) -> Tuple[Optional[float], Optional[Dict[str, Any]]]:
        for page in pages:
            lines: Any = [cls._normalize_pdf_line(line) for line in str(page.get("text") or "").splitlines()]
            for index, line in enumerate(lines):
                if line.lower() != "revenues":
                    continue
                for candidate in lines[index + 1:index + 10]:
                    if candidate.lower().startswith("cost of revenues"):
                        break
                    if re.match(r"^\d+[A-Za-z()]?\s+", candidate):
                        values: Any = cls._line_values(candidate, expected_columns=expected_columns)
                        if len(values) > value_index and values[value_index] is not None:
                            return values[value_index], {
                                "page_number": page.get("page_number"),
                                "line": candidate,
                                "value_index": value_index,
                            }
                break
        return None, None


class CninfoFinancialReportBase:
    user_agent: Any = "Mozilla/5.0 serenity-chan-stock-skill/0.1"

    def _locate_official_report_evidence(
        self,
        symbol: SymbolInfo,
        *,
        raw_dir: Optional[str | Path] = None,
        download_limit: int = 2,
    ) -> Dict[str, Any]:
        code: Any
        _: Any
        suffix: Any
        code, _, suffix = symbol.symbol.partition(".")
        evidence: Dict[str, Any] = {
            "status": "FAILED",
            "source": "CNINFO_Announcements_L0",
            "source_level": SourceLevel.L0.value,
            "reports": [],
            "downloaded_reports": [],
            "errors": [],
        }
        try:
            cninfo: Any = CninfoAnnouncementsProvider()
            listing: Any = cninfo._lookup_listing(code)
            if not listing:
                evidence["errors"].append(f"could not resolve CNINFO orgId for {symbol.symbol}")
                return evidence
            announcements: List[Any] = []
            queried_pages: Any = 0
            seen_report_keys: set[str] = set()
            reports: List[Dict[str, Any]] = []
            issuer_name: Any = str(listing.get("zwjc") or "")
            raw_scan_pages: str = os.getenv("SERENITY_CNINFO_REPORT_SCAN_PAGES", str(CNINFO_REPORT_SCAN_PAGE_LIMIT_DEFAULT))
            try:
                max_scan_pages: int = int(raw_scan_pages or CNINFO_REPORT_SCAN_PAGE_LIMIT_DEFAULT)
            except ValueError:
                max_scan_pages = CNINFO_REPORT_SCAN_PAGE_LIMIT_DEFAULT
            for scan_attempt in range(2):
                announcements = []
                queried_pages = 0
                seen_report_keys = set()
                reports = []
                for page_num in range(1, max(1, max_scan_pages) + 1):
                    payload: Any = cninfo._query_announcements(code, str(listing.get("orgId") or ""), suffix, page_num=page_num, page_size=30)
                    page_announcements: Any = payload.get("announcements") or []
                    if not isinstance(page_announcements, list) or not page_announcements:
                        break
                    queried_pages += 1
                    announcements.extend(page_announcements)
                    for item in page_announcements:
                        if not isinstance(item, Mapping):
                            continue
                        record: Any = cninfo._normalize_announcement(item)
                        title: Any = self._clean_title(str(record.get("title") or record.get("short_title") or ""))
                        if not self._is_periodic_report_title(title, issuer_name=issuer_name):
                            continue
                        record["title"] = title
                        record["report_kind"] = self._report_kind(title)
                        report_key: Any = str(record.get("announcement_id") or record.get("pdf_url") or title)
                        if report_key in seen_report_keys:
                            continue
                        seen_report_keys.add(report_key)
                        reports.append(record)
                        if len(reports) >= 16 and self._comparable_report_coverage_ready(reports):
                            break
                    if len(reports) >= 4 and self._comparable_report_coverage_ready(reports):
                        break
                if reports or scan_attempt == 1:
                    break
                time.sleep(0.75)
            if raw_dir and reports and download_limit > 0:
                self._attach_official_report_downloads(
                    reports,
                    raw_dir=Path(raw_dir) / "official_reports",
                    symbol=symbol.symbol,
                    limit=download_limit,
                    errors=evidence["errors"],
                )

            selected_reports: Any = self._select_reports_for_download(reports, download_limit) if reports and download_limit > 0 else []
            downloaded_reports: Any = [
                report for report in reports
                if report.get("download_status") == "OK" and report.get("pdf_path")
            ]
            if not reports:
                evidence_status: Any = "PARTIAL"
            elif raw_dir and selected_reports and len(downloaded_reports) < len(selected_reports):
                evidence_status = "PARTIAL"
            else:
                evidence_status = "OK"
            evidence.update({
                "status": evidence_status,
                "code": code,
                "org_id": listing.get("orgId"),
                "name": listing.get("zwjc"),
                "queried_announcements": len(announcements) if isinstance(announcements, list) else 0,
                "queried_pages": queried_pages,
                "selected_report_count": len(selected_reports),
                "downloaded_report_count": len(downloaded_reports),
                "reports": reports,
                "downloaded_reports": [
                    {
                        "report_kind": report.get("report_kind"),
                        "title": report.get("title"),
                        "announcement_date": report.get("announcement_date"),
                        "pdf_path": report.get("pdf_path"),
                        "pdf_hash": report.get("pdf_hash"),
                        "pdf_size_bytes": report.get("pdf_size_bytes"),
                        "line_extraction": report.get("line_extraction"),
                    }
                    for report in downloaded_reports
                ],
            })
            if not reports:
                evidence["errors"].append("CNINFO announcement query succeeded but no recent periodic report PDF was found in the scanned pages")
            return evidence
        except Exception as exc:
            evidence["errors"].append(f"CNINFO report evidence lookup failed: {type(exc).__name__}: {exc}")
            return evidence

    @staticmethod
    def _clean_title(title: str) -> str:
        return re.sub(r"<[^>]+>", "", title).replace("&nbsp;", " ").strip()

    @staticmethod
    def _is_periodic_report_title(title: str, *, issuer_name: str = "") -> bool:
        if not title or "摘要" in title:
            return False
        excluded: Any = ["跟踪报告", "持续督导", "审计报告", "内控", "社会责任", "ESG", "保荐", "核查意见", "说明会", "提示性公告", "披露提示"]
        if any(token in title for token in excluded):
            return False
        if "关于披露" in title:
            return False
        if issuer_name and "：" in title:
            _: Any
            right: Any
            _, _, right = title.partition("：")
            if right and not right.startswith(issuer_name) and re.search(r"(股份有限公司|有限公司).*(年度报告|季度报告|半年度报告)", right):
                return False
        return bool(re.search(r"(年度报告|半年度报告|第一季度报告|第三季度报告|一季度报告|三季度报告|季度报告)", title))

    @staticmethod
    def _report_kind(title: str) -> str:
        if "半年度报告" in title:
            return "semiannual"
        if "年度报告" in title:
            return "annual"
        if "第一季度报告" in title or "一季度报告" in title:
            return "q1"
        if "第三季度报告" in title or "三季度报告" in title:
            return "q3"
        if "季度报告" in title:
            return "quarterly"
        return "periodic"

    @staticmethod
    def _report_period_key(report: Mapping[str, Any]) -> str:
        title: Any = str(report.get("title") or "")
        kind: Any = str(report.get("report_kind") or "periodic")
        compact: Any = re.sub(r"\s+", "", title)
        year_match: Any = re.search(r"((?:19|20)\d{2})年", compact)
        year: Any = year_match.group(1) if year_match else ""
        if year:
            if kind == "annual":
                return f"{year}-12-31"
            if kind == "semiannual":
                return f"{year}-06-30"
            if kind == "q1":
                return f"{year}-03-31"
            if kind == "q3":
                return f"{year}-09-30"
        return str(report.get("announcement_date") or "")

    @staticmethod
    def _report_variant_rank(report: Mapping[str, Any]) -> int:
        title: Any = str(report.get("title") or "")
        if "英文版" in title or re.search(r"\benglish\b", title, flags=re.I):
            return 1
        return 0

    @classmethod
    def _rank_report_candidate(cls, report: Mapping[str, Any]) -> Tuple[str, int, str]:
        return (
            str(report.get("announcement_date") or ""),
            -cls._report_variant_rank(report),
            str(report.get("title") or ""),
        )

    @classmethod
    def _select_reports_for_download(cls, reports: Sequence[Mapping[str, Any]], limit: int) -> List[Mapping[str, Any]]:
        selected: List[Mapping[str, Any]] = []
        preferred_order: Any = ["annual", "q1", "semiannual", "q3", "quarterly", "periodic"]
        seen_periods: set[Tuple[str, str]] = set()
        grouped: Dict[str, List[Mapping[str, Any]]] = {}
        for report in reports:
            kind: Any = str(report.get("report_kind") or "")
            if not report.get("pdf_url") or kind not in preferred_order:
                continue
            grouped.setdefault(kind, []).append(report)
        sorted_by_kind: Dict[str, List[Mapping[str, Any]]] = {
            kind: sorted(grouped.get(kind, []), key=cls._rank_report_candidate, reverse=True)
            for kind in preferred_order
        }

        def add_next(kind: str) -> bool:
            candidates: List[Mapping[str, Any]] = sorted_by_kind.get(kind, [])
            for report in candidates:
                period_key: Any = (kind, cls._report_period_key(report))
                if period_key in seen_periods:
                    continue
                selected.append(report)
                seen_periods.add(period_key)
                return True
            return False

        for _ in range(2):
            for kind in ("annual", "q1"):
                add_next(kind)
                if len(selected) >= limit:
                    return selected
        for kind in ("semiannual", "q3", "quarterly", "periodic"):
            add_next(kind)
            if len(selected) >= limit:
                return selected
        for kind in preferred_order:
            candidates = sorted_by_kind.get(kind, [])
            for report in candidates:
                period_key = (kind, cls._report_period_key(report))
                if report in selected or period_key in seen_periods:
                    continue
                selected.append(report)
                seen_periods.add(period_key)
                if len(selected) >= limit:
                    return selected
        for kind in preferred_order:
            candidates = sorted(grouped.get(kind, []), key=cls._rank_report_candidate, reverse=True)
            for report in candidates:
                if report not in selected:
                    selected.append(report)
                if len(selected) >= limit:
                    return selected
        return selected

    @classmethod
    def _comparable_report_coverage_ready(cls, reports: Sequence[Mapping[str, Any]]) -> bool:
        annual_periods: set[str] = set()
        q1_periods: set[str] = set()
        for report in reports:
            kind: str = str(report.get("report_kind") or "")
            period_key: str = cls._report_period_key(report)
            if kind == "annual" and period_key:
                annual_periods.add(period_key)
            elif kind == "q1" and period_key:
                q1_periods.add(period_key)
        return len(annual_periods) >= 2 and len(q1_periods) >= 2

    @staticmethod
    def _financial_period_row_key(row: Mapping[str, Any]) -> Tuple[str, str]:
        return (
            str(row.get("period") or ""),
            str(row.get("source_report_kind") or row.get("period_type") or row.get("fp") or row.get("form") or ""),
        )

    @classmethod
    def _financial_period_row_rank(cls, row: Mapping[str, Any]) -> Tuple[int, int, str, str]:
        core_fields: Any = ["revenue", "net_income", "operating_cash_flow", "assets", "liabilities", "equity"]
        core_count: Any = sum(1 for field in core_fields if row.get(field) is not None)
        title: Any = str(row.get("source_title") or row.get("title") or "")
        return (
            core_count,
            -cls._report_variant_rank({"title": title}),
            str(row.get("source_announcement_date") or row.get("filed") or ""),
            title,
        )

    @classmethod
    def _dedupe_financial_period_rows(cls, rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for row in rows:
            key: Any = cls._financial_period_row_key(row)
            if not key[0]:
                continue
            candidate: Any = dict(row)
            existing: Any = by_key.get(key)
            if existing is None or cls._financial_period_row_rank(candidate) > cls._financial_period_row_rank(existing):
                by_key[key] = candidate
        return sorted(by_key.values(), key=lambda row: str(row.get("period") or ""))

    def _attach_official_report_downloads(
        self,
        reports: List[Dict[str, Any]],
        *,
        raw_dir: Path,
        symbol: str,
        limit: int,
        errors: List[str],
    ) -> None:
        selected: Any = self._select_reports_for_download(reports, limit)
        for report in reports:
            if report not in selected:
                report["download_status"] = "NOT_SELECTED"
        for report in selected:
            url: Any = str(report.get("pdf_url") or "")
            report_kind: Any = str(report.get("report_kind") or "periodic")
            announcement_date: Any = str(report.get("announcement_date") or "")
            title: Any = str(report.get("title") or report_kind)
            filename: Any = _safe_artifact_name(f"{symbol}_{announcement_date}_{report_kind}_{title}") + ".pdf"
            try:
                payload: Any = https_bytes(
                    url,
                    user_agent=self.user_agent,
                    headers={"Referer": "https://www.cninfo.com.cn/"},
                    timeout=45,
                    max_bytes=90 * 1024 * 1024,
                )
                if not payload.startswith(b"%PDF"):
                    raise RuntimeError("downloaded artifact does not start with a PDF header")
                pdf_path: Any
                pdf_hash: Any
                pdf_path, pdf_hash = save_raw_bytes(payload, raw_dir, filename)
                report["download_status"] = "OK"
                report["pdf_path"] = pdf_path
                report["pdf_hash"] = pdf_hash
                report["pdf_size_bytes"] = len(payload)
            except Exception as exc:
                report["download_status"] = "FAILED"
                report["download_error"] = f"{type(exc).__name__}: {exc}"
                errors.append(f"official report PDF download failed for {title}: {type(exc).__name__}: {exc}")


def _first_non_empty(row: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value: Any = row.get(key)
        if value is not None and value != "":
            return value
    return None


def _first_number(row: Mapping[str, Any], keys: Sequence[str]) -> Optional[float]:
    for key in keys:
        value: Any = _safe_float(row.get(key))
        if value is not None:
            return value
    return None


def _date10(value: Any) -> Optional[str]:
    if value is None:
        return None
    text: Any = str(value).strip()
    if not text:
        return None
    return text[:10]


def _put_number(target: Dict[str, Any], key: str, value: Any) -> None:
    number: Any = _safe_float(value)
    if number is not None:
        target[key] = number


class CninfoFinancialReportsProvider(CninfoFinancialReportBase, PdfTextExtractionMixin):
    """Official CNINFO periodic-report PDF line-item adapter for A-share financials."""

    name: Any = "CNINFO_FinancialReports_L0"
    level: Any = SourceLevel.L0
    markets: Any = [Market.CN_A]
    datasets: Any = [Dataset.FINANCIALS]

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        if symbol.market != Market.CN_A:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "CNINFO financial reports only support A-share symbols")
        if dataset != Dataset.FINANCIALS:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported dataset {dataset.value}")
        raw_dir: Any = kwargs.get("raw_dir")
        try:
            download_limit: Any = int(kwargs.get("official_report_download_limit", OFFICIAL_REPORT_DOWNLOAD_LIMIT_DEFAULT) or OFFICIAL_REPORT_DOWNLOAD_LIMIT_DEFAULT)
            evidence: Any = self._locate_official_report_evidence(symbol, raw_dir=raw_dir, download_limit=download_limit)
            reports: Any = evidence.get("reports", []) if isinstance(evidence.get("reports"), list) else []
            if not reports:
                return DataResult(
                    False,
                    dataset,
                    symbol.symbol,
                    self.name,
                    self.level,
                    utc_now(),
                    data={"official_report_evidence": evidence},
                    currency=symbol.currency or "CNY",
                    errors=["CNINFO returned no periodic report PDFs for financial extraction."],
                )

            extracted_periods: List[Dict[str, Any]] = []
            extraction_errors: List[str] = []
            extraction_warnings: List[str] = []
            extraction_raw_dir: Any = Path(raw_dir) / "official_reports" if raw_dir else None
            for report in reports:
                if report.get("download_status") != "OK" or not report.get("pdf_path"):
                    continue
                extraction: Any = self._extract_cninfo_report_period(report, raw_dir=extraction_raw_dir)
                report["line_extraction"] = {
                    key: value
                    for key, value in extraction.items()
                    if key != "period"
                }
                if extraction.get("status") in {"OK", "PARTIAL"} and isinstance(extraction.get("period"), Mapping):
                    extracted_periods.append(dict(extraction["period"]))
                if extraction.get("status") != "OK":
                    extraction_errors.extend(str(error) for error in extraction.get("errors", []) or [])
                    extraction_warnings.append(
                        f"{report.get('report_kind')} {report.get('announcement_date')} extraction status={extraction.get('status')} missing={extraction.get('missing_fields')}"
                    )

            raw_extracted_period_count: Any = len(extracted_periods)
            extracted_periods = self._dedupe_financial_period_rows(extracted_periods)
            if len(extracted_periods) < raw_extracted_period_count:
                extraction_warnings.append("Duplicate official report versions were collapsed into unique financial periods.")

            core_statement_fields: Any = ["revenue", "net_income", "operating_cash_flow", "assets", "liabilities", "equity"]
            supplement_info: Dict[str, Any] = self._supplement_missing_periods_from_structured_preflight(
                symbol,
                reports=reports,
                periods=extracted_periods,
                raw_dir=raw_dir,
                core_statement_fields=core_statement_fields,
            )
            if supplement_info.get("supplemented_periods"):
                extraction_warnings.append(
                    "Structured L3 financial preflight supplemented official PDF periods that were found but not machine-readable."
                )
            extracted_periods.sort(key=lambda row: str(row.get("period") or ""))
            ok_periods: Any = [
                row for row in extracted_periods
                if all(row.get(field) is not None for field in core_statement_fields)
            ]
            latest_extracted_period: Any = extracted_periods[-1] if extracted_periods else None
            latest_period: Any = str(latest_extracted_period.get("period") or "") if latest_extracted_period else None
            latest_core_statement_missing_fields: Any = [
                field for field in core_statement_fields
                if not latest_extracted_period or latest_extracted_period.get(field) is None
            ]
            latest_core_statement_complete: Any = bool(latest_extracted_period) and not latest_core_statement_missing_fields
            latest_core_complete_period: Any = max((str(row.get("period") or "") for row in ok_periods), default=None)
            if latest_extracted_period and latest_core_statement_missing_fields:
                extraction_warnings.append(
                    f"latest period {latest_period} missing core statement fields={latest_core_statement_missing_fields}"
                )
            downloaded_reports: Any = [
                report for report in reports
                if report.get("download_status") == "OK" and report.get("pdf_path")
            ]
            extracted_financial_sector_profile: Any = any(
                isinstance(row.get("financial_sector_profile"), Mapping)
                for row in extracted_periods
            )
            financial_sector_profile_required: Any = (
                self._requires_financial_sector_profile(evidence, reports)
                or extracted_financial_sector_profile
            )
            financial_sector_profile_status: Any = self._financial_sector_profile_status(
                extracted_periods,
                required=financial_sector_profile_required,
            )
            financial_sector_profile_fallback: Any = self._financial_sector_profile_fallback(
                extracted_periods,
                required=financial_sector_profile_required,
            )
            evidence.update({
                "source": self.name,
                "source_level": self.level.value,
                "line_extraction_status": "OK" if latest_core_statement_complete and len(ok_periods) == len(extracted_periods) else ("PARTIAL" if extracted_periods else "FAILED"),
                "extracted_period_count": len(extracted_periods),
                "core_complete_period_count": len(ok_periods),
                "core_statement_fields": core_statement_fields,
                "latest_period": latest_period,
                "latest_core_statement_complete": latest_core_statement_complete,
                "latest_core_statement_missing_fields": latest_core_statement_missing_fields,
                "latest_core_complete_period": latest_core_complete_period,
                "financial_sector_profile_required": financial_sector_profile_required,
                "financial_sector_profile_status": financial_sector_profile_status,
                "financial_sector_profile_fallback": financial_sector_profile_fallback,
                "downloaded_reports": [
                    {
                        "report_kind": report.get("report_kind"),
                        "title": report.get("title"),
                        "announcement_date": report.get("announcement_date"),
                        "pdf_path": report.get("pdf_path"),
                        "pdf_hash": report.get("pdf_hash"),
                        "pdf_size_bytes": report.get("pdf_size_bytes"),
                        "line_extraction": report.get("line_extraction"),
                    }
                    for report in downloaded_reports
                ],
                "errors": list(evidence.get("errors", []) or []) + extraction_errors,
            })

            raw_path: Any
            raw_hash: Any
            raw_path = raw_hash = None
            if raw_dir:
                raw_path, raw_hash = save_raw_json(
                    {"official_report_evidence": evidence},
                    raw_dir,
                    f"{symbol.symbol}_cninfo_financial_reports_raw.json",
                )

            if not extracted_periods:
                return DataResult(
                    False,
                    dataset,
                    symbol.symbol,
                    self.name,
                    self.level,
                    utc_now(),
                    data={"official_report_evidence": evidence, "periods": []},
                    raw_path=raw_path,
                    raw_hash=raw_hash,
                    currency=symbol.currency or "CNY",
                    errors=["CNINFO official PDFs were downloaded, but no core financial statement lines could be extracted."],
                )

            output_unit: Any = self._period_unit(extracted_periods)
            return DataResult(
                True,
                dataset,
                symbol.symbol,
                self.name,
                self.level,
                utc_now(),
                as_of_date=latest_period,
                data={
                    "symbol": symbol.symbol,
                    "source": self.name,
                    "source_level": self.level.value,
                    "currency": symbol.currency or "CNY",
                    "unit": output_unit,
                    "period_basis": "CNINFO official report PDF line extraction; income and cash-flow rows are reported cumulative periods, and balance-sheet rows are period-end.",
                    "latest_period": latest_period,
                    "official_report_evidence": evidence,
                    "periods": extracted_periods,
                    "source_usage": {
                        "preferred_source": "CNINFO/SSE/SZSE/BSE periodic report PDFs",
                        "preferred_source_status": evidence.get("status"),
                        "preferred_source_records": len(reports),
                        "report_pdf_evidence_used": True,
                        "report_line_items_extracted": latest_core_statement_complete,
                        "extracted_period_count": len(extracted_periods),
                        "core_complete_period_count": len(ok_periods),
                        "core_statement_fields": core_statement_fields,
                        "latest_core_statement_complete": latest_core_statement_complete,
                        "latest_core_statement_missing_fields": latest_core_statement_missing_fields,
                        "latest_core_complete_period": latest_core_complete_period,
                        "financial_sector_profile_required": financial_sector_profile_required,
                        "financial_sector_profile_status": financial_sector_profile_status,
                        "financial_sector_profile_fallback": financial_sector_profile_fallback,
                        "structured_supplement_used": bool(supplement_info.get("supplemented_periods")),
                        "structured_supplement_source": supplement_info.get("source", ""),
                        "structured_supplemented_periods": supplement_info.get("supplemented_periods", []),
                        "structured_supplement_errors": supplement_info.get("errors", []),
                        "source_role": "L0_OFFICIAL_REPORT_LINE_ITEMS",
                        "required_ai_action": "Review CNINFO PDF line evidence, reporting period basis, unit, industry reporting fit, and missing fields before assigning S/A.",
                    },
                },
                raw_path=raw_path,
                raw_hash=raw_hash,
                unit=output_unit,
                currency=symbol.currency or "CNY",
                warnings=[
                    "CNINFO official financial report PDFs were parsed into core financial lines where machine-readable page text was available.",
                    "Use consolidated statements; do not mix parent-company statements with consolidated revenue, cash flow, assets, liabilities, or equity.",
                ] + (
                    [f"Financial-sector issuer detected; industry profile status={financial_sector_profile_status}."]
                    if financial_sector_profile_required else []
                ) + extraction_warnings,
            )
        except Exception as exc:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"CNINFO financial report fetch failed: {type(exc).__name__}: {exc}")

    def _supplement_missing_periods_from_structured_preflight(
        self,
        symbol: SymbolInfo,
        *,
        reports: Sequence[Mapping[str, Any]],
        periods: List[Dict[str, Any]],
        raw_dir: Any,
        core_statement_fields: Sequence[str],
    ) -> Dict[str, Any]:
        desired_periods: Dict[str, str] = {}
        complete_periods: set[str] = {
            str(row.get("period") or "")
            for row in periods
            if row.get("period") and all(row.get(field) is not None for field in core_statement_fields)
        }
        for report in reports:
            kind: str = str(report.get("report_kind") or "")
            if kind not in {"annual", "q1"} or report.get("download_status") != "OK":
                continue
            period_key: str = self._report_period_key(report)
            if period_key and period_key not in complete_periods:
                desired_periods[period_key] = kind
        if not desired_periods:
            return {"source": "", "supplemented_periods": [], "errors": []}

        errors: List[str] = []
        raw_payloads: Dict[str, Any] = {}
        try:
            provider: Any = EastmoneyF10FinancialsProvider()
            table_rows: Dict[str, List[Mapping[str, Any]]] = {}
            for table_name, report_name in provider.table_specs.items():
                payload: Any = provider._fetch_table(symbol.symbol, report_name, page_size=24)
                raw_payloads[table_name] = payload
                rows: List[Mapping[str, Any]] = provider._extract_rows(payload)
                if rows:
                    table_rows[table_name] = rows
            structured_periods: List[Dict[str, Any]] = provider._merge_period_rows(table_rows)
        except Exception as exc:
            return {
                "source": "Eastmoney_F10_Financials_L3",
                "supplemented_periods": [],
                "errors": [f"Eastmoney structured supplement failed: {type(exc).__name__}: {exc}"],
            }

        structured_by_period: Dict[str, Dict[str, Any]] = {
            str(row.get("period") or ""): dict(row)
            for row in structured_periods
            if row.get("period")
        }
        existing_by_period: Dict[str, Dict[str, Any]] = {
            str(row.get("period") or ""): row
            for row in periods
            if row.get("period")
        }
        supplemented_periods: List[Dict[str, Any]] = []
        for period_key, kind in desired_periods.items():
            supplement: Optional[Dict[str, Any]] = structured_by_period.get(period_key)
            if not supplement:
                errors.append(f"Eastmoney structured rows did not include required comparable period {period_key}")
                continue
            target: Optional[Dict[str, Any]] = existing_by_period.get(period_key)
            fields_added: List[str] = []
            if target is None:
                target = dict(supplement)
                target["source_report_kind"] = kind
                target["source"] = "Eastmoney_F10_Financials_L3"
                target["source_level"] = SourceLevel.L3.value
                target["source_role"] = "L3_STRUCTURED_PREFLIGHT_SUPPLEMENT"
                target["supplement_reason"] = "CNINFO official PDF was available, but core line extraction did not produce a complete comparable period."
                periods.append(target)
                fields_added = [field for field in core_statement_fields if target.get(field) is not None]
            else:
                for field, value in supplement.items():
                    if field == "period" or value in (None, ""):
                        continue
                    if target.get(field) in (None, ""):
                        target[field] = value
                        fields_added.append(field)
                target["supplemental_source"] = "Eastmoney_F10_Financials_L3"
                target["supplemental_source_level"] = SourceLevel.L3.value
                target["supplement_reason"] = "CNINFO official PDF period was partial; missing fields were supplemented from structured L3 preflight."
            supplemented_periods.append({
                "period": period_key,
                "report_kind": kind,
                "source": "Eastmoney_F10_Financials_L3",
                "fields_added": fields_added,
            })

        if raw_dir and raw_payloads:
            try:
                save_raw_json(
                    raw_payloads,
                    raw_dir,
                    f"{symbol.symbol}_eastmoney_f10_financials_supplement_raw.json",
                )
            except Exception as exc:
                errors.append(f"failed to save Eastmoney supplemental raw payloads: {type(exc).__name__}: {exc}")
        return {
            "source": "Eastmoney_F10_Financials_L3",
            "supplemented_periods": supplemented_periods,
            "errors": errors,
        }

    def _extract_cninfo_report_period(self, report: Mapping[str, Any], *, raw_dir: Optional[Path] = None) -> Dict[str, Any]:
        pdf_path: Any = str(report.get("pdf_path") or "")
        report_kind: Any = str(report.get("report_kind") or "periodic")
        title: Any = str(report.get("title") or "")
        if not pdf_path:
            return {"status": "FAILED", "errors": ["report has no downloaded pdf_path"]}

        page_bundle: Any = self._extract_pdf_pages(pdf_path, max_pages=260, timeout=90)
        if not page_bundle.get("ok"):
            return {"status": "FAILED", "errors": page_bundle.get("errors", ["PDF text extraction failed"])}
        pages: Any = page_bundle.get("pages", [])
        if not isinstance(pages, list) or not pages:
            return {"status": "FAILED", "errors": ["PDF text extraction returned no text pages"]}

        if raw_dir:
            text_name: Any = _safe_artifact_name(f"{Path(pdf_path).stem}_pdf_text") + ".txt"
            combined_text: Any = "\n\n".join(
                f"--- page {page.get('page_number')} ---\n{page.get('text') or ''}"
                for page in pages
            )
            text_path: Any
            text_hash: Any
            text_path, text_hash = save_raw_text(combined_text, raw_dir / "extracted_text", text_name)
        else:
            text_path = text_hash = None

        balance_pages: Any = self._cn_statement_pages(
            pages,
            "合并资产负债表",
            stop_titles=["母公司资产负债表", "公司资产负债表", "合并利润表", "母公司利润表", "合并现金流量表"],
            signals=["资产总计", "资产合计", "负债合计", "所有者权益", "股东权益"],
        )
        if not balance_pages:
            balance_pages = self._cn_statement_pages(
                pages,
                "Consolidated balance sheet",
                stop_titles=[
                    "Parent company balance sheet",
                    "Consolidated income statement",
                    "Parent company income statement",
                    "Consolidated cash flow statement",
                ],
                signals=["Totalassets", "Totalliabilities", "Totalequity", "Liabilities&Equity"],
            )
        income_pages: Any = self._cn_statement_pages(
            pages,
            "合并利润表",
            stop_titles=["母公司利润表", "公司利润表", "合并现金流量表", "母公司现金流量表"],
            signals=["营业总收入", "营业收入", "归属于母公司"],
        )
        if not income_pages:
            income_pages = self._cn_statement_pages(
                pages,
                "Consolidated income statement",
                stop_titles=["Parent company income statement", "Consolidated cash flow statement", "Parent company cash flow statement"],
                signals=["Totaloperatingrevenue", "Operatingrevenue", "Netprofitattributabletoownersofparentcompany"],
            )
        cashflow_pages: Any = self._cn_statement_pages(
            pages,
            "合并现金流量表",
            stop_titles=["母公司现金流量表", "公司现金流量表", "所有者权益变动表", "合并所有者权益变动表"],
            signals=["经营活动产生的现金流量净额", "经营活动产生的现金流"],
        )
        if not cashflow_pages:
            cashflow_pages = self._cn_statement_pages(
                pages,
                "Consolidated cash flow statement",
                stop_titles=["Parent company cash flow statement", "Consolidated statement of changes in equity"],
                signals=["Netcashflowsfromoperatingactivities", "Cashflowsfromoperatingactivities"],
            )
        unit: Any = self._cn_unit_from_pages(balance_pages + income_pages + cashflow_pages)

        fields: Dict[str, Any] = {}
        evidence: Dict[str, Any] = {}

        def put(field: str, value: Optional[float], source: Optional[Dict[str, Any]]) -> None:
            if value is None:
                return
            fields[field] = value
            if source:
                evidence[field] = source

        value: Any
        source: Any
        value, source = self._extract_cn_value(balance_pages, [["资产总计"]], exclude_groups=[["负债", "权益"]])
        if value is None:
            value, source = self._extract_cn_value(
                balance_pages,
                [["资产合计"], ["资产总额"]],
                exclude_groups=[["流动资产合计"], ["非流动资产合计"], ["负债", "权益"]],
            )
        if value is None:
            value, source = self._extract_cn_value(balance_pages, [["Totalassets"]], exclude_groups=[["liabilities", "equity"]])
        put("assets", value, source)
        value, source = self._extract_cn_value(balance_pages, [["负债合计"]], exclude_groups=[["流动负债合计"], ["非流动负债合计"]])
        if value is None:
            value, source = self._extract_cn_value(balance_pages, [["Totalliabilities"]], exclude_groups=[["currentliabilities"], ["non-currentliabilities"], ["liabilities&equity"]])
        put("liabilities", value, source)
        value, source = self._extract_cn_value(
            balance_pages,
            [["所有者权益", "计"], ["股东权益", "计"]],
            exclude_groups=[["归属于母公司"], ["少数股东"], ["负债", "所有者权益"]],
        )
        if value is None:
            value, source = self._extract_cn_value(balance_pages, [["Totalequity"]], exclude_groups=[["attributable"], ["liabilities&equity"]])
        put("equity", value, source)
        value, source = self._extract_cn_value(balance_pages, [["归属于母公司", "权益", "合计"]])
        if value is None:
            value, source = self._extract_cn_value(balance_pages, [["Totalequityattributabletotheparentcompany"]])
        put("parent_equity", value, source)
        value, source = self._extract_cn_value(balance_pages, [["货币资金"]])
        if value is None:
            value, source = self._extract_cn_value(balance_pages, [["Cashandbankbalances"], ["Cashandcashequivalents"]])
        put("cash", value, source)

        value, source = self._extract_cn_value(income_pages, [["其中", "营业收入"], ["营业收入"]], exclude_groups=[["营业总收入"], ["营业成本"], ["增长率"], ["比重"]])
        if value is None:
            value, source = self._extract_cn_value(income_pages, [["营业总收入"]])
        if value is None:
            value, source = self._extract_cn_value(income_pages, [["Including:Operatingrevenue"], ["Operatingrevenue"]], exclude_groups=[["Totaloperatingrevenue"], ["Operatingcost"]])
        if value is None:
            value, source = self._extract_cn_value(income_pages, [["Totaloperatingrevenue"]])
        put("revenue", value, source)
        value, source = self._extract_cn_value(income_pages, [["营业利润"]], exclude_groups=[["二、营业总成本"]])
        if value is None:
            value, source = self._extract_cn_value(income_pages, [["Operatingprofit"]])
        put("operating_income", value, source)
        value, source = self._extract_cn_value(income_pages, [["利润总额"]])
        if value is None:
            value, source = self._extract_cn_value(income_pages, [["Profitbeforetax"]])
        put("profit_before_tax", value, source)
        value, source = self._extract_cn_value(income_pages, [["五", "净利润"], ["净利润"]], exclude_groups=[["归属于母公司"], ["少数股东"], ["综合收益"]])
        if value is None:
            value, source = self._extract_cn_value(income_pages, [["Netprofit"]], exclude_groups=[["attributable"], ["continuingoperations"], ["discontinuedoperations"]])
        put("total_net_profit", value, source)
        value, source = self._extract_cn_value(income_pages, [["归属于母公司", "净利润"], ["归属于母公司股东", "净利润"]], exclude_groups=[["综合收益"]])
        if value is None:
            value, source = self._extract_cn_value(income_pages, [["Netprofitattributabletoownersofparentcompany"]])
        if value is None:
            value, source = fields.get("total_net_profit"), evidence.get("total_net_profit")
        put("net_income", value, source)

        value, source = self._extract_cn_value(cashflow_pages, [["经营活动产生的现金流量净额"], ["经营活动产生的现金流", "量净额"], ["经营活动产生的现金流量净"]])
        if value is None:
            value, source = self._extract_cn_value(cashflow_pages, [["Netcashflowsfromoperatingactivities"]])
        put("operating_cash_flow", value, source)
        value, source = self._extract_cn_value(cashflow_pages, [["投资活动产生的现金流量净额"], ["投资活动产生的现金流", "量净额"], ["投资活动产生的现金流量净"]])
        if value is None:
            value, source = self._extract_cn_value(cashflow_pages, [["Netcashflowsfrominvestingactivities"]])
        put("investing_cash_flow", value, source)
        value, source = self._extract_cn_value(cashflow_pages, [["筹资活动产生的现金流量净额"], ["筹资活动产生的现金流", "量净额"], ["筹资活动产生的现金流量净"]])
        if value is None:
            value, source = self._extract_cn_value(cashflow_pages, [["Netcashflowsfromfinancingactivities"]])
        put("financing_cash_flow", value, source)

        financial_sector_profile: Any = self._extract_financial_sector_profile(pages, unit=unit)
        period: Any = self._cn_period_from_report(report, balance_pages + income_pages + cashflow_pages)
        required: Any = ["revenue", "net_income", "operating_cash_flow", "assets", "liabilities", "equity"]
        missing: Any = [field for field in required if fields.get(field) is None]
        status: Any = "OK" if not missing else ("PARTIAL" if fields else "FAILED")
        section_pages: Any = {
            "balance": [page.get("page_number") for page in balance_pages],
            "income": [page.get("page_number") for page in income_pages],
            "cashflow": [page.get("page_number") for page in cashflow_pages],
        }
        period_row: Any = {
            "period": period,
            "period_type": report_kind,
            "source": self.name,
            "source_level": self.level.value,
            "source_report_kind": report_kind,
            "source_title": title,
            "source_announcement_date": report.get("announcement_date"),
            "currency": "CNY",
            "unit": unit,
            **fields,
            "field_evidence": evidence,
            "section_pages": section_pages,
        }
        if financial_sector_profile:
            period_row["financial_sector_profile"] = financial_sector_profile
        return {
            "status": status,
            "period": period_row if fields else None,
            "missing_fields": missing,
            "section_pages": section_pages,
            "parser": page_bundle.get("parser"),
            "parser_python": page_bundle.get("python"),
            "page_count": page_bundle.get("page_count"),
            "text_path": text_path,
            "text_hash": text_hash,
            "warnings": page_bundle.get("warnings", []),
            "errors": [] if fields else ["No consolidated core financial fields could be extracted from PDF text."],
        }

    @classmethod
    def _cn_statement_pages(
        cls,
        pages: Sequence[Mapping[str, Any]],
        title: str,
        *,
        stop_titles: Sequence[str],
        signals: Sequence[str],
        max_section_pages: int = 8,
    ) -> List[Dict[str, Any]]:
        start_index: Any = cls._cn_statement_start_index(pages, title, signals)
        if start_index is None:
            return []
        title_compact: Any = cls._compact_cn(title)
        stop_compacts: Any = [cls._compact_cn(stop) for stop in stop_titles]
        output: List[Dict[str, Any]] = []
        for page in pages[start_index:start_index + max_section_pages]:
            lines: Any = [cls._normalize_pdf_line(line) for line in str(page.get("text") or "").splitlines()]
            if not lines:
                continue
            start_line: Any = 0
            for idx, line in enumerate(lines):
                if title_compact in cls._compact_cn(line):
                    start_line = idx
                    break
            kept: List[str] = []
            for idx, line in enumerate(lines[start_line:], start=start_line):
                compact: Any = cls._compact_cn(line)
                if idx > start_line and any(stop in compact for stop in stop_compacts):
                    break
                kept.append(line)
            if kept:
                output.append({"page_number": page.get("page_number"), "text": "\n".join(kept)})
            if len(kept) < len(lines[start_line:]):
                break
        return output

    @classmethod
    def _cn_statement_start_index(cls, pages: Sequence[Mapping[str, Any]], title: str, signals: Sequence[str]) -> Optional[int]:
        title_compact: Any = cls._compact_cn(title)
        signal_compacts: Any = [cls._compact_cn(signal) for signal in signals]
        scored: List[Tuple[int, int, int]] = []
        for idx, page in enumerate(pages):
            text: Any = str(page.get("text") or "")
            compact_text: Any = cls._compact_cn(text)
            if title_compact not in compact_text:
                continue
            lines: Any = [cls._normalize_pdf_line(line) for line in text.splitlines()]
            score: Any = 0
            for line_index, line in enumerate(lines):
                compact_line: Any = cls._compact_cn(line)
                if title_compact not in compact_line:
                    continue
                score += 8
                is_continuation_page: Any = "续" in compact_line[:len(title_compact) + 8] or "continued" in compact_line
                if is_continuation_page:
                    score -= 10
                else:
                    score += 10
                if len(compact_line) <= len(title_compact) + 6:
                    score += 6
                nearby: Any = cls._compact_cn("".join(lines[line_index:line_index + 8]))
                if "项目" in nearby:
                    score += 3
                if "单位" in nearby:
                    score += 2
                break
            signal_hits: Any = sum(1 for signal in signal_compacts if signal in compact_text)
            score += signal_hits * 2
            if "财务报表附注" in text or "附注" in text and score < 14:
                score -= 4
            scored.append((score, idx, signal_hits))
        if not scored:
            return None
        candidate_pool: Any = [item for item in scored if item[2] > 0] or scored
        best_score: Any = max(score for score, _, _ in candidate_pool)
        high_confidence: Any = [
            (score, idx)
            for score, idx, _ in candidate_pool
            if score >= max(20, best_score - 4)
        ]
        if high_confidence:
            return min(idx for _, idx in high_confidence)
        scored.sort(key=lambda item: (-item[0], item[1]))
        return scored[0][1]

    @classmethod
    def _extract_cn_value(
        cls,
        pages: Sequence[Mapping[str, Any]],
        label_groups: Sequence[Sequence[str]],
        *,
        expected_columns: int = 2,
        value_index: int = 0,
        exclude_groups: Sequence[Sequence[str]] = (),
    ) -> Tuple[Optional[float], Optional[Dict[str, Any]]]:
        compact_groups: Any = [[cls._compact_cn(label) for label in group] for group in label_groups]
        compact_excludes: Any = [[cls._compact_cn(label) for label in group] for group in exclude_groups]
        for page in pages:
            lines: Any = [cls._normalize_pdf_line(line) for line in str(page.get("text") or "").splitlines()]
            for idx in range(len(lines)):
                for width in range(1, 5):
                    window: Any = lines[idx:idx + width]
                    if not window:
                        continue
                    joined: Any = " ".join(window)
                    compact: Any = cls._compact_cn(joined)
                    first_line: Any = cls._compact_cn(window[0])
                    if not any(
                        group
                        and group[0] in first_line
                        and all(label in compact for label in group)
                        for group in compact_groups
                    ):
                        continue
                    if any(all(label in compact for label in group) for group in compact_excludes):
                        continue
                    values: Any = cls._cn_line_values(joined, expected_columns=expected_columns)
                    if len(values) <= value_index or values[value_index] is None:
                        continue
                    return values[value_index], {
                        "page_number": page.get("page_number"),
                        "line": joined,
                        "value_index": value_index,
                    }
        return None, None

    @classmethod
    def _cn_line_values(cls, line: str, *, expected_columns: int) -> List[Optional[float]]:
        values: List[float] = []
        for token in cls._pdf_number_tokens(line):
            value: Any = cls._parse_pdf_number(token)
            if value is None:
                continue
            token_text: Any = token.strip("()")
            is_small_index: Any = (
                "," not in token_text
                and "." not in token_text
                and token_text.lstrip("-").isdigit()
                and abs(value) <= 100
            )
            if is_small_index:
                continue
            values.append(value)
        if len(values) > expected_columns:
            values = values[-expected_columns:]
        return values

    @classmethod
    def _extract_financial_sector_profile(cls, pages: Sequence[Mapping[str, Any]], *, unit: str) -> Optional[Dict[str, Any]]:
        sector: Any = cls._financial_sector_kind_from_pages(pages)
        if not sector:
            return None
        extractors: Any = {
            "insurance": cls._extract_insurance_profile,
            "securities": cls._extract_securities_profile,
            "bank": cls._extract_bank_profile,
        }
        extractor: Any = extractors.get(sector)
        return extractor(pages, unit=unit) if extractor else None

    @classmethod
    def _financial_sector_kind_from_pages(cls, pages: Sequence[Mapping[str, Any]]) -> Optional[str]:
        text: Any = cls._compact_cn(" ".join(str(page.get("text") or "") for page in pages[:80]))
        if any(token in text for token in ["保险合同负债", "偿付能力", "保险服务收入", "内含价值", "合同服务边际"]):
            return "insurance"
        if any(token in text for token in ["净资本", "风险覆盖率", "代理买卖证券款", "证券及其衍生品/净资本", "证券及证券衍生品净资本"]):
            return "securities"
        if any(token in text for token in ["不良贷款率", "拨备覆盖率", "客户存款总额", "贷款和垫款总额"]):
            return "bank"
        return None

    @classmethod
    def _extract_bank_profile(cls, pages: Sequence[Mapping[str, Any]], *, unit: str) -> Optional[Dict[str, Any]]:
        amount_specs: Any = {
            "net_interest_income": [["净利息收入"]],
            "non_interest_income": [["非利息净收入总额"], ["非利息净收入"]],
            "loans_and_advances": [["贷款和垫款总额"]],
            "customer_deposits": [["客户存款总额"], ["客户存款"]],
        }
        percent_specs: Any = {
            "net_interest_margin_pct": [["净利息收益率"], ["净息差"]],
            "non_performing_loan_ratio_pct": [["不良贷款率"]],
            "provision_coverage_ratio_pct": [["拨备覆盖率"]],
            "capital_adequacy_ratio_pct": [["资本充足率"]],
            "tier1_capital_adequacy_ratio_pct": [["一级资本充足率"]],
            "core_tier1_capital_adequacy_ratio_pct": [["核心一级资本充足率"]],
        }
        metrics: Dict[str, Any] = {}
        evidence: Dict[str, Any] = {}
        for field, groups in amount_specs.items():
            value: Any
            source: Any
            value, source = cls._extract_bank_amount(pages, groups)
            if value is None:
                continue
            metrics[field] = value
            if source:
                evidence[field] = source
        for field, groups in percent_specs.items():
            value, source = cls._extract_bank_percent(pages, groups)
            if value is None:
                continue
            metrics[field] = value
            if source:
                evidence[field] = source

        required: Any = [
            "net_interest_income",
            "net_interest_margin_pct",
            "non_performing_loan_ratio_pct",
            "provision_coverage_ratio_pct",
            "capital_adequacy_ratio_pct",
            "core_tier1_capital_adequacy_ratio_pct",
            "customer_deposits",
            "loans_and_advances",
        ]
        if not any(field in metrics for field in required):
            return None
        sanity_warnings: Any = cls._bank_profile_sanity_warnings(metrics)
        missing: Any = [field for field in required if field not in metrics]
        return {
            "sector": "bank",
            "status": "OK" if not missing and not sanity_warnings else "PARTIAL",
            "unit": unit,
            "metrics": metrics,
            "missing_metrics": missing,
            "sanity_warnings": sanity_warnings,
            "field_evidence": evidence,
        }

    @classmethod
    def _extract_securities_profile(cls, pages: Sequence[Mapping[str, Any]], *, unit: str) -> Optional[Dict[str, Any]]:
        amount_specs: Any = {
            "net_capital": [["净资本"]],
            "net_assets_parent": [["净资产"]],
            "customer_fund_deposits": [["客户资金存款"]],
            "agency_securities_liabilities": [["代理买卖证券款"]],
            "net_fee_and_commission_income": [["手续费及佣金净收入"]],
            "investment_income": [["投资收益"]],
            "net_interest_income": [["利息净收入"]],
        }
        percent_specs: Any = {
            "risk_coverage_ratio_pct": [["风险覆盖率"]],
            "capital_leverage_ratio_pct": [["资本杠杆率"]],
            "liquidity_coverage_ratio_pct": [["流动性覆盖率"]],
            "net_stable_funding_ratio_pct": [["净稳定资金率"]],
            "net_capital_to_net_assets_pct": [["净资本", "净资产"]],
            "net_capital_to_liabilities_pct": [["净资本", "负债"]],
            "proprietary_equity_to_net_capital_pct": [["自营权益类证券", "净资本"]],
            "proprietary_non_equity_to_net_capital_pct": [["自营非权益类证券", "净资本"]],
        }
        metrics: Any
        evidence: Any
        metrics, evidence = cls._extract_profile_metrics(pages, amount_specs, percent_specs)
        required: Any = [
            "net_capital",
            "risk_coverage_ratio_pct",
            "capital_leverage_ratio_pct",
            "liquidity_coverage_ratio_pct",
            "net_stable_funding_ratio_pct",
        ]
        if not any(field in metrics for field in required):
            return None
        sanity_warnings: Any = cls._profile_sanity_warnings(
            metrics,
            amount_fields=["net_capital", "net_assets_parent", "customer_fund_deposits", "agency_securities_liabilities"],
            ratio_bounds={
                "risk_coverage_ratio_pct": (50.0, 1000.0),
                "capital_leverage_ratio_pct": (1.0, 100.0),
                "liquidity_coverage_ratio_pct": (50.0, 1000.0),
                "net_stable_funding_ratio_pct": (50.0, 1000.0),
                "net_capital_to_net_assets_pct": (1.0, 100.0),
                "net_capital_to_liabilities_pct": (1.0, 100.0),
                "proprietary_equity_to_net_capital_pct": (0.0, 1000.0),
                "proprietary_non_equity_to_net_capital_pct": (0.0, 1000.0),
            },
        )
        missing: Any = [field for field in required if field not in metrics]
        return {
            "sector": "securities",
            "status": "OK" if not missing and not sanity_warnings else "PARTIAL",
            "unit": unit,
            "metrics": metrics,
            "missing_metrics": missing,
            "sanity_warnings": sanity_warnings,
            "field_evidence": evidence,
        }

    @classmethod
    def _extract_insurance_profile(cls, pages: Sequence[Mapping[str, Any]], *, unit: str) -> Optional[Dict[str, Any]]:
        amount_specs: Any = {
            "insurance_service_revenue": [["保险服务收入"]],
            "insurance_contract_liabilities": [["保险合同负债"]],
            "operating_profit_parent": [["归属于母公司股东", "营运利润"]],
            "embedded_value": [["内含价值"]],
            "new_business_value": [["新业务价值"]],
            "contract_service_margin": [["合同服务边际余额"], ["合同服务边际"]],
        }
        percent_specs: Any = {
            "core_solvency_ratio_pct": [["核心偿付能力充足率"]],
            "comprehensive_solvency_ratio_pct": [["综合偿付能力充足率"]],
            "combined_ratio_pct": [["综合成本率"]],
            "operating_roe_pct": [["营运ROE"]],
            "net_investment_yield_pct": [["净投资收益率"]],
            "comprehensive_investment_yield_pct": [["综合投资收益率"]],
        }
        metrics: Any
        evidence: Any
        metrics, evidence = cls._extract_profile_metrics(
            pages,
            amount_specs,
            percent_specs,
            amount_selects={"insurance_service_revenue": "max_abs"},
        )
        required: Any = [
            "insurance_service_revenue",
            "insurance_contract_liabilities",
            "core_solvency_ratio_pct",
            "comprehensive_solvency_ratio_pct",
        ]
        if not any(field in metrics for field in required):
            return None
        sanity_warnings: Any = cls._profile_sanity_warnings(
            metrics,
            amount_fields=[
                "insurance_service_revenue",
                "insurance_contract_liabilities",
                "operating_profit_parent",
                "embedded_value",
                "new_business_value",
                "contract_service_margin",
            ],
            ratio_bounds={
                "core_solvency_ratio_pct": (50.0, 500.0),
                "comprehensive_solvency_ratio_pct": (80.0, 600.0),
                "combined_ratio_pct": (0.0, 150.0),
                "operating_roe_pct": (-100.0, 100.0),
                "net_investment_yield_pct": (-50.0, 50.0),
                "comprehensive_investment_yield_pct": (-50.0, 50.0),
            },
        )
        missing: Any = [field for field in required if field not in metrics]
        return {
            "sector": "insurance",
            "status": "OK" if not missing and not sanity_warnings else "PARTIAL",
            "unit": unit,
            "metrics": metrics,
            "missing_metrics": missing,
            "sanity_warnings": sanity_warnings,
            "field_evidence": evidence,
        }

    @classmethod
    def _extract_profile_metrics(
        cls,
        pages: Sequence[Mapping[str, Any]],
        amount_specs: Mapping[str, Sequence[Sequence[str]]],
        percent_specs: Mapping[str, Sequence[Sequence[str]]],
        *,
        amount_selects: Optional[Mapping[str, str]] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        metrics: Dict[str, Any] = {}
        evidence: Dict[str, Any] = {}
        for field, groups in amount_specs.items():
            value: Any
            source: Any
            value, source = cls._extract_bank_amount(
                pages,
                groups,
                select=str((amount_selects or {}).get(field) or "first"),
            )
            if value is None:
                continue
            metrics[field] = value
            if source:
                evidence[field] = source
        for field, groups in percent_specs.items():
            value, source = cls._extract_bank_percent(pages, groups)
            if value is None:
                continue
            metrics[field] = value
            if source:
                evidence[field] = source
        return metrics, evidence

    @classmethod
    def _extract_bank_amount(
        cls,
        pages: Sequence[Mapping[str, Any]],
        label_groups: Sequence[Sequence[str]],
        *,
        exclude_groups: Sequence[Sequence[str]] = (),
        select: str = "first",
    ) -> Tuple[Optional[float], Optional[Dict[str, Any]]]:
        excluded: Any = list(exclude_groups) + [["占营业收入百分比"], ["占比"], ["比例"], ["平均余额"], ["日均余额"], ["利息支出"], ["亿元"]]
        return cls._extract_bank_metric(
            pages,
            label_groups,
            value_kind="amount",
            exclude_groups=excluded,
            select=select,
        )

    @classmethod
    def _extract_bank_percent(
        cls,
        pages: Sequence[Mapping[str, Any]],
        label_groups: Sequence[Sequence[str]],
        *,
        exclude_groups: Sequence[Sequence[str]] = (),
    ) -> Tuple[Optional[float], Optional[Dict[str, Any]]]:
        return cls._extract_bank_metric(
            pages,
            label_groups,
            value_kind="percent",
            exclude_groups=exclude_groups,
            select="first",
        )

    @classmethod
    def _extract_bank_metric(
        cls,
        pages: Sequence[Mapping[str, Any]],
        label_groups: Sequence[Sequence[str]],
        *,
        value_kind: str,
        exclude_groups: Sequence[Sequence[str]] = (),
        select: str = "first",
    ) -> Tuple[Optional[float], Optional[Dict[str, Any]]]:
        compact_groups: Any = [[cls._compact_cn(label) for label in group] for group in label_groups]
        compact_excludes: Any = [[cls._compact_cn(label) for label in group] for group in exclude_groups]
        matches: List[Tuple[float, Dict[str, Any]]] = []
        for page in pages:
            lines: Any = [cls._normalize_pdf_line(line) for line in str(page.get("text") or "").splitlines()]
            for line in lines:
                compact: Any = cls._compact_cn(line)
                leading: Any = cls._leading_metric_compact(compact)
                matched_group: Any = next(
                    (
                        group
                        for group in compact_groups
                        if group
                        and leading.startswith(group[0])
                        and all(label in compact for label in group)
                    ),
                    None,
                )
                if not matched_group:
                    continue
                if any(all(label in compact for label in group) for group in compact_excludes):
                    continue
                tokens: Any = cls._bank_metric_values(line, value_kind=value_kind)
                if not tokens:
                    continue
                source: Any = {
                    "page_number": page.get("page_number"),
                    "line": line,
                    "value_index": 0,
                    "value_kind": value_kind,
                    "label_group": matched_group,
                }
                if select == "max_abs":
                    matches.append((tokens[0], source))
                    continue
                return tokens[0], source
        if select == "max_abs" and matches:
            return max(matches, key=lambda item: abs(item[0]))
        return None, None

    @classmethod
    def _bank_metric_values(cls, line: str, *, value_kind: str) -> List[float]:
        values: List[float] = []
        for token in cls._pdf_number_tokens(line):
            value: Any = cls._parse_pdf_number(token)
            if value is None:
                continue
            token_text: Any = token.strip("()")
            is_small_index: Any = (
                "," not in token_text
                and "." not in token_text
                and token_text.lstrip("-").isdigit()
                and abs(value) <= 100
            )
            if is_small_index:
                continue
            if value_kind == "amount":
                if "," not in token_text and abs(value) < 1000:
                    continue
            elif value_kind == "percent":
                if "," in token_text or abs(value) > 1000:
                    continue
            values.append(value)
        return values

    @staticmethod
    def _leading_metric_compact(compact_line: str) -> str:
        return compact_line.lstrip("-－—–·•*")

    @staticmethod
    def _bank_profile_sanity_warnings(metrics: Mapping[str, Any]) -> List[str]:
        warnings: List[str] = []

        def number(key: str) -> Optional[float]:
            value: Any = metrics.get(key)
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                return float(value)
            return None

        for key in ["net_interest_income", "non_interest_income", "loans_and_advances", "customer_deposits"]:
            value: Any = number(key)
            if value is not None and abs(value) < 1000:
                warnings.append(f"{key} is too small for a reported bank amount: {value}")

        bounded_specs: Any = {
            "net_interest_margin_pct": (0.0, 10.0),
            "non_performing_loan_ratio_pct": (0.0, 20.0),
            "provision_coverage_ratio_pct": (20.0, 1000.0),
            "capital_adequacy_ratio_pct": (5.0, 40.0),
            "tier1_capital_adequacy_ratio_pct": (5.0, 40.0),
            "core_tier1_capital_adequacy_ratio_pct": (5.0, 40.0),
        }
        for key, (lower, upper) in bounded_specs.items():
            value = number(key)
            if value is not None and not (lower <= value <= upper):
                warnings.append(f"{key} is outside the expected bank ratio range {lower}-{upper}: {value}")

        core: Any = number("core_tier1_capital_adequacy_ratio_pct")
        tier1: Any = number("tier1_capital_adequacy_ratio_pct")
        total: Any = number("capital_adequacy_ratio_pct")
        if core is not None and tier1 is not None and tier1 + 1e-9 < core:
            warnings.append("tier1_capital_adequacy_ratio_pct is below core_tier1_capital_adequacy_ratio_pct")
        if tier1 is not None and total is not None and total + 1e-9 < tier1:
            warnings.append("capital_adequacy_ratio_pct is below tier1_capital_adequacy_ratio_pct")
        if core is not None and total is not None and total + 1e-9 < core:
            warnings.append("capital_adequacy_ratio_pct is below core_tier1_capital_adequacy_ratio_pct")

        return warnings

    @staticmethod
    def _profile_sanity_warnings(
        metrics: Mapping[str, Any],
        *,
        amount_fields: Sequence[str],
        ratio_bounds: Mapping[str, Tuple[float, float]],
    ) -> List[str]:
        warnings: List[str] = []

        def number(key: str) -> Optional[float]:
            value: Any = metrics.get(key)
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                return float(value)
            return None

        for key in amount_fields:
            value: Any = number(key)
            if value is not None and abs(value) < 1000:
                warnings.append(f"{key} is too small for a reported financial-sector amount: {value}")

        for key, (lower, upper) in ratio_bounds.items():
            value = number(key)
            if value is not None and not (lower <= value <= upper):
                warnings.append(f"{key} is outside the expected financial-sector ratio range {lower}-{upper}: {value}")

        return warnings

    @staticmethod
    def _financial_sector_profile_status(periods: Sequence[Mapping[str, Any]], *, required: bool) -> str:
        if not required:
            return "not_applicable"
        sorted_periods: Any = sorted(periods, key=lambda row: str(row.get("period") or ""))
        profiles: Any = []
        for period in sorted_periods:
            profile: Any = period.get("financial_sector_profile")
            if isinstance(profile, Mapping):
                profiles.append(profile)
        latest_period: Any = sorted_periods[-1] if sorted_periods else None
        latest_profile: Any = (
            latest_period.get("financial_sector_profile")
            if isinstance(latest_period, Mapping) and isinstance(latest_period.get("financial_sector_profile"), Mapping)
            else None
        )
        if latest_profile and latest_profile.get("status") == "OK":
            return "OK"
        if profiles:
            return "PARTIAL"
        return "FAILED"

    @staticmethod
    def _financial_sector_profile_fallback(periods: Sequence[Mapping[str, Any]], *, required: bool) -> Dict[str, Any]:
        if not required:
            return {"available": False, "reason": "not_applicable"}
        sorted_periods: Any = sorted(periods, key=lambda row: str(row.get("period") or ""))
        if not sorted_periods:
            return {"available": False, "reason": "no_periods"}
        latest_period: Mapping[str, Any] = sorted_periods[-1]
        latest_profile: Any = latest_period.get("financial_sector_profile")
        if isinstance(latest_profile, Mapping) and latest_profile.get("status") == "OK":
            return {"available": False, "reason": "latest_profile_ok"}
        for period in reversed(sorted_periods[:-1]):
            profile: Any = period.get("financial_sector_profile")
            if isinstance(profile, Mapping) and profile.get("status") == "OK":
                return {
                    "available": True,
                    "stale": True,
                    "fallback_period": str(period.get("period") or ""),
                    "latest_period": str(latest_period.get("period") or ""),
                    "sector": str(profile.get("sector") or ""),
                    "missing_latest_metrics": (
                        list(latest_profile.get("missing_metrics", []))
                        if isinstance(latest_profile, Mapping) and isinstance(latest_profile.get("missing_metrics"), list)
                        else []
                    ),
                    "reason": "latest_profile_partial_but_prior_profile_ok",
                }
        return {"available": False, "reason": "no_prior_ok_profile"}

    @classmethod
    def _cn_period_from_report(cls, report: Mapping[str, Any], section_pages: Sequence[Mapping[str, Any]]) -> str:
        text: Any = " ".join([str(report.get("title") or "")] + [str(page.get("text") or "") for page in section_pages[:3]])
        match: Any = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", text)
        if match:
            year: Any
            month: Any
            day: Any
            year, month, day = match.groups()
            return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        title: Any = str(report.get("title") or "")
        year_match: Any = re.search(r"(\d{4})年", title)
        year = int(year_match.group(1)) if year_match else dt.datetime.now().year
        report_kind: Any = str(report.get("report_kind") or "")
        if report_kind == "q1":
            return f"{year:04d}-03-31"
        if report_kind == "semiannual":
            return f"{year:04d}-06-30"
        if report_kind == "q3":
            return f"{year:04d}-09-30"
        return f"{year:04d}-12-31"

    @classmethod
    def _cn_unit_from_pages(cls, pages: Sequence[Mapping[str, Any]]) -> str:
        text: Any = cls._compact_cn(" ".join(str(page.get("text") or "") for page in pages[:3]))
        if "人民币百万元" in text or "单位:百万元" in text or "单位:人民币百万元" in text:
            return "million_yuan"
        if "人民币千元" in text or "单位:千元" in text or "单位:人民币千元" in text:
            return "thousand_yuan"
        if "人民币万元" in text or "单位:万元" in text or "单位:人民币万元" in text:
            return "ten_thousand_yuan"
        return "yuan"

    @staticmethod
    def _period_unit(periods: Sequence[Mapping[str, Any]]) -> str:
        units: Any = {str(period.get("unit") or "") for period in periods if period.get("unit")}
        if len(units) == 1:
            return next(iter(units))
        if units:
            return "mixed"
        return "yuan"

    @classmethod
    def _requires_financial_sector_profile(
        cls,
        evidence: Mapping[str, Any],
        reports: Sequence[Mapping[str, Any]],
    ) -> bool:
        text_parts: Any = [str(evidence.get("name") or "")]
        text_parts.extend(str(report.get("title") or "") for report in reports)
        compact: Any = cls._compact_cn(" ".join(text_parts))
        return any(token in compact for token in ["银行", "保险", "证券", "信托", "期货", "券商"])

    @staticmethod
    def _compact_cn(text: str) -> str:
        normalized: Any = (
            text.replace("：", ":")
            .replace("（", "(")
            .replace("）", ")")
            .replace("－", "-")
            .replace("—", "-")
            .replace(" ", "")
            .lower()
        )
        return re.sub(r"\s+", "", normalized)


class EastmoneyF10FinancialsProvider(CninfoFinancialReportBase):
    """Eastmoney F10 L3 structured preflight for A-share financial statements.

    The adapter records official periodic-report evidence when available and
    exposes cumulative income/cash-flow plus period-end balance data. Final S/A
    conclusions require L0/L1 verification of the key financial lines.
    """

    name: Any = "Eastmoney_F10_Financials_L3"
    level: Any = SourceLevel.L3
    markets: Any = [Market.CN_A]
    datasets: Any = [Dataset.FINANCIALS]
    user_agent: Any = "Mozilla/5.0 serenity-chan-stock-skill/0.1"
    api_url: Any = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
    referer: Any = "https://emweb.securities.eastmoney.com/"
    table_specs: Any = {
        "income": "RPT_F10_FINANCE_GINCOME",
        "balance": "RPT_F10_FINANCE_GBALANCE",
        "cashflow": "RPT_F10_FINANCE_GCASHFLOW",
    }

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        if symbol.market != Market.CN_A:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Eastmoney F10 financials only support A-share symbols")
        if dataset != Dataset.FINANCIALS:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported dataset {dataset.value}")

        official_report_evidence: Any = self._locate_official_report_evidence(
            symbol,
            raw_dir=kwargs.get("raw_dir"),
            download_limit=int(kwargs.get("official_report_download_limit", OFFICIAL_REPORT_DOWNLOAD_LIMIT_DEFAULT) or OFFICIAL_REPORT_DOWNLOAD_LIMIT_DEFAULT),
        )
        page_size: Any = int(kwargs.get("page_size", 16) or 16)
        raw_payloads: Dict[str, Any] = {"official_report_evidence": official_report_evidence}
        table_rows: Dict[str, List[Mapping[str, Any]]] = {}
        warnings: List[str] = [
            "Eastmoney F10 provides L3 structured preflight financial data; verify important conclusions against CNINFO/exchange report PDFs or L1 databases.",
            "A-share income and cash-flow fields use reported cumulative statement periods; do not treat interim periods as standalone quarters without conversion.",
        ]
        if official_report_evidence.get("status") != "OK":
            warnings.append("Official periodic-report evidence status is not OK; keep the financial evidence cap at B.")
        errors: List[str] = []

        for table_name, report_name in self.table_specs.items():
            try:
                payload: Any = self._fetch_table(symbol.symbol, report_name, page_size=page_size)
            except Exception as exc:
                errors.append(f"{table_name}: {type(exc).__name__}: {exc}")
                continue
            raw_payloads[table_name] = payload
            rows: Any = self._extract_rows(payload)
            if rows:
                table_rows[table_name] = rows
            else:
                errors.append(f"{table_name}: no rows returned")

        if "income" not in table_rows or "balance" not in table_rows or "cashflow" not in table_rows:
            if not table_rows:
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Eastmoney F10 returned no usable financial tables: " + " | ".join(errors))
            warnings.append("One or more Eastmoney F10 financial tables were unavailable: " + " | ".join(errors))

        periods: Any = self._merge_period_rows(table_rows)
        if not periods:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Eastmoney F10 rows could not be normalized")

        raw_path: Any
        raw_hash: Any
        raw_path = raw_hash = None
        raw_dir: Any = kwargs.get("raw_dir")
        if raw_dir:
            raw_path, raw_hash = save_raw_json(
                raw_payloads,
                raw_dir,
                f"{symbol.symbol}_eastmoney_f10_financials_raw.json",
            )

        latest_period: Any = max((str(row.get("period") or "") for row in periods), default=None)
        latest_notice: Any = max((str(row.get("notice_date") or "") for row in periods if row.get("notice_date")), default=None)
        return DataResult(
            True,
            Dataset.FINANCIALS,
            symbol.symbol,
            self.name,
            self.level,
            utc_now(),
            as_of_date=latest_period,
            data={
                "symbol": symbol.symbol,
                "source": self.name,
                "source_level": self.level.value,
                "currency": symbol.currency or "CNY",
                "unit": "yuan",
                "period_basis": "A-share reported cumulative statement periods for income and cash flow; balance sheet values are period-end.",
                "latest_period": latest_period,
                "latest_notice_date": latest_notice,
                "official_report_evidence": official_report_evidence,
                "source_usage": {
                    "preferred_source": "CNINFO/SSE/SZSE/BSE annual or quarterly report PDF",
                    "preferred_source_status": official_report_evidence.get("status"),
                    "preferred_source_records": len(official_report_evidence.get("reports", []) or []),
                    "structured_source": self.name,
                    "structured_source_level": self.level.value,
                    "structured_preflight_used": True,
                    "source_role": "L3_STRUCTURED_PREFLIGHT",
                    "source_policy_reason": "Official periodic-report evidence anchors disclosure provenance; Eastmoney F10 supplies L3 structured preflight fields under the source policy.",
                    "required_ai_action": "Compare key financial lines with official PDFs or L1 exports before assigning S/A.",
                },
                "periods": periods,
            },
            raw_path=raw_path,
            raw_hash=raw_hash,
            unit="yuan",
            currency=symbol.currency or "CNY",
            warnings=warnings,
        )

    def _fetch_table(self, secucode: str, report_name: str, *, page_size: int) -> Mapping[str, Any]:
        params: Any = urllib.parse.urlencode({
            "reportName": report_name,
            "columns": "ALL",
            "filter": f'(SECUCODE="{secucode}")',
            "pageNumber": "1",
            "pageSize": str(page_size),
            "sortTypes": "-1",
            "sortColumns": "REPORT_DATE",
            "source": "HSF10",
            "client": "PC",
        })
        payload: Any = https_json(
            f"{self.api_url}?{params}",
            user_agent=self.user_agent,
            headers={"Referer": self.referer},
        )
        if not isinstance(payload, Mapping):
            raise RuntimeError("Eastmoney response is not a JSON object")
        if payload.get("success") is False:
            raise RuntimeError(str(payload.get("message") or "Eastmoney returned success=false"))
        return payload

    @staticmethod
    def _extract_rows(payload: Mapping[str, Any]) -> List[Mapping[str, Any]]:
        result: Any = payload.get("result") if isinstance(payload.get("result"), Mapping) else {}
        rows: Any = result.get("data") if isinstance(result, Mapping) else []
        return [row for row in rows if isinstance(row, Mapping)]

    def _merge_period_rows(self, table_rows: Mapping[str, Sequence[Mapping[str, Any]]]) -> List[Dict[str, Any]]:
        periods: Dict[str, Dict[str, Any]] = {}

        for row in table_rows.get("income", []):
            period: Any = _date10(row.get("REPORT_DATE"))
            if not period:
                continue
            target: Any = periods.setdefault(period, {"period": period})
            self._copy_common_fields(target, row)
            revenue: Any = _first_number(row, ["TOTAL_OPERATE_INCOME", "OPERATE_INCOME"])
            operating_cost: Any = _first_number(row, ["OPERATE_COST", "TOTAL_OPERATE_COST"])
            _put_number(target, "revenue", revenue)
            _put_number(target, "operating_income", _first_number(row, ["OPERATE_PROFIT"]))
            _put_number(target, "net_income", _first_number(row, ["PARENT_NETPROFIT", "NETPROFIT"]))
            _put_number(target, "net_profit", target.get("net_income"))
            _put_number(target, "total_net_profit", _first_number(row, ["NETPROFIT"]))
            _put_number(target, "operating_cost", operating_cost)
            _put_number(target, "research_expense", _first_number(row, ["RESEARCH_EXPENSE", "ME_RESEARCH_EXPENSE"]))
            _put_number(target, "sales_expense", _first_number(row, ["SALE_EXPENSE"]))
            _put_number(target, "management_expense", _first_number(row, ["MANAGE_EXPENSE"]))
            _put_number(target, "finance_expense", _first_number(row, ["FINANCE_EXPENSE"]))
            _put_number(target, "basic_eps", _first_number(row, ["BASIC_EPS"]))
            if revenue is not None and operating_cost is not None:
                target["gross_profit"] = revenue - operating_cost

        for row in table_rows.get("balance", []):
            period = _date10(row.get("REPORT_DATE"))
            if not period:
                continue
            target = periods.setdefault(period, {"period": period})
            self._copy_common_fields(target, row)
            assets: Any = _first_number(row, ["TOTAL_ASSETS"])
            liabilities: Any = _first_number(row, ["TOTAL_LIABILITIES"])
            equity: Any = _first_number(row, ["TOTAL_EQUITY"])
            _put_number(target, "assets", assets)
            _put_number(target, "total_assets", assets)
            _put_number(target, "liabilities", liabilities)
            _put_number(target, "total_liabilities", liabilities)
            _put_number(target, "equity", equity)
            _put_number(target, "total_equity", equity)
            _put_number(target, "parent_equity", _first_number(row, ["TOTAL_PARENT_EQUITY"]))
            _put_number(target, "cash", _first_number(row, ["MONETARYFUNDS"]))
            _put_number(target, "accounts_receivable", _first_number(row, ["ACCOUNTS_RECE"]))
            _put_number(target, "notes_and_accounts_receivable", _first_number(row, ["NOTE_ACCOUNTS_RECE"]))
            _put_number(target, "inventory", _first_number(row, ["INVENTORY"]))
            _put_number(target, "goodwill", _first_number(row, ["GOODWILL"]))
            _put_number(target, "current_assets", _first_number(row, ["TOTAL_CURRENT_ASSETS"]))
            _put_number(target, "current_liabilities", _first_number(row, ["TOTAL_CURRENT_LIAB"]))
            _put_number(target, "noncurrent_assets", _first_number(row, ["TOTAL_NONCURRENT_ASSETS"]))
            _put_number(target, "noncurrent_liabilities", _first_number(row, ["TOTAL_NONCURRENT_LIAB"]))
            _put_number(target, "share_capital", _first_number(row, ["SHARE_CAPITAL"]))
            _put_number(target, "short_term_borrowings", _first_number(row, ["SHORT_LOAN"]))
            _put_number(target, "bonds_payable", _first_number(row, ["BOND_PAYABLE"]))

        for row in table_rows.get("cashflow", []):
            period = _date10(row.get("REPORT_DATE"))
            if not period:
                continue
            target = periods.setdefault(period, {"period": period})
            self._copy_common_fields(target, row)
            ocf: Any = _first_number(row, ["NETCASH_OPERATE", "NETCASH_OPERATENOTE"])
            capex: Any = _first_number(row, ["CONSTRUCT_LONG_ASSET"])
            _put_number(target, "operating_cash_flow", ocf)
            _put_number(target, "investing_cash_flow", _first_number(row, ["NETCASH_INVEST"]))
            _put_number(target, "financing_cash_flow", _first_number(row, ["NETCASH_FINANCE"]))
            _put_number(target, "cash_and_equivalents_end", _first_number(row, ["END_CCE"]))
            _put_number(target, "sales_cash_received", _first_number(row, ["SALES_SERVICES"]))
            _put_number(target, "cash_paid_for_goods_services", _first_number(row, ["BUY_SERVICES"]))
            _put_number(target, "capital_expenditure_cash_outflow", capex)
            if ocf is not None and capex is not None:
                target["free_cash_flow_after_capex"] = ocf - capex

        rows: Any = list(periods.values())
        rows.sort(key=lambda item: str(item.get("period") or ""))
        return rows[-16:]

    @staticmethod
    def _copy_common_fields(target: Dict[str, Any], row: Mapping[str, Any]) -> None:
        for output_key, candidates in {
            "security_name": ["SECURITY_NAME_ABBR"],
            "report_type": ["REPORT_TYPE"],
            "report_date_name": ["REPORT_DATE_NAME"],
            "notice_date": ["NOTICE_DATE"],
            "update_date": ["UPDATE_DATE"],
            "currency": ["CURRENCY"],
        }.items():
            value: Any = _first_non_empty(row, candidates)
            if value is None:
                continue
            if output_key.endswith("_date"):
                value = _date10(value)
            target.setdefault(output_key, value)


class HkexNewsBase:
    level: Any = SourceLevel.L0
    markets: Any = [Market.HK]
    user_agent: Any = "Mozilla/5.0"
    base_url: Any = "https://www1.hkexnews.hk"
    active_stock_url: Any = "https://www1.hkexnews.hk/ncms/script/eds/activestock_sehk_e.json"
    title_search_url: Any = "https://www1.hkexnews.hk/search/titleSearchServlet.do"
    referer: Any = "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=en"

    def _headers(self, *, accept: str = "application/json, text/javascript, */*; q=0.01") -> Dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Referer": self.referer,
            "Accept": accept,
            "X-Requested-With": "XMLHttpRequest",
        }

    def _fetch_bytes_via_curl(self, url: str, headers: Mapping[str, str], *, timeout: int) -> bytes:
        cmd: Any = [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--compressed",
            "--connect-timeout",
            str(min(5, timeout)),
            "--max-time",
            str(timeout),
            "-H",
            f"User-Agent: {headers['User-Agent']}",
        ]
        for key, value in headers.items():
            if key == "User-Agent":
                continue
            cmd.extend(["-H", f"{key}: {value}"])
        cmd.append(url)
        completed: Any = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout + 2,
            check=True,
        )
        if completed.stdout:
            return completed.stdout
        raise RuntimeError("bounded curl returned empty response")

    def _fetch_bytes(
        self,
        url: str,
        *,
        accept: str = "application/json, text/javascript, */*; q=0.01",
        timeout: int = 30,
        curl_retries: int = 1,
        use_urllib_secondary_route: bool = True,
    ) -> bytes:
        headers: Any = self._headers(accept=accept)
        bounded_timeout: Any = max(5, int(timeout))
        curl_primary_error: Optional[BaseException] = None
        if "application/pdf" in accept.lower():
            try:
                return self._fetch_bytes_via_curl(url, headers, timeout=bounded_timeout)
            except Exception as exc:
                curl_primary_error = exc
                if not use_urllib_secondary_route:
                    raise

        urllib_error: Optional[BaseException] = None
        try:
            return https_bytes(
                url,
                user_agent=headers["User-Agent"],
                headers={k: v for k, v in headers.items() if k != "User-Agent"},
                timeout=bounded_timeout,
                retries=max(0, int(curl_retries)),
                max_bytes=120 * 1024 * 1024,
            )
        except Exception as exc:
            urllib_error = exc
            if not use_urllib_secondary_route:
                raise

        try:
            return self._fetch_bytes_via_curl(url, headers, timeout=bounded_timeout)
        except Exception as exc:
            curl_detail: Any = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, subprocess.CalledProcessError):
                stderr: Any = exc.stderr.decode("utf-8", errors="replace")[:500]
                curl_detail = f"CalledProcessError: {exc}; stderr={stderr}"
            primary_detail: Any = ""
            if curl_primary_error is not None:
                primary_detail = f" after primary bounded curl failed ({type(curl_primary_error).__name__}: {curl_primary_error})"
            raise RuntimeError(f"HKEX HTTPS fetch failed{primary_detail} via urllib ({type(urllib_error).__name__}: {urllib_error}) and bounded curl ({curl_detail})") from exc

    def _fetch_json(self, url: str, *, timeout: int = 12, attempts: int = 1) -> Any:
        errors: List[str] = []
        max_attempts: Any = max(1, int(attempts))
        for attempt in range(1, max_attempts + 1):
            payload: Any = self._fetch_bytes(url, timeout=timeout, curl_retries=0)
            try:
                return json.loads(payload.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                errors.append(self._json_error_detail(payload, exc, attempt=attempt))
                if attempt < max_attempts:
                    time.sleep(min(1.0, 0.25 * attempt))
                    continue
                raise RuntimeError(f"HKEX JSON parse failed after {max_attempts} attempts: " + " | ".join(errors)) from exc

    @staticmethod
    def _json_error_detail(payload: bytes, exc: BaseException, *, attempt: int) -> str:
        text: Any = payload.decode("utf-8-sig", errors="replace")
        pos: Any = int(getattr(exc, "pos", 0) or 0)
        start: Any = max(0, pos - 120)
        end: Any = min(len(text), pos + 120)
        snippet: Any = re.sub(r"\s+", " ", text[start:end])[:260]
        return (
            f"attempt={attempt} {type(exc).__name__} at char={pos} "
            f"bytes={len(payload)} sha256={sha256(payload).hexdigest()} near={snippet!r}"
        )

    def _lookup_listing(self, code: str) -> Optional[Dict[str, Any]]:
        data: Any = self._fetch_json(self.active_stock_url, timeout=12, attempts=1)
        if not isinstance(data, list):
            return None
        normalized: Any = code.zfill(5)
        for item in data:
            if isinstance(item, Mapping) and str(item.get("c") or "").zfill(5) == normalized:
                return {
                    "stock_id": str(item.get("i") or ""),
                    "stock_code": str(item.get("c") or normalized).zfill(5),
                    "stock_name": item.get("n"),
                    "security_id": item.get("s"),
                }
        return None

    def _query_title_search(
        self,
        *,
        stock_id: str,
        from_date: str,
        to_date: str,
        title: str = "",
        row_range: int = 100,
    ) -> Dict[str, Any]:
        params: Any = urllib.parse.urlencode({
            "sortDir": "0",
            "sortByOptions": "DateTime",
            "category": "0",
            "market": "SEHK",
            "stockId": stock_id,
            "documentType": "",
            "fromDate": from_date,
            "toDate": to_date,
            "title": title,
            "searchType": "",
            "t1code": "",
            "t2Gcode": "",
            "t2code": "",
            "rowRange": str(row_range),
            "lang": "en",
        })
        payload: Any = self._fetch_json(f"{self.title_search_url}?{params}", timeout=10, attempts=1)
        if not isinstance(payload, Mapping):
            raise RuntimeError("HKEX title search response is not a JSON object")
        return dict(payload)

    def _date_window(self, *, years: int = 2) -> Tuple[str, str]:
        today: Any = dt.datetime.now().date()
        start: Any = today - dt.timedelta(days=years * 366)
        return start.strftime("%Y%m%d"), today.strftime("%Y%m%d")

    def _records_from_payload(self, payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
        raw_result: Any = payload.get("result") or "[]"
        try:
            records: Any = json.loads(str(raw_result))
        except Exception:
            return []
        if not isinstance(records, list):
            return []
        return [self._normalize_record(record) for record in records if isinstance(record, Mapping)]

    def _normalize_record(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        file_link: Any = str(record.get("FILE_LINK") or "")
        pdf_url: Any = urllib.parse.urljoin(self.base_url, file_link)
        date_time: Any = self._parse_hkex_datetime(record.get("DATE_TIME"))
        return {
            "news_id": str(record.get("NEWS_ID") or ""),
            "announcement_datetime": date_time,
            "announcement_date": date_time[:10] if date_time else None,
            "stock_code": self._clean_text(record.get("STOCK_CODE")),
            "stock_name": self._clean_text(record.get("STOCK_NAME")),
            "title": self._clean_text(record.get("TITLE")),
            "category": self._clean_text(record.get("LONG_TEXT") or record.get("SHORT_TEXT")),
            "file_type": str(record.get("FILE_TYPE") or ""),
            "file_info": str(record.get("FILE_INFO") or ""),
            "file_link": file_link,
            "pdf_url": pdf_url if file_link else "",
            "dod_web_path": str(record.get("DOD_WEB_PATH") or ""),
        }

    @staticmethod
    def _clean_text(value: Any) -> str:
        text: Any = html.unescape(str(value or ""))
        text = re.sub(r"<br\s*/?>", " / ", text, flags=re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _parse_hkex_datetime(value: Any) -> Optional[str]:
        text: Any = str(value or "").strip()
        try:
            return dt.datetime.strptime(text, "%d/%m/%Y %H:%M").isoformat()
        except Exception:
            return None

    @staticmethod
    def _pdf_python_candidates() -> List[str]:
        candidates: List[str] = []
        env_python: Any = os.getenv("SERENITY_PDF_PYTHON")
        if env_python:
            candidates.append(env_python)
        candidates.append(sys.executable)
        runtime_python: Any = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
        candidates.append(str(runtime_python))
        output: List[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            if Path(candidate).exists():
                output.append(candidate)
        return output

    @staticmethod
    def _pdfplumber_extract_script() -> str:
        return r'''
import json
import sys

import pdfplumber

path = sys.argv[1]
max_pages = int(sys.argv[2])
pages = []
with pdfplumber.open(path) as pdf:
    total_pages = len(pdf.pages)
    for index, page in enumerate(pdf.pages[:max_pages], start=1):
        text = page.extract_text() or ""
        if text.strip():
            pages.append({"page_number": index, "text": text})
print(json.dumps({"parser": "pdfplumber", "page_count": total_pages, "pages": pages}, ensure_ascii=False))
'''

    def _extract_pdf_pages(self, pdf_path: str | Path, *, max_pages: int = 220, timeout: int = 60) -> Dict[str, Any]:
        path: Any = Path(pdf_path)
        errors: List[str] = []

        try:
            import pdfplumber  # type: ignore

            pages: List[Dict[str, Any]] = []
            with pdfplumber.open(str(path)) as pdf:
                total_pages: Any = len(pdf.pages)
                for index, page in enumerate(pdf.pages[:max_pages], start=1):
                    text: Any = page.extract_text() or ""
                    if text.strip():
                        pages.append({"page_number": index, "text": text})
            return {"ok": True, "parser": "pdfplumber", "page_count": total_pages, "pages": pages, "errors": []}
        except Exception as exc:
            errors.append(f"in-process pdfplumber unavailable: {type(exc).__name__}: {exc}")

        script: Any = self._pdfplumber_extract_script()
        for python_exe in self._pdf_python_candidates():
            try:
                completed: Any = subprocess.run(
                    [python_exe, "-", str(path), str(max_pages)],
                    input=script.encode("utf-8"),
                    capture_output=True,
                    timeout=timeout,
                    check=True,
                )
                payload: Any = json.loads(completed.stdout.decode("utf-8"))
                payload["ok"] = True
                payload["python"] = python_exe
                payload.setdefault("errors", [])
                return payload
            except Exception as exc:
                stderr: Any = ""
                if isinstance(exc, subprocess.CalledProcessError):
                    stderr = exc.stderr.decode("utf-8", errors="replace")[:500]
                errors.append(f"{python_exe}: {type(exc).__name__}: {exc} {stderr}".strip())

        return {"ok": False, "parser": "none", "page_count": 0, "pages": [], "errors": errors}

    @staticmethod
    def _normalize_pdf_line(line: str) -> str:
        line = line.replace("\u2019", "'").replace("\u2013", "-").replace("\u2014", "-")
        return re.sub(r"\s+", " ", line).strip()

    @classmethod
    def _pdf_number_tokens(cls, line: str) -> List[str]:
        normalized: Any = cls._normalize_pdf_line(line)
        return re.findall(r"\(?-?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?|\(?-?\d+(?:\.\d+)?\)?|(?<!\w)[-–](?!\w)", normalized)

    @staticmethod
    def _parse_pdf_number(token: str) -> Optional[float]:
        text: Any = token.strip()
        if text in {"-", "–", ""}:
            return None
        negative: Any = text.startswith("(") and text.endswith(")")
        text = text.strip("()").replace(",", "")
        try:
            value: Any = float(text)
        except Exception:
            return None
        return -value if negative else value

    @classmethod
    def _line_values(cls, line: str, *, expected_columns: int) -> List[Optional[float]]:
        tokens: Any = cls._pdf_number_tokens(line)
        while len(tokens) > expected_columns:
            first: Any = tokens[0].strip("()")
            if "," not in first and "." not in first and first.lstrip("-").isdigit() and abs(int(first)) <= 80:
                tokens.pop(0)
                continue
            break
        if len(tokens) > expected_columns:
            tokens = tokens[-expected_columns:]
        return [cls._parse_pdf_number(token) for token in tokens]

    @staticmethod
    def _month_number(name: str) -> str:
        months: Any = {
            "january": "01",
            "february": "02",
            "march": "03",
            "april": "04",
            "may": "05",
            "june": "06",
            "july": "07",
            "august": "08",
            "september": "09",
            "october": "10",
            "november": "11",
            "december": "12",
        }
        return months.get(name.lower(), "01")

    @classmethod
    def _period_from_report_text(cls, text: str) -> Optional[str]:
        match: Any = re.search(r"(?:ended|at)\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text, flags=re.I)
        if not match:
            return None
        day: Any
        month: Any
        year: Any
        day, month, year = match.groups()
        return f"{year}-{cls._month_number(month)}-{int(day):02d}"

    @staticmethod
    def _page_texts_containing(pages: Sequence[Mapping[str, Any]], *needles: str) -> List[Mapping[str, Any]]:
        lower_needles: Any = [needle.lower() for needle in needles]
        output: List[Mapping[str, Any]] = []
        for page in pages:
            text: Any = str(page.get("text") or "")
            lower_text: Any = text.lower()
            if all(needle in lower_text for needle in lower_needles):
                output.append(page)
        return output

    @classmethod
    def _extract_label_value(
        cls,
        pages: Sequence[Mapping[str, Any]],
        labels: Sequence[str],
        *,
        expected_columns: int,
        value_index: int,
    ) -> Tuple[Optional[float], Optional[Dict[str, Any]]]:
        lower_labels: Any = [label.lower() for label in labels]
        for page in pages:
            text: Any = str(page.get("text") or "")
            for line in text.splitlines():
                clean: Any = cls._normalize_pdf_line(line)
                clean_lower: Any = clean.lower()
                if not any(clean_lower.startswith(label) or label in clean_lower for label in lower_labels):
                    continue
                values: Any = cls._line_values(clean, expected_columns=expected_columns)
                if len(values) <= value_index or values[value_index] is None:
                    continue
                return values[value_index], {
                    "page_number": page.get("page_number"),
                    "line": clean,
                    "value_index": value_index,
                }
        return None, None

    @classmethod
    def _extract_revenue_value(
        cls,
        pages: Sequence[Mapping[str, Any]],
        *,
        expected_columns: int,
        value_index: int,
    ) -> Tuple[Optional[float], Optional[Dict[str, Any]]]:
        for page in pages:
            lines: Any = [cls._normalize_pdf_line(line) for line in str(page.get("text") or "").splitlines()]
            for index, line in enumerate(lines):
                if line.lower() != "revenues":
                    continue
                for candidate in lines[index + 1:index + 10]:
                    if candidate.lower().startswith("cost of revenues"):
                        break
                    if re.match(r"^\d+[A-Za-z()]?\s+", candidate):
                        values: Any = cls._line_values(candidate, expected_columns=expected_columns)
                        if len(values) > value_index and values[value_index] is not None:
                            return values[value_index], {
                                "page_number": page.get("page_number"),
                                "line": candidate,
                                "value_index": value_index,
                            }
                break
        return None, None

    @classmethod
    def _extract_summary_value(
        cls,
        page: Mapping[str, Any],
        label_pattern: str,
        *,
        expected_columns: int,
        value_index: int,
    ) -> Tuple[Optional[float], Optional[Dict[str, Any]]]:
        lines: List[str] = [
            cls._normalize_pdf_line(line)
            for line in str(page.get("text") or "").splitlines()
            if cls._normalize_pdf_line(line)
        ]
        for index, line in enumerate(lines):
            candidates: List[str] = [line]
            if not cls._pdf_number_tokens(line) and index + 1 < len(lines):
                candidates.append(f"{line} {lines[index + 1]}")
            for candidate in candidates:
                match: Any = re.match(
                    rf"^{label_pattern}\s+((?:\(?-?\d[\d,]*(?:\.\d+)?\)?\s*){{1,12}})$",
                    candidate,
                    flags=re.I,
                )
                if not match:
                    continue
                values: List[Optional[float]] = cls._line_values(candidate, expected_columns=expected_columns)
                if len(values) <= value_index or values[value_index] is None:
                    continue
                return values[value_index], {
                    "page_number": page.get("page_number"),
                    "line": candidate[:260],
                    "value_index": value_index,
                    "source_section": "financial_summary",
                }
        return None, None

    @classmethod
    def _extract_hkex_financial_summary_fields(
        cls,
        pages: Sequence[Mapping[str, Any]],
    ) -> Tuple[Dict[str, float], Dict[str, Dict[str, Any]], Optional[str]]:
        fields: Dict[str, float] = {}
        evidence: Dict[str, Dict[str, Any]] = {}
        period: Optional[str] = None

        summary_pages: List[Mapping[str, Any]] = [
            page for page in pages
            if "financial summary" in str(page.get("text") or "").lower()
            and "revenue" in str(page.get("text") or "").lower()
            and "total assets" in str(page.get("text") or "").lower()
        ]
        for page in summary_pages:
            text: str = str(page.get("text") or "")
            year_rows: List[List[str]] = [
                re.findall(r"\b(20\d{2})\b", line)
                for line in text.splitlines()
                if len(re.findall(r"\b(20\d{2})\b", line)) >= 2
            ]
            if not year_rows:
                continue
            years: List[str] = max(year_rows, key=len)
            expected_columns: int = len(years)
            value_index: int = expected_columns - 1
            if "march 31" in text.lower() and years[value_index]:
                period = f"{years[value_index]}-03-31"

            for field, pattern in {
                "revenue": r"Revenue",
                "net_income": r"Net income attributable to ordinary\s+shareholders",
                "assets": r"Total assets",
                "liabilities": r"Total liabilities",
                "equity": r"Total equity",
            }.items():
                value: Optional[float]
                source: Optional[Dict[str, Any]]
                value, source = cls._extract_summary_value(
                    page,
                    pattern,
                    expected_columns=expected_columns,
                    value_index=value_index,
                )
                if value is not None:
                    fields[field] = value
                    if source:
                        evidence[field] = source
            if fields:
                break

        for page in pages:
            text: str = str(page.get("text") or "")
            if "net cash provided by operating activities" not in text.lower():
                continue
            normalized: str = re.sub(r"\s+", " ", text)
            value_rows: List[str] = re.findall(
                r"Net cash provided by operating activities\s+((?:\(?-?\d[\d,]*(?:\.\d+)?\)?\s+){1,8})",
                normalized,
                flags=re.I,
            )
            if not value_rows:
                continue
            raw_values: List[str] = cls._pdf_number_tokens(value_rows[0])
            parsed_values: List[Optional[float]] = [cls._parse_pdf_number(token) for token in raw_values]
            values: List[float] = [value for value in parsed_values if value is not None]
            if not values:
                continue
            value_index: int = len(values) - 2 if len(values) >= 4 and "US$" in normalized else len(values) - 1
            if value_index < 0 or value_index >= len(values):
                continue
            fields["operating_cash_flow"] = values[value_index]
            evidence["operating_cash_flow"] = {
                "page_number": page.get("page_number"),
                "line": f"Net cash provided by operating activities {value_rows[0]}".strip()[:260],
                "value_index": value_index,
                "source_section": "cash_flow_summary",
            }
            break

        return fields, evidence, period

    def _extract_hkex_report_period(self, report: Mapping[str, Any], *, raw_dir: Optional[Path] = None) -> Dict[str, Any]:
        pdf_path: Any = str(report.get("pdf_path") or "")
        title: Any = str(report.get("title") or "")
        report_kind: Any = str(report.get("report_kind") or "periodic")
        if not pdf_path:
            return {"status": "FAILED", "errors": ["report has no downloaded pdf_path"]}

        page_bundle: Any = self._extract_pdf_pages(pdf_path, max_pages=160, timeout=18)
        if not page_bundle.get("ok"):
            return {"status": "FAILED", "errors": page_bundle.get("errors", ["PDF text extraction failed"])}

        pages: Any = page_bundle.get("pages", [])
        if not isinstance(pages, list) or not pages:
            return {"status": "FAILED", "errors": ["PDF text extraction returned no text pages"]}

        if raw_dir:
            text_name: Any = _safe_artifact_name(f"{Path(pdf_path).stem}_pdf_text") + ".txt"
            combined_text: Any = "\n\n".join(
                f"--- page {page.get('page_number')} ---\n{page.get('text') or ''}"
                for page in pages
            )
            text_path: Any
            text_hash: Any
            text_path, text_hash = save_raw_text(combined_text, raw_dir / "extracted_text", text_name)
        else:
            text_path = text_hash = None

        income_pages: Any = self._page_texts_containing(pages, "income statement")
        income_pages = [page for page in income_pages if "comprehensive income" not in str(page.get("text") or "").lower()]
        position_pages: Any = self._page_texts_containing(pages, "statement of financial position")
        cashflow_pages: Any = self._page_texts_containing(pages, "statement of cash flows")

        income_text: Any = "\n".join(str(page.get("text") or "") for page in income_pages)
        position_text: Any = "\n".join(str(page.get("text") or "") for page in position_pages)
        cashflow_text: Any = "\n".join(str(page.get("text") or "") for page in cashflow_pages)
        period: Any = self._period_from_report_text(income_text) or self._period_from_report_text(position_text) or str(report.get("announcement_date") or "")
        period_type: Any = "annual" if report_kind == "annual" else "interim"
        income_columns: Any = 4 if "six months ended" in income_text.lower() else 2
        income_index: Any = 2 if income_columns == 4 else 0

        fields: Dict[str, Any] = {}
        evidence: Dict[str, Any] = {}

        def put(field: str, value: Optional[float], source: Optional[Dict[str, Any]]) -> None:
            if value is None:
                return
            fields[field] = value
            if source:
                evidence[field] = source

        value: Any
        source: Any
        value, source = self._extract_revenue_value(income_pages, expected_columns=income_columns, value_index=income_index)
        put("revenue", value, source)
        for field, labels in {
            "gross_profit": ["gross profit"],
            "operating_profit": ["operating profit"],
            "profit_before_tax": ["profit before income tax"],
            "total_net_profit": ["profit for the year", "profit for the period"],
            "profit_attributable_to_equity_holders": ["equity holders of the company"],
        }.items():
            value, source = self._extract_label_value(income_pages, labels, expected_columns=income_columns, value_index=income_index)
            put(field, value, source)
        net_income_value: Any = fields.get("profit_attributable_to_equity_holders") or fields.get("total_net_profit")
        if net_income_value is not None:
            fields["net_income"] = net_income_value
            if "net_income" not in evidence:
                evidence["net_income"] = evidence.get("profit_attributable_to_equity_holders") or evidence.get("total_net_profit")

        for field, labels in {
            "assets": ["total assets"],
            "equity": ["total equity"],
            "liabilities": ["total liabilities"],
            "cash": ["cash and cash equivalents"],
        }.items():
            value, source = self._extract_label_value(position_pages, labels, expected_columns=2, value_index=0)
            put(field, value, source)

        for field, labels in {
            "operating_cash_flow": ["net cash flows generated from operating activities"],
            "cash_generated_from_operations": ["cash generated from operations"],
        }.items():
            value, source = self._extract_label_value(cashflow_pages, labels, expected_columns=2, value_index=0)
            put(field, value, source)

        missing_before_summary: set[str] = {"revenue", "net_income", "operating_cash_flow", "assets", "liabilities", "equity"} - set(fields)
        if missing_before_summary:
            summary_fields: Dict[str, float]
            summary_evidence: Dict[str, Dict[str, Any]]
            summary_period: Optional[str]
            summary_fields, summary_evidence, summary_period = self._extract_hkex_financial_summary_fields(pages)
            for field in sorted(missing_before_summary):
                value: Optional[float] = summary_fields.get(field)
                if value is not None:
                    put(field, value, summary_evidence.get(field))
            if summary_period and (not period or period == str(report.get("announcement_date") or "")):
                period = summary_period

        required: Any = ["revenue", "net_income", "operating_cash_flow", "assets", "liabilities", "equity"]
        missing: Any = [field for field in required if fields.get(field) is None]
        status: Any = "OK" if not missing else ("PARTIAL" if fields else "FAILED")
        period_row: Any = {
            "period": period,
            "period_type": period_type,
            "source": self.name,
            "source_level": self.level.value,
            "source_report_kind": report_kind,
            "source_title": title,
            "source_announcement_date": report.get("announcement_date"),
            "currency": "RMB",
            "unit": "million",
            **fields,
            "field_evidence": evidence,
        }
        return {
            "status": status,
            "period": period_row if fields else None,
            "missing_fields": missing,
            "parser": page_bundle.get("parser"),
            "parser_python": page_bundle.get("python"),
            "page_count": page_bundle.get("page_count"),
            "text_path": text_path,
            "text_hash": text_hash,
            "warnings": page_bundle.get("warnings", []),
            "errors": [] if fields else ["No core financial fields could be extracted from PDF text."],
        }

    @classmethod
    def _report_kind(cls, record: Mapping[str, Any]) -> str:
        text: Any = f"{record.get('title') or ''} {record.get('category') or ''}".lower()
        if "monthly return" in text:
            return "monthly_return"
        if "next day disclosure return" in text:
            return "next_day_disclosure"
        if "annual report" in text:
            return "annual"
        if "interim" in text or "half-year" in text or "half year" in text:
            return "interim"
        if "quarterly report" in text:
            return "quarterly"
        if "annual results" in text or "final results" in text:
            return "final_results"
        if "quarterly results" in text:
            return "quarterly_results"
        if "results" in text:
            return "results"
        return "periodic"

    @classmethod
    def _select_reports_for_download(cls, reports: Sequence[Mapping[str, Any]], limit: int) -> List[Mapping[str, Any]]:
        selected: List[Mapping[str, Any]] = []
        preferred_order: Any = ["annual", "interim", "monthly_return", "next_day_disclosure", "quarterly", "final_results", "quarterly_results", "results", "periodic"]
        sorted_by_kind: Dict[str, List[Mapping[str, Any]]] = {}
        for kind in preferred_order:
            candidates: List[Mapping[str, Any]] = [
                report for report in reports
                if str(report.get("report_kind") or "") == kind and report.get("pdf_url")
            ]
            candidates.sort(key=lambda report: str(report.get("announcement_datetime") or ""), reverse=True)
            sorted_by_kind[kind] = candidates

        def add_index(kind: str, index: int) -> None:
            candidates: List[Mapping[str, Any]] = sorted_by_kind.get(kind, [])
            if len(candidates) > index and candidates[index] not in selected:
                selected.append(candidates[index])

        for index in range(2):
            for kind in ("annual", "interim"):
                add_index(kind, index)
                if len(selected) >= limit:
                    return selected
        for kind in preferred_order:
            add_index(kind, 0)
            if len(selected) >= limit:
                return selected
        for kind in preferred_order:
            candidates = sorted_by_kind.get(kind, [])
            for report in candidates:
                if report not in selected:
                    selected.append(report)
                if len(selected) >= limit:
                    return selected
        return selected

    def _attach_report_downloads(
        self,
        reports: List[Dict[str, Any]],
        *,
        raw_dir: Path,
        symbol: str,
        limit: int,
        errors: List[str],
    ) -> None:
        selected: Any = self._select_reports_for_download(reports, limit)
        for report in reports:
            report["download_status"] = "SELECTED" if report in selected else "NOT_SELECTED"
        fallback_candidates: Any = [
            report for report in reports
            if report not in selected and report.get("pdf_url")
        ]
        attempt_limit: Any = max(limit, int(os.getenv("SERENITY_HKEX_REPORT_DOWNLOAD_ATTEMPT_LIMIT", str(limit + 2))))
        downloaded_count: Any = 0
        attempted_count: Any = 0
        for report in [*selected, *fallback_candidates]:
            if downloaded_count >= limit or attempted_count >= attempt_limit:
                break
            url: Any = str(report.get("pdf_url") or "")
            if not url:
                continue
            attempted_count += 1
            title: Any = str(report.get("title") or "hkex_report")
            report_kind: Any = str(report.get("report_kind") or "periodic")
            announcement_date: Any = str(report.get("announcement_date") or "")
            filename: Any = _safe_artifact_name(f"{symbol}_{announcement_date}_{report_kind}_{title}") + ".pdf"
            try:
                pdf_download_timeout: Any = int(os.getenv("SERENITY_HKEX_PDF_DOWNLOAD_TIMEOUT_SECONDS", "15"))
                payload: Any = self._fetch_bytes(url, accept="application/pdf,*/*", timeout=max(5, pdf_download_timeout), curl_retries=0)
                if not payload.startswith(b"%PDF"):
                    raise RuntimeError("downloaded artifact does not start with a PDF header")
                pdf_path: Any
                pdf_hash: Any
                pdf_path, pdf_hash = save_raw_bytes(payload, raw_dir, filename)
                report["download_status"] = "OK"
                report["pdf_path"] = pdf_path
                report["pdf_hash"] = pdf_hash
                report["pdf_size_bytes"] = len(payload)
                downloaded_count += 1
            except Exception as exc:
                report["download_status"] = "FAILED"
                report["download_error"] = f"{type(exc).__name__}: {exc}"
                errors.append(f"HKEX report PDF download failed for {title}: {type(exc).__name__}: {exc}")


class HkexAnnouncementsProvider(HkexNewsBase):
    """Official HKEXnews announcement metadata adapter for HK-listed securities."""

    name: Any = "HKEXnews_Announcements_L0"
    datasets: Any = [Dataset.FILINGS]

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        if symbol.market != Market.HK:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "HKEXnews announcements only support HK symbols")
        if dataset != Dataset.FILINGS:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported dataset {dataset.value}")
        code: Any = symbol.symbol.partition(".")[0].zfill(5)
        try:
            listing: Any = self._lookup_listing(code)
            if not listing:
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"could not resolve HKEX stock id for {symbol.symbol}")
            from_date: Any
            to_date: Any
            from_date, to_date = self._date_window(years=int(kwargs.get("years", 2) or 2))
            payloads: Dict[str, Any] = {}
            errors: List[str] = []
            announcements: List[Dict[str, Any]] = []
            seen: set[str] = set()

            def add_records(query_name: str, payload: Mapping[str, Any]) -> None:
                payloads[query_name] = dict(payload)
                for record in self._records_from_payload(payload):
                    key: Any = str(record.get("news_id") or record.get("file_link") or "")
                    if key in seen:
                        continue
                    seen.add(key)
                    announcements.append(record)

            try:
                payload: Any = self._query_title_search(
                    stock_id=str(listing["stock_id"]),
                    from_date=from_date,
                    to_date=to_date,
                    row_range=int(kwargs.get("row_range", 100) or 100),
                )
                add_records("broad", payload)
            except Exception as exc:
                errors.append(f"HKEX broad announcement search failed: {type(exc).__name__}: {exc}")

            if not announcements:
                for title in ["Annual Report", "Interim Report", "Results", "Monthly Return", "Next Day Disclosure Return", "Announcement"]:
                    try:
                        payload = self._query_title_search(
                            stock_id=str(listing["stock_id"]),
                            from_date=from_date,
                            to_date=to_date,
                            title=title,
                            row_range=30,
                        )
                        add_records(f"title:{title}", payload)
                    except Exception as exc:
                        errors.append(f"HKEX targeted announcement search failed for {title}: {type(exc).__name__}: {exc}")
            if not announcements:
                reason: Any = "HKEXnews returned no announcements"
                if errors:
                    reason += ": " + " | ".join(errors)
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, reason)
            announcements.sort(key=lambda row: str(row.get("announcement_datetime") or ""), reverse=True)
            raw_path: Any
            raw_hash: Any
            raw_path = raw_hash = None
            raw_dir: Any = kwargs.get("raw_dir")
            if raw_dir:
                raw_path, raw_hash = save_raw_json(
                    {"payloads": payloads, "errors": errors},
                    raw_dir,
                    f"{symbol.symbol}_{dataset.value}_hkex_announcements_raw.json",
                )
            warnings: Any = ["HKEXnews metadata/PDF links fetched; document contents are not parsed by this adapter."]
            if "broad" not in payloads:
                warnings.append("HKEX broad announcement search was unavailable; targeted title searches supplied announcement metadata.")
            return DataResult(
                True,
                dataset,
                symbol.symbol,
                self.name,
                self.level,
                utc_now(),
                as_of_date=announcements[0].get("announcement_date"),
                data={
                    "source": self.name,
                    "source_level": self.level.value,
                    "stock": listing,
                    "record_count": sum(_safe_int(payload.get("recordCnt")) or 0 for payload in payloads.values() if isinstance(payload, Mapping)),
                    "loaded_record_count": len(announcements),
                    "query_count": len(payloads),
                    "announcements": announcements,
                },
                raw_path=raw_path,
                raw_hash=raw_hash,
                currency=symbol.currency or "HKD",
                warnings=warnings,
            )
        except Exception as exc:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"HKEXnews fetch failed: {type(exc).__name__}: {exc}")


class HkexFinancialReportsProvider(HkexNewsBase):
    """Official HKEX annual/interim report PDF evidence adapter for HK financials."""

    name: Any = "HKEXnews_FinancialReports_L0"
    datasets: Any = [Dataset.FINANCIALS]

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        if symbol.market != Market.HK:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "HKEX financial reports only support HK symbols")
        if dataset != Dataset.FINANCIALS:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported dataset {dataset.value}")
        code: Any = symbol.symbol.partition(".")[0].zfill(5)
        try:
            listing: Any = self._lookup_listing(code)
            if not listing:
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"could not resolve HKEX stock id for {symbol.symbol}")
            from_date: Any
            to_date: Any
            from_date, to_date = self._date_window(years=int(kwargs.get("years", 3) or 3))
            payloads: Dict[str, Any] = {}
            reports: List[Dict[str, Any]] = []
            seen: set[str] = set()
            errors: List[str] = []
            for title in ["Annual Report", "Interim Report", "Quarterly Report"]:
                try:
                    payload: Any = self._query_title_search(
                        stock_id=str(listing["stock_id"]),
                        from_date=from_date,
                        to_date=to_date,
                        title=title,
                        row_range=20,
                    )
                    payloads[title] = payload
                    for record in self._records_from_payload(payload):
                        key: Any = str(record.get("news_id") or record.get("file_link") or "")
                        if key in seen:
                            continue
                        seen.add(key)
                        record["report_kind"] = self._report_kind(record)
                        reports.append(record)
                except Exception as exc:
                    errors.append(f"HKEX title search failed for {title}: {type(exc).__name__}: {exc}")
            if not {"annual", "interim"}.issubset({str(report.get("report_kind") or "") for report in reports}):
                try:
                    payload = self._query_title_search(
                        stock_id=str(listing["stock_id"]),
                        from_date=from_date,
                        to_date=to_date,
                        row_range=200,
                    )
                    payloads["all_announcements_report_scan"] = payload
                    for record in self._records_from_payload(payload):
                        report_kind: Any = self._report_kind(record)
                        if report_kind not in {"annual", "interim", "quarterly"}:
                            continue
                        key = str(record.get("news_id") or record.get("file_link") or "")
                        if key in seen:
                            continue
                        seen.add(key)
                        record["report_kind"] = report_kind
                        reports.append(record)
                except Exception as exc:
                    errors.append(f"HKEX broad announcement report scan failed: {type(exc).__name__}: {exc}")
            reports.sort(key=lambda report: str(report.get("announcement_datetime") or ""), reverse=True)
            raw_dir: Any = kwargs.get("raw_dir")
            download_limit: Any = int(kwargs.get("official_report_download_limit", OFFICIAL_REPORT_DOWNLOAD_LIMIT_DEFAULT) or OFFICIAL_REPORT_DOWNLOAD_LIMIT_DEFAULT)
            if raw_dir and reports and download_limit > 0:
                self._attach_report_downloads(
                    reports,
                    raw_dir=Path(raw_dir) / "official_reports",
                    symbol=symbol.symbol,
                    limit=download_limit,
                    errors=errors,
                )
            selected_reports: Any = self._select_reports_for_download(reports, download_limit) if reports and download_limit > 0 else []
            extracted_periods: List[Dict[str, Any]] = []
            extraction_errors: List[str] = []
            extraction_warnings: List[str] = []
            extraction_raw_dir: Any = Path(raw_dir) / "official_reports" if raw_dir else None
            for report in reports:
                if report.get("download_status") != "OK" or not report.get("pdf_path"):
                    continue
                extraction: Any = self._extract_hkex_report_period(report, raw_dir=extraction_raw_dir)
                report["line_extraction"] = {
                    key: value
                    for key, value in extraction.items()
                    if key != "period"
                }
                if extraction.get("status") in {"OK", "PARTIAL"} and isinstance(extraction.get("period"), Mapping):
                    extracted_periods.append(dict(extraction["period"]))
                if extraction.get("status") != "OK":
                    extraction_errors.extend(str(error) for error in extraction.get("errors", []) or [])
                    extraction_warnings.append(
                        f"{report.get('report_kind')} {report.get('announcement_date')} extraction status={extraction.get('status')} missing={extraction.get('missing_fields')}"
                    )
            extracted_periods.sort(key=lambda row: str(row.get("period") or ""))
            downloaded_reports: Any = [
                report for report in reports
                if report.get("download_status") == "OK" and report.get("pdf_path")
            ]
            if not reports:
                evidence_status: Any = "FAILED"
            elif raw_dir and selected_reports and len(downloaded_reports) < len(selected_reports):
                evidence_status = "PARTIAL"
            else:
                evidence_status = "OK"
            evidence: Any = {
                "status": evidence_status,
                "source": self.name,
                "source_level": self.level.value,
                "stock": listing,
                "selected_report_count": len(selected_reports),
                "downloaded_report_count": len(downloaded_reports),
                "line_extraction_status": "OK" if extracted_periods and not extraction_warnings else ("PARTIAL" if extracted_periods else "FAILED"),
                "extracted_period_count": len(extracted_periods),
                "reports": reports,
                "downloaded_reports": [
                    {
                        "report_kind": report.get("report_kind"),
                        "title": report.get("title"),
                        "announcement_date": report.get("announcement_date"),
                        "pdf_path": report.get("pdf_path"),
                        "pdf_hash": report.get("pdf_hash"),
                        "pdf_size_bytes": report.get("pdf_size_bytes"),
                        "line_extraction": report.get("line_extraction"),
                    }
                    for report in downloaded_reports
                ],
                "errors": errors + extraction_errors,
            }
            raw_path: Any
            raw_hash: Any
            raw_path = raw_hash = None
            if raw_dir:
                raw_path, raw_hash = save_raw_json(
                    {"payloads": payloads, "errors": errors},
                    raw_dir,
                    f"{symbol.symbol}_{dataset.value}_hkex_financial_reports_raw.json",
                )
            if not reports:
                reason: Any = "HKEXnews returned no annual/interim report PDFs"
                if errors:
                    reason += ": " + " | ".join(errors)
                return DataResult(
                    False,
                    dataset,
                    symbol.symbol,
                    self.name,
                    self.level,
                    utc_now(),
                    data={"official_report_evidence": evidence},
                    raw_path=raw_path,
                    raw_hash=raw_hash,
                    currency=symbol.currency or "HKD",
                    errors=[reason],
                )
            return DataResult(
                True,
                dataset,
                symbol.symbol,
                self.name,
                self.level,
                utc_now(),
                as_of_date=reports[0].get("announcement_date"),
                data={
                    "source": self.name,
                    "source_level": self.level.value,
                    "official_report_evidence": evidence,
                    "periods": extracted_periods,
                    "period_basis": "HKEX official report PDF line extraction; annual rows are full-year and interim rows are six-month cumulative periods.",
                    "currency": "RMB" if extracted_periods else (symbol.currency or "HKD"),
                    "unit": "million" if extracted_periods else None,
                    "reports": reports,
                    "source_usage": {
                        "preferred_source": "HKEX annual/interim report PDFs",
                        "preferred_source_status": evidence_status,
                        "preferred_source_records": len(reports),
                        "report_pdf_evidence_used": True,
                        "report_line_items_extracted": bool(extracted_periods),
                        "extracted_period_count": len(extracted_periods),
                        "source_role": "L0_OFFICIAL_REPORT_EVIDENCE",
                        "required_ai_action": "Review extracted HKEX PDF line evidence, period basis, and missing fields before assigning S/A.",
                    },
                },
                raw_path=raw_path,
                raw_hash=raw_hash,
                currency=symbol.currency or "HKD",
                warnings=[
                    "HKEX official financial report PDFs were parsed into core financial lines where machine-readable page text was available.",
                    "Keep HKD/listing currency, RMB/reporting currency, share-count basis, and connected-transaction context explicit.",
                ] + extraction_warnings,
            )
        except Exception as exc:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"HKEX financial report fetch failed: {type(exc).__name__}: {exc}")


class HkexValuationInputsProvider(HkexFinancialReportsProvider):
    """HK valuation inputs from official HKEX report share count plus L2 quote price."""

    name: Any = "HKEX_Yahoo_Valuation_L0L2"
    level: Any = SourceLevel.L2
    datasets: Any = [Dataset.SHARE_CAPITAL, Dataset.VALUATION_INPUTS]

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        if symbol.market != Market.HK:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "HK valuation inputs only support HK symbols")
        if dataset not in self.datasets:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported dataset {dataset.value}")
        code: Any = symbol.symbol.partition(".")[0].zfill(5)
        raw_dir: Any = Path(kwargs["raw_dir"]) if kwargs.get("raw_dir") else None
        try:
            listing: Any = self._lookup_listing(code)
            if not listing:
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"could not resolve HKEX stock id for {symbol.symbol}")

            cached_quote_result: Any = kwargs.get("current_quote_result")
            quote: Mapping[str, Any] = {}
            quote_as_of: Optional[str] = None
            quote_source_name: Any = "current_quote_cache"
            quote_source_level: Any = SourceLevel.L2.value
            warnings: List[str] = []
            if isinstance(cached_quote_result, Mapping):
                cached_quote: Any = cached_quote_result.get("data")
                if isinstance(cached_quote, Mapping):
                    quote = cached_quote
                    quote_as_of = str(cached_quote_result.get("as_of_date") or "")
                    quote_source_name = str(cached_quote_result.get("source_name") or quote_source_name)
                    quote_source_level = str(cached_quote_result.get("source_level") or quote_source_level)
            if not quote:
                quote_errors: List[str] = []
                for quote_provider in [
                    YahooChartProvider(),
                    YahooChartProvider(name="Yahoo_Chart_Query2_L2", host="query2.finance.yahoo.com"),
                ]:
                    quote_result: Any = quote_provider.fetch(symbol, Dataset.CURRENT_QUOTE, raw_dir=raw_dir)
                    if quote_result.ok and isinstance(quote_result.data, Mapping):
                        quote = quote_result.data
                        quote_as_of = quote_result.as_of_date
                        quote_source_name = quote_result.source_name
                        quote_source_level = quote_result.source_level.value
                        break
                    quote_errors.extend(quote_result.errors)
                if not quote and quote_errors:
                    warnings.extend(quote_errors)
            price: Any = _safe_float(quote.get("regular_market_price"))
            quote_market_cap: Any = _safe_float(quote.get("market_cap"))
            if dataset == Dataset.VALUATION_INPUTS and (price is None or price <= 0):
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Yahoo current quote did not expose a usable HK regular market price")

            reports: Any = self._latest_share_count_reports(str(listing["stock_id"]))
            download_limit: Any = int(
                kwargs.get("valuation_report_download_limit", HK_VALUATION_REPORT_DOWNLOAD_LIMIT_DEFAULT)
                or HK_VALUATION_REPORT_DOWNLOAD_LIMIT_DEFAULT
            )
            share_evidence: Any = self._latest_issued_shares_from_reports(
                reports,
                raw_dir=raw_dir / "valuation_reports" if raw_dir else None,
                symbol=symbol.symbol,
                download_limit=max(1, download_limit),
            )
            total_shares: Any = _safe_float(share_evidence.get("total_shares")) if share_evidence else None
            total_market_cap: Optional[float] = None
            source_basis: Any = "official_disclosure"
            share_count_basis: Any = str(share_evidence.get("basis") or "HKEX official annual/interim report issued-share disclosure.") if share_evidence else ""
            market_cap_basis: Any = "Yahoo HK regular_market_price * HKEX official issued shares; listing currency HKD."
            if total_shares is None or total_shares <= 0:
                if quote_market_cap is None or quote_market_cap <= 0 or price is None or price <= 0:
                    raw_path: Optional[str] = None
                    raw_hash: Optional[str] = None
                    if raw_dir:
                        raw_path, raw_hash = save_raw_json(
                            {
                                "reports": reports,
                                "share_evidence": share_evidence,
                                "quote": quote,
                                "download_limit": download_limit,
                                "failure_reason": "HKEX reports did not expose a usable issued-share count and Yahoo quote did not expose both market_cap and regular_market_price",
                            },
                            raw_dir,
                            f"{symbol.symbol}_{dataset.value}_hkex_yahoo_valuation_failed_raw.json",
                        )
                    return DataResult(
                        False,
                        dataset,
                        symbol.symbol,
                        self.name,
                        self.level,
                        utc_now(),
                        raw_path=raw_path,
                        raw_hash=raw_hash,
                        warnings=warnings,
                        errors=[
                            "HKEX reports did not expose a usable issued-share count and Yahoo quote did not expose both market_cap and regular_market_price"
                        ],
                    )
                total_shares = quote_market_cap / price
                total_market_cap = quote_market_cap
                source_basis = "quote_derived_preflight"
                share_count_basis = "Yahoo HK market_cap / regular_market_price preflight; replace with HKEX issued-share evidence before final valuation claims."
                market_cap_basis = "Yahoo HK market_cap field; listing currency HKD."
                share_evidence = {
                    "total_shares": total_shares,
                    "basis": share_count_basis,
                    "as_of_date": quote_as_of,
                    "source": quote_source_name,
                    "source_level": quote_source_level,
                    "market_cap": quote_market_cap,
                    "regular_market_price": price,
                }
                warnings.append("HKEX issued-share evidence was unavailable in the automated path; HK valuation inputs use Yahoo market_cap/price as preflight data.")

            if total_market_cap is None and price is not None and price > 0:
                total_market_cap = price * total_shares
            data: Any = {
                "symbol": symbol.symbol,
                "name": quote.get("name") or listing.get("stock_name") or symbol.name,
                "currency": quote.get("currency") or symbol.currency or "HKD",
                "exchange": symbol.exchange,
                "as_of_date": quote_as_of or share_evidence.get("as_of_date"),
                "regular_market_price": round(price, 6) if price is not None else None,
                "regular_market_time": str(quote.get("regular_market_time") or ""),
                "total_shares": round(total_shares, 6),
                "float_shares": None,
                "total_market_cap": round(total_market_cap, 6) if total_market_cap is not None else None,
                "float_market_cap": None,
                "source_basis": source_basis,
                "share_count_basis": share_count_basis,
                "market_cap_basis": market_cap_basis,
                "requires_l0_l1_verification": True,
                "official_share_evidence": share_evidence,
            }
            raw_path: Any
            raw_hash: Any
            raw_path = raw_hash = None
            if raw_dir:
                raw_path, raw_hash = save_raw_json(
                    {"reports": reports, "share_evidence": share_evidence, "quote": quote},
                    raw_dir,
                    f"{symbol.symbol}_{dataset.value}_hkex_yahoo_valuation_raw.json",
                )
            return DataResult(
                True,
                dataset,
                symbol.symbol,
                self.name,
                self.level,
                utc_now(),
                as_of_date=str(data.get("as_of_date") or ""),
                data=data,
                raw_path=raw_path,
                raw_hash=raw_hash,
                currency=str(data["currency"]),
                warnings=warnings,
            )
        except Exception as exc:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"HK valuation input fetch failed: {type(exc).__name__}: {exc}")

    def _latest_share_count_reports(self, stock_id: str) -> List[Dict[str, Any]]:
        from_date: Any
        to_date: Any
        from_date, to_date = self._date_window(years=3)
        reports: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for title in ["Monthly Return", "Next Day Disclosure Return", "Annual Report", "Interim Report"]:
            try:
                payload: Any = self._query_title_search(
                    stock_id=stock_id,
                    from_date=from_date,
                    to_date=to_date,
                    title=title,
                    row_range=20,
                )
            except Exception:
                continue
            for record in self._records_from_payload(payload):
                key: Any = str(record.get("news_id") or record.get("file_link") or "")
                if key in seen:
                    continue
                seen.add(key)
                record["report_kind"] = self._report_kind(record)
                if record["report_kind"] in {"monthly_return", "next_day_disclosure", "annual", "interim"}:
                    reports.append(record)
        reports.sort(key=lambda report: str(report.get("announcement_datetime") or ""), reverse=True)
        return reports[:6]

    def _latest_issued_shares_from_reports(
        self,
        reports: Sequence[Mapping[str, Any]],
        *,
        raw_dir: Optional[Path],
        symbol: str = "",
        download_limit: int = 3,
    ) -> Optional[Dict[str, Any]]:
        download_errors: List[str] = []
        for report_index, report in enumerate(reports):
            if report_index >= max(1, download_limit):
                break
            if raw_dir and symbol and not report.get("pdf_path"):
                self._attach_report_downloads(
                    [report],
                    raw_dir=raw_dir,
                    symbol=symbol,
                    limit=1,
                    errors=download_errors,
                )
            pdf_path: Any = str(report.get("pdf_path") or "")
            if not pdf_path:
                continue
            page_bundle: Any = self._extract_pdf_pages(pdf_path, max_pages=80, timeout=8)
            pages: Any = page_bundle.get("pages", []) if isinstance(page_bundle, Mapping) else []
            if not isinstance(pages, list) or not pages:
                continue
            combined_text: Any = "\n\n".join(
                f"--- page {page.get('page_number')} ---\n{page.get('text') or ''}"
                for page in pages
                if isinstance(page, Mapping)
            )
            text_path: Any
            text_hash: Any
            text_path = text_hash = None
            if raw_dir:
                text_name: Any = _safe_artifact_name(f"{Path(pdf_path).stem}_valuation_text") + ".txt"
                text_path, text_hash = save_raw_text(combined_text, raw_dir / "extracted_text", text_name)
            extracted: Any = self._extract_issued_shares_from_text(combined_text)
            if not extracted:
                continue
            extracted.update({
                "report_kind": report.get("report_kind"),
                "title": report.get("title"),
                "announcement_date": report.get("announcement_date"),
                "as_of_date": report.get("announcement_date"),
                "pdf_path": pdf_path,
                "pdf_hash": report.get("pdf_hash"),
                "text_path": text_path,
                "text_hash": text_hash,
            })
            if download_errors:
                extracted["download_warnings"] = download_errors
            return extracted
        return None

    @classmethod
    def _extract_issued_shares_from_text(cls, text: str) -> Optional[Dict[str, Any]]:
        normalized: Any = re.sub(r"\s+", " ", text)
        patterns: Any = [
            r"closing\s+balance\s+as\s+at.{0,180}?\d{1,2}\s+[A-Za-z]+\s+\d{4}\s+([0-9][0-9,]{5,})\s+\d+\s+([0-9][0-9,]{5,})",
            r"total\s+number\s+of\s+issued\s+shares\s+of\s+the\s+company\s+was\s+([0-9][0-9,]{5,})",
            r"total\s+number\s+of\s+issued\s+shares\s+was\s+([0-9][0-9,]{5,})",
            r"(?:number|no\.)\s+of\s+issued\s+shares[^0-9]{0,120}([0-9][0-9,]{5,})",
            r"balance\s+at\s+close\s+of\s+(?:the\s+)?(?:preceding|current)\s+month[^0-9]{0,160}([0-9][0-9,]{5,})",
            r"number\s+of\s+issued\s+shares(?:\s+of\s+the\s+company)?[^.]{0,180}?\b(?:was|were|is|are|:)\s*([0-9][0-9,]{5,})",
            r"(?:shares\s+in\s+issue|issued\s+shares)\s+(?:as\s+at|at|on)[^.]{0,180}?\b(?:was|were|is|are|:)?\s*([0-9][0-9,]{5,})",
            r"([0-9][0-9,]{5,})\s+(?:ordinary\s+)?shares\s+(?:in\s+issue|issued\s+and\s+fully\s+paid)",
            r"issued\s+and\s+fully\s+paid[^.]{0,180}?([0-9][0-9,]{5,})\s+(?:ordinary\s+)?shares",
            r"issued\s+share\s+capital[^.]{0,240}?([0-9][0-9,]{5,})\s+(?:shares|ordinary\s+shares)",
        ]
        for pattern in patterns:
            match: Any = re.search(pattern, normalized, flags=re.I)
            if not match:
                continue
            captured: Any = next((group for group in reversed(match.groups()) if group), "")
            value: Any = _safe_float(captured.replace(",", ""))
            if value is None or value <= 0:
                continue
            return {
                "total_shares": value,
                "basis": "HKEX official report issued-share disclosure.",
                "matched_text": match.group(0)[:260],
            }
        return None


class CninfoTencentAdjustedKlineProvider:
    """Build A-share qfq history from Tencent daily rows plus CNINFO corporate actions.

    This provider closes the BJ boundary where Tencent exposes daily rows but not
    a separate qfqday array. It uses CNINFO official distribution announcements
    to construct a forward-adjusted series instead of treating unknown raw rows
    as adjusted.
    """

    name: Any = "CNINFO_Tencent_Adjusted_Kline_L0L2"
    level: Any = SourceLevel.L2
    markets: Any = [Market.CN_A]
    datasets: Any = [Dataset.PRICE_HISTORY_ADJUSTED]
    user_agent: Any = "Mozilla/5.0 serenity-chan-stock-skill/0.1"

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        if symbol.market != Market.CN_A:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "CNINFO/Tencent adjusted history only supports A-share symbols")
        if dataset != Dataset.PRICE_HISTORY_ADJUSTED:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported dataset {dataset.value}")

        raw_dir: Any = Path(kwargs["raw_dir"]) if kwargs.get("raw_dir") else None
        tencent: Any = TencentQuoteKlineProvider()
        base: Any = tencent.fetch(
            symbol,
            dataset,
            range=str(kwargs.get("range", "2y")),
            interval=str(kwargs.get("interval", "1d")),
            raw_dir=raw_dir,
        )
        if not base.ok or not isinstance(base.data, list) or not base.data:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Tencent daily rows unavailable for official adjustment: " + "; ".join(base.errors))
        if base.adjust == "qfq":
            base.source_name = self.name
            return base

        rows: Any = [dict(row) for row in base.data]
        start_date: Any = str(rows[0].get("trade_date") or "")
        end_date: Any = str(rows[-1].get("trade_date") or "")
        actions: Any
        action_errors: Any
        actions, action_errors = self._load_distribution_actions(symbol, raw_dir=raw_dir, start_date=start_date, end_date=end_date)
        if action_errors:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Official corporate-action lookup failed: " + " | ".join(action_errors))
        applicable: Any = [action for action in actions if start_date <= str(action.get("ex_date") or "") <= end_date]
        if not applicable:
            raw_path: Any
            raw_hash: Any
            raw_path = raw_hash = None
            if raw_dir:
                raw_path, raw_hash = save_raw_json(
                    {"base_source": base.source_name, "base_raw_path": base.raw_path, "official_actions": actions, "adjustment": "no official distribution event inside fetched window"},
                    raw_dir,
                    f"{symbol.symbol}_{dataset.value}_cninfo_tencent_adjustment_raw.json",
                )
            return DataResult(
                True,
                dataset,
                symbol.symbol,
                self.name,
                self.level,
                utc_now(),
                as_of_date=rows[-1]["trade_date"],
                data=rows,
                raw_path=raw_path,
                raw_hash=raw_hash,
                currency=symbol.currency or "CNY",
                adjust="qfq_no_official_distribution_in_window",
                warnings=["Tencent daily rows were accepted as qfq-equivalent because CNINFO official announcements show no distribution event inside the fetched window."],
            )

        adjustment_events: Any = self._apply_forward_adjustments(rows, applicable)
        if not adjustment_events:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Official distribution events were found but no prior trading-day factor could be computed")

        raw_path = raw_hash = None
        if raw_dir:
            raw_path, raw_hash = save_raw_json(
                {
                    "base_source": base.source_name,
                    "base_raw_path": base.raw_path,
                    "official_actions": actions,
                    "applied_adjustment_events": adjustment_events,
                },
                raw_dir,
                f"{symbol.symbol}_{dataset.value}_cninfo_tencent_adjustment_raw.json",
            )
        return DataResult(
            True,
            dataset,
            symbol.symbol,
            self.name,
            self.level,
            utc_now(),
            as_of_date=rows[-1]["trade_date"],
            data=rows,
            raw_path=raw_path,
            raw_hash=raw_hash,
            currency=symbol.currency or "CNY",
            adjust="qfq_by_cninfo_distribution",
            warnings=[
                "Tencent daily rows were forward-adjusted using CNINFO official equity-distribution announcements.",
                *[
                    f"{event['ex_date']}: factor={event['factor']:.8f}, cash_per_share={event['cash_per_share']}, share_ratio={event['share_ratio']}"
                    for event in adjustment_events
                ],
            ],
        )

    def _load_distribution_actions(
        self,
        symbol: SymbolInfo,
        *,
        raw_dir: Optional[Path],
        start_date: str,
        end_date: str,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        code: Any
        _: Any
        suffix: Any
        code, _, suffix = symbol.symbol.partition(".")
        cninfo: Any = CninfoAnnouncementsProvider()
        errors: List[str] = []
        actions: List[Dict[str, Any]] = []
        try:
            listing: Any = cninfo._lookup_listing(code)
        except Exception as exc:
            return [], [f"CNINFO listing lookup failed: {type(exc).__name__}: {exc}"]
        if not listing:
            return [], [f"could not resolve CNINFO orgId for {symbol.symbol}"]

        seen: set[str] = set()
        for page_num in range(1, 9):
            try:
                payload: Any = cninfo._query_announcements(code, str(listing.get("orgId") or ""), suffix, page_num=page_num, page_size=30)
            except Exception as exc:
                errors.append(f"page {page_num}: {type(exc).__name__}: {exc}")
                continue
            page_announcements: Any = payload.get("announcements") if isinstance(payload, Mapping) else None
            if not isinstance(page_announcements, list) or not page_announcements:
                break
            for item in page_announcements:
                if not isinstance(item, Mapping):
                    continue
                record: Any = cninfo._normalize_announcement(item)
                title: Any = str(record.get("title") or "")
                if "权益分派实施公告" not in title:
                    continue
                key: Any = str(record.get("announcement_id") or record.get("pdf_url") or title)
                if key in seen:
                    continue
                seen.add(key)
                action: Any = self._parse_distribution_announcement(record, raw_dir=raw_dir)
                if action:
                    actions.append(action)
        actions.sort(key=lambda action: str(action.get("ex_date") or ""))
        if raw_dir:
            save_raw_json(
                {
                    "symbol": symbol.symbol,
                    "scan_window": {"start_date": start_date, "end_date": end_date},
                    "actions": actions,
                    "errors": errors,
                },
                raw_dir,
                f"{symbol.symbol}_cninfo_distribution_actions_raw.json",
            )
        return actions, errors

    def _parse_distribution_announcement(self, record: Mapping[str, Any], *, raw_dir: Optional[Path]) -> Optional[Dict[str, Any]]:
        pdf_url: Any = str(record.get("pdf_url") or "")
        if not pdf_url:
            return None
        try:
            payload: Any = https_bytes(
                pdf_url,
                user_agent=self.user_agent,
                headers={"Referer": "https://www.cninfo.com.cn/"},
                timeout=30,
                max_bytes=20 * 1024 * 1024,
            )
            pdf_path: Any
            pdf_hash: Any
            pdf_path = pdf_hash = None
            if raw_dir:
                pdf_path, pdf_hash = save_raw_bytes(
                    payload,
                    raw_dir / "corporate_actions",
                    _safe_artifact_name(f"{record.get('sec_code')}_{record.get('announcement_date')}_{record.get('title')}") + ".pdf",
                )
                page_bundle: Any = HkexNewsBase()._extract_pdf_pages(pdf_path, max_pages=20, timeout=30)
            else:
                temp_dir: Any = Path(os.getenv("SERENITY_TMP_DIR", "/tmp/serenity-chan-corporate-actions"))
                pdf_path, pdf_hash = save_raw_bytes(payload, temp_dir, _safe_artifact_name(str(record.get("announcement_id") or "distribution")) + ".pdf")
                page_bundle = HkexNewsBase()._extract_pdf_pages(pdf_path, max_pages=20, timeout=30)
            if not page_bundle.get("ok"):
                return None
            text: Any = "\n".join(str(page.get("text") or "") for page in page_bundle.get("pages", []) if isinstance(page, Mapping))
            compact: Any = re.sub(r"\s+", "", text)
            ex_date: Any = self._parse_cn_date(compact, r"除权除息日为[:：]?(\d{4})年(\d{1,2})月(\d{1,2})日")
            record_date: Any = self._parse_cn_date(compact, r"权益登记日为[:：]?(\d{4})年(\d{1,2})月(\d{1,2})日")
            cash_match: Any = re.search(r"每10股派([0-9.]+)元", compact)
            transfer_match: Any = re.search(r"每10股转增([0-9.]+)股", compact)
            bonus_match: Any = re.search(r"每10股送(?:红股)?([0-9.]+)股", compact)
            cash_per_share: Any = float(cash_match.group(1)) / 10.0 if cash_match else 0.0
            transfer_ratio: Any = float(transfer_match.group(1)) / 10.0 if transfer_match else 0.0
            bonus_ratio: Any = float(bonus_match.group(1)) / 10.0 if bonus_match else 0.0
            if not ex_date:
                return None
            text_path: Any
            text_hash: Any
            text_path = text_hash = None
            if raw_dir:
                text_path, text_hash = save_raw_text(
                    text,
                    raw_dir / "corporate_actions" / "extracted_text",
                    _safe_artifact_name(f"{record.get('sec_code')}_{record.get('announcement_date')}_{record.get('title')}_text") + ".txt",
                )
            return {
                "action_type": "equity_distribution",
                "title": record.get("title"),
                "announcement_id": record.get("announcement_id"),
                "announcement_date": record.get("announcement_date"),
                "record_date": record_date,
                "ex_date": ex_date,
                "cash_per_share": cash_per_share,
                "transfer_ratio": transfer_ratio,
                "bonus_ratio": bonus_ratio,
                "share_ratio": transfer_ratio + bonus_ratio,
                "pdf_url": pdf_url,
                "pdf_path": pdf_path,
                "pdf_hash": pdf_hash,
                "text_path": text_path,
                "text_hash": text_hash,
            }
        except Exception:
            return None

    @staticmethod
    def _parse_cn_date(text: str, pattern: str) -> Optional[str]:
        match: Any = re.search(pattern, text)
        if not match:
            return None
        year: Any
        month: Any
        day: Any
        year, month, day = match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    @staticmethod
    def _apply_forward_adjustments(rows: List[Dict[str, Any]], actions: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        for action in sorted(actions, key=lambda item: str(item.get("ex_date") or "")):
            ex_date: Any = str(action.get("ex_date") or "")
            prior_indexes: Any = [idx for idx, row in enumerate(rows) if str(row.get("trade_date") or "") < ex_date]
            if not prior_indexes:
                continue
            previous_index: Any = prior_indexes[-1]
            previous_close: Any = _safe_float(rows[previous_index].get("raw_close") or rows[previous_index].get("close"))
            if previous_close is None or previous_close <= 0:
                continue
            cash_per_share: Any = _safe_float(action.get("cash_per_share")) or 0.0
            share_ratio: Any = (_safe_float(action.get("share_ratio")) or 0.0)
            ex_right_reference: Any = (previous_close - cash_per_share) / (1.0 + share_ratio)
            if ex_right_reference <= 0:
                continue
            factor: Any = ex_right_reference / previous_close
            for idx in prior_indexes:
                row: Any = rows[idx]
                for field in ("open", "high", "low", "close", "adj_close"):
                    value: Any = _safe_float(row.get(field))
                    if value is not None:
                        row[field] = round(value * factor, 6)
                raw_close: Any = _safe_float(row.get("raw_close"))
                if raw_close is not None:
                    row["raw_close"] = round(raw_close, 6)
            events.append({
                "ex_date": ex_date,
                "record_date": action.get("record_date"),
                "cash_per_share": cash_per_share,
                "share_ratio": share_ratio,
                "previous_close": previous_close,
                "ex_right_reference": round(ex_right_reference, 6),
                "factor": factor,
                "source_title": action.get("title"),
                "pdf_hash": action.get("pdf_hash"),
            })
        return events


def _sec_user_agent() -> Tuple[str, List[str]]:
    identity: Any = os.getenv("SEC_USER_AGENT") or os.getenv("EDGAR_IDENTITY")
    if identity:
        return identity, []
    return (
        "serenity-chan-stock-skill/0.1 contact@example.com",
        ["SEC_USER_AGENT or EDGAR_IDENTITY is not set; using bundled placeholder SEC User-Agent. Set a real contact identity for production research."],
    )


_SEC_SUBMISSIONS_CACHE: Dict[str, Mapping[str, Any]] = {}


def _sec_text_tokens(values: Iterable[str]) -> set[str]:
    output: set[str] = set()
    for token in values:
        cleaned: Any = str(token).strip().upper()
        if not cleaned:
            continue
        output.add(cleaned)
        output.add(cleaned.replace(".", "-"))
        output.add(cleaned.replace("-", "."))
    return output


def _sec_symbol_tokens(symbol: SymbolInfo) -> set[str]:
    return _sec_text_tokens([symbol.symbol, symbol.input_value])


def _fetch_sec_submissions_payload(cik: str, *, user_agent: str) -> Mapping[str, Any]:
    padded_cik: Any = f"{int(str(cik)):010d}"
    if padded_cik not in _SEC_SUBMISSIONS_CACHE:
        payload: Any = https_json(f"https://data.sec.gov/submissions/CIK{padded_cik}.json", user_agent=user_agent)
        if not isinstance(payload, Mapping):
            raise ValueError("SEC submissions payload is not an object")
        _SEC_SUBMISSIONS_CACHE[padded_cik] = payload
    return _SEC_SUBMISSIONS_CACHE[padded_cik]


def _sec_submission_matches_tokens(expected: set[str], payload: Mapping[str, Any]) -> bool:
    tickers: Any = payload.get("tickers", [])
    if not isinstance(tickers, list):
        return False
    actual: Any = {str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()}
    expanded_actual: Any = set(actual)
    for ticker in actual:
        expanded_actual.add(ticker.replace(".", "-"))
        expanded_actual.add(ticker.replace("-", "."))
    return bool(expected & expanded_actual)


def _sec_submission_matches_symbol(symbol: SymbolInfo, payload: Mapping[str, Any]) -> bool:
    return _sec_submission_matches_tokens(_sec_symbol_tokens(symbol), payload)


def _sec_identity_error(symbol: SymbolInfo, cik: str, payload: Mapping[str, Any]) -> Optional[str]:
    if _sec_submission_matches_symbol(symbol, payload):
        return None
    return (
        f"SEC CIK {cik} belongs to tickers {payload.get('tickers', [])}, "
        f"not requested symbol {symbol.symbol}"
    )


def _sec_identity_summary(cik: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "cik": f"{int(str(cik)):010d}",
        "entity_name": payload.get("name"),
        "tickers": payload.get("tickers", []),
        "exchanges": payload.get("exchanges", []),
    }


_NUMBER_WORDS: Dict[str, float] = {
    "one": 1.0,
    "two": 2.0,
    "three": 3.0,
    "four": 4.0,
    "five": 5.0,
    "six": 6.0,
    "seven": 7.0,
    "eight": 8.0,
    "nine": 9.0,
    "ten": 10.0,
    "twenty": 20.0,
}


def _number_token(value: str) -> Optional[float]:
    token: Any = re.sub(r"[^0-9A-Za-z.]+", " ", value).strip().lower()
    if not token:
        return None
    numeric: Any = re.search(r"\d+(?:\.\d+)?", token)
    if numeric:
        parsed: Any = _safe_float(numeric.group(0))
        return parsed if parsed and parsed > 0 else None
    for word, number in _NUMBER_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", token):
            return number
    return None


def _extract_ads_ratio_from_text(text: str) -> Optional[Dict[str, Any]]:
    plain: Any = re.sub(r"<[^>]+>", " ", text)
    plain = html.unescape(re.sub(r"\s+", " ", plain))
    patterns: Any = [
        r"(?i)(each|one|1)\s+(?:american\s+depositary\s+share|ads)\s+represents\s+([A-Za-z0-9().\- ]{1,60})\s+(?:common|ordinary)\s+shares?",
        r"(?i)([A-Za-z0-9().\- ]{1,60})\s+(?:common|ordinary)\s+shares?\s+(?:per|for each)\s+(?:american\s+depositary\s+share|ads)",
    ]
    for pattern in patterns:
        match: Any = re.search(pattern, plain)
        if not match:
            continue
        token: Any = match.group(2) if "represents" in pattern else match.group(1)
        ratio: Any = _number_token(token)
        if ratio is None or ratio <= 0:
            continue
        return {
            "ratio": ratio,
            "basis": "SEC annual report ADS/common-share description",
            "evidence_excerpt": match.group(0)[:280],
        }
    return None


def _latest_sec_primary_document(
    cik: str,
    submissions_payload: Mapping[str, Any],
    *,
    forms: set[str],
) -> Optional[Dict[str, str]]:
    recent: Any = submissions_payload.get("filings", {}).get("recent", {}) if isinstance(submissions_payload.get("filings"), Mapping) else {}
    form_values: Any = recent.get("form", []) or []
    accession_numbers: Any = recent.get("accessionNumber", []) or []
    primary_documents: Any = recent.get("primaryDocument", []) or []
    filing_dates: Any = recent.get("filingDate", []) or []
    report_dates: Any = recent.get("reportDate", []) or []
    candidates: List[Dict[str, str]] = []
    for idx, form in enumerate(form_values):
        form_value: Any = str(form or "").upper()
        if form_value not in forms:
            continue
        accession: Any = str(accession_numbers[idx] if idx < len(accession_numbers) else "")
        document: Any = str(primary_documents[idx] if idx < len(primary_documents) else "")
        if not accession or not document:
            continue
        url: Any = f"https://www.sec.gov/Archives/edgar/data/{int(str(cik))}/{accession.replace('-', '')}/{document}"
        candidates.append({
            "form": form_value,
            "accession_number": accession,
            "primary_document": document,
            "filing_date": str(filing_dates[idx] if idx < len(filing_dates) else ""),
            "report_date": str(report_dates[idx] if idx < len(report_dates) else ""),
            "url": url,
        })
    candidates.sort(key=lambda row: (row.get("filing_date", ""), row.get("accession_number", "")))
    return candidates[-1] if candidates else None


def _ads_ratio_from_sec_report(
    cik: str,
    submissions_payload: Mapping[str, Any],
    *,
    user_agent: str,
) -> Optional[Dict[str, Any]]:
    document: Any = _latest_sec_primary_document(cik, submissions_payload, forms={"20-F", "20-F/A", "40-F", "40-F/A"})
    if not document:
        return None
    text: Any = https_text(str(document["url"]), user_agent=user_agent, timeout=30, retries=1)
    ratio: Any = _extract_ads_ratio_from_text(text)
    if not ratio:
        return None
    ratio.update({
        "source": "SEC_PRIMARY_DOCUMENT",
        "form": document.get("form"),
        "accession_number": document.get("accession_number"),
        "primary_document": document.get("primary_document"),
        "url": document.get("url"),
    })
    return ratio


def _ads_ratio_from_paired_otc_history(
    symbol: SymbolInfo,
    submissions_payload: Mapping[str, Any],
    *,
    raw_dir: Optional[str | Path],
) -> Optional[Dict[str, Any]]:
    tickers: Any = [
        str(ticker).strip().upper()
        for ticker in submissions_payload.get("tickers", [])
        if str(ticker).strip()
    ]
    requested: Any = symbol.symbol.upper()
    candidates: Any = [ticker for ticker in tickers if ticker != requested and ticker.endswith("F")]
    if not candidates:
        return None
    provider: Any = YahooChartProvider(name="Yahoo_ADR_Ratio_History_L2")
    requested_history: Any = provider.fetch(symbol, Dataset.PRICE_HISTORY_ADJUSTED, raw_dir=raw_dir, range="6mo")
    if not requested_history.ok or not isinstance(requested_history.data, list):
        return None
    requested_by_date: Any = {
        str(row.get("trade_date")): _safe_float(row.get("close"))
        for row in requested_history.data
        if isinstance(row, Mapping)
    }
    requested_by_date = {date: close for date, close in requested_by_date.items() if close and close > 0}
    for candidate in candidates:
        candidate_symbol: Any = SymbolInfo(
            input_value=candidate,
            symbol=candidate,
            market=Market.US,
            exchange="US",
            currency=symbol.currency or "USD",
        )
        candidate_history: Any = provider.fetch(candidate_symbol, Dataset.PRICE_HISTORY_ADJUSTED, raw_dir=raw_dir, range="6mo")
        if not candidate_history.ok or not isinstance(candidate_history.data, list):
            continue
        candidate_by_date: Any = {
            str(row.get("trade_date")): _safe_float(row.get("close"))
            for row in candidate_history.data
            if isinstance(row, Mapping)
        }
        candidate_by_date = {date: close for date, close in candidate_by_date.items() if close and close > 0}
        common_dates: Any = sorted(set(requested_by_date) & set(candidate_by_date))
        ratios: List[float] = []
        for date in common_dates[-20:]:
            ratio: Any = requested_by_date[date] / candidate_by_date[date]
            if math.isfinite(ratio) and ratio > 0:
                ratios.append(ratio)
        if not ratios:
            continue
        ratios.sort()
        median_ratio: Any = ratios[len(ratios) // 2]
        integer_ratio: Any = round(median_ratio)
        if integer_ratio < 1 or integer_ratio > 10:
            continue
        relative_error: Any = abs(median_ratio - integer_ratio) / max(integer_ratio, 1)
        if relative_error > 0.08:
            continue
        return {
            "ratio": float(integer_ratio),
            "basis": "same-issuer ADR/ordinary OTC adjusted-close ratio",
            "source": "YAHOO_PAIRED_OTC_HISTORY",
            "paired_ticker": candidate,
            "median_price_ratio": median_ratio,
            "relative_error": relative_error,
            "sample_count": len(ratios),
        }
    return None


def _ads_ratio_required(symbol: SymbolInfo, submissions_payload: Mapping[str, Any]) -> bool:
    recent: Any = submissions_payload.get("filings", {}).get("recent", {}) if isinstance(submissions_payload.get("filings"), Mapping) else {}
    forms: Any = {str(form or "").upper() for form in recent.get("form", []) or []}
    return bool(forms & {"20-F", "20-F/A", "40-F", "40-F/A"})


def _resolve_ads_ratio(
    symbol: SymbolInfo,
    cik: str,
    submissions_payload: Mapping[str, Any],
    *,
    user_agent: str,
    raw_dir: Optional[str | Path],
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    attempts: List[Dict[str, Any]] = []
    try:
        ratio: Any = _ads_ratio_from_sec_report(cik, submissions_payload, user_agent=user_agent)
        if ratio:
            attempts.append({"source": "SEC_PRIMARY_DOCUMENT", "status": "OK"})
            return ratio, attempts
        attempts.append({"source": "SEC_PRIMARY_DOCUMENT", "status": "NO_RATIO"})
    except Exception as exc:
        attempts.append({"source": "SEC_PRIMARY_DOCUMENT", "status": "ERROR", "reason": f"{type(exc).__name__}: {exc}"})
    try:
        ratio = _ads_ratio_from_paired_otc_history(symbol, submissions_payload, raw_dir=raw_dir)
        if ratio:
            attempts.append({"source": "YAHOO_PAIRED_OTC_HISTORY", "status": "OK"})
            return ratio, attempts
        attempts.append({"source": "YAHOO_PAIRED_OTC_HISTORY", "status": "NO_RATIO"})
    except Exception as exc:
        attempts.append({"source": "YAHOO_PAIRED_OTC_HISTORY", "status": "ERROR", "reason": f"{type(exc).__name__}: {exc}"})
    return None, attempts


def _sec_bootstrap_path() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "sec_cik_bootstrap.json"


def _sec_cik_from_bootstrap(ticker: str) -> Optional[str]:
    path: Any = _sec_bootstrap_path()
    if not path.exists():
        return None
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    records: Any = payload.get("tickers", {}) if isinstance(payload, Mapping) else {}
    record: Any = None
    if isinstance(records, Mapping):
        for token in _sec_text_tokens([ticker]):
            candidate: Any = records.get(token)
            if isinstance(candidate, Mapping):
                record = candidate
                break
    if not isinstance(record, Mapping):
        return None
    raw_cik: Any = record.get("cik")
    if raw_cik is None:
        return None
    try:
        return f"{int(str(raw_cik)):010d}"
    except ValueError:
        digits: Any = re.sub(r"\D", "", str(raw_cik))
        return digits.zfill(10) if digits else None


def _sec_cik_from_ticker_exchange_json(ticker: str, *, user_agent: str) -> Optional[str]:
    payload: Any = https_json("https://www.sec.gov/files/company_tickers_exchange.json", user_agent=user_agent)
    token: Any = ticker.upper().replace("-", ".")
    fields: Any = payload.get("fields", []) if isinstance(payload, Mapping) else []
    data: Any = payload.get("data", []) if isinstance(payload, Mapping) else []
    if not isinstance(fields, list) or not isinstance(data, list):
        return None
    try:
        ticker_idx: Any = fields.index("ticker")
        cik_idx: Any = fields.index("cik")
    except ValueError:
        return None
    for row in data:
        if not isinstance(row, list) or len(row) <= max(ticker_idx, cik_idx):
            continue
        if str(row[ticker_idx]).upper() == token:
            return f"{int(row[cik_idx]):010d}"
    return None


def _sec_cik_from_company_tickers_json(ticker: str, *, user_agent: str) -> Optional[str]:
    payload: Any = https_json("https://www.sec.gov/files/company_tickers.json", user_agent=user_agent)
    token: Any = ticker.upper().replace("-", ".")
    if not isinstance(payload, Mapping):
        return None
    for row in payload.values():
        if not isinstance(row, Mapping):
            continue
        if str(row.get("ticker", "")).upper() == token:
            return f"{int(row['cik_str']):010d}"
    return None


def _sec_cik_from_ticker_txt(ticker: str, *, user_agent: str) -> Optional[str]:
    text: Any = https_text("https://www.sec.gov/include/ticker.txt", user_agent=user_agent)
    token: Any = ticker.lower().replace("-", ".")
    for line in text.splitlines():
        parts: Any = line.strip().split()
        if len(parts) != 2:
            continue
        raw_ticker: Any
        raw_cik: Any
        raw_ticker, raw_cik = parts
        if raw_ticker.lower() == token:
            return f"{int(raw_cik):010d}"
    return None


def _sec_cik_from_ticker(ticker: str, *, user_agent: str) -> Optional[str]:
    token: Any = ticker.upper().replace("-", ".")
    candidates: List[str] = []

    def add_candidate(cik: Optional[str]) -> None:
        if not cik:
            return
        try:
            normalized: Any = f"{int(str(cik)):010d}"
        except ValueError:
            digits: Any = re.sub(r"\D", "", str(cik))
            normalized = digits.zfill(10) if digits else ""
        if normalized and normalized not in candidates:
            candidates.append(normalized)

    bootstrap_cik: Any = _sec_cik_from_bootstrap(token)
    add_candidate(bootstrap_cik)
    resolvers: Any = [
        _sec_cik_from_ticker_exchange_json,
        _sec_cik_from_company_tickers_json,
        _sec_cik_from_ticker_txt,
    ]
    for resolver in resolvers:
        try:
            cik: Any = resolver(token, user_agent=user_agent)
        except Exception:
            continue
        add_candidate(cik)
    expected_tokens: Any = _sec_text_tokens([token])
    for cik in candidates:
        try:
            payload: Any = _fetch_sec_submissions_payload(cik, user_agent=user_agent)
        except Exception:
            continue
        if _sec_submission_matches_tokens(expected_tokens, payload):
            return cik
    return None


SEC_FINANCIAL_FORMS: Any = {"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A"}


SEC_FINANCIAL_CONCEPT_ROUTES: Dict[str, List[Tuple[str, str]]] = {
    "revenue": [
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "Revenues"),
        ("us-gaap", "SalesRevenueNet"),
        ("us-gaap", "SalesRevenueGoodsNet"),
        ("us-gaap", "SalesRevenueServicesNet"),
        ("ifrs-full", "Revenue"),
        ("ifrs-full", "RevenueFromContractsWithCustomers"),
        ("ifrs-full", "RevenueFromSaleOfGoods"),
    ],
    "gross_profit": [("us-gaap", "GrossProfit"), ("ifrs-full", "GrossProfit")],
    "net_income": [
        ("us-gaap", "NetIncomeLoss"),
        ("us-gaap", "ProfitLoss"),
        ("ifrs-full", "ProfitLoss"),
        ("ifrs-full", "ProfitLossAttributableToOwnersOfParent"),
    ],
    "operating_cash_flow": [
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
        ("ifrs-full", "CashFlowsFromUsedInOperatingActivities"),
    ],
    "assets": [("us-gaap", "Assets"), ("ifrs-full", "Assets")],
    "liabilities": [("us-gaap", "Liabilities"), ("ifrs-full", "Liabilities")],
    "equity": [
        ("us-gaap", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
        ("us-gaap", "StockholdersEquity"),
        ("ifrs-full", "Equity"),
        ("ifrs-full", "EquityAttributableToOwnersOfParent"),
    ],
    "accounts_receivable": [
        ("us-gaap", "AccountsReceivableNetCurrent"),
        ("us-gaap", "AccountsAndOtherReceivablesNetCurrent"),
        ("ifrs-full", "CurrentTradeReceivables"),
        ("ifrs-full", "TradeAndOtherCurrentReceivables"),
    ],
    "inventory": [("us-gaap", "InventoryNet"), ("ifrs-full", "Inventories")],
}


SEC_FINANCIAL_CONCEPTS: Dict[str, List[str]] = {
    field: [concept for _, concept in routes]
    for field, routes in SEC_FINANCIAL_CONCEPT_ROUTES.items()
}


def _sec_concept_key(taxonomy: str, concept: str) -> str:
    return f"{taxonomy}:{concept}"


def _sec_concept_routes_for_names(concept_names: Sequence[str]) -> List[Tuple[str, str]]:
    routes: List[Tuple[str, str]] = []
    for taxonomy in ("us-gaap", "ifrs-full"):
        for concept in concept_names:
            route: Any = (taxonomy, concept)
            if route not in routes:
                routes.append(route)
    return routes


def _all_sec_financial_concept_routes() -> List[Tuple[str, str]]:
    ordered: List[Tuple[str, str]] = []
    for routes in SEC_FINANCIAL_CONCEPT_ROUTES.values():
        for route in routes:
            if route not in ordered:
                ordered.append(route)
    return ordered


def _facts_from_concept_object(
    concept: str,
    concept_obj: Mapping[str, Any],
    *,
    taxonomy: str = "",
    concept_priority: int = 0,
) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    units: Any = concept_obj.get("units", {}) if isinstance(concept_obj.get("units"), Mapping) else {}
    unit: Any = "USD" if "USD" in units else next(iter(units), None)
    if not unit:
        return output
    facts: Any = units.get(unit, [])
    if not isinstance(facts, list):
        return output
    for fact in facts:
        if not isinstance(fact, Mapping):
            continue
        if fact.get("form") not in SEC_FINANCIAL_FORMS:
            continue
        if "val" not in fact or "end" not in fact:
            continue
        output.append({
            "concept": concept,
            "taxonomy": taxonomy,
            "concept_priority": concept_priority,
            "label": concept_obj.get("label", concept),
            "unit": unit,
            "start": fact.get("start"),
            "period": fact.get("end"),
            "fy": fact.get("fy"),
            "fp": fact.get("fp"),
            "form": fact.get("form"),
            "filed": fact.get("filed"),
            "frame": fact.get("frame"),
            "value": fact.get("val"),
            "accession": fact.get("accn"),
        })
    output.sort(key=lambda row: (str(row.get("period") or ""), str(row.get("filed") or ""), str(row.get("concept") or "")))
    return output


def _latest_facts_by_concept(companyfacts: Mapping[str, Any], concept_names: Sequence[str]) -> List[Dict[str, Any]]:
    facts: Any = companyfacts.get("facts", {}) if isinstance(companyfacts.get("facts"), Mapping) else {}
    output: List[Dict[str, Any]] = []
    for concept_priority, (taxonomy, concept) in enumerate(_sec_concept_routes_for_names(concept_names)):
        taxonomy_obj: Any = facts.get(taxonomy, {}) if isinstance(facts.get(taxonomy), Mapping) else {}
        concept_obj: Any = taxonomy_obj.get(concept)
        if isinstance(concept_obj, Mapping):
            output.extend(_facts_from_concept_object(concept, concept_obj, taxonomy=taxonomy, concept_priority=concept_priority))
    output.sort(key=lambda row: (str(row.get("period") or ""), str(row.get("filed") or ""), str(row.get("concept") or "")))
    return output


def _latest_sec_shares_outstanding(companyfacts: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    facts: Any = companyfacts.get("facts", {}) if isinstance(companyfacts.get("facts"), Mapping) else {}
    candidates: List[Dict[str, Any]] = []
    concept_routes: Any = [
        ("dei", "EntityCommonStockSharesOutstanding"),
        ("us-gaap", "CommonStocksIncludingAdditionalPaidInCapital"),
    ]
    for taxonomy, concept in concept_routes:
        taxonomy_obj: Any = facts.get(taxonomy, {}) if isinstance(facts.get(taxonomy), Mapping) else {}
        concept_obj: Any = taxonomy_obj.get(concept)
        if not isinstance(concept_obj, Mapping):
            continue
        units: Any = concept_obj.get("units", {}) if isinstance(concept_obj.get("units"), Mapping) else {}
        for unit, rows in units.items():
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                value: Any = _safe_float(row.get("val"))
                if value is None or value <= 0:
                    continue
                if concept == "CommonStocksIncludingAdditionalPaidInCapital" and "share" not in str(unit).lower():
                    continue
                candidates.append({
                    "concept": concept,
                    "taxonomy": taxonomy,
                    "unit": unit,
                    "value": value,
                    "period": row.get("end"),
                    "filed": row.get("filed"),
                    "form": row.get("form"),
                    "accession": row.get("accn"),
                    "frame": row.get("frame"),
                })
    if not candidates:
        return None
    candidates.sort(key=lambda row: (str(row.get("filed") or ""), str(row.get("period") or ""), str(row.get("concept") or "")))
    return candidates[-1]


SEC_FLOW_FIELDS: Any = {"revenue", "gross_profit", "net_income", "operating_cash_flow"}
SEC_METADATA_FIELDS: Any = ["revenue", "net_income", "operating_cash_flow", "gross_profit", "assets", "liabilities", "equity", "accounts_receivable", "inventory"]


def _sec_period_key_for_fact(field: str, fact: Mapping[str, Any]) -> Tuple[str, str]:
    period: Any = str(fact.get("period") or "")
    if field in SEC_FLOW_FIELDS:
        return period, str(fact.get("fp") or fact.get("form") or fact.get("start") or "")
    return period, ""


def _sec_fact_preferred(row: Mapping[str, Any], field: str, fact: Mapping[str, Any]) -> bool:
    if field not in row:
        return True
    existing_priority: Any = _safe_int(row.get(f"{field}_concept_priority"))
    new_priority: Any = _safe_int(fact.get("concept_priority"))
    if existing_priority is not None and new_priority is not None and existing_priority != new_priority:
        return new_priority < existing_priority
    existing_filed: Any = str(row.get(f"{field}_filed") or "")
    new_filed: Any = str(fact.get("filed") or "")
    if field in SEC_FLOW_FIELDS:
        existing_fy: Any = _safe_int(row.get(f"{field}_fy"))
        new_fy: Any = _safe_int(fact.get("fy"))
        if existing_fy is not None and new_fy is not None and existing_fy != new_fy:
            return bool(new_filed and (not existing_filed or new_filed < existing_filed))
    return new_filed >= existing_filed


def _put_sec_fact(row: Dict[str, Any], field: str, fact: Mapping[str, Any]) -> None:
    if not _sec_fact_preferred(row, field, fact):
        return
    row[field] = fact.get("value")
    row[f"{field}_concept"] = fact.get("concept")
    row[f"{field}_taxonomy"] = fact.get("taxonomy")
    row[f"{field}_unit"] = fact.get("unit")
    row[f"{field}_concept_priority"] = fact.get("concept_priority")
    row[f"{field}_filed"] = fact.get("filed")
    row[f"{field}_form"] = fact.get("form")
    row[f"{field}_fy"] = fact.get("fy")
    row[f"{field}_fp"] = fact.get("fp")
    row[f"{field}_frame"] = fact.get("frame")
    row[f"{field}_accession"] = fact.get("accession")
    row[f"{field}_period_start"] = fact.get("start")


def _set_sec_row_metadata(row: Dict[str, Any]) -> None:
    for field in SEC_METADATA_FIELDS:
        if row.get(field) is None:
            continue
        for source_key, target_key in [
            ("fy", "fy"),
            ("fp", "fp"),
            ("form", "form"),
            ("filed", "filed"),
            ("frame", "frame"),
            ("accession", "accession"),
            ("period_start", "period_start"),
        ]:
            value: Any = row.get(f"{field}_{source_key}")
            if value not in (None, ""):
                row[target_key] = value
        return


def _period_rows_from_field_facts(field_facts: Mapping[str, Sequence[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    rows_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
    rows_by_period: Dict[str, List[Dict[str, Any]]] = {}

    for field, facts in field_facts.items():
        if field not in SEC_FLOW_FIELDS:
            continue
        for fact in facts:
            key: Any = _sec_period_key_for_fact(field, fact)
            if not key[0]:
                continue
            row: Any = rows_by_key.setdefault(key, {"period": fact.get("period")})
            period_rows: Any = rows_by_period.setdefault(key[0], [])
            if row not in period_rows:
                period_rows.append(row)
            _put_sec_fact(row, field, fact)

    for field, facts in field_facts.items():
        if field in SEC_FLOW_FIELDS:
            continue
        for fact in facts:
            period: Any = str(fact.get("period") or "")
            if not period:
                continue
            target_rows: Any = rows_by_period.get(period)
            if not target_rows:
                key = _sec_period_key_for_fact(field, fact)
                row = rows_by_key.setdefault(key, {"period": fact.get("period")})
                rows_by_period.setdefault(period, []).append(row)
                target_rows = [row]
            for row in target_rows:
                _put_sec_fact(row, field, fact)

    for row in rows_by_key.values():
        _set_sec_row_metadata(row)

    core_fields: Any = ["revenue", "net_income", "operating_cash_flow", "assets", "liabilities", "equity"]
    rows: Any = [
        row
        for row in rows_by_key.values()
        if any(row.get(field) is not None for field in core_fields)
    ]
    for row in rows:
        if "liabilities" not in row and "assets" in row and "equity" in row:
            assets: Any = _safe_float(row.get("assets"))
            equity: Any = _safe_float(row.get("equity"))
            if assets is not None and equity is not None:
                row["liabilities"] = assets - equity
                row["liabilities_concept"] = "derived_from_assets_minus_equity"
    rows.sort(key=lambda row: (str(row.get("period") or ""), str(row.get("filed") or "")))
    return rows[-16:]


def _period_rows_from_sec_facts(companyfacts: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return _period_rows_from_field_facts({
        field: _latest_facts_by_concept(companyfacts, SEC_FINANCIAL_CONCEPTS[field])
        for field in SEC_FINANCIAL_CONCEPTS
    })


def _period_rows_from_companyconcepts(concept_payloads: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    field_facts: Dict[str, List[Dict[str, Any]]] = {}
    for field, routes in SEC_FINANCIAL_CONCEPT_ROUTES.items():
        facts: List[Dict[str, Any]] = []
        for concept_priority, (taxonomy, concept) in enumerate(routes):
            payload: Any = concept_payloads.get(_sec_concept_key(taxonomy, concept))
            if isinstance(payload, Mapping):
                facts.extend(_facts_from_concept_object(concept, payload, taxonomy=taxonomy, concept_priority=concept_priority))
        field_facts[field] = facts
    return _period_rows_from_field_facts(field_facts)


def _sec_reporting_currency(periods: Sequence[Mapping[str, Any]]) -> Optional[str]:
    core_fields: Any = ["revenue", "net_income", "operating_cash_flow", "assets", "liabilities", "equity"]
    for row in reversed(periods):
        units: Any = {
            str(row.get(f"{field}_unit") or "").strip()
            for field in core_fields
            if row.get(field) is not None and row.get(f"{field}_unit")
        }
        units.discard("")
        if len(units) == 1:
            return next(iter(units))
    return None


class SecCompanyFactsProvider:
    """Official SEC JSON adapter for US company facts and submissions."""

    name: Any = "SEC_Companyfacts_L0"
    level: Any = SourceLevel.L0
    markets: Any = [Market.US]
    datasets: Any = [Dataset.FINANCIALS, Dataset.FILINGS, Dataset.SHARE_CAPITAL, Dataset.VALUATION_INPUTS]

    def __init__(self) -> None:
        self._cik_cache: Dict[str, str] = {}

    def _resolve_cik(self, symbol: SymbolInfo, *, user_agent: str) -> Optional[str]:
        if symbol.cik:
            return str(symbol.cik).zfill(10)
        token: Any = symbol.symbol.upper()
        if token not in self._cik_cache:
            cik: Any = _sec_cik_from_ticker(token, user_agent=user_agent)
            if cik:
                self._cik_cache[token] = cik
        return self._cik_cache.get(token)

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        if symbol.market != Market.US:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "SEC JSON only supports US/SEC filers")
        user_agent: Any
        warnings: Any
        user_agent, warnings = _sec_user_agent()
        try:
            cik: Any = self._resolve_cik(symbol, user_agent=user_agent)
            if not cik:
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"could not resolve SEC CIK for {symbol.symbol}")
            raw_dir: Any = kwargs.get("raw_dir")
            if dataset == Dataset.FILINGS:
                return self._fetch_filings(symbol, cik, user_agent=user_agent, raw_dir=raw_dir, warnings=warnings)
            if dataset == Dataset.FINANCIALS:
                return self._fetch_financials(symbol, cik, user_agent=user_agent, raw_dir=raw_dir, warnings=warnings)
            if dataset in {Dataset.SHARE_CAPITAL, Dataset.VALUATION_INPUTS}:
                return self._fetch_valuation_inputs(symbol, cik, dataset, user_agent=user_agent, raw_dir=raw_dir, warnings=warnings)
        except Exception as exc:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"SEC JSON fetch failed: {type(exc).__name__}: {exc}")
        return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported dataset {dataset.value}")

    def _fetch_valuation_inputs(
        self,
        symbol: SymbolInfo,
        cik: str,
        dataset: Dataset,
        *,
        user_agent: str,
        raw_dir: Optional[str | Path],
        warnings: List[str],
    ) -> DataResult:
        submissions_payload: Any = _fetch_sec_submissions_payload(cik, user_agent=user_agent)
        identity_error: Any = _sec_identity_error(symbol, cik, submissions_payload)
        if identity_error:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, identity_error)
        url: Any = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        payload: Any = https_json(url, user_agent=user_agent)
        share_fact: Any = _latest_sec_shares_outstanding(payload)
        if not share_fact:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "SEC companyfacts returned no usable common shares outstanding fact")
        total_shares: Any = _safe_float(share_fact.get("value"))
        if total_shares is None or total_shares <= 0:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "SEC companyfacts share count is not positive")
        valuation_shares: Any = total_shares
        ads_ratio: Optional[Dict[str, Any]] = None
        quote: Mapping[str, Any] = {}
        quote_result: Optional[DataResult] = None
        price: Optional[float] = None
        total_market_cap: Optional[float] = None
        ads_ratio_attempts: List[Dict[str, Any]] = []
        source_basis: Any = "official_disclosure"
        market_cap_basis: Any = "Current market-cap fields were not requested for share_capital."
        valuation_warnings: Any = list(warnings)
        if dataset == Dataset.VALUATION_INPUTS:
            quote_result = YahooChartProvider().fetch(symbol, Dataset.CURRENT_QUOTE, raw_dir=raw_dir)
            if not quote_result.ok:
                reason: Any = "; ".join(quote_result.errors) or "Yahoo current quote did not return usable data"
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"SEC share count resolved, but current quote is unavailable for valuation inputs: {reason}")
            quote = quote_result.data if isinstance(quote_result.data, Mapping) else {}
            price = _safe_float(quote.get("regular_market_price"))
            if price is None or price <= 0:
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "SEC share count resolved, but current quote price is not positive")
            if _ads_ratio_required(symbol, submissions_payload):
                ads_ratio, ads_ratio_attempts = _resolve_ads_ratio(symbol, cik, submissions_payload, user_agent=user_agent, raw_dir=raw_dir)
                ratio_value: Any = _safe_float(ads_ratio.get("ratio") if isinstance(ads_ratio, Mapping) else None)
                if ratio_value is None or ratio_value <= 0:
                    attempt_summary: Any = "; ".join(
                        f"{item.get('source')}={item.get('status')}{': ' + str(item.get('reason')) if item.get('reason') else ''}"
                        for item in ads_ratio_attempts
                    ) or "no ADS-ratio source attempted"
                    return DataResult.failed(
                        dataset,
                        symbol.symbol,
                        self.name,
                        self.level,
                        "SEC share count appears to be underlying ordinary shares for an ADR/foreign issuer; "
                        f"ADS ratio is required before deriving market cap from the US quote. Attempts: {attempt_summary}",
                    )
                valuation_shares = total_shares / ratio_value
                valuation_warnings.append(
                    f"ADR valuation uses ADS-equivalent shares derived from SEC underlying shares divided by ADS ratio {ratio_value:g}."
                )
            total_market_cap = price * valuation_shares
            source_basis = "quote_derived_preflight"
            market_cap_basis = "Yahoo regular_market_price * SEC official shares outstanding; listing currency USD."
            if ads_ratio:
                market_cap_basis = "Yahoo regular_market_price * SEC shares outstanding adjusted to ADS-equivalent shares by resolved ADS ratio."
            if quote_result.source_level == SourceLevel.L2:
                valuation_warnings.append("US valuation_inputs combine SEC official share count with an L2 current quote; verify before final valuation claims.")

        share_count_basis: Any = f"SEC {share_fact.get('taxonomy')}:{share_fact.get('concept')} filed={share_fact.get('filed')} form={share_fact.get('form')}"
        if ads_ratio:
            ratio_value = _safe_float(ads_ratio.get("ratio"))
            if ratio_value is not None:
                share_count_basis = f"SEC underlying shares converted to ADS-equivalent shares by ADS ratio {ratio_value:g}"

        raw_path: Any
        raw_hash: Any
        raw_path = raw_hash = None
        if raw_dir:
            raw_payload: Mapping[str, Any]
            if dataset == Dataset.VALUATION_INPUTS:
                raw_payload = {
                    "sec_companyfacts": payload,
                    "sec_submission_identity": _sec_identity_summary(cik, submissions_payload),
                    "share_fact": share_fact,
                    "ads_ratio": ads_ratio,
                    "ads_ratio_attempts": ads_ratio_attempts,
                    "quote": quote,
                    "quote_source": quote_result.source_name if quote_result else None,
                    "quote_raw_path": quote_result.raw_path if quote_result else None,
                    "quote_raw_hash": quote_result.raw_hash if quote_result else None,
                }
            else:
                raw_payload = payload
            raw_path, raw_hash = save_raw_json(raw_payload, raw_dir, f"{symbol.symbol}_{dataset.value}_sec_companyfacts_raw.json")
        data: Any = {
            "symbol": symbol.symbol,
            "name": quote.get("name") or payload.get("entityName") or submissions_payload.get("name"),
            "cik": cik,
            "currency": quote.get("currency") or symbol.currency or "USD",
            "exchange": quote.get("exchange") or symbol.exchange,
            "as_of_date": quote_result.as_of_date if quote_result and quote_result.as_of_date else share_fact.get("period") or share_fact.get("filed"),
            "regular_market_price": round(price, 6) if price is not None else None,
            "regular_market_time": str(quote.get("regular_market_time") or ""),
            "total_shares": round(valuation_shares, 6) if valuation_shares is not None else None,
            "float_shares": None,
            "total_market_cap": round(total_market_cap, 6) if total_market_cap is not None else None,
            "float_market_cap": None,
            "source_basis": source_basis,
            "share_count_basis": share_count_basis,
            "market_cap_basis": market_cap_basis,
            "requires_l0_l1_verification": dataset == Dataset.VALUATION_INPUTS,
            "identity_check": _sec_identity_summary(cik, submissions_payload),
            "source_fact": share_fact,
        }
        if ads_ratio:
            data.update({
                "underlying_total_shares": round(total_shares, 6),
                "underlying_share_count_basis": f"SEC {share_fact.get('taxonomy')}:{share_fact.get('concept')} filed={share_fact.get('filed')} form={share_fact.get('form')}",
                "ads_ratio": ads_ratio.get("ratio"),
                "ads_ratio_basis": ads_ratio.get("basis"),
                "ads_ratio_source": ads_ratio.get("source"),
                "ads_ratio_evidence": {k: v for k, v in ads_ratio.items() if k not in {"ratio", "basis", "source"}},
            })
        return DataResult(
            True,
            dataset,
            symbol.symbol,
            self.name,
            self.level,
            utc_now(),
            as_of_date=data["as_of_date"],
            data=data,
            raw_path=raw_path,
            raw_hash=raw_hash,
            currency=data["currency"],
            warnings=valuation_warnings,
        )

    def _fetch_filings(
        self,
        symbol: SymbolInfo,
        cik: str,
        *,
        user_agent: str,
        raw_dir: Optional[str | Path],
        warnings: List[str],
    ) -> DataResult:
        payload: Any = _fetch_sec_submissions_payload(cik, user_agent=user_agent)
        identity_error: Any = _sec_identity_error(symbol, cik, payload)
        if identity_error:
            return DataResult.failed(Dataset.FILINGS, symbol.symbol, self.name, self.level, identity_error)
        raw_path: Any
        raw_hash: Any
        raw_path = raw_hash = None
        if raw_dir:
            raw_path, raw_hash = save_raw_json(payload, raw_dir, f"{symbol.symbol}_sec_submissions_raw.json")
        recent: Any = payload.get("filings", {}).get("recent", {}) if isinstance(payload.get("filings"), Mapping) else {}
        forms: Any = recent.get("form", []) or []
        filing_dates: Any = recent.get("filingDate", []) or []
        report_dates: Any = recent.get("reportDate", []) or []
        accession_numbers: Any = recent.get("accessionNumber", []) or []
        primary_documents: Any = recent.get("primaryDocument", []) or []
        filings: List[Dict[str, Any]] = []
        for idx, form in enumerate(forms[:80]):
            filings.append({
                "form": form,
                "filing_date": filing_dates[idx] if idx < len(filing_dates) else None,
                "report_date": report_dates[idx] if idx < len(report_dates) else None,
                "accession_number": accession_numbers[idx] if idx < len(accession_numbers) else None,
                "primary_document": primary_documents[idx] if idx < len(primary_documents) else None,
            })
        if not filings:
            return DataResult.failed(Dataset.FILINGS, symbol.symbol, self.name, self.level, "SEC submissions returned no recent filings")
        return DataResult(
            True,
            Dataset.FILINGS,
            symbol.symbol,
            self.name,
            self.level,
            utc_now(),
            as_of_date=filings[0].get("filing_date"),
            data={
                "cik": cik,
                "entity_name": payload.get("name"),
                "tickers": payload.get("tickers", []),
                "recent_filings": filings,
                "identity_check": _sec_identity_summary(cik, payload),
            },
            raw_path=raw_path,
            raw_hash=raw_hash,
            warnings=warnings,
        )

    def _fetch_financials(
        self,
        symbol: SymbolInfo,
        cik: str,
        *,
        user_agent: str,
        raw_dir: Optional[str | Path],
        warnings: List[str],
    ) -> DataResult:
        submissions_payload: Any = _fetch_sec_submissions_payload(cik, user_agent=user_agent)
        identity_error: Any = _sec_identity_error(symbol, cik, submissions_payload)
        if identity_error:
            return DataResult.failed(Dataset.FINANCIALS, symbol.symbol, self.name, self.level, identity_error)
        url: Any = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        payload: Any = https_json(url, user_agent=user_agent)
        raw_path: Any
        raw_hash: Any
        raw_path = raw_hash = None
        if raw_dir:
            raw_path, raw_hash = save_raw_json(payload, raw_dir, f"{symbol.symbol}_sec_companyfacts_raw.json")
        periods: Any = _period_rows_from_sec_facts(payload)
        facts: Any = _latest_facts_by_concept(payload, [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
            "GrossProfit",
            "NetIncomeLoss",
            "NetCashProvidedByUsedInOperatingActivities",
            "Assets",
            "Liabilities",
            "StockholdersEquity",
        ])
        if not periods and not facts:
            return DataResult.failed(Dataset.FINANCIALS, symbol.symbol, self.name, self.level, "SEC companyfacts returned no usable financial facts")
        as_of: Any = max((str(row.get("filed") or row.get("period") or "") for row in periods), default=None)
        reporting_currency: Any = _sec_reporting_currency(periods) or symbol.currency
        return DataResult(
            True,
            Dataset.FINANCIALS,
            symbol.symbol,
            self.name,
            self.level,
            utc_now(),
            as_of_date=as_of,
            data={
                "cik": cik,
                "entity_name": payload.get("entityName"),
                "currency": reporting_currency,
                "unit": reporting_currency,
                "period_basis": "SEC XBRL facts from 10-K, 10-Q, 20-F, or 40-F; income and cash-flow rows are reported for the period, balance-sheet rows are period-end.",
                "periods": periods,
                "latest_facts": facts[-40:],
                "identity_check": _sec_identity_summary(cik, submissions_payload),
            },
            raw_path=raw_path,
            raw_hash=raw_hash,
            currency=reporting_currency,
            warnings=warnings,
        )


class SecCompanyConceptsProvider:
    """Official SEC companyconcept adapter for US financial statements.

    This provider fetches smaller per-concept SEC XBRL payloads. It is useful
    when the larger companyfacts endpoint is blocked, reset, or too large for
    the current network path.
    """

    name: Any = "SEC_CompanyConcepts_L0"
    level: Any = SourceLevel.L0
    markets: Any = [Market.US]
    datasets: Any = [Dataset.FINANCIALS]

    def __init__(self) -> None:
        self._cik_cache: Dict[str, str] = {}

    def _resolve_cik(self, symbol: SymbolInfo, *, user_agent: str) -> Optional[str]:
        if symbol.cik:
            return str(symbol.cik).zfill(10)
        token: Any = symbol.symbol.upper()
        if token not in self._cik_cache:
            cik: Any = _sec_cik_from_ticker(token, user_agent=user_agent)
            if cik:
                self._cik_cache[token] = cik
        return self._cik_cache.get(token)

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        if symbol.market != Market.US:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "SEC companyconcept only supports US/SEC filers")
        if dataset != Dataset.FINANCIALS:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported dataset {dataset.value}")
        user_agent: Any
        warnings: Any
        user_agent, warnings = _sec_user_agent()
        try:
            cik: Any = self._resolve_cik(symbol, user_agent=user_agent)
            if not cik:
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"could not resolve SEC CIK for {symbol.symbol}")
            return self._fetch_financials(symbol, cik, user_agent=user_agent, raw_dir=kwargs.get("raw_dir"), warnings=warnings)
        except Exception as exc:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"SEC companyconcept fetch failed: {type(exc).__name__}: {exc}")

    def _fetch_financials(
        self,
        symbol: SymbolInfo,
        cik: str,
        *,
        user_agent: str,
        raw_dir: Optional[str | Path],
        warnings: List[str],
    ) -> DataResult:
        submissions_payload: Any = _fetch_sec_submissions_payload(cik, user_agent=user_agent)
        identity_error: Any = _sec_identity_error(symbol, cik, submissions_payload)
        if identity_error:
            return DataResult.failed(Dataset.FINANCIALS, symbol.symbol, self.name, self.level, identity_error)
        concept_payloads: Dict[str, Mapping[str, Any]] = {}
        concept_errors: List[str] = []
        for taxonomy, concept in _all_sec_financial_concept_routes():
            concept_key: Any = _sec_concept_key(taxonomy, concept)
            url: Any = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/{taxonomy}/{concept}.json"
            try:
                payload: Any = https_json(url, user_agent=user_agent, retries=1)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    concept_errors.append(f"{concept_key}: not reported")
                    continue
                concept_errors.append(f"{concept_key}: HTTP {exc.code}")
                continue
            except Exception as exc:
                concept_errors.append(f"{concept_key}: {type(exc).__name__}: {exc}")
                continue
            if isinstance(payload, Mapping):
                concept_payloads[concept_key] = payload

        if not concept_payloads:
            return DataResult.failed(
                Dataset.FINANCIALS,
                symbol.symbol,
                self.name,
                self.level,
                "SEC companyconcept returned no usable concepts: " + " | ".join(concept_errors[:8]),
            )

        raw_path: Any
        raw_hash: Any
        raw_path = raw_hash = None
        if raw_dir:
            raw_path, raw_hash = save_raw_json(
                {
                    "cik": cik,
                    "symbol": symbol.symbol,
                    "concept_payloads": concept_payloads,
                    "concept_errors": concept_errors,
                },
                raw_dir,
                f"{symbol.symbol}_sec_companyconcepts_raw.json",
            )

        periods: Any = _period_rows_from_companyconcepts(concept_payloads)
        latest_facts: List[Dict[str, Any]] = []
        for concept_key, payload in concept_payloads.items():
            taxonomy: Any
            _: Any
            concept: Any
            taxonomy, _, concept = concept_key.partition(":")
            latest_facts.extend(_facts_from_concept_object(concept, payload, taxonomy=taxonomy))
        latest_facts.sort(key=lambda row: (str(row.get("period") or ""), str(row.get("filed") or ""), str(row.get("concept") or "")))

        if not periods and not latest_facts:
            return DataResult.failed(
                Dataset.FINANCIALS,
                symbol.symbol,
                self.name,
                self.level,
                "SEC companyconcept returned payloads but no 10-K/10-Q financial facts",
            )

        entity_name: Any = next(
            (
                str(payload.get("entityName"))
                for payload in concept_payloads.values()
                if isinstance(payload, Mapping) and payload.get("entityName")
            ),
            None,
        )
        as_of: Any = max((str(row.get("filed") or row.get("period") or "") for row in periods), default=None)
        reporting_currency: Any = _sec_reporting_currency(periods) or symbol.currency
        if concept_errors:
            warnings = list(warnings) + ["Some SEC concepts were unavailable: " + " | ".join(concept_errors[:8])]
        return DataResult(
            True,
            Dataset.FINANCIALS,
            symbol.symbol,
            self.name,
            self.level,
            utc_now(),
            as_of_date=as_of,
            data={
                "cik": cik,
                "entity_name": entity_name,
                "currency": reporting_currency,
                "unit": reporting_currency,
                "period_basis": "SEC companyconcept XBRL facts from 10-K, 10-Q, 20-F, or 40-F; income and cash-flow rows are reported for the period, balance-sheet rows are period-end.",
                "periods": periods,
                "latest_facts": latest_facts[-40:],
                "concepts_fetched": sorted(concept_payloads),
                "identity_check": _sec_identity_summary(cik, submissions_payload),
            },
            raw_path=raw_path,
            raw_hash=raw_hash,
            currency=reporting_currency,
            warnings=warnings,
        )


class SecEdgarProvider:
    """Optional US filings/fundamentals provider using edgartools if installed.

    Production notes:
    - SEC access requires a real identity/User-Agent.
    - Keep SEC-reported facts separate from market estimates.
    - This skeleton returns failure if edgartools is unavailable or identity is missing.
    """

    name: Any = "SEC_EDGAR_edgartools"
    level: Any = SourceLevel.L0
    markets: Any = [Market.US]
    datasets: Any = [Dataset.FILINGS, Dataset.FINANCIALS]

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        if symbol.market != Market.US:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "SEC EDGAR only supports US/SEC filers")
        identity: Any = os.getenv("EDGAR_IDENTITY")
        if not identity:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "EDGAR_IDENTITY env var is required")
        try:
            from edgar import Company, set_identity  # type: ignore
            set_identity(identity)
            company: Any = Company(symbol.symbol)
            if dataset == Dataset.FILINGS:
                filings: Any = company.get_filings()
                return DataResult(True, dataset, symbol.symbol, self.name, self.level, utc_now(), data=filings, currency=symbol.currency)
            if dataset == Dataset.FINANCIALS:
                financials: Any = company.get_financials()
                return DataResult(True, dataset, symbol.symbol, self.name, self.level, utc_now(), data=financials, currency=symbol.currency)
        except Exception as exc:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"edgartools failed: {type(exc).__name__}: {exc}")
        return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported dataset {dataset.value}")


def default_real_providers(symbol: Optional[SymbolInfo] = None) -> List[DataProvider]:
    providers: List[DataProvider] = []
    if symbol is None or symbol.market == Market.CN_A:
        providers.append(EastmoneyQuoteKlineProvider())
        providers.append(CninfoTencentAdjustedKlineProvider())
        providers.append(TencentQuoteKlineProvider())
    if symbol is None or symbol.market in {Market.US, Market.HK, Market.CN_A}:
        providers.append(YahooChartProvider())
        providers.append(YahooChartProvider(name="Yahoo_Chart_Query2_L2", host="query2.finance.yahoo.com"))
    if symbol is None or symbol.market == Market.HK:
        providers.append(HkexValuationInputsProvider())
        providers.append(HkexAnnouncementsProvider())
        providers.append(HkexFinancialReportsProvider())
    if symbol is None or symbol.market == Market.CN_A:
        providers.append(CninfoAnnouncementsProvider())
        providers.append(CninfoFinancialReportsProvider())
        providers.append(EastmoneyF10FinancialsProvider())
    if symbol is None or symbol.market in {Market.CN_A, Market.HK, Market.US}:
        providers.append(DisclosureCustomerEvidenceProvider())
    if symbol is None or symbol.market == Market.US:
        providers.append(SecCompanyFactsProvider())
        providers.append(SecCompanyConceptsProvider())
        if os.getenv("EDGAR_IDENTITY"):
            providers.append(SecEdgarProvider())
    return providers


# Real Tushare/Wind/Choice/AKShare adapters should implement DataProvider.
# Keep credentialed adapters separate so credentials, rate limits, and legal usage
# are explicit. The free adapters above are preflight/auxiliary sources, not a substitute
# for official filings or licensed structured financial-data sources.


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse
    parser: Any = argparse.ArgumentParser(description="Serenity + Chan data routing helper")
    parser.add_argument("symbol", nargs="?", default="688019", help="ticker or stock code")
    parser.add_argument("--plan", action="store_true", help="print data fetch plan JSON")
    args: Any = parser.parse_args()

    if args.plan:
        print(json.dumps(build_data_fetch_plan(args.symbol), ensure_ascii=False, indent=2, default=str))
        return

    symbol: Any = resolve_symbol(args.symbol)
    print(json.dumps(symbol.__dict__, ensure_ascii=False, indent=2, default=str))
    for d in [Dataset.CURRENT_QUOTE, Dataset.FINANCIALS, Dataset.FILINGS, Dataset.PRICE_HISTORY_ADJUSTED]:
        print(json.dumps(source_policy(symbol.market, d).__dict__, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
