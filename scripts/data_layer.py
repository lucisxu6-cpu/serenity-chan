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
from enum import Enum
import gzip
import http.client
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple
import datetime as dt
import json
import math
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    import pandas as pd  # type: ignore
except Exception:  # pragma: no cover
    pd = None  # type: ignore


# ---------------------------------------------------------------------------
# Enums and data contracts
# ---------------------------------------------------------------------------

class Market(str, Enum):
    CN_A = "CN_A"
    HK = "HK"
    US = "US"
    GLOBAL = "GLOBAL"
    UNKNOWN = "UNKNOWN"


class SourceLevel(str, Enum):
    L0 = "L0_OFFICIAL_DISCLOSURE"
    L1 = "L1_LICENSED_OR_PRO_DATABASE"
    L2 = "L2_FREE_API_OR_OPEN_SOURCE"
    L3 = "L3_MEDIA_F10_RESEARCH"
    L4 = "L4_RUMOR_OR_UNVERIFIED"


class Dataset(str, Enum):
    CURRENT_QUOTE = "current_quote"
    PRICE_HISTORY_RAW = "price_history_raw"
    PRICE_HISTORY_ADJUSTED = "price_history_adjusted"
    SHARE_CAPITAL = "share_capital"
    FINANCIALS = "financials"
    FILINGS = "filings_announcements"
    CUSTOMER_EVIDENCE = "customer_order_capacity_evidence"
    PEER_VALUATION = "peer_valuation"
    ESTIMATES = "consensus_estimates"
    TRADING_CALENDAR = "trading_calendar"


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
    D = "D"
    OBSERVE_ONLY = "OBSERVE_ONLY"


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
            Dataset.CURRENT_QUOTE: SourcePolicy(market, dataset, ["Exchange/vendor"], ["Wind", "Choice", "Tushare Pro"], ["AKShare", "Eastmoney", "Sina"], ["SEC EDGAR"], "Latest A-share trading day required."),
            Dataset.PRICE_HISTORY_ADJUSTED: SourcePolicy(market, dataset, ["licensed vendor"], ["Tushare Pro", "BaoStock"], ["AKShare"], ["SEC EDGAR"], "Use qfq/front-adjusted for technical."),
            Dataset.PRICE_HISTORY_RAW: SourcePolicy(market, dataset, ["licensed vendor"], ["Tushare Pro", "BaoStock"], ["AKShare"], ["SEC EDGAR"], "Use raw for actual current/reference price."),
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
# Data Fetch Plan
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
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(yahoo_symbol)}?{params}"
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


class EastmoneyF10FinancialsProvider:
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

        official_report_evidence = self._locate_official_report_evidence(symbol)
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

    def _locate_official_report_evidence(self, symbol: SymbolInfo) -> Dict[str, Any]:
        code, _, suffix = symbol.symbol.partition(".")
        evidence: Dict[str, Any] = {
            "status": "FAILED",
            "source": "CNINFO_Announcements_L0",
            "source_level": SourceLevel.L0.value,
            "reports": [],
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
            evidence.update({
                "status": "OK" if reports else "PARTIAL",
                "code": code,
                "org_id": listing.get("orgId"),
                "name": listing.get("zwjc"),
                "queried_announcements": len(announcements) if isinstance(announcements, list) else 0,
                "queried_pages": queried_pages,
                "reports": reports,
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
        return bool(re.search(r"(年度报告|半年度报告|第一季度报告|第三季度报告|季度报告)", title))

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


def _sec_user_agent() -> Tuple[str, List[str]]:
    identity = os.getenv("SEC_USER_AGENT") or os.getenv("EDGAR_IDENTITY")
    if identity:
        return identity, []
    return (
        "serenity-chan-stock-skill/0.1 (set SEC_USER_AGENT for contact)",
        ["SEC_USER_AGENT or EDGAR_IDENTITY is not set; using generic research User-Agent."],
    )


def _sec_cik_from_ticker(ticker: str, *, user_agent: str) -> Optional[str]:
    payload = https_json("https://www.sec.gov/files/company_tickers.json", user_agent=user_agent)
    token = ticker.upper().replace("-", ".")
    for row in payload.values():
        if str(row.get("ticker", "")).upper() == token:
            cik = int(row["cik_str"])
            return f"{cik:010d}"
    return None


def _latest_facts_by_concept(companyfacts: Mapping[str, Any], concept_names: Sequence[str]) -> List[Dict[str, Any]]:
    facts = companyfacts.get("facts", {}) if isinstance(companyfacts.get("facts"), Mapping) else {}
    us_gaap = facts.get("us-gaap", {}) if isinstance(facts.get("us-gaap"), Mapping) else {}
    output: List[Dict[str, Any]] = []
    for concept in concept_names:
        concept_obj = us_gaap.get(concept)
        if not isinstance(concept_obj, Mapping):
            continue
        units = concept_obj.get("units", {}) if isinstance(concept_obj.get("units"), Mapping) else {}
        unit = "USD" if "USD" in units else next(iter(units), None)
        if not unit:
            continue
        for fact in units.get(unit, []):
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


def _period_rows_from_sec_facts(companyfacts: Mapping[str, Any]) -> List[Dict[str, Any]]:
    concepts = {
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
    rows_by_period: Dict[Tuple[str, str, str, str, str], Dict[str, Any]] = {}
    for field, candidates in concepts.items():
        facts = _latest_facts_by_concept(companyfacts, candidates)
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
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        payload = https_json(url, user_agent=user_agent)
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
    if symbol is None or symbol.market in {Market.US, Market.HK, Market.CN_A}:
        providers.append(YahooChartProvider())
    if symbol is None or symbol.market == Market.CN_A:
        providers.append(CninfoAnnouncementsProvider())
        providers.append(EastmoneyF10FinancialsProvider())
    if symbol is None or symbol.market == Market.US:
        providers.append(SecCompanyFactsProvider())
    return providers


# Real Tushare/Wind/Choice/AKShare/HKEX adapters should implement DataProvider.
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
