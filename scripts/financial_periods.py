#!/usr/bin/env python3
"""Normalize financial reporting periods across CN A-share, US, and HK data."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Mapping, Optional, Sequence


ANNUAL_FORMS: set[str] = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
QUARTER_FORMS: set[str] = {"10-Q", "10-Q/A", "6-K", "6-K/A"}
ANNUAL_TOKENS: set[str] = {"FY", "A", "ANNUAL", "ANNUAL_REPORT", "YEAR", "YEAR_END"}
Q1_TOKENS: set[str] = {"Q1", "1Q", "FIRST_QUARTER", "QUARTER_1", "一季度", "第一季度"}
Q2_TOKENS: set[str] = {"Q2", "2Q", "SECOND_QUARTER", "QUARTER_2"}
Q3_TOKENS: set[str] = {"Q3", "3Q", "THIRD_QUARTER", "QUARTER_3", "三季度", "第三季度"}
INTERIM_TOKENS: set[str] = {"INTERIM", "HALF_YEAR", "HALF-YEAR", "H1", "SEMIANNUAL", "SEMI-ANNUAL", "半年度"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _upper(value: Any) -> str:
    return _text(value).upper().replace(" ", "_")


def parse_period_end(value: Any) -> Optional[str]:
    text: str = _text(value)
    if not text:
        return None
    text = text[:10].replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def period_year(period: Any) -> Optional[int]:
    period_end: Optional[str] = parse_period_end(period)
    if not period_end:
        return None
    try:
        return int(period_end[:4])
    except ValueError:
        return None


def _row_blob(row: Mapping[str, Any]) -> str:
    keys: list[str] = [
        "period_type",
        "fiscal_period",
        "fp",
        "form",
        "form_type",
        "source_form",
        "source_report_kind",
        "report_kind",
        "report_type",
        "source_title",
        "title",
    ]
    return " ".join(_text(row.get(key)) for key in keys)


def _normalized_token_set(row: Mapping[str, Any]) -> set[str]:
    tokens: set[str] = {_upper(row.get(key)) for key in ["period_type", "fiscal_period", "fp", "source_report_kind", "report_kind", "report_type"]}
    tokens.discard("")
    blob: str = _row_blob(row)
    if re.search(r"年度报告|年報|ANNUAL REPORT", blob, flags=re.I):
        tokens.add("ANNUAL_REPORT")
    if re.search(r"第一季度|一季度|FIRST QUARTER", blob, flags=re.I):
        tokens.add("Q1")
    if re.search(r"第三季度|三季度|THIRD QUARTER", blob, flags=re.I):
        tokens.add("Q3")
    if re.search(r"半年度|中期|INTERIM|HALF[- ]?YEAR", blob, flags=re.I):
        tokens.add("INTERIM")
    return tokens


def _form_type(row: Mapping[str, Any]) -> str:
    return _upper(row.get("form") or row.get("form_type") or row.get("source_form"))


def fiscal_year(row: Mapping[str, Any]) -> Optional[int]:
    for key in ("fiscal_year", "fy"):
        value: Any = row.get(key)
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    return period_year(row.get("period") or row.get("period_end"))


def normalize_financial_period(row: Mapping[str, Any], *, market: str = "", source: str = "") -> dict[str, Any]:
    period_end: Optional[str] = parse_period_end(row.get("period") or row.get("period_end") or row.get("date"))
    tokens: set[str] = _normalized_token_set(row)
    form: str = _form_type(row)
    lower_blob: str = _row_blob(row).lower()

    is_annual: bool = form in ANNUAL_FORMS or bool(tokens & ANNUAL_TOKENS)
    is_q1: bool = bool(tokens & Q1_TOKENS)
    is_q2: bool = bool(tokens & Q2_TOKENS)
    is_q3: bool = bool(tokens & Q3_TOKENS)
    is_interim: bool = bool(tokens & INTERIM_TOKENS)
    if not (is_annual or is_q1 or is_q2 or is_q3 or is_interim) and period_end:
        if period_end.endswith("-03-31"):
            is_q1 = True
        elif period_end.endswith("-06-30"):
            is_interim = True
        elif period_end.endswith("-09-30"):
            is_q3 = True
        elif period_end.endswith("-12-31"):
            is_annual = True

    if "年度报告摘要" in lower_blob:
        is_annual = False
    if is_q1 or is_q2 or is_q3 or is_interim:
        is_annual = False
    if form in QUARTER_FORMS and form not in ANNUAL_FORMS:
        is_annual = False

    period_type: str = "annual" if is_annual else "q1" if is_q1 else "q2" if is_q2 else "q3" if is_q3 else "interim" if is_interim else "unknown"
    selection_rule: str = ""
    if is_annual and form in ANNUAL_FORMS:
        selection_rule = f"form={form}"
    elif is_annual and tokens & ANNUAL_TOKENS:
        selection_rule = "period_token=annual"
    elif is_annual:
        selection_rule = "date_fallback=12-31" if period_end and period_end.endswith("-12-31") else "title_or_report_kind=annual"
    elif period_type != "unknown":
        selection_rule = f"period_token={period_type}"

    return {
        "period_end": period_end,
        "calendar_year": period_year(period_end),
        "fiscal_year": fiscal_year(row),
        "fiscal_period": _text(row.get("fiscal_period") or row.get("fp") or row.get("period_type") or row.get("source_report_kind") or row.get("report_kind")),
        "form_type": form,
        "period_type": period_type,
        "is_annual": is_annual,
        "is_quarter": period_type in {"q1", "q2", "q3"},
        "is_interim": is_interim,
        "source": source or _text(row.get("source")),
        "market": market,
        "selection_rule": selection_rule or "unclassified_period_metadata",
    }


def is_annual_period(row: Mapping[str, Any], *, market: str = "", source: str = "") -> bool:
    return bool(normalize_financial_period(row, market=market, source=source)["is_annual"])


def is_quarter_period(row: Mapping[str, Any], quarter: str, *, market: str = "", source: str = "") -> bool:
    normalized: dict[str, Any] = normalize_financial_period(row, market=market, source=source)
    return normalized["period_type"] == quarter.lower()


def _selection_key(row: Mapping[str, Any], *, market: str = "", source: str = "") -> tuple[int, str]:
    normalized: dict[str, Any] = normalize_financial_period(row, market=market, source=source)
    fy: Any = normalized.get("fiscal_year")
    year: int = fy if isinstance(fy, int) else period_year(normalized.get("period_end")) or -1
    return year, str(normalized.get("period_end") or "")


def latest_annual(
    rows: Sequence[Mapping[str, Any]],
    *,
    before_fiscal_year: Optional[int] = None,
    before_period_end: Optional[str] = None,
    market: str = "",
    source: str = "",
) -> Optional[Mapping[str, Any]]:
    candidates: list[Mapping[str, Any]] = []
    for row in rows:
        normalized: dict[str, Any] = normalize_financial_period(row, market=market, source=source)
        year: Any = normalized.get("fiscal_year")
        period_end: str = str(normalized.get("period_end") or "")
        if not normalized.get("is_annual") or not isinstance(year, int):
            continue
        if before_period_end is not None and period_end >= before_period_end:
            continue
        if before_fiscal_year is not None and year >= before_fiscal_year:
            continue
        candidates.append(row)
    return max(candidates, key=lambda row: _selection_key(row, market=market, source=source)) if candidates else None


def latest_quarter(
    rows: Sequence[Mapping[str, Any]],
    quarter: str,
    *,
    before_fiscal_year: Optional[int] = None,
    before_period_end: Optional[str] = None,
    market: str = "",
    source: str = "",
) -> Optional[Mapping[str, Any]]:
    quarter_normalized: str = quarter.lower()
    candidates: list[Mapping[str, Any]] = []
    for row in rows:
        normalized: dict[str, Any] = normalize_financial_period(row, market=market, source=source)
        year: Any = normalized.get("fiscal_year")
        period_end: str = str(normalized.get("period_end") or "")
        if normalized.get("period_type") != quarter_normalized or not isinstance(year, int):
            continue
        if before_period_end is not None and period_end >= before_period_end:
            continue
        if before_fiscal_year is not None and year >= before_fiscal_year:
            continue
        candidates.append(row)
    return max(candidates, key=lambda row: _selection_key(row, market=market, source=source)) if candidates else None
