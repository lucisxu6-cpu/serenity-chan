#!/usr/bin/env python3
"""Validate a Serenity + Chan Markdown report before delivery.

The checker is intentionally conservative: it does not try to judge the
investment thesis. It only verifies the safety contract that prevents the most
damaging failures: missing data-quality disclosure, high ratings despite data
failure, current buy-point claims without price history, weak evidence upgraded
into strong conclusions, and prohibited investment-advice wording.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence


STATUSES = ("OK", "PARTIAL", "STALE", "FAILED", "PENDING", "NOT_APPLICABLE")
RATING_ORDER = {
    "OBSERVE_ONLY": 0,
    "D": 1,
    "C": 2,
    "B": 3,
    "A": 4,
    "S": 5,
}

FORBIDDEN_PHRASES = [
    "必涨",
    "马上梭哈",
    "无风险",
    "确定翻倍",
    "内幕消息",
    "内幕信息",
]


@dataclass
class Finding:
    severity: str
    code: str
    message: str


@dataclass
class ValidationResult:
    ok: bool
    findings: List[Finding]
    extracted: Dict[str, Optional[str]]


def _field_lines(text: str) -> Iterable[str]:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("|"):
            continue
        yield re.sub(r"^(?:[-*+]\s+|\d+\.\s+)", "", stripped).strip()


def _labeled_value(labels: Sequence[str], values: Sequence[str], text: str) -> Optional[str]:
    value_pattern = "|".join(re.escape(value) for value in values)
    for line in _field_lines(text):
        for label in labels:
            pattern = rf"^{re.escape(label)}\s*[：:]\s*\**\s*({value_pattern})\b"
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                return match.group(1).upper()
    return None


def _status_for(labels: Sequence[str], text: str) -> Optional[str]:
    return _labeled_value(labels, STATUSES, text)


def _rating_for(labels: Sequence[str], text: str) -> Optional[str]:
    return _labeled_value(labels, tuple(RATING_ORDER), text)


def _rating_above(value: Optional[str], cap: str) -> bool:
    if value is None:
        return False
    return RATING_ORDER.get(value, -1) > RATING_ORDER[cap]


def _has_heading(text: str, keywords: Sequence[str]) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if not re.match(r"^#{2,6}\s+", stripped):
            continue
        stripped = stripped.lstrip("#").strip(" *\t ")
        if any(keyword.lower() in stripped.lower() for keyword in keywords):
            return True
    return False


def _claims_current_buy_point(text: str) -> bool:
    labels = ["当前买点", "Current buy point", "Current Buy Point"]
    claim_pattern = re.compile(r"(一买|二买|三买|first|second|third|1st|2nd|3rd)", re.I)
    negation_pattern = re.compile(r"(数据不足|无买点|等待|not enough|insufficient|no buy|wait)", re.I)
    for line in _field_lines(text):
        for label in labels:
            match = re.search(rf"^{re.escape(label)}\s*[：:]\s*(.+)$", line, re.I)
            if match:
                value = match.group(1)
                if claim_pattern.search(value) and not negation_pattern.search(value):
                    return True
    return False


def _has_strong_evidence_line(text: str) -> bool:
    source_signal = re.compile(r"(L0|L1|Strong|Verified|原始公告|官方公告|年报|招股书|合同|filing)", re.I)
    support_signal = re.compile(r"(Strong|Verified|确认|证实|支持|披露|disclosed|reported)", re.I)
    weak_line = re.compile(r"(KOL|社媒|截图|群聊|传闻|rumor|social|Weak|L4)", re.I)
    for line in text.splitlines():
        if weak_line.search(line):
            continue
        if source_signal.search(line) and support_signal.search(line):
            return True
    return False


def validate_text(text: str) -> ValidationResult:
    findings: List[Finding] = []
    extracted: Dict[str, Optional[str]] = {
        "rating": _rating_for(["评级", "最终评级", "Final rating", "Final Rating"], text),
        "rating_cap": _rating_for([
            "评级上限",
            "本报告评级上限",
            "因数据限制，本报告评级上限",
            "Rating cap",
            "Rating Cap",
            "Max rating allowed",
            "Max Rating Allowed",
        ], text),
        "current_price": _status_for(["当前价格", "Current price", "Price data"], text),
        "adjusted_history": _status_for(["历史复权行情", "复权历史行情", "Adjusted history", "Technical data"], text),
        "financials": _status_for(["财报数据", "最新财报", "Financial data", "Financials"], text),
        "filings": _status_for(["公告/filing", "公告", "Filing", "Filings"], text),
    }

    required_headings = [
        (["数据质量与限制", "Data Quality"], "missing_data_quality", "missing data-quality section"),
        (["证据", "Evidence"], "missing_evidence", "missing evidence section"),
        (["证伪", "Falsification"], "missing_falsification", "missing falsification section"),
        (["最终动作", "当前动作", "Action"], "missing_action", "missing action section"),
    ]
    for keywords, code, message in required_headings:
        if not _has_heading(text, keywords):
            findings.append(Finding("error", code, message))

    for phrase in FORBIDDEN_PHRASES:
        if phrase in text:
            findings.append(Finding("error", "forbidden_phrase", f"forbidden phrase appears: {phrase}"))

    rating = extracted["rating"]
    rating_cap = extracted["rating_cap"]
    if rating is None:
        findings.append(Finding("error", "missing_rating", "missing final rating"))
    if rating_cap is None:
        findings.append(Finding("error", "missing_rating_cap", "missing rating cap"))
    if rating and rating_cap and _rating_above(rating, rating_cap):
        findings.append(Finding("error", "rating_exceeds_cap", f"rating {rating} exceeds cap {rating_cap}"))

    current_price = extracted["current_price"]
    adjusted_history = extracted["adjusted_history"]
    financials = extracted["financials"]
    filings = extracted["filings"]
    buy_point_claimed = _claims_current_buy_point(text)

    required_statuses = {
        "current_price": "missing current-price status",
        "adjusted_history": "missing adjusted-history status",
        "financials": "missing financial-data status",
        "filings": "missing filing/announcement status",
    }
    for key, message in required_statuses.items():
        if extracted[key] is None:
            findings.append(Finding("error", f"missing_{key}_status", message))

    if current_price in {"FAILED", "PENDING"} and buy_point_claimed:
        findings.append(Finding("error", "buy_point_without_current_price", "current buy point claimed while current price is unavailable"))
    if adjusted_history in {"FAILED", "PENDING"} and buy_point_claimed:
        findings.append(Finding("error", "buy_point_without_adjusted_history", "Chan buy point claimed without adjusted price history"))
    if current_price in {"FAILED", "PENDING"} and _rating_above(rating_cap, "B"):
        findings.append(Finding("error", "rating_cap_not_downgraded_for_price", "rating cap must be B or lower when current price is unavailable"))
    if adjusted_history in {"FAILED", "PENDING"} and _rating_above(rating_cap, "B"):
        findings.append(Finding("error", "rating_cap_not_downgraded_for_adjusted_history", "rating cap must be B or lower when adjusted history is unavailable"))
    if financials in {"FAILED", "PENDING"} and _rating_above(rating, "B"):
        findings.append(Finding("error", "high_rating_without_financials", "S/A rating is not allowed when latest financials are unavailable"))
    if financials in {"FAILED", "PENDING"} and _rating_above(rating_cap, "B"):
        findings.append(Finding("error", "rating_cap_not_downgraded_for_financials", "rating cap must be B or lower when latest financials are unavailable"))
    if filings in {"FAILED", "PENDING"} and _rating_above(rating, "B"):
        findings.append(Finding("error", "high_rating_without_filings", "S/A rating is not allowed when primary filings are unavailable"))
    if filings in {"FAILED", "PENDING"} and _rating_above(rating_cap, "B"):
        findings.append(Finding("error", "rating_cap_not_downgraded_for_filings", "rating cap must be B or lower when primary filings are unavailable"))

    weak_evidence_only = re.search(r"(KOL|社媒|截图|群聊|传闻|rumor|social)", text, re.I)
    if weak_evidence_only and not _has_strong_evidence_line(text):
        if _rating_above(rating, "C"):
            findings.append(Finding("error", "weak_evidence_high_rating", "weak evidence appears upgraded above C without strong support"))
        if _rating_above(rating_cap, "C"):
            findings.append(Finding("error", "weak_evidence_cap_not_downgraded", "rating cap must be C or lower when weak evidence lacks strong support"))

    if not all(marker in text for marker in ["我确定的事实", "我的推断", "还缺"]):
        findings.append(Finding("warning", "missing_uncertainty_statement", "uncertainty statement should include confirmed facts, inference, and missing evidence"))

    ok = not any(f.severity == "error" for f in findings)
    return ValidationResult(ok=ok, findings=findings, extracted=extracted)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a Serenity + Chan Markdown report contract")
    parser.add_argument("report", help="Markdown report path")
    parser.add_argument("--json", action="store_true", help="emit machine-readable result")
    args = parser.parse_args(argv)

    text = Path(args.report).read_text(encoding="utf-8")
    result = validate_text(text)
    if args.json:
        print(json.dumps({
            "ok": result.ok,
            "findings": [asdict(f) for f in result.findings],
            "extracted": result.extracted,
        }, ensure_ascii=False, indent=2))
    else:
        status = "OK" if result.ok else "FAILED"
        print(f"{status}: {args.report}")
        for finding in result.findings:
            print(f"- {finding.severity.upper()} {finding.code}: {finding.message}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
