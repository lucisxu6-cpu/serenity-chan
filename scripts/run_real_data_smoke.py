#!/usr/bin/env python3
"""Run optional real-data smoke tests for Serenity + Chan data adapters.

This script intentionally depends on external network sources and should not be
part of offline CI. It verifies that real fetches work for:

- the current NVDA use case,
- representative NVDA upstream / adjacent symbols,
- A-share current quote, valuation inputs, adjusted history, structured financials, and announcements,
- HK current quote, valuation inputs, adjusted history, announcements, and official report PDFs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time
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
    minimum_downloaded_reports: int = 0
    required_financial_sector_profile_status: Optional[str] = None
    required_financial_sector_profile_sector: Optional[str] = None
    required_financial_sector_profile_metrics: set[str] = field(default_factory=set)
    required_latest_core_statement_complete: bool = False
    minimum_core_complete_periods: int = 0
    note: str = ""


DEFAULT_CASES = [
    SmokeCase(
        name="nvda-core-us",
        symbol="NVDA",
        case_set="core",
        datasets=["current_quote", "price_history_adjusted", "valuation_inputs", "financials", "filings_announcements"],
        required_quality={"current_price": "OK", "adjusted_history": "OK", "valuation_inputs": "OK", "financials": "OK", "filings": "OK"},
        note="Primary US use case: quote/history via Yahoo L2, valuation share count via SEC L0, financials/filings via SEC L0.",
    ),
    SmokeCase(
        name="nvda-upstream-memory-mu",
        symbol="MU",
        case_set="upstream",
        datasets=["current_quote", "price_history_adjusted", "valuation_inputs", "financials", "filings_announcements"],
        required_quality={"current_price": "OK", "adjusted_history": "OK", "valuation_inputs": "OK", "financials": "OK", "filings": "OK"},
        note="HBM / memory upstream sample.",
    ),
    SmokeCase(
        name="nvda-upstream-accelerator-amd",
        symbol="AMD",
        case_set="upstream",
        datasets=["current_quote", "price_history_adjusted", "valuation_inputs", "financials", "filings_announcements"],
        required_quality={"current_price": "OK", "adjusted_history": "OK", "valuation_inputs": "OK", "financials": "OK", "filings": "OK"},
        note="Accelerator competitor / adjacent compute sample.",
    ),
    SmokeCase(
        name="nvda-upstream-networking-avgo",
        symbol="AVGO",
        case_set="upstream",
        datasets=["current_quote", "price_history_adjusted", "valuation_inputs", "financials", "filings_announcements"],
        required_quality={"current_price": "OK", "adjusted_history": "OK", "valuation_inputs": "OK", "financials": "OK", "filings": "OK"},
        note="Networking / ASIC adjacent sample.",
    ),
    SmokeCase(
        name="nvda-upstream-foundry-adr-tsm",
        symbol="TSM",
        case_set="upstream",
        datasets=["current_quote", "price_history_adjusted", "valuation_inputs", "financials", "filings_announcements"],
        required_quality={"current_price": "OK", "adjusted_history": "OK", "valuation_inputs": "OK", "financials": "OK", "filings": "OK"},
        note="ADR upstream sample: SEC 20-F / IFRS XBRL facts must produce core financial periods.",
    ),
    SmokeCase(
        name="nvda-upstream-equipment-adr-asml",
        symbol="ASML",
        case_set="upstream",
        datasets=["current_quote", "price_history_adjusted", "valuation_inputs", "financials", "filings_announcements"],
        required_quality={"current_price": "OK", "adjusted_history": "OK", "valuation_inputs": "OK", "financials": "OK", "filings": "OK"},
        note="ADR upstream sample: SEC 20-F / US-GAAP XBRL facts must produce core financial periods.",
    ),
    SmokeCase(
        name="a-share-current-688019",
        symbol="688019",
        case_set="a-share",
        datasets=["current_quote", "price_history_adjusted", "valuation_inputs", "financials", "filings_announcements"],
        required_quality={"current_price": "OK", "adjusted_history": "OK", "valuation_inputs": "OK", "financials": "OK", "filings": "OK"},
        note="A-share SH quote/K-line, CNINFO L0 official report PDF financial line extraction, CNINFO metadata, and selected official report PDF sample.",
    ),
    SmokeCase(
        name="a-share-current-300750",
        symbol="300750",
        case_set="a-share",
        datasets=["current_quote", "price_history_adjusted", "valuation_inputs", "financials", "filings_announcements"],
        required_quality={"current_price": "OK", "adjusted_history": "OK", "valuation_inputs": "OK", "financials": "OK", "filings": "OK"},
        required_report_kinds={"annual", "q1"},
        minimum_report_records=2,
        minimum_downloaded_reports=2,
        note="A-share SZ quote/K-line, CNINFO L0 official report PDF financial line extraction, CNINFO metadata, and selected official report PDF sample.",
    ),
    SmokeCase(
        name="a-share-current-300480",
        symbol="300480",
        case_set="a-share",
        datasets=["current_quote", "price_history_adjusted", "valuation_inputs", "financials", "filings_announcements"],
        required_quality={"current_price": "OK", "valuation_inputs": "OK", "financials": "OK", "filings": "OK"},
        allowed_quality={"adjusted_history": {"OK", "PARTIAL"}},
        required_report_kinds={"annual", "q1"},
        minimum_report_records=2,
        minimum_downloaded_reports=2,
        note="A-share regression sample that exercises the real 300480.SZ workflow across market data, financial line extraction, filings, and official report PDF evidence.",
    ),
    SmokeCase(
        name="a-share-financial-sector-600036",
        symbol="600036",
        case_set="a-share",
        datasets=["current_quote", "price_history_adjusted", "valuation_inputs", "financials", "filings_announcements"],
        required_quality={"current_price": "OK", "adjusted_history": "OK", "valuation_inputs": "OK", "financials": "OK", "filings": "OK"},
        required_financial_sector_profile_status="OK",
        required_financial_sector_profile_sector="bank",
        required_financial_sector_profile_metrics={
            "net_interest_income",
            "net_interest_margin_pct",
            "loans_and_advances",
            "customer_deposits",
            "non_performing_loan_ratio_pct",
            "provision_coverage_ratio_pct",
            "core_tier1_capital_adequacy_ratio_pct",
            "capital_adequacy_ratio_pct",
        },
        note="A-share bank sample: CNINFO L0 official report PDF extraction must include bank-specific profile metrics and avoid ordinary-company shortcuts.",
    ),
    SmokeCase(
        name="a-share-securities-sector-600030",
        symbol="600030",
        case_set="a-share",
        datasets=["current_quote", "price_history_adjusted", "valuation_inputs", "financials", "filings_announcements"],
        required_quality={"current_price": "OK", "adjusted_history": "OK", "valuation_inputs": "OK", "financials": "OK", "filings": "OK"},
        required_financial_sector_profile_status="OK",
        required_financial_sector_profile_sector="securities",
        required_financial_sector_profile_metrics={
            "net_capital",
            "risk_coverage_ratio_pct",
            "capital_leverage_ratio_pct",
            "liquidity_coverage_ratio_pct",
            "net_stable_funding_ratio_pct",
        },
        note="A-share securities sample: CNINFO L0 official report PDF extraction must include securities risk-control profile metrics.",
    ),
    SmokeCase(
        name="a-share-insurance-sector-601318",
        symbol="601318",
        case_set="a-share",
        datasets=["current_quote", "price_history_adjusted", "valuation_inputs", "financials", "filings_announcements"],
        required_quality={"current_price": "OK", "adjusted_history": "OK", "valuation_inputs": "OK", "financials": "OK", "filings": "OK"},
        required_financial_sector_profile_status="OK",
        required_financial_sector_profile_sector="insurance",
        required_financial_sector_profile_metrics={
            "insurance_service_revenue",
            "insurance_contract_liabilities",
            "core_solvency_ratio_pct",
            "comprehensive_solvency_ratio_pct",
        },
        note="A-share insurance sample: CNINFO L0 official report PDF extraction must include insurance service, liabilities, and solvency profile metrics.",
    ),
    SmokeCase(
        name="a-share-bj-boundary-920593",
        symbol="920593",
        case_set="a-share",
        datasets=["current_quote", "price_history_adjusted", "valuation_inputs", "financials", "filings_announcements"],
        required_quality={"current_price": "OK", "adjusted_history": "OK", "valuation_inputs": "OK", "financials": "OK", "filings": "OK"},
        required_report_kinds={"annual", "q1"},
        minimum_report_records=2,
        minimum_downloaded_reports=2,
        note="BJ coverage sample: quote/history, official CNINFO equity-distribution adjustment, CNINFO L0 financial line extraction, CNINFO metadata, and selected official report PDFs must resolve.",
    ),
    SmokeCase(
        name="hk-current-0700",
        symbol="0700.HK",
        case_set="hk",
        datasets=["current_quote", "price_history_adjusted", "valuation_inputs", "financials", "filings_announcements"],
        required_quality={"current_price": "OK", "valuation_inputs": "OK", "financials": "OK", "filings": "OK"},
        allowed_quality={"adjusted_history": {"OK", "PARTIAL"}},
        required_report_kinds={"annual", "interim"},
        minimum_report_records=2,
        minimum_downloaded_reports=2,
        note="HK coverage sample: Yahoo quote/history plus HKEXnews valuation inputs, announcements, and official annual/interim report PDFs must resolve; financials are PARTIAL only when PDF line extraction or core field coverage is incomplete.",
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


def _core_statement_expectations(case: SmokeCase) -> tuple[bool, int]:
    requires_latest = case.required_latest_core_statement_complete or (
        case.case_set == "a-share" and case.required_quality.get("financials") == "OK"
    )
    minimum_periods = max(case.minimum_core_complete_periods, 2 if requires_latest else 0)
    return requires_latest, minimum_periods


def _evaluate(case: SmokeCase, manifest: dict[str, Any]) -> tuple[bool, list[str]]:
    quality = manifest.get("data_quality", {}) if isinstance(manifest.get("data_quality"), dict) else {}
    ai_review = manifest.get("ai_review", {}) if isinstance(manifest.get("ai_review"), dict) else {}
    data_acquisition = manifest.get("data_acquisition", {}) if isinstance(manifest.get("data_acquisition"), dict) else {}
    attempt_ledger = data_acquisition.get("attempt_ledger", [])
    data_gaps = data_acquisition.get("data_gaps", [])
    research_debt = data_acquisition.get("research_debt", [])
    manual_tasks = data_acquisition.get("manual_retrieval_tasks", [])
    failures: list[str] = []
    if ai_review.get("required") is not True:
        failures.append("ai_review.required: expected True")
    if not isinstance(data_acquisition, dict) or not data_acquisition:
        failures.append("data_acquisition: expected non-empty object")
    if not isinstance(attempt_ledger, list) or not attempt_ledger:
        failures.append("attempt_ledger: expected non-empty array")
    if not isinstance(data_gaps, list):
        failures.append("data_gaps: expected array")
    if not isinstance(research_debt, list):
        failures.append("research_debt: expected array")
    if not isinstance(manual_tasks, list):
        failures.append("manual_retrieval_tasks: expected array")
    if isinstance(attempt_ledger, list):
        attempted_datasets = {
            str(item.get("dataset"))
            for item in attempt_ledger
            if isinstance(item, dict)
        }
        missing_attempts = sorted(set(case.datasets) - attempted_datasets)
        if missing_attempts:
            failures.append(f"attempt_ledger: missing requested datasets {missing_attempts}")
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
    if case.minimum_downloaded_reports:
        payload = _load_financial_payload(manifest)
        evidence = payload.get("official_report_evidence", {}) if isinstance(payload.get("official_report_evidence"), dict) else {}
        downloaded_reports = evidence.get("downloaded_reports", []) if isinstance(evidence.get("downloaded_reports"), list) else []
        if len(downloaded_reports) < case.minimum_downloaded_reports:
            failures.append(
                f"official_report_evidence.downloaded_reports: expected at least {case.minimum_downloaded_reports}, got {len(downloaded_reports)}"
            )
    requires_latest_core, minimum_core_periods = _core_statement_expectations(case)
    if requires_latest_core or minimum_core_periods:
        payload = _load_financial_payload(manifest)
        source_usage = payload.get("source_usage", {}) if isinstance(payload.get("source_usage"), dict) else {}
        evidence = payload.get("official_report_evidence", {}) if isinstance(payload.get("official_report_evidence"), dict) else {}
        latest_complete = source_usage.get("latest_core_statement_complete", evidence.get("latest_core_statement_complete"))
        missing_latest = source_usage.get("latest_core_statement_missing_fields", evidence.get("latest_core_statement_missing_fields"))
        core_count = source_usage.get("core_complete_period_count", evidence.get("core_complete_period_count"))
        if requires_latest_core and latest_complete is not True:
            failures.append(
                "source_usage.latest_core_statement_complete: expected True, "
                f"got {latest_complete}; missing={missing_latest}"
            )
        try:
            actual_core_periods = int(core_count)
        except (TypeError, ValueError):
            actual_core_periods = -1
        if minimum_core_periods and actual_core_periods < minimum_core_periods:
            failures.append(
                f"source_usage.core_complete_period_count: expected at least {minimum_core_periods}, got {core_count}"
            )
    if case.required_financial_sector_profile_status or case.required_financial_sector_profile_sector or case.required_financial_sector_profile_metrics:
        payload = _load_financial_payload(manifest)
        source_usage = payload.get("source_usage", {}) if isinstance(payload.get("source_usage"), dict) else {}
        expected_status = case.required_financial_sector_profile_status
        if expected_status and source_usage.get("financial_sector_profile_status") != expected_status:
            failures.append(
                f"source_usage.financial_sector_profile_status: expected {expected_status}, got {source_usage.get('financial_sector_profile_status')}"
            )
        profiles = [
            row.get("financial_sector_profile")
            for row in payload.get("periods", [])
            if isinstance(row, dict) and isinstance(row.get("financial_sector_profile"), dict)
        ] if isinstance(payload.get("periods"), list) else []
        if not profiles:
            failures.append("periods.financial_sector_profile: expected at least one profile")
        else:
            latest_profile = profiles[-1]
            expected_sector = case.required_financial_sector_profile_sector
            if expected_sector and latest_profile.get("sector") != expected_sector:
                failures.append(
                    f"periods.financial_sector_profile.sector: expected {expected_sector}, got {latest_profile.get('sector')}"
                )
            expected_profile_status = case.required_financial_sector_profile_status
            if expected_profile_status and latest_profile.get("status") != expected_profile_status:
                failures.append(
                    f"periods.financial_sector_profile.status: expected {expected_profile_status}, got {latest_profile.get('status')}"
                )
            metrics = latest_profile.get("metrics", {}) if isinstance(latest_profile.get("metrics"), dict) else {}
            missing_metrics = sorted(metric for metric in case.required_financial_sector_profile_metrics if metric not in metrics)
            if missing_metrics:
                failures.append(f"periods.financial_sector_profile.metrics: missing {missing_metrics}")
            sanity_warnings = latest_profile.get("sanity_warnings", [])
            if sanity_warnings:
                failures.append(f"periods.financial_sector_profile.sanity_warnings: expected empty, got {sanity_warnings}")
    financial_result = next(
        (
            item
            for item in manifest.get("results", [])
            if isinstance(item, dict) and item.get("dataset") == "financials"
        ),
        {},
    )
    if str(financial_result.get("source_level", "")).startswith("L3_"):
        has_financial_gap = any(
            isinstance(item, dict)
            and item.get("dataset") == "financials"
            and item.get("gap_type") == "NOT_MACHINE_READABLE"
            for item in data_gaps
        )
        has_financial_debt = any(
            isinstance(item, dict)
            and item.get("dataset") == "financials"
            for item in research_debt
        )
        if not has_financial_gap:
            failures.append("data_gaps: L3 financials require NOT_MACHINE_READABLE gap")
        if not has_financial_debt:
            failures.append("research_debt: L3 financials require financial verification debt")
    return not failures, failures


def run_cases(
    cases: Sequence[SmokeCase],
    *,
    out_root: Path,
    sec_user_agent: Optional[str],
    chart_range: str,
    interval: str,
    progress: bool = False,
) -> dict[str, Any]:
    if sec_user_agent:
        os.environ["SEC_USER_AGENT"] = sec_user_agent
    out_root.mkdir(parents=True, exist_ok=True)

    results = []
    for case in cases:
        started = time.monotonic()
        case_dir = out_root / _safe_case_dir(case.name)
        if progress:
            print(f"[RUN] {case.name} {case.symbol}", flush=True)
        try:
            manifest = fetch_real_data(
                case.symbol,
                datasets=case.datasets,
                out_dir=str(case_dir),
                chart_range=chart_range,
                interval=interval,
            )
            ok, failures = _evaluate(case, manifest)
            out_dir = manifest.get("out_dir")
            data_quality = manifest.get("data_quality", {})
            data_acquisition = manifest.get("data_acquisition", {})
        except Exception as exc:
            ok = False
            failures = [f"fetch_real_data raised {type(exc).__name__}: {exc}"]
            out_dir = str(case_dir)
            data_quality = {}
            data_acquisition = {}
        elapsed = time.monotonic() - started
        if progress:
            marker = "PASS" if ok else "FAIL"
            print(f"[{marker}] {case.name} {case.symbol} elapsed={elapsed:.1f}s", flush=True)
        results.append({
            "name": case.name,
            "symbol": case.symbol,
            "case_set": case.case_set,
            "ok": ok,
            "failures": failures,
            "note": case.note,
            "out_dir": out_dir,
            "elapsed_seconds": round(elapsed, 3),
            "data_quality": data_quality,
            "data_acquisition": data_acquisition,
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
        acquisition = item.get("data_acquisition", {})
        print(
            "[{marker}] {name} {symbol}: current={current} adjusted={adjusted} "
            "valuation={valuation} financials={financials} filings={filings} cap={cap} requested_cap={requested_cap} debt={debt}".format(
                marker=marker,
                name=item["name"],
                symbol=item["symbol"],
                current=quality.get("current_price"),
                adjusted=quality.get("adjusted_history"),
                valuation=quality.get("valuation_inputs"),
                financials=quality.get("financials"),
                filings=quality.get("filings"),
                cap=quality.get("rating_cap"),
                requested_cap=quality.get("requested_data_rating_cap"),
                debt=acquisition.get("research_debt_count"),
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
        progress=not args.json,
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print_human(summary)
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
