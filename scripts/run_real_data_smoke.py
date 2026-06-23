#!/usr/bin/env python3
"""Run optional real-data smoke tests for Serenity + Chan data adapters.

This script intentionally depends on external network sources and should not be
part of offline CI. It verifies that real fetches work for:

- the current NVDA use case,
- representative NVDA upstream / adjacent symbols,
- A-share current quote, adjusted history, structured financials, and announcements,
- HK current quote and adjusted history.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

try:
    from data_router import fetch_real_data
except ModuleNotFoundError:  # pragma: no cover - supports python -m scripts.run_real_data_smoke
    from scripts.data_router import fetch_real_data


@dataclass(frozen=True)
class SmokeCase:
    name: str
    symbol: str
    case_set: str
    datasets: Sequence[str]
    required_quality: dict[str, str]
    allowed_quality: dict[str, set[str]] = field(default_factory=dict)
    required_report_kinds: set[str] = field(default_factory=set)
    minimum_report_records: int = 0
    note: str = ""


DEFAULT_CASES = [
    SmokeCase(
        name="nvda-core-us",
        symbol="NVDA",
        case_set="core",
        datasets=["current_quote", "price_history_adjusted", "financials", "filings_announcements"],
        required_quality={"current_price": "OK", "adjusted_history": "OK", "financials": "OK", "filings": "OK"},
        note="Primary US use case: quote/history via Yahoo L2, financials/filings via SEC L0.",
    ),
    SmokeCase(
        name="nvda-upstream-memory-mu",
        symbol="MU",
        case_set="upstream",
        datasets=["current_quote", "price_history_adjusted", "financials", "filings_announcements"],
        required_quality={"current_price": "OK", "adjusted_history": "OK", "financials": "OK", "filings": "OK"},
        note="HBM / memory upstream sample.",
    ),
    SmokeCase(
        name="nvda-upstream-accelerator-amd",
        symbol="AMD",
        case_set="upstream",
        datasets=["current_quote", "price_history_adjusted", "financials", "filings_announcements"],
        required_quality={"current_price": "OK", "adjusted_history": "OK", "financials": "OK", "filings": "OK"},
        note="Accelerator competitor / adjacent compute sample.",
    ),
    SmokeCase(
        name="nvda-upstream-networking-avgo",
        symbol="AVGO",
        case_set="upstream",
        datasets=["current_quote", "price_history_adjusted", "financials", "filings_announcements"],
        required_quality={"current_price": "OK", "adjusted_history": "OK", "financials": "OK", "filings": "OK"},
        note="Networking / ASIC adjacent sample.",
    ),
    SmokeCase(
        name="nvda-upstream-foundry-adr-tsm",
        symbol="TSM",
        case_set="upstream",
        datasets=["current_quote", "price_history_adjusted", "financials", "filings_announcements"],
        required_quality={"current_price": "OK", "adjusted_history": "OK", "filings": "OK"},
        allowed_quality={"financials": {"OK", "FAILED", "PENDING"}},
        note="ADR boundary sample: SEC submissions may exist while companyfacts financials are unavailable.",
    ),
    SmokeCase(
        name="nvda-upstream-equipment-adr-asml",
        symbol="ASML",
        case_set="upstream",
        datasets=["current_quote", "price_history_adjusted", "financials", "filings_announcements"],
        required_quality={"current_price": "OK", "adjusted_history": "OK", "filings": "OK"},
        allowed_quality={"financials": {"OK", "FAILED", "PENDING"}},
        note="ADR boundary sample: SEC submissions may exist while companyfacts financials are unavailable.",
    ),
    SmokeCase(
        name="a-share-current-688019",
        symbol="688019",
        case_set="a-share",
        datasets=["current_quote", "price_history_adjusted", "financials", "filings_announcements"],
        required_quality={"current_price": "OK", "adjusted_history": "OK", "financials": "OK", "filings": "OK"},
        note="A-share SH quote/history, Eastmoney F10 structured financials, and CNINFO announcement metadata sample.",
    ),
    SmokeCase(
        name="a-share-current-300750",
        symbol="300750",
        case_set="a-share",
        datasets=["current_quote", "price_history_adjusted", "financials", "filings_announcements"],
        required_quality={"current_price": "OK", "adjusted_history": "OK", "financials": "OK", "filings": "OK"},
        required_report_kinds={"annual", "q1"},
        minimum_report_records=2,
        note="A-share SZ quote/history, Eastmoney F10 structured financials, and CNINFO announcement metadata sample.",
    ),
    SmokeCase(
        name="a-share-current-300480",
        symbol="300480",
        case_set="a-share",
        datasets=["current_quote", "price_history_adjusted", "financials", "filings_announcements"],
        required_quality={"current_price": "OK", "adjusted_history": "OK", "financials": "OK", "filings": "OK"},
        required_report_kinds={"annual", "q1"},
        minimum_report_records=2,
        note="A-share regression sample that exercises the real 300480.SZ workflow.",
    ),
    SmokeCase(
        name="a-share-financial-sector-600036",
        symbol="600036",
        case_set="a-share",
        datasets=["current_quote", "price_history_adjusted", "financials", "filings_announcements"],
        required_quality={"current_price": "OK", "adjusted_history": "OK", "filings": "OK"},
        allowed_quality={"financials": {"OK", "PARTIAL"}},
        note="A-share bank boundary: ordinary three-statement L3 preflight data may be partial and must remain capped.",
    ),
    SmokeCase(
        name="a-share-bj-boundary-920593",
        symbol="920593",
        case_set="a-share",
        datasets=["current_quote", "price_history_adjusted", "financials", "filings_announcements"],
        required_quality={"financials": "OK", "filings": "OK"},
        allowed_quality={"current_price": {"OK", "FAILED"}, "adjusted_history": {"OK", "FAILED"}},
        note="BJ boundary: financials/filings can resolve while Yahoo quote/history may be unavailable; entry claims must stay capped.",
    ),
    SmokeCase(
        name="hk-current-0700",
        symbol="0700.HK",
        case_set="hk",
        datasets=["current_quote", "price_history_adjusted"],
        required_quality={"current_price": "OK"},
        allowed_quality={"adjusted_history": {"OK", "PARTIAL"}},
        note="HK current quote and adjusted history sample; source-quality validation may cap technical conclusions when adjusted OHLC is partial.",
    ),
]


def _case_matches(case: SmokeCase, case_set: str) -> bool:
    return case_set == "all" or case.case_set == case_set


def _safe_case_dir(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in name)


def _load_financial_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    for item in manifest.get("results", []):
        if not isinstance(item, dict) or item.get("dataset") != "financials":
            continue
        path = item.get("data_path")
        if not path:
            return {}
        try:
            payload = json.loads(Path(str(path)).read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def _evaluate(case: SmokeCase, manifest: dict[str, Any]) -> tuple[bool, list[str]]:
    quality = manifest.get("data_quality", {}) if isinstance(manifest.get("data_quality"), dict) else {}
    ai_review = manifest.get("ai_review", {}) if isinstance(manifest.get("ai_review"), dict) else {}
    failures: list[str] = []
    if ai_review.get("required") is not True:
        failures.append("ai_review.required: expected True")
    for key, expected in case.required_quality.items():
        actual = quality.get(key)
        if actual != expected:
            failures.append(f"{key}: expected {expected}, got {actual}")
    for key, allowed in case.allowed_quality.items():
        actual = quality.get(key)
        if actual not in allowed:
            failures.append(f"{key}: expected one of {sorted(allowed)}, got {actual}")
    if case.required_report_kinds or case.minimum_report_records:
        payload = _load_financial_payload(manifest)
        evidence = payload.get("official_report_evidence", {}) if isinstance(payload.get("official_report_evidence"), dict) else {}
        reports = evidence.get("reports", []) if isinstance(evidence.get("reports"), list) else []
        report_kinds = {str(report.get("report_kind")) for report in reports if isinstance(report, dict)}
        if len(reports) < case.minimum_report_records:
            failures.append(f"official_report_evidence.reports: expected at least {case.minimum_report_records}, got {len(reports)}")
        missing_kinds = sorted(case.required_report_kinds - report_kinds)
        if missing_kinds:
            failures.append(f"official_report_evidence.report_kind: missing {missing_kinds}, got {sorted(report_kinds)}")
    return not failures, failures


def run_cases(
    cases: Sequence[SmokeCase],
    *,
    out_root: Path,
    sec_user_agent: Optional[str],
    chart_range: str,
    interval: str,
) -> dict[str, Any]:
    if sec_user_agent:
        os.environ["SEC_USER_AGENT"] = sec_user_agent
    out_root.mkdir(parents=True, exist_ok=True)

    results = []
    for case in cases:
        case_dir = out_root / _safe_case_dir(case.name)
        manifest = fetch_real_data(
            case.symbol,
            datasets=case.datasets,
            out_dir=str(case_dir),
            chart_range=chart_range,
            interval=interval,
        )
        ok, failures = _evaluate(case, manifest)
        results.append({
            "name": case.name,
            "symbol": case.symbol,
            "case_set": case.case_set,
            "ok": ok,
            "failures": failures,
            "note": case.note,
            "out_dir": manifest.get("out_dir"),
            "data_quality": manifest.get("data_quality", {}),
        })

    return {
        "ok": all(item["ok"] for item in results),
        "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "out_root": str(out_root),
        "results": results,
    }


def _print_human(summary: dict[str, Any]) -> None:
    print(f"Real-data smoke: {'OK' if summary['ok'] else 'FAILED'}")
    print(f"Out root: {summary['out_root']}")
    for item in summary["results"]:
        marker = "PASS" if item["ok"] else "FAIL"
        quality = item["data_quality"]
        print(
            "[{marker}] {name} {symbol}: current={current} adjusted={adjusted} "
            "financials={financials} filings={filings} cap={cap} requested_cap={requested_cap}".format(
                marker=marker,
                name=item["name"],
                symbol=item["symbol"],
                current=quality.get("current_price"),
                adjusted=quality.get("adjusted_history"),
                financials=quality.get("financials"),
                filings=quality.get("filings"),
                cap=quality.get("rating_cap"),
                requested_cap=quality.get("requested_data_rating_cap"),
            )
        )
        for failure in item["failures"]:
            print(f"  - {failure}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run optional real-data smoke tests")
    parser.add_argument("--case-set", choices=["core", "upstream", "a-share", "hk", "all"], default="all")
    parser.add_argument("--out-root", default="/tmp/serenity-chan-real-data-smoke")
    parser.add_argument("--sec-user-agent", help="SEC-compliant User-Agent, e.g. 'Your Name your.email@example.com'")
    parser.add_argument("--range", dest="chart_range", default="2y")
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    selected = [case for case in DEFAULT_CASES if _case_matches(case, args.case_set)]
    summary = run_cases(
        selected,
        out_root=Path(args.out_root),
        sec_user_agent=args.sec_user_agent,
        chart_range=args.chart_range,
        interval=args.interval,
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print_human(summary)
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
