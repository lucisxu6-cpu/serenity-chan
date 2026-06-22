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
import sys
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


def _find_first(patterns: Iterable[str], text: str, flags: int = re.IGNORECASE) -> Optional[str]:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            return match.group(1).strip()
    return None


def _status_for(labels: Sequence[str], text: str) -> Optional[str]:
    label_pattern = "|".join(re.escape(label) for label in labels)
    status_pattern = "|".join(STATUSES)
    value = _find_first([
        rf"(?:{label_pattern})\s*[：:]\s*({status_pattern})\b",
        rf"(?:{label_pattern}).{{0,24}}\b({status_pattern})\b",
    ], text)
    return value.upper() if value else None


def _rating_for(labels: Sequence[str], text: str) -> Optional[str]:
    label_pattern = "|".join(re.escape(label) for label in labels)
    value = _find_first([
        rf"(?:{label_pattern})\s*[：:]\s*\**\s*(OBSERVE_ONLY|S|A|B|C|D)\b",
        rf"(?:{label_pattern}).{{0,16}}\**\s*(OBSERVE_ONLY|S|A|B|C|D)\b",
    ], text)
    return value.upper() if value else None


def _rating_above(value: Optional[str], cap: str) -> bool:
    if value is None:
        return False
    return RATING_ORDER.get(value, -1) > RATING_ORDER[cap]


def _has_heading(text: str, keywords: Sequence[str]) -> bool:
    for line in text.splitlines():
        stripped = line.strip("# *\t ")
        if any(keyword.lower() in stripped.lower() for keyword in keywords):
            return True
    return False


def _claims_current_buy_point(text: str) -> bool:
    buy_point = _find_first([
        r"当前买点\s*[：:]\s*(一买|二买|三买)",
        r"Current\s+buy\s+point\s*[：:]\s*(first|second|third|1st|2nd|3rd)",
    ], text)
    if not buy_point:
        return False
    surrounding = re.search(r"当前买点\s*[：:].{0,32}", text)
    if surrounding and re.search(r"数据不足|无买点|not enough|insufficient|no buy", surrounding.group(0), re.I):
        return False
    return True


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
        "rating": _rating_for(["评级", "Final rating", "Final Rating"], text),
        "rating_cap": _rating_for(["评级上限", "Rating cap", "Max rating allowed", "Max Rating Allowed"], text),
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

    if current_price in {"FAILED", "PENDING"} and buy_point_claimed:
        findings.append(Finding("error", "buy_point_without_current_price", "current buy point claimed while current price is unavailable"))
    if adjusted_history in {"FAILED", "PENDING"} and buy_point_claimed:
        findings.append(Finding("error", "buy_point_without_adjusted_history", "Chan buy point claimed without adjusted price history"))
    if financials in {"FAILED", "PENDING"} and _rating_above(rating, "B"):
        findings.append(Finding("error", "high_rating_without_financials", "S/A rating is not allowed when latest financials are unavailable"))
    if filings in {"FAILED", "PENDING"} and _rating_above(rating, "B"):
        findings.append(Finding("error", "high_rating_without_filings", "S/A rating is not allowed when primary filings are unavailable"))

    weak_evidence_only = re.search(r"(KOL|社媒|截图|群聊|传闻|rumor|social)", text, re.I)
    if weak_evidence_only and not _has_strong_evidence_line(text) and _rating_above(rating, "C"):
        findings.append(Finding("error", "weak_evidence_high_rating", "weak evidence appears upgraded above C without strong support"))

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
