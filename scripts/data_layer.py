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
    pd = None  # type: ignore

try:
    from data_contracts import DataStatus, Dataset, Market, RatingCap, SourceLevel
except ModuleNotFoundError:  # pragma: no cover - supports python -m scripts.data_layer
    from scripts.data_contracts import DataStatus, Dataset, Market, RatingCap, SourceLevel


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

RATING_ORDER = [RatingCap.S, RatingCap.A, RatingCap.B, RatingCap.C, RatingCap.D, RatingCap.OBSERVE_ONLY]


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def file_sha256(path: str | Path) -> str:
    p = Path(path)
    h = sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def save_raw_json(obj: Any, raw_dir: str | Path, name: str) -> Tuple[str, str]:
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / name
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return str(path), file_sha256(path)


def save_raw_bytes(data: bytes, raw_dir: str | Path, name: str) -> Tuple[str, str]:
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / name
    path.write_bytes(data)
    return str(path), file_sha256(path)


def save_raw_text(text: str, raw_dir: str | Path, name: str) -> Tuple[str, str]:
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / name
    path.write_text(text, encoding="utf-8")
    return str(path), file_sha256(path)


def _safe_artifact_name(value: str, *, max_length: int = 120) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("._-")
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
    merged_headers = {
        "User-Agent": user_agent,
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    }
    if headers:
        merged_headers.update(headers)

    try:
        import certifi  # type: ignore

        context = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        context = ssl.create_default_context()

    last_error: Optional[BaseException] = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, headers=merged_headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                raw = response.read()
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
    merged_headers = {
        "User-Agent": user_agent,
        "Accept": "text/plain,*/*",
        "Accept-Encoding": "gzip",
    }
    if headers:
        merged_headers.update(headers)

    try:
        import certifi  # type: ignore

        context = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        context = ssl.create_default_context()

    last_error: Optional[BaseException] = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, headers=merged_headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                raw = response.read()
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
    merged_headers = {
        "User-Agent": user_agent,
        "Accept": "application/pdf,*/*",
        "Accept-Encoding": "gzip",
    }
    if headers:
        merged_headers.update(headers)

    try:
        import certifi  # type: ignore

        context = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        context = ssl.create_default_context()

    last_error: Optional[BaseException] = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, headers=merged_headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > max_bytes:
                    raise RuntimeError(f"artifact is too large: {content_length} bytes > {max_bytes}")
                raw = response.read(max_bytes + 1)
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
    merged_headers = {
        "User-Agent": user_agent,
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if headers:
        merged_headers.update(headers)

    body = urllib.parse.urlencode(form).encode("utf-8")
    context = None
    if url.lower().startswith("https://"):
        try:
            import certifi  # type: ignore

            context = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            context = ssl.create_default_context()

    last_error: Optional[BaseException] = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, data=body, headers=merged_headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                raw = response.read()
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

CN_A_SH_PREFIX = ("600", "601", "603", "605", "688", "689")
CN_A_SZ_PREFIX = ("000", "001", "002", "003", "300", "301")
CN_A_BJ_PREFIX = ("430", "830", "831", "832", "833", "834", "835", "836", "837", "838", "839", "870", "871", "872", "873", "920")


def resolve_symbol(input_value: str, *, master_table: Optional[Mapping[str, Mapping[str, Any]]] = None) -> SymbolInfo:
    """Resolve a ticker-like input into a market-aware SymbolInfo.

    This resolver is conservative. It handles common forms and allows an optional
    master table to override/confirm exchange/name/currency. For production,
    use a licensed or official security master and keep the result traceable.
    """
    raw = input_value.strip()
    token = raw.upper().replace(" ", "")

    if master_table and token in master_table:
        row = master_table[token]
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
    m = re.fullmatch(r"(\d{6})\.(SH|SZ|BJ|SS)", token)
    if m:
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
        }
        return policies.get(dataset, SourcePolicy(market, dataset, ["CNINFO/SSE/SZSE/BSE as applicable"], ["Wind/Choice/CSMAR/Tushare"], ["AKShare/Eastmoney"], ["SEC EDGAR"]))

    if market == Market.US:
        policies = {
            Dataset.FILINGS: SourcePolicy(market, dataset, ["SEC EDGAR", "Company IR"], ["edgartools", "licensed vendor"], ["company website"], ["CNINFO"], "10-K/10-Q/8-K/S-1/S-3/20-F/6-K as applicable."),
            Dataset.FINANCIALS: SourcePolicy(market, dataset, ["SEC XBRL", "SEC filings"], ["FactSet", "Koyfin", "TIKR", "Visible Alpha"], ["yfinance", "FMP"], ["CNINFO"], "Separate reported facts from estimates."),
            Dataset.CURRENT_QUOTE: SourcePolicy(market, dataset, ["exchange/vendor"], ["FactSet", "Koyfin", "Bloomberg"], ["yfinance", "Nasdaq/Yahoo"], ["CNINFO"], "Check split/dividend adjustments."),
            Dataset.PRICE_HISTORY_ADJUSTED: SourcePolicy(market, dataset, ["exchange/vendor"], ["FactSet", "Koyfin", "Bloomberg"], ["yfinance", "Stooq"], ["CNINFO"], "Use adjusted series consistently."),
            Dataset.ESTIMATES: SourcePolicy(market, dataset, ["company guidance"], ["FactSet", "Visible Alpha", "Koyfin", "TIKR"], ["SeekingAlpha", "Yahoo Analysis"], ["CNINFO"], "Consensus is not reported fact."),
        }
        return policies.get(dataset, SourcePolicy(market, dataset, ["SEC/Company IR"], ["FactSet/Koyfin/TIKR"], ["yfinance"], ["CNINFO"]))

    if market == Market.HK:
        policies = {
            Dataset.FILINGS: SourcePolicy(market, dataset, ["HKEXnews", "Company IR"], ["Wind", "Choice", "Bloomberg"], ["Company website"], ["SEC EDGAR unless ADR/dual-listed"], "Watch placings, connected transactions, and circulars."),
            Dataset.FINANCIALS: SourcePolicy(market, dataset, ["HKEX annual/interim reports", "Company IR"], ["Wind", "Choice", "Bloomberg"], ["AAStocks", "company website"], ["SEC EDGAR unless ADR/dual-listed"], "Keep HKD/reporting-currency and share-count basis explicit."),
            Dataset.CURRENT_QUOTE: SourcePolicy(market, dataset, ["HKEX market data", "licensed vendor"], ["Wind", "Choice", "Bloomberg"], ["yfinance", "AAStocks"], ["SEC EDGAR unless ADR/dual-listed"], "HK quote must use HK ticker, currency, lot size, and latest trading day."),
            Dataset.PRICE_HISTORY_RAW: SourcePolicy(market, dataset, ["HKEX market data", "licensed vendor"], ["Wind", "Choice", "Bloomberg"], ["yfinance", "AAStocks"], ["SEC EDGAR unless ADR/dual-listed"], "Use HK ticker and HKD history; do not substitute ADR history."),
            Dataset.PRICE_HISTORY_ADJUSTED: SourcePolicy(market, dataset, ["licensed vendor"], ["Wind", "Choice", "Bloomberg"], ["yfinance", "AAStocks"], ["SEC EDGAR unless ADR/dual-listed"], "Use adjusted HK series consistently for Chan/GF-DMA."),
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
        allowed, reason = provider_is_allowed(provider, symbol, dataset)
        if not allowed:
            failures.append(reason)
            continue
        try:
            result = provider.fetch(symbol, dataset, **kwargs)
            if result.ok:
                return result
            failures.append(f"{provider.name}: {'; '.join(result.errors) or 'not ok'}")
        except Exception as exc:  # defensive; provider errors must not crash whole agent
            failures.append(f"{provider.name}: {type(exc).__name__}: {exc}")
    return DataResult.failed(dataset, symbol.symbol, "provider_chain", SourceLevel.L4, "All providers failed or incompatible: " + " | ".join(failures))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

REQUIRED_PRICE_COLUMNS = ["trade_date", "open", "high", "low", "close", "volume"]


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
    df = result.data
    if df is None or not hasattr(df, "columns"):
        return ValidationReport(result.dataset, DataStatus.FAILED, errors=["price data is not a DataFrame"])
    if df.empty:
        return ValidationReport(result.dataset, DataStatus.FAILED, errors=["empty price frame"])

    errors: List[str] = []
    warnings: List[str] = []
    missing = [c for c in REQUIRED_PRICE_COLUMNS if c not in df.columns]
    if missing:
        errors.append(f"missing columns: {missing}")
        return ValidationReport(result.dataset, DataStatus.FAILED, errors=errors)

    if require_adjusted and result.adjust not in {"qfq", "hfq", "adjusted"}:
        errors.append(f"adjusted price required, got adjust={result.adjust}")

    numeric_cols = ["open", "high", "low", "close", "volume"]
    for col in numeric_cols:
        series = pd.to_numeric(df[col], errors="coerce")
        if series.isna().any():
            warnings.append(f"{col} contains NaN or non-numeric values")
        if col != "volume" and (series <= 0).any():
            errors.append(f"{col} contains non-positive values")
        if col == "volume" and (series < 0).any():
            errors.append("volume contains negative values")

    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    open_ = pd.to_numeric(df["open"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")
    if (high < pd.concat([open_, close], axis=1).max(axis=1)).any():
        errors.append("high < max(open, close) on one or more rows")
    if (low > pd.concat([open_, close], axis=1).min(axis=1)).any():
        errors.append("low > min(open, close) on one or more rows")

    dates = pd.to_datetime(df["trade_date"], errors="coerce")
    if dates.isna().any():
        errors.append("trade_date cannot be parsed")
    else:
        if dates.duplicated().any():
            errors.append("duplicated trade_date values")
        if not dates.is_monotonic_increasing:
            warnings.append("trade_date is not monotonically increasing")
        last_date = dates.max().date()
        age = (dt.datetime.now().date() - last_date).days
        if age > max_stale_calendar_days:
            warnings.append(f"price history appears stale: last_date={last_date}, age={age} days")
            status = DataStatus.STALE if not errors else DataStatus.FAILED
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
        df = r.data
        if "close" not in df.columns or df.empty:
            warnings.append(f"skip {r.source_name}: no close")
            continue
        latest = df.iloc[-1]
        closes.append((r.source_name, float(latest["close"]), str(latest.get("trade_date", r.as_of_date))))
    if len(closes) < 2:
        return ValidationReport("latest_close_crosscheck", DataStatus.PARTIAL, warnings=["fewer than two usable sources"] + warnings)
    values = [x[1] for x in closes]
    min_v, max_v = min(values), max(values)
    diff_pct = (max_v - min_v) / min_v * 100 if min_v > 0 else math.inf
    if diff_pct > block_pct:
        return ValidationReport("latest_close_crosscheck", DataStatus.FAILED, errors=[f"latest close difference {diff_pct:.2f}% > {block_pct:.2f}%"], warnings=warnings, stats={"closes": closes, "diff_pct": diff_pct})
    if diff_pct > warn_pct:
        return ValidationReport("latest_close_crosscheck", DataStatus.PARTIAL, warnings=warnings + [f"latest close difference {diff_pct:.2f}% > {warn_pct:.2f}%"], stats={"closes": closes, "diff_pct": diff_pct})
    return ValidationReport("latest_close_crosscheck", DataStatus.OK, warnings=warnings, stats={"closes": closes, "diff_pct": diff_pct})


REQUIRED_FINANCIAL_FIELDS = ["period", "revenue", "gross_profit", "net_profit", "operating_cash_flow", "total_assets", "total_liabilities", "total_equity"]


def validate_financial_frame(result: DataResult, *, max_stale_days: int = 240) -> ValidationReport:
    if not result.ok:
        return ValidationReport(result.dataset, DataStatus.FAILED, errors=result.errors)
    if pd is None:
        return ValidationReport(result.dataset, DataStatus.FAILED, errors=["pandas is not installed"])
    df = result.data
    if df is None or not hasattr(df, "columns") or df.empty:
        return ValidationReport(result.dataset, DataStatus.FAILED, errors=["financial data is empty or not DataFrame"])

    errors: List[str] = []
    warnings: List[str] = []
    missing = [c for c in REQUIRED_FINANCIAL_FIELDS if c not in df.columns]
    if missing:
        warnings.append(f"missing recommended fields: {missing}")

    if {"total_assets", "total_liabilities", "total_equity"}.issubset(df.columns):
        lhs = pd.to_numeric(df["total_assets"], errors="coerce")
        rhs = pd.to_numeric(df["total_liabilities"], errors="coerce") + pd.to_numeric(df["total_equity"], errors="coerce")
        denom = lhs.abs().replace(0, math.nan)
        diff = ((lhs - rhs).abs() / denom).fillna(0)
        if (diff > 0.01).any():
            warnings.append(f"assets != liabilities + equity by >1% on {int((diff > 0.01).sum())} rows")

    if {"revenue", "accounts_receivable"}.issubset(df.columns):
        # Warn if receivables grow much faster than revenue in the latest period.
        if len(df) >= 2:
            rev_growth = _safe_growth(df["revenue"].iloc[-2], df["revenue"].iloc[-1])
            ar_growth = _safe_growth(df["accounts_receivable"].iloc[-2], df["accounts_receivable"].iloc[-1])
            if ar_growth is not None and rev_growth is not None and ar_growth > rev_growth + 0.30:
                warnings.append(f"receivables growth exceeds revenue growth by >30ppt: AR={ar_growth:.1%}, revenue={rev_growth:.1%}")

    if {"net_profit", "operating_cash_flow"}.issubset(df.columns):
        np = pd.to_numeric(df["net_profit"], errors="coerce")
        ocf = pd.to_numeric(df["operating_cash_flow"], errors="coerce")
        if ((np > 0) & (ocf < 0)).sum() >= 2:
            warnings.append("positive net profit with negative operating cash flow in multiple periods")

    if "period" in df.columns:
        dates = pd.to_datetime(df["period"], errors="coerce")
        if not dates.isna().all():
            last_period = dates.max().date()
            age = (dt.datetime.now().date() - last_period).days
            if age > max_stale_days:
                warnings.append(f"financial data may be stale: last_period={last_period}, age={age} days")
                status = DataStatus.STALE if not errors else DataStatus.FAILED
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
    price = price_report.status if price_report else DataStatus.FAILED
    financials = financial_report.status if financial_report else DataStatus.FAILED
    technical = technical_report.status if technical_report else DataStatus.FAILED
    cross = cross_validation_report.status if cross_validation_report else DataStatus.PARTIAL

    cap = RatingCap.S
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
    symbol = resolve_symbol(symbol_or_theme)
    datasets = [
        Dataset.CURRENT_QUOTE,
        Dataset.PRICE_HISTORY_RAW,
        Dataset.PRICE_HISTORY_ADJUSTED,
        Dataset.SHARE_CAPITAL,
        Dataset.FINANCIALS,
        Dataset.FILINGS,
        Dataset.CUSTOMER_EVIDENCE,
        Dataset.PEER_VALUATION,
        Dataset.ESTIMATES,
        Dataset.TRADING_CALENDAR,
    ]
    policies = {d.value: source_policy(symbol.market, d).__dict__ for d in datasets}
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
        key = dataset.value
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
        number = float(value)
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
        code, _, suffix = symbol.symbol.partition(".")
        if suffix == "SH":
            return f"{code}.SS"
        if suffix in {"SZ", "BJ"}:
            return f"{code}.{suffix}"
    return symbol.symbol


def _eastmoney_secid(symbol: SymbolInfo) -> Optional[str]:
    if symbol.market != Market.CN_A:
        return None
    code, _, suffix = symbol.symbol.partition(".")
    if not re.fullmatch(r"\d{6}", code):
        return None
    if suffix == "SH":
        return f"1.{code}"
    if suffix in {"SZ", "BJ"}:
        return f"0.{code}"
    return None


def _eastmoney_price(value: Any) -> Optional[float]:
    number = _safe_float(value)
    if number is None or number <= 0:
        return None
    return round(number / 100, 4)


def _eastmoney_history_begin(chart_range: str) -> str:
    token = chart_range.strip().lower()
    today = dt.datetime.now().date()
    if token in {"max", "all"}:
        return "19900101"
    if token == "ytd":
        return f"{today.year}0101"
    match = re.fullmatch(r"(\d+)(d|mo|y)", token)
    if not match:
        return "19900101"
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "d":
        days = amount
    elif unit == "mo":
        days = amount * 31
    else:
        days = amount * 366
    return (today - dt.timedelta(days=days)).strftime("%Y%m%d")


def _tencent_quote_alias(symbol: SymbolInfo) -> Optional[str]:
    if symbol.market != Market.CN_A:
        return None
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
    quote_alias = _tencent_quote_alias(symbol)
    if quote_alias and quote_alias.startswith("bj"):
        return "nq" + quote_alias[2:]
    return quote_alias


def _tencent_timestamp_to_date(value: Any) -> Optional[str]:
    token = str(value or "")
    if not re.fullmatch(r"\d{14}", token):
        return None
    return f"{token[:4]}-{token[4:6]}-{token[6:8]}"


def _epoch_to_date(timestamp: int, gmtoffset: int = 0) -> str:
    shifted = dt.datetime.fromtimestamp(timestamp + gmtoffset, dt.timezone.utc)
    return shifted.date().isoformat()


def _millis_to_date(value: Any) -> Optional[str]:
    millis = _safe_int(value)
    if millis is None:
        return None
    return dt.datetime.fromtimestamp(millis / 1000, dt.timezone.utc).date().isoformat()


class YahooChartProvider:
    """Free Yahoo chart adapter for quote and historical OHLCV auxiliary data.

    This is an L2 auxiliary source. It is useful for automated preflight, but it
    must not replace market-specific official filings or licensed/pro databases.
    """

    name = "Yahoo_Chart_L2"
    level = SourceLevel.L2
    markets = [Market.US, Market.HK, Market.CN_A]
    datasets = [Dataset.CURRENT_QUOTE, Dataset.PRICE_HISTORY_RAW, Dataset.PRICE_HISTORY_ADJUSTED]
    user_agent = "Mozilla/5.0 serenity-chan-stock-skill/0.1"

    def __init__(self, *, name: str = "Yahoo_Chart_L2", host: str = "query1.finance.yahoo.com") -> None:
        self.name = name
        self.host = host

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        yahoo_symbol = _yahoo_symbol(symbol)
        chart_range = str(kwargs.get("range", "5d" if dataset == Dataset.CURRENT_QUOTE else "1y"))
        interval = str(kwargs.get("interval", "1d"))
        params = urllib.parse.urlencode({
            "range": chart_range,
            "interval": interval,
            "events": "history|div|split",
            "includeAdjustedClose": "true",
        })
        url = f"https://{self.host}/v8/finance/chart/{urllib.parse.quote(yahoo_symbol)}?{params}"
        try:
            payload = https_json(url, user_agent=self.user_agent)
        except Exception as exc:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"https fetch failed: {type(exc).__name__}: {exc}")

        chart = payload.get("chart", {}) if isinstance(payload, Mapping) else {}
        if chart.get("error"):
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"Yahoo chart error: {chart['error']}")
        results = chart.get("result") or []
        if not results:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Yahoo chart returned no result")

        result = results[0]
        meta = result.get("meta", {}) if isinstance(result, Mapping) else {}
        raw_path = raw_hash = None
        raw_dir = kwargs.get("raw_dir")
        if raw_dir:
            raw_path, raw_hash = save_raw_json(payload, raw_dir, f"{symbol.symbol}_{dataset.value}_yahoo_chart_raw.json")

        if dataset == Dataset.CURRENT_QUOTE:
            market_time = _safe_int(meta.get("regularMarketTime"))
            as_of_date = _epoch_to_date(market_time, int(meta.get("gmtoffset", 0) or 0)) if market_time else None
            data = {
                "symbol": meta.get("symbol", yahoo_symbol),
                "name": meta.get("longName") or meta.get("shortName"),
                "currency": meta.get("currency") or symbol.currency,
                "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
                "regular_market_price": _safe_float(meta.get("regularMarketPrice")),
                "regular_market_time": market_time,
                "regular_market_day_high": _safe_float(meta.get("regularMarketDayHigh")),
                "regular_market_day_low": _safe_float(meta.get("regularMarketDayLow")),
                "regular_market_volume": _safe_int(meta.get("regularMarketVolume")),
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

        rows = self._price_rows(result, adjusted=(dataset == Dataset.PRICE_HISTORY_ADJUSTED))
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
        timestamps = result.get("timestamp") or []
        meta = result.get("meta", {}) if isinstance(result.get("meta"), Mapping) else {}
        gmtoffset = int(meta.get("gmtoffset", 0) or 0)
        indicators = result.get("indicators", {}) if isinstance(result.get("indicators"), Mapping) else {}
        quotes = indicators.get("quote") or []
        if not quotes:
            return []
        quote = quotes[0]
        adj = (indicators.get("adjclose") or [{}])[0].get("adjclose") or []
        rows: List[Dict[str, Any]] = []
        for idx, ts in enumerate(timestamps):
            open_ = _safe_float((quote.get("open") or [None])[idx] if idx < len(quote.get("open") or []) else None)
            high = _safe_float((quote.get("high") or [None])[idx] if idx < len(quote.get("high") or []) else None)
            low = _safe_float((quote.get("low") or [None])[idx] if idx < len(quote.get("low") or []) else None)
            close = _safe_float((quote.get("close") or [None])[idx] if idx < len(quote.get("close") or []) else None)
            volume = _safe_int((quote.get("volume") or [None])[idx] if idx < len(quote.get("volume") or []) else None)
            adj_close = _safe_float(adj[idx] if idx < len(adj) else None)
            if open_ is None or high is None or low is None or close is None or volume is None:
                continue
            raw_close = close
            if adjusted and adj_close is not None and close:
                ratio = adj_close / close
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

    name = "Tencent_Quote_Kline_L2"
    level = SourceLevel.L2
    markets = [Market.CN_A]
    datasets = [Dataset.CURRENT_QUOTE, Dataset.PRICE_HISTORY_ADJUSTED]
    user_agent = "Mozilla/5.0"
    quote_url = "https://qt.gtimg.cn/q="
    kline_url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        if dataset == Dataset.CURRENT_QUOTE:
            alias = _tencent_quote_alias(symbol)
            if not alias:
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported A-share exchange for {symbol.symbol}")
            return self._fetch_quote(symbol, dataset, alias, raw_dir=kwargs.get("raw_dir"))
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

            context = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            context = ssl.create_default_context()

        last_error: Optional[BaseException] = None
        for attempt in range(retries + 1):
            request = urllib.request.Request(
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
        url = self.quote_url + urllib.parse.quote(alias)
        try:
            raw = self._read_bytes(url)
            text = raw.decode("gb18030", errors="replace")
        except Exception as exc:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"https fetch failed: {type(exc).__name__}: {exc}")

        match = re.search(rf"v_{re.escape(alias)}=\"([^\"]*)\"", text)
        if not match:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Tencent quote returned no matching symbol data")
        fields = match.group(1).split("~")
        if len(fields) < 35:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"Tencent quote returned too few fields: {len(fields)}")
        price = _safe_float(fields[3])
        if price is None or price <= 0:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Tencent quote missing regular market price")

        raw_path = raw_hash = None
        if raw_dir:
            raw_path, raw_hash = save_raw_json({"alias": alias, "raw_text": text}, raw_dir, f"{symbol.symbol}_{dataset.value}_tencent_quote_raw.json")

        market_time = fields[30] if len(fields) > 30 else ""
        data = {
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
        limit = self._history_limit(chart_range)
        params = urllib.parse.urlencode({"param": f"{alias},day,,,{limit},qfq"})
        url = f"{self.kline_url}?{params}"
        try:
            raw = self._read_bytes(url)
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"https fetch failed: {type(exc).__name__}: {exc}")
        data = payload.get("data") if isinstance(payload, Mapping) else None
        stock_payload = data.get(alias) if isinstance(data, Mapping) else None
        if not isinstance(stock_payload, Mapping):
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Tencent kline returned no matching symbol data")

        qfq_rows = stock_payload.get("qfqday")
        day_rows = stock_payload.get("day")
        rows_source = qfq_rows if isinstance(qfq_rows, list) and qfq_rows else day_rows
        adjust = "qfq" if isinstance(qfq_rows, list) and qfq_rows else "unknown"
        rows = self._kline_rows(rows_source if isinstance(rows_source, list) else [])
        if not rows:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Tencent kline returned no usable OHLCV rows")

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
        token = chart_range.strip().lower()
        if token in {"max", "all"}:
            return 10000
        match = re.fullmatch(r"(\d+)(d|mo|y)", token)
        if not match:
            return 800
        amount = int(match.group(1))
        unit = match.group(2)
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
            trade_date = str(item[0])
            open_ = _safe_float(item[1])
            close = _safe_float(item[2])
            high = _safe_float(item[3])
            low = _safe_float(item[4])
            volume = _safe_int(float(item[5])) if _safe_float(item[5]) is not None else None
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

    name = "Eastmoney_Quote_Kline_L2"
    level = SourceLevel.L2
    markets = [Market.CN_A]
    datasets = [Dataset.CURRENT_QUOTE, Dataset.PRICE_HISTORY_RAW, Dataset.PRICE_HISTORY_ADJUSTED]
    user_agent = "Mozilla/5.0 serenity-chan-stock-skill/0.1"
    quote_url = "https://push2.eastmoney.com/api/qt/stock/get"
    kline_url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        secid = _eastmoney_secid(symbol)
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
        fields = ",".join([
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
        params = urllib.parse.urlencode({"secid": secid, "fields": fields})
        url = f"{self.quote_url}?{params}"
        try:
            payload = https_json(url, user_agent=self.user_agent, headers=self._headers())
        except Exception as exc:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"https fetch failed: {type(exc).__name__}: {exc}")
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(data, Mapping):
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Eastmoney quote returned no data")

        market_price = _eastmoney_price(data.get("f43"))
        if market_price is None:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Eastmoney quote missing regular market price")
        market_time = _safe_int(data.get("f86"))
        raw_path = raw_hash = None
        if raw_dir:
            raw_path, raw_hash = save_raw_json(payload, raw_dir, f"{symbol.symbol}_{dataset.value}_eastmoney_quote_raw.json")

        quote = {
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
        klt = self._kline_interval(interval)
        if not klt:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported Eastmoney interval {interval}")
        params = urllib.parse.urlencode({
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": klt,
            "fqt": "1" if dataset == Dataset.PRICE_HISTORY_ADJUSTED else "0",
            "beg": _eastmoney_history_begin(chart_range),
            "end": dt.datetime.now().date().strftime("%Y%m%d"),
        })
        url = f"{self.kline_url}?{params}"
        try:
            payload = https_json(url, user_agent=self.user_agent, headers=self._headers())
        except Exception as exc:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"https fetch failed: {type(exc).__name__}: {exc}")
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(data, Mapping):
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Eastmoney kline returned no data")
        rows = self._kline_rows(data.get("klines") or [])
        if not rows:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Eastmoney kline returned no usable OHLCV rows")
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
        normalized = interval.strip().lower()
        mapping = {
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
            parts = item.split(",")
            if len(parts) < 6:
                continue
            trade_date = parts[0]
            open_ = _safe_float(parts[1])
            close = _safe_float(parts[2])
            high = _safe_float(parts[3])
            low = _safe_float(parts[4])
            volume = _safe_int(parts[5])
            amount = _safe_float(parts[6]) if len(parts) > 6 else None
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

    name = "CNINFO_Announcements_L0"
    level = SourceLevel.L0
    markets = [Market.CN_A]
    datasets = [Dataset.FILINGS]
    user_agent = "Mozilla/5.0 serenity-chan-stock-skill/0.1"
    top_search_url = "http://www.cninfo.com.cn/new/information/topSearch/query"
    announcement_url = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
    referer = "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search"

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        if symbol.market != Market.CN_A:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "CNINFO announcements only support A-share symbols")
        if dataset != Dataset.FILINGS:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported dataset {dataset.value}")
        try:
            code, _, suffix = symbol.symbol.partition(".")
            listing = self._lookup_listing(code)
            if not listing:
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"could not resolve CNINFO orgId for {symbol.symbol}")
            payload = self._query_announcements(code, str(listing.get("orgId") or ""), suffix)
            announcements = payload.get("announcements") or []
            if not isinstance(announcements, list) or not announcements:
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "CNINFO returned no announcements")

            raw_path = raw_hash = None
            raw_dir = kwargs.get("raw_dir")
            if raw_dir:
                raw_path, raw_hash = save_raw_json(
                    {"lookup": listing, "announcements": payload},
                    raw_dir,
                    f"{symbol.symbol}_cninfo_announcements_raw.json",
                )

            records = [self._normalize_announcement(item) for item in announcements[:80] if isinstance(item, Mapping)]
            records = [record for record in records if record]
            if not records:
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "CNINFO announcements could not be normalized")
            as_of = records[0].get("announcement_date")
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
        payload = form_json(
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
        column = "sse" if suffix == "SH" else "szse" if suffix == "SZ" else "bj"
        payload = form_json(
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
        adjunct = str(item.get("adjunctUrl") or "")
        pdf_url = f"https://static.cninfo.com.cn/{adjunct}" if adjunct else None
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
            "page_column": item.get("pageColumn"),
        }


class PdfTextExtractionMixin:
    """Shared PDF text extraction utilities for official report adapters."""

    @staticmethod
    def _pdf_python_candidates() -> List[str]:
        candidates: List[str] = []
        env_python = os.getenv("SERENITY_PDF_PYTHON")
        if env_python:
            candidates.append(env_python)
        candidates.append(sys.executable)
        runtime_python = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
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
        path = Path(pdf_path)
        errors: List[str] = []

        try:
            import pdfplumber  # type: ignore

            pages: List[Dict[str, Any]] = []
            with pdfplumber.open(str(path)) as pdf:
                total_pages = len(pdf.pages)
                for index, page in enumerate(pdf.pages[:max_pages], start=1):
                    text = page.extract_text() or ""
                    if text.strip():
                        pages.append({"page_number": index, "text": text})
            return {"ok": True, "parser": "pdfplumber", "page_count": total_pages, "pages": pages, "errors": []}
        except Exception as exc:
            errors.append(f"in-process pdfplumber unavailable: {type(exc).__name__}: {exc}")

        script = self._pdfplumber_extract_script()
        for python_exe in self._pdf_python_candidates():
            try:
                completed = subprocess.run(
                    [python_exe, "-", str(path), str(max_pages)],
                    input=script.encode("utf-8"),
                    capture_output=True,
                    timeout=timeout,
                    check=True,
                )
                payload = json.loads(completed.stdout.decode("utf-8"))
                payload["ok"] = True
                payload["python"] = python_exe
                payload.setdefault("errors", [])
                return payload
            except Exception as exc:
                stderr = ""
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
        normalized = cls._normalize_pdf_line(line)
        return re.findall(r"\(?-?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?|\(?-?\d+(?:\.\d+)?\)?|(?<!\w)[-–](?!\w)", normalized)

    @staticmethod
    def _parse_pdf_number(token: str) -> Optional[float]:
        text = token.strip()
        if text in {"-", "–", ""}:
            return None
        negative = text.startswith("(") and text.endswith(")")
        text = text.strip("()").replace(",", "")
        try:
            value = float(text)
        except Exception:
            return None
        return -value if negative else value

    @classmethod
    def _line_values(cls, line: str, *, expected_columns: int) -> List[Optional[float]]:
        tokens = cls._pdf_number_tokens(line)
        while len(tokens) > expected_columns:
            first = tokens[0].strip("()")
            if "," not in first and "." not in first and first.lstrip("-").isdigit() and abs(int(first)) <= 80:
                tokens.pop(0)
                continue
            break
        if len(tokens) > expected_columns:
            tokens = tokens[-expected_columns:]
        return [cls._parse_pdf_number(token) for token in tokens]

    @staticmethod
    def _page_texts_containing(pages: Sequence[Mapping[str, Any]], *needles: str) -> List[Mapping[str, Any]]:
        lower_needles = [needle.lower() for needle in needles]
        output: List[Mapping[str, Any]] = []
        for page in pages:
            text = str(page.get("text") or "")
            lower_text = text.lower()
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
        lower_labels = [label.lower() for label in labels]
        for page in pages:
            text = str(page.get("text") or "")
            for line in text.splitlines():
                clean = cls._normalize_pdf_line(line)
                clean_lower = clean.lower()
                if not any(clean_lower.startswith(label) or label in clean_lower for label in lower_labels):
                    continue
                values = cls._line_values(clean, expected_columns=expected_columns)
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
            lines = [cls._normalize_pdf_line(line) for line in str(page.get("text") or "").splitlines()]
            for index, line in enumerate(lines):
                if line.lower() != "revenues":
                    continue
                for candidate in lines[index + 1:index + 10]:
                    if candidate.lower().startswith("cost of revenues"):
                        break
                    if re.match(r"^\d+[A-Za-z()]?\s+", candidate):
                        values = cls._line_values(candidate, expected_columns=expected_columns)
                        if len(values) > value_index and values[value_index] is not None:
                            return values[value_index], {
                                "page_number": page.get("page_number"),
                                "line": candidate,
                                "value_index": value_index,
                            }
                break
        return None, None


class CninfoFinancialReportBase:
    user_agent = "Mozilla/5.0 serenity-chan-stock-skill/0.1"

    def _locate_official_report_evidence(
        self,
        symbol: SymbolInfo,
        *,
        raw_dir: Optional[str | Path] = None,
        download_limit: int = 2,
    ) -> Dict[str, Any]:
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
            cninfo = CninfoAnnouncementsProvider()
            listing = cninfo._lookup_listing(code)
            if not listing:
                evidence["errors"].append(f"could not resolve CNINFO orgId for {symbol.symbol}")
                return evidence
            announcements: List[Any] = []
            queried_pages = 0
            seen_report_keys: set[str] = set()
            reports: List[Dict[str, Any]] = []
            for page_num in range(1, 7):
                payload = cninfo._query_announcements(code, str(listing.get("orgId") or ""), suffix, page_num=page_num, page_size=30)
                page_announcements = payload.get("announcements") or []
                if not isinstance(page_announcements, list) or not page_announcements:
                    break
                queried_pages += 1
                announcements.extend(page_announcements)
                for item in page_announcements:
                    if not isinstance(item, Mapping):
                        continue
                    record = cninfo._normalize_announcement(item)
                    title = self._clean_title(str(record.get("title") or record.get("short_title") or ""))
                    if not self._is_periodic_report_title(title):
                        continue
                    record["title"] = title
                    record["report_kind"] = self._report_kind(title)
                    report_key = str(record.get("announcement_id") or record.get("pdf_url") or title)
                    if report_key in seen_report_keys:
                        continue
                    seen_report_keys.add(report_key)
                    reports.append(record)
                    if len(reports) >= 8:
                        break
                if len(reports) >= 8:
                    break
            if raw_dir and reports and download_limit > 0:
                self._attach_official_report_downloads(
                    reports,
                    raw_dir=Path(raw_dir) / "official_reports",
                    symbol=symbol.symbol,
                    limit=download_limit,
                    errors=evidence["errors"],
                )

            selected_reports = self._select_reports_for_download(reports, download_limit) if reports and download_limit > 0 else []
            downloaded_reports = [
                report for report in reports
                if report.get("download_status") == "OK" and report.get("pdf_path")
            ]
            if not reports:
                evidence_status = "PARTIAL"
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
    def _is_periodic_report_title(title: str) -> bool:
        if not title or "摘要" in title:
            return False
        excluded = ["跟踪报告", "持续督导", "审计报告", "内控", "社会责任", "ESG", "保荐", "核查意见", "说明会"]
        if any(token in title for token in excluded):
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

    @classmethod
    def _select_reports_for_download(cls, reports: Sequence[Mapping[str, Any]], limit: int) -> List[Mapping[str, Any]]:
        selected: List[Mapping[str, Any]] = []
        preferred_order = ["annual", "q1", "semiannual", "q3", "quarterly", "periodic"]
        for kind in preferred_order:
            candidates = [
                report for report in reports
                if str(report.get("report_kind") or "") == kind and report.get("pdf_url")
            ]
            candidates.sort(key=lambda report: str(report.get("announcement_date") or ""), reverse=True)
            for report in candidates:
                if report not in selected:
                    selected.append(report)
                if len(selected) >= limit:
                    return selected
        return selected

    def _attach_official_report_downloads(
        self,
        reports: List[Dict[str, Any]],
        *,
        raw_dir: Path,
        symbol: str,
        limit: int,
        errors: List[str],
    ) -> None:
        selected = self._select_reports_for_download(reports, limit)
        for report in reports:
            if report not in selected:
                report["download_status"] = "NOT_SELECTED"
        for report in selected:
            url = str(report.get("pdf_url") or "")
            report_kind = str(report.get("report_kind") or "periodic")
            announcement_date = str(report.get("announcement_date") or "")
            title = str(report.get("title") or report_kind)
            filename = _safe_artifact_name(f"{symbol}_{announcement_date}_{report_kind}_{title}") + ".pdf"
            try:
                payload = https_bytes(
                    url,
                    user_agent=self.user_agent,
                    headers={"Referer": "https://www.cninfo.com.cn/"},
                    timeout=45,
                    max_bytes=90 * 1024 * 1024,
                )
                if not payload.startswith(b"%PDF"):
                    raise RuntimeError("downloaded artifact does not start with a PDF header")
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
        value = row.get(key)
        if value is not None and value != "":
            return value
    return None


def _first_number(row: Mapping[str, Any], keys: Sequence[str]) -> Optional[float]:
    for key in keys:
        value = _safe_float(row.get(key))
        if value is not None:
            return value
    return None


def _date10(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:10]


def _put_number(target: Dict[str, Any], key: str, value: Any) -> None:
    number = _safe_float(value)
    if number is not None:
        target[key] = number


class CninfoFinancialReportsProvider(CninfoFinancialReportBase, PdfTextExtractionMixin):
    """Official CNINFO periodic-report PDF line-item adapter for A-share financials."""

    name = "CNINFO_FinancialReports_L0"
    level = SourceLevel.L0
    markets = [Market.CN_A]
    datasets = [Dataset.FINANCIALS]

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        if symbol.market != Market.CN_A:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "CNINFO financial reports only support A-share symbols")
        if dataset != Dataset.FINANCIALS:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported dataset {dataset.value}")
        raw_dir = kwargs.get("raw_dir")
        try:
            download_limit = int(kwargs.get("official_report_download_limit", 2) or 2)
            evidence = self._locate_official_report_evidence(symbol, raw_dir=raw_dir, download_limit=download_limit)
            reports = evidence.get("reports", []) if isinstance(evidence.get("reports"), list) else []
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
            extraction_raw_dir = Path(raw_dir) / "official_reports" if raw_dir else None
            for report in reports:
                if report.get("download_status") != "OK" or not report.get("pdf_path"):
                    continue
                extraction = self._extract_cninfo_report_period(report, raw_dir=extraction_raw_dir)
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

            core_statement_fields = ["revenue", "net_income", "operating_cash_flow", "assets", "liabilities", "equity"]
            extracted_periods.sort(key=lambda row: str(row.get("period") or ""))
            ok_periods = [
                row for row in extracted_periods
                if all(row.get(field) is not None for field in core_statement_fields)
            ]
            latest_extracted_period = extracted_periods[-1] if extracted_periods else None
            latest_period = str(latest_extracted_period.get("period") or "") if latest_extracted_period else None
            latest_core_statement_missing_fields = [
                field for field in core_statement_fields
                if not latest_extracted_period or latest_extracted_period.get(field) is None
            ]
            latest_core_statement_complete = bool(latest_extracted_period) and not latest_core_statement_missing_fields
            latest_core_complete_period = max((str(row.get("period") or "") for row in ok_periods), default=None)
            if latest_extracted_period and latest_core_statement_missing_fields:
                extraction_warnings.append(
                    f"latest period {latest_period} missing core statement fields={latest_core_statement_missing_fields}"
                )
            downloaded_reports = [
                report for report in reports
                if report.get("download_status") == "OK" and report.get("pdf_path")
            ]
            financial_sector_profile_required = self._requires_financial_sector_profile(evidence, reports)
            financial_sector_profile_status = self._financial_sector_profile_status(
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

            output_unit = self._period_unit(extracted_periods)
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

    def _extract_cninfo_report_period(self, report: Mapping[str, Any], *, raw_dir: Optional[Path] = None) -> Dict[str, Any]:
        pdf_path = str(report.get("pdf_path") or "")
        report_kind = str(report.get("report_kind") or "periodic")
        title = str(report.get("title") or "")
        if not pdf_path:
            return {"status": "FAILED", "errors": ["report has no downloaded pdf_path"]}

        page_bundle = self._extract_pdf_pages(pdf_path, max_pages=260, timeout=90)
        if not page_bundle.get("ok"):
            return {"status": "FAILED", "errors": page_bundle.get("errors", ["PDF text extraction failed"])}
        pages = page_bundle.get("pages", [])
        if not isinstance(pages, list) or not pages:
            return {"status": "FAILED", "errors": ["PDF text extraction returned no text pages"]}

        if raw_dir:
            text_name = _safe_artifact_name(f"{Path(pdf_path).stem}_pdf_text") + ".txt"
            combined_text = "\n\n".join(
                f"--- page {page.get('page_number')} ---\n{page.get('text') or ''}"
                for page in pages
            )
            text_path, text_hash = save_raw_text(combined_text, raw_dir / "extracted_text", text_name)
        else:
            text_path = text_hash = None

        balance_pages = self._cn_statement_pages(
            pages,
            "合并资产负债表",
            stop_titles=["母公司资产负债表", "公司资产负债表", "合并利润表", "母公司利润表", "合并现金流量表"],
            signals=["资产总计", "资产合计", "负债合计", "所有者权益", "股东权益"],
        )
        income_pages = self._cn_statement_pages(
            pages,
            "合并利润表",
            stop_titles=["母公司利润表", "公司利润表", "合并现金流量表", "母公司现金流量表"],
            signals=["营业总收入", "营业收入", "归属于母公司"],
        )
        cashflow_pages = self._cn_statement_pages(
            pages,
            "合并现金流量表",
            stop_titles=["母公司现金流量表", "公司现金流量表", "所有者权益变动表", "合并所有者权益变动表"],
            signals=["经营活动产生的现金流量净额", "经营活动产生的现金流"],
        )
        unit = self._cn_unit_from_pages(balance_pages + income_pages + cashflow_pages)

        fields: Dict[str, Any] = {}
        evidence: Dict[str, Any] = {}

        def put(field: str, value: Optional[float], source: Optional[Dict[str, Any]]) -> None:
            if value is None:
                return
            fields[field] = value
            if source:
                evidence[field] = source

        value, source = self._extract_cn_value(balance_pages, [["资产总计"]], exclude_groups=[["负债", "权益"]])
        if value is None:
            value, source = self._extract_cn_value(
                balance_pages,
                [["资产合计"], ["资产总额"]],
                exclude_groups=[["流动资产合计"], ["非流动资产合计"], ["负债", "权益"]],
            )
        put("assets", value, source)
        value, source = self._extract_cn_value(balance_pages, [["负债合计"]], exclude_groups=[["流动负债合计"], ["非流动负债合计"]])
        put("liabilities", value, source)
        value, source = self._extract_cn_value(
            balance_pages,
            [["所有者权益", "计"], ["股东权益", "计"]],
            exclude_groups=[["归属于母公司"], ["少数股东"], ["负债", "所有者权益"]],
        )
        put("equity", value, source)
        value, source = self._extract_cn_value(balance_pages, [["归属于母公司", "权益", "合计"]])
        put("parent_equity", value, source)
        value, source = self._extract_cn_value(balance_pages, [["货币资金"]])
        put("cash", value, source)

        value, source = self._extract_cn_value(income_pages, [["其中", "营业收入"], ["营业收入"]], exclude_groups=[["营业总收入"], ["营业成本"], ["增长率"], ["比重"]])
        if value is None:
            value, source = self._extract_cn_value(income_pages, [["营业总收入"]])
        put("revenue", value, source)
        value, source = self._extract_cn_value(income_pages, [["营业利润"]], exclude_groups=[["二、营业总成本"]])
        put("operating_income", value, source)
        value, source = self._extract_cn_value(income_pages, [["利润总额"]])
        put("profit_before_tax", value, source)
        value, source = self._extract_cn_value(income_pages, [["五", "净利润"], ["净利润"]], exclude_groups=[["归属于母公司"], ["少数股东"], ["综合收益"]])
        put("total_net_profit", value, source)
        value, source = self._extract_cn_value(income_pages, [["归属于母公司", "净利润"], ["归属于母公司股东", "净利润"]], exclude_groups=[["综合收益"]])
        if value is None:
            value, source = fields.get("total_net_profit"), evidence.get("total_net_profit")
        put("net_income", value, source)

        value, source = self._extract_cn_value(cashflow_pages, [["经营活动产生的现金流量净额"], ["经营活动产生的现金流", "量净额"], ["经营活动产生的现金流量净"]])
        put("operating_cash_flow", value, source)
        value, source = self._extract_cn_value(cashflow_pages, [["投资活动产生的现金流量净额"], ["投资活动产生的现金流", "量净额"], ["投资活动产生的现金流量净"]])
        put("investing_cash_flow", value, source)
        value, source = self._extract_cn_value(cashflow_pages, [["筹资活动产生的现金流量净额"], ["筹资活动产生的现金流", "量净额"], ["筹资活动产生的现金流量净"]])
        put("financing_cash_flow", value, source)

        financial_sector_profile = self._extract_financial_sector_profile(pages, unit=unit)
        period = self._cn_period_from_report(report, balance_pages + income_pages + cashflow_pages)
        required = ["revenue", "net_income", "operating_cash_flow", "assets", "liabilities", "equity"]
        missing = [field for field in required if fields.get(field) is None]
        status = "OK" if not missing else ("PARTIAL" if fields else "FAILED")
        section_pages = {
            "balance": [page.get("page_number") for page in balance_pages],
            "income": [page.get("page_number") for page in income_pages],
            "cashflow": [page.get("page_number") for page in cashflow_pages],
        }
        period_row = {
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
        start_index = cls._cn_statement_start_index(pages, title, signals)
        if start_index is None:
            return []
        title_compact = cls._compact_cn(title)
        stop_compacts = [cls._compact_cn(stop) for stop in stop_titles]
        output: List[Dict[str, Any]] = []
        for page in pages[start_index:start_index + max_section_pages]:
            lines = [cls._normalize_pdf_line(line) for line in str(page.get("text") or "").splitlines()]
            if not lines:
                continue
            start_line = 0
            for idx, line in enumerate(lines):
                if title_compact in cls._compact_cn(line):
                    start_line = idx
                    break
            kept: List[str] = []
            for idx, line in enumerate(lines[start_line:], start=start_line):
                compact = cls._compact_cn(line)
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
        title_compact = cls._compact_cn(title)
        signal_compacts = [cls._compact_cn(signal) for signal in signals]
        scored: List[Tuple[int, int, int]] = []
        for idx, page in enumerate(pages):
            text = str(page.get("text") or "")
            compact_text = cls._compact_cn(text)
            if title_compact not in compact_text:
                continue
            lines = [cls._normalize_pdf_line(line) for line in text.splitlines()]
            score = 0
            for line_index, line in enumerate(lines):
                compact_line = cls._compact_cn(line)
                if title_compact not in compact_line:
                    continue
                score += 8
                is_continuation_page = "续" in compact_line[:len(title_compact) + 8]
                if is_continuation_page:
                    score -= 10
                else:
                    score += 10
                if len(compact_line) <= len(title_compact) + 6:
                    score += 6
                nearby = cls._compact_cn("".join(lines[line_index:line_index + 8]))
                if "项目" in nearby:
                    score += 3
                if "单位" in nearby:
                    score += 2
                break
            signal_hits = sum(1 for signal in signal_compacts if signal in compact_text)
            score += signal_hits * 2
            if "财务报表附注" in text or "附注" in text and score < 14:
                score -= 4
            scored.append((score, idx, signal_hits))
        if not scored:
            return None
        candidate_pool = [item for item in scored if item[2] > 0] or scored
        best_score = max(score for score, _, _ in candidate_pool)
        high_confidence = [
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
        compact_groups = [[cls._compact_cn(label) for label in group] for group in label_groups]
        compact_excludes = [[cls._compact_cn(label) for label in group] for group in exclude_groups]
        for page in pages:
            lines = [cls._normalize_pdf_line(line) for line in str(page.get("text") or "").splitlines()]
            for idx in range(len(lines)):
                for width in range(1, 5):
                    window = lines[idx:idx + width]
                    if not window:
                        continue
                    joined = " ".join(window)
                    compact = cls._compact_cn(joined)
                    first_line = cls._compact_cn(window[0])
                    if not any(
                        group
                        and group[0] in first_line
                        and all(label in compact for label in group)
                        for group in compact_groups
                    ):
                        continue
                    if any(all(label in compact for label in group) for group in compact_excludes):
                        continue
                    values = cls._cn_line_values(joined, expected_columns=expected_columns)
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
            value = cls._parse_pdf_number(token)
            if value is None:
                continue
            token_text = token.strip("()")
            is_small_index = (
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
        sector = cls._financial_sector_kind_from_pages(pages)
        extractors = {
            "insurance": cls._extract_insurance_profile,
            "securities": cls._extract_securities_profile,
            "bank": cls._extract_bank_profile,
        }
        ordered = [sector] if sector else []
        ordered.extend(name for name in ["insurance", "securities", "bank"] if name not in ordered)
        for name in ordered:
            extractor = extractors.get(name)
            if extractor is None:
                continue
            profile = extractor(pages, unit=unit)
            if profile:
                return profile
        return None

    @classmethod
    def _financial_sector_kind_from_pages(cls, pages: Sequence[Mapping[str, Any]]) -> Optional[str]:
        text = cls._compact_cn(" ".join(str(page.get("text") or "") for page in pages[:80]))
        if any(token in text for token in ["保险合同负债", "偿付能力", "保险服务收入", "内含价值", "合同服务边际"]):
            return "insurance"
        if any(token in text for token in ["净资本", "风险覆盖率", "代理买卖证券款", "证券及其衍生品/净资本", "证券及证券衍生品净资本"]):
            return "securities"
        if any(token in text for token in ["不良贷款率", "拨备覆盖率", "客户存款总额", "贷款和垫款总额"]):
            return "bank"
        return None

    @classmethod
    def _extract_bank_profile(cls, pages: Sequence[Mapping[str, Any]], *, unit: str) -> Optional[Dict[str, Any]]:
        amount_specs = {
            "net_interest_income": [["净利息收入"]],
            "non_interest_income": [["非利息净收入总额"], ["非利息净收入"]],
            "loans_and_advances": [["贷款和垫款总额"]],
            "customer_deposits": [["客户存款总额"], ["客户存款"]],
        }
        percent_specs = {
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

        required = [
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
        sanity_warnings = cls._bank_profile_sanity_warnings(metrics)
        missing = [field for field in required if field not in metrics]
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
        amount_specs = {
            "net_capital": [["净资本"]],
            "net_assets_parent": [["净资产"]],
            "customer_fund_deposits": [["客户资金存款"]],
            "agency_securities_liabilities": [["代理买卖证券款"]],
            "net_fee_and_commission_income": [["手续费及佣金净收入"]],
            "investment_income": [["投资收益"]],
            "net_interest_income": [["利息净收入"]],
        }
        percent_specs = {
            "risk_coverage_ratio_pct": [["风险覆盖率"]],
            "capital_leverage_ratio_pct": [["资本杠杆率"]],
            "liquidity_coverage_ratio_pct": [["流动性覆盖率"]],
            "net_stable_funding_ratio_pct": [["净稳定资金率"]],
            "net_capital_to_net_assets_pct": [["净资本", "净资产"]],
            "net_capital_to_liabilities_pct": [["净资本", "负债"]],
            "proprietary_equity_to_net_capital_pct": [["自营权益类证券", "净资本"]],
            "proprietary_non_equity_to_net_capital_pct": [["自营非权益类证券", "净资本"]],
        }
        metrics, evidence = cls._extract_profile_metrics(pages, amount_specs, percent_specs)
        required = [
            "net_capital",
            "risk_coverage_ratio_pct",
            "capital_leverage_ratio_pct",
            "liquidity_coverage_ratio_pct",
            "net_stable_funding_ratio_pct",
        ]
        if not any(field in metrics for field in required):
            return None
        sanity_warnings = cls._profile_sanity_warnings(
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
        missing = [field for field in required if field not in metrics]
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
        amount_specs = {
            "insurance_service_revenue": [["保险服务收入"]],
            "insurance_contract_liabilities": [["保险合同负债"]],
            "operating_profit_parent": [["归属于母公司股东", "营运利润"]],
            "embedded_value": [["内含价值"]],
            "new_business_value": [["新业务价值"]],
            "contract_service_margin": [["合同服务边际余额"], ["合同服务边际"]],
        }
        percent_specs = {
            "core_solvency_ratio_pct": [["核心偿付能力充足率"]],
            "comprehensive_solvency_ratio_pct": [["综合偿付能力充足率"]],
            "combined_ratio_pct": [["综合成本率"]],
            "operating_roe_pct": [["营运ROE"]],
            "net_investment_yield_pct": [["净投资收益率"]],
            "comprehensive_investment_yield_pct": [["综合投资收益率"]],
        }
        metrics, evidence = cls._extract_profile_metrics(
            pages,
            amount_specs,
            percent_specs,
            amount_selects={"insurance_service_revenue": "max_abs"},
        )
        required = [
            "insurance_service_revenue",
            "insurance_contract_liabilities",
            "core_solvency_ratio_pct",
            "comprehensive_solvency_ratio_pct",
        ]
        if not any(field in metrics for field in required):
            return None
        sanity_warnings = cls._profile_sanity_warnings(
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
        missing = [field for field in required if field not in metrics]
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
        excluded = list(exclude_groups) + [["占营业收入百分比"], ["占比"], ["比例"], ["平均余额"], ["日均余额"], ["利息支出"], ["亿元"]]
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
        compact_groups = [[cls._compact_cn(label) for label in group] for group in label_groups]
        compact_excludes = [[cls._compact_cn(label) for label in group] for group in exclude_groups]
        matches: List[Tuple[float, Dict[str, Any]]] = []
        for page in pages:
            lines = [cls._normalize_pdf_line(line) for line in str(page.get("text") or "").splitlines()]
            for line in lines:
                compact = cls._compact_cn(line)
                leading = cls._leading_metric_compact(compact)
                matched_group = next(
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
                tokens = cls._bank_metric_values(line, value_kind=value_kind)
                if not tokens:
                    continue
                source = {
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
            value = cls._parse_pdf_number(token)
            if value is None:
                continue
            token_text = token.strip("()")
            is_small_index = (
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
            value = metrics.get(key)
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                return float(value)
            return None

        for key in ["net_interest_income", "non_interest_income", "loans_and_advances", "customer_deposits"]:
            value = number(key)
            if value is not None and abs(value) < 1000:
                warnings.append(f"{key} is too small for a reported bank amount: {value}")

        bounded_specs = {
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

        core = number("core_tier1_capital_adequacy_ratio_pct")
        tier1 = number("tier1_capital_adequacy_ratio_pct")
        total = number("capital_adequacy_ratio_pct")
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
            value = metrics.get(key)
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                return float(value)
            return None

        for key in amount_fields:
            value = number(key)
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
        sorted_periods = sorted(periods, key=lambda row: str(row.get("period") or ""))
        profiles = []
        for period in sorted_periods:
            profile = period.get("financial_sector_profile")
            if isinstance(profile, Mapping):
                profiles.append(profile)
        latest_period = sorted_periods[-1] if sorted_periods else None
        latest_profile = (
            latest_period.get("financial_sector_profile")
            if isinstance(latest_period, Mapping) and isinstance(latest_period.get("financial_sector_profile"), Mapping)
            else None
        )
        if latest_profile and latest_profile.get("status") == "OK":
            return "OK"
        if profiles:
            return "PARTIAL"
        return "FAILED"

    @classmethod
    def _cn_period_from_report(cls, report: Mapping[str, Any], section_pages: Sequence[Mapping[str, Any]]) -> str:
        text = " ".join([str(report.get("title") or "")] + [str(page.get("text") or "") for page in section_pages[:3]])
        match = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", text)
        if match:
            year, month, day = match.groups()
            return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        title = str(report.get("title") or "")
        year_match = re.search(r"(\d{4})年", title)
        year = int(year_match.group(1)) if year_match else dt.datetime.now().year
        report_kind = str(report.get("report_kind") or "")
        if report_kind == "q1":
            return f"{year:04d}-03-31"
        if report_kind == "semiannual":
            return f"{year:04d}-06-30"
        if report_kind == "q3":
            return f"{year:04d}-09-30"
        return f"{year:04d}-12-31"

    @classmethod
    def _cn_unit_from_pages(cls, pages: Sequence[Mapping[str, Any]]) -> str:
        text = cls._compact_cn(" ".join(str(page.get("text") or "") for page in pages[:3]))
        if "人民币百万元" in text or "单位:百万元" in text or "单位:人民币百万元" in text:
            return "million_yuan"
        if "人民币千元" in text or "单位:千元" in text or "单位:人民币千元" in text:
            return "thousand_yuan"
        if "人民币万元" in text or "单位:万元" in text or "单位:人民币万元" in text:
            return "ten_thousand_yuan"
        return "yuan"

    @staticmethod
    def _period_unit(periods: Sequence[Mapping[str, Any]]) -> str:
        units = {str(period.get("unit") or "") for period in periods if period.get("unit")}
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
        text_parts = [str(evidence.get("name") or "")]
        text_parts.extend(str(report.get("title") or "") for report in reports)
        compact = cls._compact_cn(" ".join(text_parts))
        return any(token in compact for token in ["银行", "保险", "证券", "信托", "期货", "券商"])

    @staticmethod
    def _compact_cn(text: str) -> str:
        normalized = (
            text.replace("：", ":")
            .replace("（", "(")
            .replace("）", ")")
            .replace("－", "-")
            .replace("—", "-")
            .replace(" ", "")
        )
        return re.sub(r"\s+", "", normalized)


class EastmoneyF10FinancialsProvider(CninfoFinancialReportBase):
    """Eastmoney F10 L3 structured preflight for A-share financial statements.

    The adapter records official periodic-report evidence when available and
    exposes cumulative income/cash-flow plus period-end balance data. Final S/A
    conclusions require L0/L1 verification of the key financial lines.
    """

    name = "Eastmoney_F10_Financials_L3"
    level = SourceLevel.L3
    markets = [Market.CN_A]
    datasets = [Dataset.FINANCIALS]
    user_agent = "Mozilla/5.0 serenity-chan-stock-skill/0.1"
    api_url = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
    referer = "https://emweb.securities.eastmoney.com/"
    table_specs = {
        "income": "RPT_F10_FINANCE_GINCOME",
        "balance": "RPT_F10_FINANCE_GBALANCE",
        "cashflow": "RPT_F10_FINANCE_GCASHFLOW",
    }

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        if symbol.market != Market.CN_A:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Eastmoney F10 financials only support A-share symbols")
        if dataset != Dataset.FINANCIALS:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported dataset {dataset.value}")

        official_report_evidence = self._locate_official_report_evidence(
            symbol,
            raw_dir=kwargs.get("raw_dir"),
            download_limit=int(kwargs.get("official_report_download_limit", 2) or 2),
        )
        page_size = int(kwargs.get("page_size", 16) or 16)
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
                payload = self._fetch_table(symbol.symbol, report_name, page_size=page_size)
            except Exception as exc:
                errors.append(f"{table_name}: {type(exc).__name__}: {exc}")
                continue
            raw_payloads[table_name] = payload
            rows = self._extract_rows(payload)
            if rows:
                table_rows[table_name] = rows
            else:
                errors.append(f"{table_name}: no rows returned")

        if "income" not in table_rows or "balance" not in table_rows or "cashflow" not in table_rows:
            if not table_rows:
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Eastmoney F10 returned no usable financial tables: " + " | ".join(errors))
            warnings.append("One or more Eastmoney F10 financial tables were unavailable: " + " | ".join(errors))

        periods = self._merge_period_rows(table_rows)
        if not periods:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Eastmoney F10 rows could not be normalized")

        raw_path = raw_hash = None
        raw_dir = kwargs.get("raw_dir")
        if raw_dir:
            raw_path, raw_hash = save_raw_json(
                raw_payloads,
                raw_dir,
                f"{symbol.symbol}_eastmoney_f10_financials_raw.json",
            )

        latest_period = max((str(row.get("period") or "") for row in periods), default=None)
        latest_notice = max((str(row.get("notice_date") or "") for row in periods if row.get("notice_date")), default=None)
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
        params = urllib.parse.urlencode({
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
        payload = https_json(
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
        result = payload.get("result") if isinstance(payload.get("result"), Mapping) else {}
        rows = result.get("data") if isinstance(result, Mapping) else []
        return [row for row in rows if isinstance(row, Mapping)]

    def _merge_period_rows(self, table_rows: Mapping[str, Sequence[Mapping[str, Any]]]) -> List[Dict[str, Any]]:
        periods: Dict[str, Dict[str, Any]] = {}

        for row in table_rows.get("income", []):
            period = _date10(row.get("REPORT_DATE"))
            if not period:
                continue
            target = periods.setdefault(period, {"period": period})
            self._copy_common_fields(target, row)
            revenue = _first_number(row, ["TOTAL_OPERATE_INCOME", "OPERATE_INCOME"])
            operating_cost = _first_number(row, ["OPERATE_COST", "TOTAL_OPERATE_COST"])
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
            assets = _first_number(row, ["TOTAL_ASSETS"])
            liabilities = _first_number(row, ["TOTAL_LIABILITIES"])
            equity = _first_number(row, ["TOTAL_EQUITY"])
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
            ocf = _first_number(row, ["NETCASH_OPERATE", "NETCASH_OPERATENOTE"])
            capex = _first_number(row, ["CONSTRUCT_LONG_ASSET"])
            _put_number(target, "operating_cash_flow", ocf)
            _put_number(target, "investing_cash_flow", _first_number(row, ["NETCASH_INVEST"]))
            _put_number(target, "financing_cash_flow", _first_number(row, ["NETCASH_FINANCE"]))
            _put_number(target, "cash_and_equivalents_end", _first_number(row, ["END_CCE"]))
            _put_number(target, "sales_cash_received", _first_number(row, ["SALES_SERVICES"]))
            _put_number(target, "cash_paid_for_goods_services", _first_number(row, ["BUY_SERVICES"]))
            _put_number(target, "capital_expenditure_cash_outflow", capex)
            if ocf is not None and capex is not None:
                target["free_cash_flow_after_capex"] = ocf - capex

        rows = list(periods.values())
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
            value = _first_non_empty(row, candidates)
            if value is None:
                continue
            if output_key.endswith("_date"):
                value = _date10(value)
            target.setdefault(output_key, value)


class HkexNewsBase:
    level = SourceLevel.L0
    markets = [Market.HK]
    user_agent = "Mozilla/5.0"
    base_url = "https://www1.hkexnews.hk"
    active_stock_url = "https://www1.hkexnews.hk/ncms/script/eds/activestock_sehk_e.json"
    title_search_url = "https://www1.hkexnews.hk/search/titleSearchServlet.do"
    referer = "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=en"

    def _headers(self, *, accept: str = "application/json, text/javascript, */*; q=0.01") -> Dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Referer": self.referer,
            "Accept": accept,
            "X-Requested-With": "XMLHttpRequest",
        }

    def _fetch_bytes(
        self,
        url: str,
        *,
        accept: str = "application/json, text/javascript, */*; q=0.01",
        timeout: int = 30,
        curl_retries: int = 1,
        fallback_to_urllib: bool = True,
    ) -> bytes:
        headers = self._headers(accept=accept)
        curl_error: Optional[BaseException] = None
        bounded_timeout = max(5, int(timeout))
        connect_timeout = min(10, max(3, bounded_timeout // 2))
        cmd = [
            "curl",
            "-L",
            "--http1.1",
            "--connect-timeout",
            str(connect_timeout),
            "--max-time",
            str(bounded_timeout),
            "--retry",
            str(max(0, int(curl_retries))),
            "--retry-delay",
            "1",
            "-sS",
            url,
        ]
        for key, value in headers.items():
            cmd.extend(["-H", f"{key}: {value}"])
        try:
            completed = subprocess.run(cmd, check=True, capture_output=True, timeout=bounded_timeout + 5)
            if completed.stdout:
                return completed.stdout
            curl_error = RuntimeError("curl returned an empty response body")
        except subprocess.TimeoutExpired as exc:
            curl_error = TimeoutError(f"curl exceeded hard timeout after {bounded_timeout + 5}s for {url}")
        except Exception as exc:
            curl_error = exc

        if not fallback_to_urllib:
            raise RuntimeError(f"HKEX fetch failed: curl={type(curl_error).__name__}: {curl_error}") from curl_error

        try:
            return https_bytes(
                url,
                user_agent=headers["User-Agent"],
                headers={k: v for k, v in headers.items() if k != "User-Agent"},
                timeout=bounded_timeout,
                max_bytes=120 * 1024 * 1024,
            )
        except Exception as urllib_error:
            if curl_error:
                raise RuntimeError(f"HKEX fetch failed: curl={type(curl_error).__name__}: {curl_error}; urllib={type(urllib_error).__name__}: {urllib_error}") from urllib_error
            raise

    def _fetch_json(self, url: str, *, timeout: int = 30, attempts: int = 2) -> Any:
        errors: List[str] = []
        max_attempts = max(1, int(attempts))
        for attempt in range(1, max_attempts + 1):
            payload = self._fetch_bytes(url, timeout=timeout)
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
        text = payload.decode("utf-8-sig", errors="replace")
        pos = int(getattr(exc, "pos", 0) or 0)
        start = max(0, pos - 120)
        end = min(len(text), pos + 120)
        snippet = re.sub(r"\s+", " ", text[start:end])[:260]
        return (
            f"attempt={attempt} {type(exc).__name__} at char={pos} "
            f"bytes={len(payload)} sha256={sha256(payload).hexdigest()} near={snippet!r}"
        )

    def _lookup_listing(self, code: str) -> Optional[Dict[str, Any]]:
        data = self._fetch_json(self.active_stock_url, timeout=25, attempts=3)
        if not isinstance(data, list):
            return None
        normalized = code.zfill(5)
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
        params = urllib.parse.urlencode({
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
        payload = self._fetch_json(f"{self.title_search_url}?{params}", timeout=25, attempts=3)
        if not isinstance(payload, Mapping):
            raise RuntimeError("HKEX title search response is not a JSON object")
        return dict(payload)

    def _date_window(self, *, years: int = 2) -> Tuple[str, str]:
        today = dt.datetime.now().date()
        start = today - dt.timedelta(days=years * 366)
        return start.strftime("%Y%m%d"), today.strftime("%Y%m%d")

    def _records_from_payload(self, payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
        raw_result = payload.get("result") or "[]"
        try:
            records = json.loads(str(raw_result))
        except Exception:
            return []
        if not isinstance(records, list):
            return []
        return [self._normalize_record(record) for record in records if isinstance(record, Mapping)]

    def _normalize_record(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        file_link = str(record.get("FILE_LINK") or "")
        pdf_url = urllib.parse.urljoin(self.base_url, file_link)
        date_time = self._parse_hkex_datetime(record.get("DATE_TIME"))
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
        text = html.unescape(str(value or ""))
        text = re.sub(r"<br\s*/?>", " / ", text, flags=re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _parse_hkex_datetime(value: Any) -> Optional[str]:
        text = str(value or "").strip()
        try:
            return dt.datetime.strptime(text, "%d/%m/%Y %H:%M").isoformat()
        except Exception:
            return None

    @staticmethod
    def _pdf_python_candidates() -> List[str]:
        candidates: List[str] = []
        env_python = os.getenv("SERENITY_PDF_PYTHON")
        if env_python:
            candidates.append(env_python)
        candidates.append(sys.executable)
        runtime_python = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
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
        path = Path(pdf_path)
        errors: List[str] = []

        try:
            import pdfplumber  # type: ignore

            pages: List[Dict[str, Any]] = []
            with pdfplumber.open(str(path)) as pdf:
                total_pages = len(pdf.pages)
                for index, page in enumerate(pdf.pages[:max_pages], start=1):
                    text = page.extract_text() or ""
                    if text.strip():
                        pages.append({"page_number": index, "text": text})
            return {"ok": True, "parser": "pdfplumber", "page_count": total_pages, "pages": pages, "errors": []}
        except Exception as exc:
            errors.append(f"in-process pdfplumber unavailable: {type(exc).__name__}: {exc}")

        script = self._pdfplumber_extract_script()
        for python_exe in self._pdf_python_candidates():
            try:
                completed = subprocess.run(
                    [python_exe, "-", str(path), str(max_pages)],
                    input=script.encode("utf-8"),
                    capture_output=True,
                    timeout=timeout,
                    check=True,
                )
                payload = json.loads(completed.stdout.decode("utf-8"))
                payload["ok"] = True
                payload["python"] = python_exe
                payload.setdefault("errors", [])
                return payload
            except Exception as exc:
                stderr = ""
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
        normalized = cls._normalize_pdf_line(line)
        return re.findall(r"\(?-?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)?|\(?-?\d+(?:\.\d+)?\)?|(?<!\w)[-–](?!\w)", normalized)

    @staticmethod
    def _parse_pdf_number(token: str) -> Optional[float]:
        text = token.strip()
        if text in {"-", "–", ""}:
            return None
        negative = text.startswith("(") and text.endswith(")")
        text = text.strip("()").replace(",", "")
        try:
            value = float(text)
        except Exception:
            return None
        return -value if negative else value

    @classmethod
    def _line_values(cls, line: str, *, expected_columns: int) -> List[Optional[float]]:
        tokens = cls._pdf_number_tokens(line)
        while len(tokens) > expected_columns:
            first = tokens[0].strip("()")
            if "," not in first and "." not in first and first.lstrip("-").isdigit() and abs(int(first)) <= 80:
                tokens.pop(0)
                continue
            break
        if len(tokens) > expected_columns:
            tokens = tokens[-expected_columns:]
        return [cls._parse_pdf_number(token) for token in tokens]

    @staticmethod
    def _month_number(name: str) -> str:
        months = {
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
        match = re.search(r"(?:ended|at)\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text, flags=re.I)
        if not match:
            return None
        day, month, year = match.groups()
        return f"{year}-{cls._month_number(month)}-{int(day):02d}"

    @staticmethod
    def _page_texts_containing(pages: Sequence[Mapping[str, Any]], *needles: str) -> List[Mapping[str, Any]]:
        lower_needles = [needle.lower() for needle in needles]
        output: List[Mapping[str, Any]] = []
        for page in pages:
            text = str(page.get("text") or "")
            lower_text = text.lower()
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
        lower_labels = [label.lower() for label in labels]
        for page in pages:
            text = str(page.get("text") or "")
            for line in text.splitlines():
                clean = cls._normalize_pdf_line(line)
                clean_lower = clean.lower()
                if not any(clean_lower.startswith(label) or label in clean_lower for label in lower_labels):
                    continue
                values = cls._line_values(clean, expected_columns=expected_columns)
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
            lines = [cls._normalize_pdf_line(line) for line in str(page.get("text") or "").splitlines()]
            for index, line in enumerate(lines):
                if line.lower() != "revenues":
                    continue
                for candidate in lines[index + 1:index + 10]:
                    if candidate.lower().startswith("cost of revenues"):
                        break
                    if re.match(r"^\d+[A-Za-z()]?\s+", candidate):
                        values = cls._line_values(candidate, expected_columns=expected_columns)
                        if len(values) > value_index and values[value_index] is not None:
                            return values[value_index], {
                                "page_number": page.get("page_number"),
                                "line": candidate,
                                "value_index": value_index,
                            }
                break
        return None, None

    def _extract_hkex_report_period(self, report: Mapping[str, Any], *, raw_dir: Optional[Path] = None) -> Dict[str, Any]:
        pdf_path = str(report.get("pdf_path") or "")
        title = str(report.get("title") or "")
        report_kind = str(report.get("report_kind") or "periodic")
        if not pdf_path:
            return {"status": "FAILED", "errors": ["report has no downloaded pdf_path"]}

        page_bundle = self._extract_pdf_pages(pdf_path, max_pages=220, timeout=70)
        if not page_bundle.get("ok"):
            return {"status": "FAILED", "errors": page_bundle.get("errors", ["PDF text extraction failed"])}

        pages = page_bundle.get("pages", [])
        if not isinstance(pages, list) or not pages:
            return {"status": "FAILED", "errors": ["PDF text extraction returned no text pages"]}

        if raw_dir:
            text_name = _safe_artifact_name(f"{Path(pdf_path).stem}_pdf_text") + ".txt"
            combined_text = "\n\n".join(
                f"--- page {page.get('page_number')} ---\n{page.get('text') or ''}"
                for page in pages
            )
            text_path, text_hash = save_raw_text(combined_text, raw_dir / "extracted_text", text_name)
        else:
            text_path = text_hash = None

        income_pages = self._page_texts_containing(pages, "income statement")
        income_pages = [page for page in income_pages if "comprehensive income" not in str(page.get("text") or "").lower()]
        position_pages = self._page_texts_containing(pages, "statement of financial position")
        cashflow_pages = self._page_texts_containing(pages, "statement of cash flows")

        income_text = "\n".join(str(page.get("text") or "") for page in income_pages)
        position_text = "\n".join(str(page.get("text") or "") for page in position_pages)
        cashflow_text = "\n".join(str(page.get("text") or "") for page in cashflow_pages)
        period = self._period_from_report_text(income_text) or self._period_from_report_text(position_text) or str(report.get("announcement_date") or "")
        period_type = "annual" if report_kind == "annual" else "interim"
        income_columns = 4 if "six months ended" in income_text.lower() else 2
        income_index = 2 if income_columns == 4 else 0

        fields: Dict[str, Any] = {}
        evidence: Dict[str, Any] = {}

        def put(field: str, value: Optional[float], source: Optional[Dict[str, Any]]) -> None:
            if value is None:
                return
            fields[field] = value
            if source:
                evidence[field] = source

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
        fields["net_income"] = fields.get("profit_attributable_to_equity_holders") or fields.get("total_net_profit")
        if "net_income" in fields and "net_income" not in evidence:
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

        required = ["revenue", "net_income", "operating_cash_flow", "assets", "liabilities", "equity"]
        missing = [field for field in required if fields.get(field) is None]
        status = "OK" if not missing else ("PARTIAL" if fields else "FAILED")
        period_row = {
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
        text = f"{record.get('title') or ''} {record.get('category') or ''}".lower()
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
        preferred_order = ["annual", "interim", "quarterly", "final_results", "quarterly_results", "results", "periodic"]
        for kind in preferred_order:
            candidates = [
                report for report in reports
                if str(report.get("report_kind") or "") == kind and report.get("pdf_url")
            ]
            candidates.sort(key=lambda report: str(report.get("announcement_datetime") or ""), reverse=True)
            if candidates and candidates[0] not in selected:
                selected.append(candidates[0])
            if len(selected) >= limit:
                return selected
        for kind in preferred_order:
            candidates = [
                report for report in reports
                if str(report.get("report_kind") or "") == kind and report.get("pdf_url")
            ]
            candidates.sort(key=lambda report: str(report.get("announcement_datetime") or ""), reverse=True)
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
        selected = self._select_reports_for_download(reports, limit)
        for report in reports:
            if report not in selected:
                report["download_status"] = "NOT_SELECTED"
        for report in selected:
            url = str(report.get("pdf_url") or "")
            title = str(report.get("title") or "hkex_report")
            report_kind = str(report.get("report_kind") or "periodic")
            announcement_date = str(report.get("announcement_date") or "")
            filename = _safe_artifact_name(f"{symbol}_{announcement_date}_{report_kind}_{title}") + ".pdf"
            try:
                payload = self._fetch_bytes(url, accept="application/pdf,*/*", timeout=30, curl_retries=0)
                if not payload.startswith(b"%PDF"):
                    raise RuntimeError("downloaded artifact does not start with a PDF header")
                pdf_path, pdf_hash = save_raw_bytes(payload, raw_dir, filename)
                report["download_status"] = "OK"
                report["pdf_path"] = pdf_path
                report["pdf_hash"] = pdf_hash
                report["pdf_size_bytes"] = len(payload)
            except Exception as exc:
                report["download_status"] = "FAILED"
                report["download_error"] = f"{type(exc).__name__}: {exc}"
                errors.append(f"HKEX report PDF download failed for {title}: {type(exc).__name__}: {exc}")


class HkexAnnouncementsProvider(HkexNewsBase):
    """Official HKEXnews announcement metadata adapter for HK-listed securities."""

    name = "HKEXnews_Announcements_L0"
    datasets = [Dataset.FILINGS]

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        if symbol.market != Market.HK:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "HKEXnews announcements only support HK symbols")
        if dataset != Dataset.FILINGS:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported dataset {dataset.value}")
        code = symbol.symbol.partition(".")[0].zfill(5)
        try:
            listing = self._lookup_listing(code)
            if not listing:
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"could not resolve HKEX stock id for {symbol.symbol}")
            from_date, to_date = self._date_window(years=int(kwargs.get("years", 2) or 2))
            payload = self._query_title_search(
                stock_id=str(listing["stock_id"]),
                from_date=from_date,
                to_date=to_date,
                row_range=int(kwargs.get("row_range", 100) or 100),
            )
            announcements = self._records_from_payload(payload)
            if not announcements:
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "HKEXnews returned no announcements")
            raw_path = raw_hash = None
            raw_dir = kwargs.get("raw_dir")
            if raw_dir:
                raw_path, raw_hash = save_raw_json(payload, raw_dir, f"{symbol.symbol}_{dataset.value}_hkex_announcements_raw.json")
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
                    "record_count": _safe_int(payload.get("recordCnt")),
                    "loaded_record_count": len(announcements),
                    "announcements": announcements,
                },
                raw_path=raw_path,
                raw_hash=raw_hash,
                currency=symbol.currency or "HKD",
                warnings=["HKEXnews metadata/PDF links fetched; document contents are not parsed by this adapter."],
            )
        except Exception as exc:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"HKEXnews fetch failed: {type(exc).__name__}: {exc}")


class HkexFinancialReportsProvider(HkexNewsBase):
    """Official HKEX annual/interim report PDF evidence adapter for HK financials."""

    name = "HKEXnews_FinancialReports_L0"
    datasets = [Dataset.FINANCIALS]

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        if symbol.market != Market.HK:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "HKEX financial reports only support HK symbols")
        if dataset != Dataset.FINANCIALS:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported dataset {dataset.value}")
        code = symbol.symbol.partition(".")[0].zfill(5)
        try:
            listing = self._lookup_listing(code)
            if not listing:
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"could not resolve HKEX stock id for {symbol.symbol}")
            from_date, to_date = self._date_window(years=int(kwargs.get("years", 3) or 3))
            payloads: Dict[str, Any] = {}
            reports: List[Dict[str, Any]] = []
            seen: set[str] = set()
            errors: List[str] = []
            for title in ["Annual Report", "Interim Report", "Quarterly Report"]:
                try:
                    payload = self._query_title_search(
                        stock_id=str(listing["stock_id"]),
                        from_date=from_date,
                        to_date=to_date,
                        title=title,
                        row_range=20,
                    )
                    payloads[title] = payload
                    for record in self._records_from_payload(payload):
                        key = str(record.get("news_id") or record.get("file_link") or "")
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
                        report_kind = self._report_kind(record)
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
            raw_dir = kwargs.get("raw_dir")
            download_limit = int(kwargs.get("official_report_download_limit", 2) or 2)
            if raw_dir and reports and download_limit > 0:
                self._attach_report_downloads(
                    reports,
                    raw_dir=Path(raw_dir) / "official_reports",
                    symbol=symbol.symbol,
                    limit=download_limit,
                    errors=errors,
                )
            selected_reports = self._select_reports_for_download(reports, download_limit) if reports and download_limit > 0 else []
            extracted_periods: List[Dict[str, Any]] = []
            extraction_errors: List[str] = []
            extraction_warnings: List[str] = []
            extraction_raw_dir = Path(raw_dir) / "official_reports" if raw_dir else None
            for report in reports:
                if report.get("download_status") != "OK" or not report.get("pdf_path"):
                    continue
                extraction = self._extract_hkex_report_period(report, raw_dir=extraction_raw_dir)
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
            downloaded_reports = [
                report for report in reports
                if report.get("download_status") == "OK" and report.get("pdf_path")
            ]
            if not reports:
                evidence_status = "FAILED"
            elif raw_dir and selected_reports and len(downloaded_reports) < len(selected_reports):
                evidence_status = "PARTIAL"
            else:
                evidence_status = "OK"
            evidence = {
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
            raw_path = raw_hash = None
            if raw_dir:
                raw_path, raw_hash = save_raw_json(
                    {"payloads": payloads, "errors": errors},
                    raw_dir,
                    f"{symbol.symbol}_{dataset.value}_hkex_financial_reports_raw.json",
                )
            if not reports:
                reason = "HKEXnews returned no annual/interim report PDFs"
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


class CninfoTencentAdjustedKlineProvider:
    """Build A-share qfq history from Tencent daily rows plus CNINFO corporate actions.

    This provider closes the BJ boundary where Tencent exposes daily rows but not
    a separate qfqday array. It uses CNINFO official distribution announcements
    to construct a forward-adjusted series instead of treating unknown raw rows
    as adjusted.
    """

    name = "CNINFO_Tencent_Adjusted_Kline_L0L2"
    level = SourceLevel.L2
    markets = [Market.CN_A]
    datasets = [Dataset.PRICE_HISTORY_ADJUSTED]
    user_agent = "Mozilla/5.0 serenity-chan-stock-skill/0.1"

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        if symbol.market != Market.CN_A:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "CNINFO/Tencent adjusted history only supports A-share symbols")
        if dataset != Dataset.PRICE_HISTORY_ADJUSTED:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported dataset {dataset.value}")

        raw_dir = Path(kwargs["raw_dir"]) if kwargs.get("raw_dir") else None
        tencent = TencentQuoteKlineProvider()
        base = tencent.fetch(
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

        rows = [dict(row) for row in base.data]
        start_date = str(rows[0].get("trade_date") or "")
        end_date = str(rows[-1].get("trade_date") or "")
        actions, action_errors = self._load_distribution_actions(symbol, raw_dir=raw_dir, start_date=start_date, end_date=end_date)
        if action_errors:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "Official corporate-action lookup failed: " + " | ".join(action_errors))
        applicable = [action for action in actions if start_date <= str(action.get("ex_date") or "") <= end_date]
        if not applicable:
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

        adjustment_events = self._apply_forward_adjustments(rows, applicable)
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
        code, _, suffix = symbol.symbol.partition(".")
        cninfo = CninfoAnnouncementsProvider()
        errors: List[str] = []
        actions: List[Dict[str, Any]] = []
        try:
            listing = cninfo._lookup_listing(code)
        except Exception as exc:
            return [], [f"CNINFO listing lookup failed: {type(exc).__name__}: {exc}"]
        if not listing:
            return [], [f"could not resolve CNINFO orgId for {symbol.symbol}"]

        seen: set[str] = set()
        for page_num in range(1, 9):
            try:
                payload = cninfo._query_announcements(code, str(listing.get("orgId") or ""), suffix, page_num=page_num, page_size=30)
            except Exception as exc:
                errors.append(f"page {page_num}: {type(exc).__name__}: {exc}")
                continue
            page_announcements = payload.get("announcements") if isinstance(payload, Mapping) else None
            if not isinstance(page_announcements, list) or not page_announcements:
                break
            for item in page_announcements:
                if not isinstance(item, Mapping):
                    continue
                record = cninfo._normalize_announcement(item)
                title = str(record.get("title") or "")
                if "权益分派实施公告" not in title:
                    continue
                key = str(record.get("announcement_id") or record.get("pdf_url") or title)
                if key in seen:
                    continue
                seen.add(key)
                action = self._parse_distribution_announcement(record, raw_dir=raw_dir)
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
        pdf_url = str(record.get("pdf_url") or "")
        if not pdf_url:
            return None
        try:
            payload = https_bytes(
                pdf_url,
                user_agent=self.user_agent,
                headers={"Referer": "https://www.cninfo.com.cn/"},
                timeout=30,
                max_bytes=20 * 1024 * 1024,
            )
            pdf_path = pdf_hash = None
            if raw_dir:
                pdf_path, pdf_hash = save_raw_bytes(
                    payload,
                    raw_dir / "corporate_actions",
                    _safe_artifact_name(f"{record.get('sec_code')}_{record.get('announcement_date')}_{record.get('title')}") + ".pdf",
                )
                page_bundle = HkexNewsBase()._extract_pdf_pages(pdf_path, max_pages=20, timeout=30)
            else:
                temp_dir = Path(os.getenv("SERENITY_TMP_DIR", "/tmp/serenity-chan-corporate-actions"))
                pdf_path, pdf_hash = save_raw_bytes(payload, temp_dir, _safe_artifact_name(str(record.get("announcement_id") or "distribution")) + ".pdf")
                page_bundle = HkexNewsBase()._extract_pdf_pages(pdf_path, max_pages=20, timeout=30)
            if not page_bundle.get("ok"):
                return None
            text = "\n".join(str(page.get("text") or "") for page in page_bundle.get("pages", []) if isinstance(page, Mapping))
            compact = re.sub(r"\s+", "", text)
            ex_date = self._parse_cn_date(compact, r"除权除息日为[:：]?(\d{4})年(\d{1,2})月(\d{1,2})日")
            record_date = self._parse_cn_date(compact, r"权益登记日为[:：]?(\d{4})年(\d{1,2})月(\d{1,2})日")
            cash_match = re.search(r"每10股派([0-9.]+)元", compact)
            transfer_match = re.search(r"每10股转增([0-9.]+)股", compact)
            bonus_match = re.search(r"每10股送(?:红股)?([0-9.]+)股", compact)
            cash_per_share = float(cash_match.group(1)) / 10.0 if cash_match else 0.0
            transfer_ratio = float(transfer_match.group(1)) / 10.0 if transfer_match else 0.0
            bonus_ratio = float(bonus_match.group(1)) / 10.0 if bonus_match else 0.0
            if not ex_date:
                return None
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
        match = re.search(pattern, text)
        if not match:
            return None
        year, month, day = match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    @staticmethod
    def _apply_forward_adjustments(rows: List[Dict[str, Any]], actions: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        for action in sorted(actions, key=lambda item: str(item.get("ex_date") or "")):
            ex_date = str(action.get("ex_date") or "")
            prior_indexes = [idx for idx, row in enumerate(rows) if str(row.get("trade_date") or "") < ex_date]
            if not prior_indexes:
                continue
            previous_index = prior_indexes[-1]
            previous_close = _safe_float(rows[previous_index].get("raw_close") or rows[previous_index].get("close"))
            if previous_close is None or previous_close <= 0:
                continue
            cash_per_share = _safe_float(action.get("cash_per_share")) or 0.0
            share_ratio = (_safe_float(action.get("share_ratio")) or 0.0)
            ex_right_reference = (previous_close - cash_per_share) / (1.0 + share_ratio)
            if ex_right_reference <= 0:
                continue
            factor = ex_right_reference / previous_close
            for idx in prior_indexes:
                row = rows[idx]
                for field in ("open", "high", "low", "close", "adj_close"):
                    value = _safe_float(row.get(field))
                    if value is not None:
                        row[field] = round(value * factor, 6)
                raw_close = _safe_float(row.get("raw_close"))
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
    identity = os.getenv("SEC_USER_AGENT") or os.getenv("EDGAR_IDENTITY")
    if identity:
        return identity, []
    return (
        "serenity-chan-stock-skill/0.1 (set SEC_USER_AGENT for contact)",
        ["SEC_USER_AGENT or EDGAR_IDENTITY is not set; using generic research User-Agent."],
    )


_SEC_SUBMISSIONS_CACHE: Dict[str, Mapping[str, Any]] = {}


def _sec_text_tokens(values: Iterable[str]) -> set[str]:
    output: set[str] = set()
    for token in values:
        cleaned = str(token).strip().upper()
        if not cleaned:
            continue
        output.add(cleaned)
        output.add(cleaned.replace(".", "-"))
        output.add(cleaned.replace("-", "."))
    return output


def _sec_symbol_tokens(symbol: SymbolInfo) -> set[str]:
    return _sec_text_tokens([symbol.symbol, symbol.input_value])


def _fetch_sec_submissions_payload(cik: str, *, user_agent: str) -> Mapping[str, Any]:
    padded_cik = f"{int(str(cik)):010d}"
    if padded_cik not in _SEC_SUBMISSIONS_CACHE:
        payload = https_json(f"https://data.sec.gov/submissions/CIK{padded_cik}.json", user_agent=user_agent)
        if not isinstance(payload, Mapping):
            raise ValueError("SEC submissions payload is not an object")
        _SEC_SUBMISSIONS_CACHE[padded_cik] = payload
    return _SEC_SUBMISSIONS_CACHE[padded_cik]


def _sec_submission_matches_tokens(expected: set[str], payload: Mapping[str, Any]) -> bool:
    tickers = payload.get("tickers", [])
    if not isinstance(tickers, list):
        return False
    actual = {str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()}
    expanded_actual = set(actual)
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


def _sec_bootstrap_path() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "sec_cik_bootstrap.json"


def _sec_cik_from_bootstrap(ticker: str) -> Optional[str]:
    path = _sec_bootstrap_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    records = payload.get("tickers", {}) if isinstance(payload, Mapping) else {}
    record = None
    if isinstance(records, Mapping):
        for token in _sec_text_tokens([ticker]):
            candidate = records.get(token)
            if isinstance(candidate, Mapping):
                record = candidate
                break
    if not isinstance(record, Mapping):
        return None
    raw_cik = record.get("cik")
    if raw_cik is None:
        return None
    try:
        return f"{int(str(raw_cik)):010d}"
    except ValueError:
        digits = re.sub(r"\D", "", str(raw_cik))
        return digits.zfill(10) if digits else None


def _sec_cik_from_ticker_exchange_json(ticker: str, *, user_agent: str) -> Optional[str]:
    payload = https_json("https://www.sec.gov/files/company_tickers_exchange.json", user_agent=user_agent)
    token = ticker.upper().replace("-", ".")
    fields = payload.get("fields", []) if isinstance(payload, Mapping) else []
    data = payload.get("data", []) if isinstance(payload, Mapping) else []
    if not isinstance(fields, list) or not isinstance(data, list):
        return None
    try:
        ticker_idx = fields.index("ticker")
        cik_idx = fields.index("cik")
    except ValueError:
        return None
    for row in data:
        if not isinstance(row, list) or len(row) <= max(ticker_idx, cik_idx):
            continue
        if str(row[ticker_idx]).upper() == token:
            return f"{int(row[cik_idx]):010d}"
    return None


def _sec_cik_from_company_tickers_json(ticker: str, *, user_agent: str) -> Optional[str]:
    payload = https_json("https://www.sec.gov/files/company_tickers.json", user_agent=user_agent)
    token = ticker.upper().replace("-", ".")
    if not isinstance(payload, Mapping):
        return None
    for row in payload.values():
        if not isinstance(row, Mapping):
            continue
        if str(row.get("ticker", "")).upper() == token:
            return f"{int(row['cik_str']):010d}"
    return None


def _sec_cik_from_ticker_txt(ticker: str, *, user_agent: str) -> Optional[str]:
    text = https_text("https://www.sec.gov/include/ticker.txt", user_agent=user_agent)
    token = ticker.lower().replace("-", ".")
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) != 2:
            continue
        raw_ticker, raw_cik = parts
        if raw_ticker.lower() == token:
            return f"{int(raw_cik):010d}"
    return None


def _sec_cik_from_ticker(ticker: str, *, user_agent: str) -> Optional[str]:
    token = ticker.upper().replace("-", ".")
    candidates: List[str] = []

    def add_candidate(cik: Optional[str]) -> None:
        if not cik:
            return
        try:
            normalized = f"{int(str(cik)):010d}"
        except ValueError:
            digits = re.sub(r"\D", "", str(cik))
            normalized = digits.zfill(10) if digits else ""
        if normalized and normalized not in candidates:
            candidates.append(normalized)

    bootstrap_cik = _sec_cik_from_bootstrap(token)
    add_candidate(bootstrap_cik)
    resolvers = [
        _sec_cik_from_ticker_exchange_json,
        _sec_cik_from_company_tickers_json,
        _sec_cik_from_ticker_txt,
    ]
    for resolver in resolvers:
        try:
            cik = resolver(token, user_agent=user_agent)
        except Exception:
            continue
        add_candidate(cik)
    expected_tokens = _sec_text_tokens([token])
    for cik in candidates:
        try:
            payload = _fetch_sec_submissions_payload(cik, user_agent=user_agent)
        except Exception:
            continue
        if _sec_submission_matches_tokens(expected_tokens, payload):
            return cik
    return None


SEC_FINANCIAL_CONCEPTS: Dict[str, List[str]] = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"],
    "gross_profit": ["GrossProfit"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "accounts_receivable": ["AccountsReceivableNetCurrent"],
    "inventory": ["InventoryNet"],
}


def _facts_from_concept_object(concept: str, concept_obj: Mapping[str, Any]) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    units = concept_obj.get("units", {}) if isinstance(concept_obj.get("units"), Mapping) else {}
    unit = "USD" if "USD" in units else next(iter(units), None)
    if not unit:
        return output
    facts = units.get(unit, [])
    if not isinstance(facts, list):
        return output
    for fact in facts:
        if not isinstance(fact, Mapping):
            continue
        if fact.get("form") not in {"10-K", "10-Q"}:
            continue
        if "val" not in fact or "end" not in fact:
            continue
        output.append({
            "concept": concept,
            "label": concept_obj.get("label", concept),
            "unit": unit,
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
    facts = companyfacts.get("facts", {}) if isinstance(companyfacts.get("facts"), Mapping) else {}
    us_gaap = facts.get("us-gaap", {}) if isinstance(facts.get("us-gaap"), Mapping) else {}
    output: List[Dict[str, Any]] = []
    for concept in concept_names:
        concept_obj = us_gaap.get(concept)
        if isinstance(concept_obj, Mapping):
            output.extend(_facts_from_concept_object(concept, concept_obj))
    output.sort(key=lambda row: (str(row.get("period") or ""), str(row.get("filed") or ""), str(row.get("concept") or "")))
    return output


def _period_rows_from_field_facts(field_facts: Mapping[str, Sequence[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    rows_by_period: Dict[Tuple[str, str, str, str, str], Dict[str, Any]] = {}
    for field, facts in field_facts.items():
        for fact in facts:
            key = (
                str(fact.get("period") or ""),
                str(fact.get("fy") or ""),
                str(fact.get("fp") or ""),
                str(fact.get("form") or ""),
                str(fact.get("filed") or ""),
            )
            row = rows_by_period.setdefault(key, {
                "period": fact.get("period"),
                "fy": fact.get("fy"),
                "fp": fact.get("fp"),
                "form": fact.get("form"),
                "filed": fact.get("filed"),
            })
            row[field] = fact.get("value")
            row[f"{field}_concept"] = fact.get("concept")
    rows = list(rows_by_period.values())
    for row in rows:
        if "liabilities" not in row and "assets" in row and "equity" in row:
            assets = _safe_float(row.get("assets"))
            equity = _safe_float(row.get("equity"))
            if assets is not None and equity is not None:
                row["liabilities"] = assets - equity
                row["liabilities_concept"] = "derived_from_assets_minus_equity"
    rows.sort(key=lambda row: (str(row.get("period") or ""), str(row.get("filed") or "")))
    return rows[-16:]


def _period_rows_from_sec_facts(companyfacts: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return _period_rows_from_field_facts({
        field: _latest_facts_by_concept(companyfacts, candidates)
        for field, candidates in SEC_FINANCIAL_CONCEPTS.items()
    })


def _period_rows_from_companyconcepts(concept_payloads: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    field_facts: Dict[str, List[Dict[str, Any]]] = {}
    for field, candidates in SEC_FINANCIAL_CONCEPTS.items():
        facts: List[Dict[str, Any]] = []
        for concept in candidates:
            payload = concept_payloads.get(concept)
            if isinstance(payload, Mapping):
                facts.extend(_facts_from_concept_object(concept, payload))
        field_facts[field] = facts
    return _period_rows_from_field_facts(field_facts)


def _all_sec_financial_concepts() -> List[str]:
    ordered: List[str] = []
    for concepts in SEC_FINANCIAL_CONCEPTS.values():
        for concept in concepts:
            if concept not in ordered:
                ordered.append(concept)
    return ordered


class SecCompanyFactsProvider:
    """Official SEC JSON adapter for US company facts and submissions."""

    name = "SEC_Companyfacts_L0"
    level = SourceLevel.L0
    markets = [Market.US]
    datasets = [Dataset.FINANCIALS, Dataset.FILINGS]

    def __init__(self) -> None:
        self._cik_cache: Dict[str, str] = {}

    def _resolve_cik(self, symbol: SymbolInfo, *, user_agent: str) -> Optional[str]:
        if symbol.cik:
            return str(symbol.cik).zfill(10)
        token = symbol.symbol.upper()
        if token not in self._cik_cache:
            cik = _sec_cik_from_ticker(token, user_agent=user_agent)
            if cik:
                self._cik_cache[token] = cik
        return self._cik_cache.get(token)

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        if symbol.market != Market.US:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "SEC JSON only supports US/SEC filers")
        user_agent, warnings = _sec_user_agent()
        try:
            cik = self._resolve_cik(symbol, user_agent=user_agent)
            if not cik:
                return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"could not resolve SEC CIK for {symbol.symbol}")
            raw_dir = kwargs.get("raw_dir")
            if dataset == Dataset.FILINGS:
                return self._fetch_filings(symbol, cik, user_agent=user_agent, raw_dir=raw_dir, warnings=warnings)
            if dataset == Dataset.FINANCIALS:
                return self._fetch_financials(symbol, cik, user_agent=user_agent, raw_dir=raw_dir, warnings=warnings)
        except Exception as exc:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"SEC JSON fetch failed: {type(exc).__name__}: {exc}")
        return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported dataset {dataset.value}")

    def _fetch_filings(
        self,
        symbol: SymbolInfo,
        cik: str,
        *,
        user_agent: str,
        raw_dir: Optional[str | Path],
        warnings: List[str],
    ) -> DataResult:
        payload = _fetch_sec_submissions_payload(cik, user_agent=user_agent)
        identity_error = _sec_identity_error(symbol, cik, payload)
        if identity_error:
            return DataResult.failed(Dataset.FILINGS, symbol.symbol, self.name, self.level, identity_error)
        raw_path = raw_hash = None
        if raw_dir:
            raw_path, raw_hash = save_raw_json(payload, raw_dir, f"{symbol.symbol}_sec_submissions_raw.json")
        recent = payload.get("filings", {}).get("recent", {}) if isinstance(payload.get("filings"), Mapping) else {}
        forms = recent.get("form", []) or []
        filing_dates = recent.get("filingDate", []) or []
        report_dates = recent.get("reportDate", []) or []
        accession_numbers = recent.get("accessionNumber", []) or []
        primary_documents = recent.get("primaryDocument", []) or []
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
        submissions_payload = _fetch_sec_submissions_payload(cik, user_agent=user_agent)
        identity_error = _sec_identity_error(symbol, cik, submissions_payload)
        if identity_error:
            return DataResult.failed(Dataset.FINANCIALS, symbol.symbol, self.name, self.level, identity_error)
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        payload = https_json(url, user_agent=user_agent)
        raw_path = raw_hash = None
        if raw_dir:
            raw_path, raw_hash = save_raw_json(payload, raw_dir, f"{symbol.symbol}_sec_companyfacts_raw.json")
        periods = _period_rows_from_sec_facts(payload)
        facts = _latest_facts_by_concept(payload, [
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
        as_of = max((str(row.get("filed") or row.get("period") or "") for row in periods), default=None)
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
                "periods": periods,
                "latest_facts": facts[-40:],
                "identity_check": _sec_identity_summary(cik, submissions_payload),
            },
            raw_path=raw_path,
            raw_hash=raw_hash,
            currency=symbol.currency,
            warnings=warnings,
        )


class SecCompanyConceptsProvider:
    """Official SEC companyconcept adapter for US financial statements.

    This provider fetches smaller per-concept SEC XBRL payloads. It is useful
    when the larger companyfacts endpoint is blocked, reset, or too large for
    the current network path.
    """

    name = "SEC_CompanyConcepts_L0"
    level = SourceLevel.L0
    markets = [Market.US]
    datasets = [Dataset.FINANCIALS]

    def __init__(self) -> None:
        self._cik_cache: Dict[str, str] = {}

    def _resolve_cik(self, symbol: SymbolInfo, *, user_agent: str) -> Optional[str]:
        if symbol.cik:
            return str(symbol.cik).zfill(10)
        token = symbol.symbol.upper()
        if token not in self._cik_cache:
            cik = _sec_cik_from_ticker(token, user_agent=user_agent)
            if cik:
                self._cik_cache[token] = cik
        return self._cik_cache.get(token)

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        if symbol.market != Market.US:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "SEC companyconcept only supports US/SEC filers")
        if dataset != Dataset.FINANCIALS:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, f"unsupported dataset {dataset.value}")
        user_agent, warnings = _sec_user_agent()
        try:
            cik = self._resolve_cik(symbol, user_agent=user_agent)
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
        submissions_payload = _fetch_sec_submissions_payload(cik, user_agent=user_agent)
        identity_error = _sec_identity_error(symbol, cik, submissions_payload)
        if identity_error:
            return DataResult.failed(Dataset.FINANCIALS, symbol.symbol, self.name, self.level, identity_error)
        concept_payloads: Dict[str, Mapping[str, Any]] = {}
        concept_errors: List[str] = []
        for concept in _all_sec_financial_concepts():
            url = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{concept}.json"
            try:
                payload = https_json(url, user_agent=user_agent, retries=1)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    concept_errors.append(f"{concept}: not reported")
                    continue
                concept_errors.append(f"{concept}: HTTP {exc.code}")
                continue
            except Exception as exc:
                concept_errors.append(f"{concept}: {type(exc).__name__}: {exc}")
                continue
            if isinstance(payload, Mapping):
                concept_payloads[concept] = payload

        if not concept_payloads:
            return DataResult.failed(
                Dataset.FINANCIALS,
                symbol.symbol,
                self.name,
                self.level,
                "SEC companyconcept returned no usable concepts: " + " | ".join(concept_errors[:8]),
            )

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

        periods = _period_rows_from_companyconcepts(concept_payloads)
        latest_facts: List[Dict[str, Any]] = []
        for concept, payload in concept_payloads.items():
            latest_facts.extend(_facts_from_concept_object(concept, payload))
        latest_facts.sort(key=lambda row: (str(row.get("period") or ""), str(row.get("filed") or ""), str(row.get("concept") or "")))

        if not periods and not latest_facts:
            return DataResult.failed(
                Dataset.FINANCIALS,
                symbol.symbol,
                self.name,
                self.level,
                "SEC companyconcept returned payloads but no 10-K/10-Q financial facts",
            )

        entity_name = next(
            (
                str(payload.get("entityName"))
                for payload in concept_payloads.values()
                if isinstance(payload, Mapping) and payload.get("entityName")
            ),
            None,
        )
        as_of = max((str(row.get("filed") or row.get("period") or "") for row in periods), default=None)
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
                "periods": periods,
                "latest_facts": latest_facts[-40:],
                "concepts_fetched": sorted(concept_payloads),
                "identity_check": _sec_identity_summary(cik, submissions_payload),
            },
            raw_path=raw_path,
            raw_hash=raw_hash,
            currency=symbol.currency,
            warnings=warnings,
        )


class SecEdgarProvider:
    """Optional US filings/fundamentals provider using edgartools if installed.

    Production notes:
    - SEC access requires a real identity/User-Agent.
    - Keep SEC-reported facts separate from market estimates.
    - This skeleton returns failure if edgartools is unavailable or identity is missing.
    """

    name = "SEC_EDGAR_edgartools"
    level = SourceLevel.L0
    markets = [Market.US]
    datasets = [Dataset.FILINGS, Dataset.FINANCIALS]

    def fetch(self, symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
        if symbol.market != Market.US:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "SEC EDGAR only supports US/SEC filers")
        identity = os.getenv("EDGAR_IDENTITY")
        if not identity:
            return DataResult.failed(dataset, symbol.symbol, self.name, self.level, "EDGAR_IDENTITY env var is required")
        try:
            from edgar import Company, set_identity  # type: ignore
            set_identity(identity)
            company = Company(symbol.symbol)
            if dataset == Dataset.FILINGS:
                filings = company.get_filings()
                return DataResult(True, dataset, symbol.symbol, self.name, self.level, utc_now(), data=filings, currency=symbol.currency)
            if dataset == Dataset.FINANCIALS:
                financials = company.get_financials()
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
        providers.append(HkexAnnouncementsProvider())
        providers.append(HkexFinancialReportsProvider())
    if symbol is None or symbol.market == Market.CN_A:
        providers.append(CninfoAnnouncementsProvider())
        providers.append(CninfoFinancialReportsProvider())
        providers.append(EastmoneyF10FinancialsProvider())
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
    parser = argparse.ArgumentParser(description="Serenity + Chan data routing helper")
    parser.add_argument("symbol", nargs="?", default="688019", help="ticker or stock code")
    parser.add_argument("--plan", action="store_true", help="print data fetch plan JSON")
    args = parser.parse_args()

    if args.plan:
        print(json.dumps(build_data_fetch_plan(args.symbol), ensure_ascii=False, indent=2, default=str))
        return

    symbol = resolve_symbol(args.symbol)
    print(json.dumps(symbol.__dict__, ensure_ascii=False, indent=2, default=str))
    for d in [Dataset.CURRENT_QUOTE, Dataset.FINANCIALS, Dataset.FILINGS, Dataset.PRICE_HISTORY_ADJUSTED]:
        print(json.dumps(source_policy(symbol.market, d).__dict__, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
