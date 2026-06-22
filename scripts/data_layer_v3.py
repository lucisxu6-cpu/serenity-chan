#!/usr/bin/env python3
"""
Serenity + Chan Data-First Equity Skill — Data Layer v3

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
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple
import datetime as dt
import json
import math
import os
import re

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
    fallback: List[str]
    forbidden: List[str]
    notes: str = ""


def source_policy(market: Market, dataset: Dataset) -> SourcePolicy:
    """Return preferred source route for a market/dataset pair."""
    if market == Market.CN_A:
        policies: Dict[Dataset, SourcePolicy] = {
            Dataset.FILINGS: SourcePolicy(market, dataset, ["CNINFO", "SSE", "SZSE", "BSE"], ["Wind", "Choice", "CSMAR"], ["Eastmoney/F10"], ["SEC EDGAR"], "Official PDFs/HTML are required for S/A evidence."),
            Dataset.FINANCIALS: SourcePolicy(market, dataset, ["Annual/Quarterly Report PDF"], ["Wind", "Choice", "CSMAR", "Tushare Pro"], ["AKShare", "BaoStock", "Eastmoney"], ["SEC EDGAR"], "Units must be normalized."),
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
        return SourcePolicy(market, dataset, ["HKEXnews", "Company IR"], ["Wind", "Choice", "Bloomberg"], ["yfinance", "AKShare", "AAStocks"], ["SEC EDGAR unless ADR/dual-listed"], "Watch liquidity, placing, connected transactions.")

    return SourcePolicy(market, dataset, [], [], [], [], "Unknown market: resolve before analysis.")


def provider_is_allowed(provider: DataProvider, symbol: SymbolInfo, dataset: Dataset) -> Tuple[bool, str]:
    if symbol.market not in provider.markets and Market.GLOBAL not in provider.markets:
        return False, f"provider {provider.name} does not support market {symbol.market.value}"
    if dataset not in provider.datasets:
        return False, f"provider {provider.name} does not support dataset {dataset.value}"
    return True, ""


def fetch_with_fallback(providers: Iterable[DataProvider], symbol: SymbolInfo, dataset: Dataset, **kwargs: Any) -> DataResult:
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
    return DataResult.failed(dataset, symbol.symbol, "fallback_chain", SourceLevel.L4, "All providers failed or incompatible: " + " | ".join(failures))


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


# Real CNINFO/Tushare/Wind/Choice/AKShare adapters should implement DataProvider.
# Keep them separate so credentials, rate limits, and legal usage are explicit.


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
