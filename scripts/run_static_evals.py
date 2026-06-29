#!/usr/bin/env python3
"""Run static contract evals for serenity-chan-stock-skill."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

try:
    import data_layer as data_layer_module
    from validate_output_contract import validate_text
    from validate_output_contract_json import validate_contract
    from validate_comparison_report import validate_file as validate_comparison_report_file
    from data_router import validate_financials, validate_valuation_inputs, _build_data_gaps, _build_research_debt, _fetch_with_attempt_ledger
    from build_falsification_dashboard import build_from_output_contract
    from a_share_capital_actions import analyze_announcements
    from a_share_capital_action_quantifier import quantify_capital_actions
    from build_ai_overlay_prompt import build_ai_overlay_prompt
    from build_comparison_report import build_comparison_report, validate_comparison_report, _action_gate_profile, _debt_gate_profile, _financial_currency, _growth_hypothesis
    from build_ai_committee_packet import build_ai_committee_packet
    from build_ai_review_packet import build_ai_review_packet
    from build_research_debt_runbook import build_runbook_rows
    from candidate_ranker import rank_candidates
    from currency_normalizer import normalize_valuation_payload
    from data_consumption import financial_consumption_audit, ranking_validity_from_consumption, valuation_consumption_audit
    from financial_amounts import financial_unit_multiplier, normalize_financial_amount
    from financial_periods import latest_annual, latest_quarter, normalize_financial_period
    from serenity_chan_scorecard import score
    from technical_health import analyze_price_rows
    from validate_ai_overlay import validate_overlay
    from validate_ai_review_outcome import validate_review_outcome
    from validate_and_merge_ai_overlay import build_validated_merged_report
    from render_research_report import render_report
    from data_layer import CninfoFinancialReportsProvider, EastmoneyF10FinancialsProvider, HkexFinancialReportsProvider, Market, SymbolInfo, default_real_providers, _sec_submission_matches_symbol
except ModuleNotFoundError:  # pragma: no cover - supports python -m scripts.run_static_evals
    from scripts import data_layer as data_layer_module
    from scripts.validate_output_contract import validate_text
    from scripts.validate_output_contract_json import validate_contract
    from scripts.validate_comparison_report import validate_file as validate_comparison_report_file
    from scripts.data_router import validate_financials, validate_valuation_inputs, _build_data_gaps, _build_research_debt, _fetch_with_attempt_ledger
    from scripts.build_falsification_dashboard import build_from_output_contract
    from scripts.a_share_capital_actions import analyze_announcements
    from scripts.a_share_capital_action_quantifier import quantify_capital_actions
    from scripts.build_ai_overlay_prompt import build_ai_overlay_prompt
    from scripts.build_comparison_report import build_comparison_report, validate_comparison_report, _action_gate_profile, _debt_gate_profile, _financial_currency, _growth_hypothesis
    from scripts.build_ai_committee_packet import build_ai_committee_packet
    from scripts.build_ai_review_packet import build_ai_review_packet
    from scripts.build_research_debt_runbook import build_runbook_rows
    from scripts.candidate_ranker import rank_candidates
    from scripts.currency_normalizer import normalize_valuation_payload
    from scripts.data_consumption import financial_consumption_audit, ranking_validity_from_consumption, valuation_consumption_audit
    from scripts.financial_amounts import financial_unit_multiplier, normalize_financial_amount
    from scripts.financial_periods import latest_annual, latest_quarter, normalize_financial_period
    from scripts.serenity_chan_scorecard import score
    from scripts.technical_health import analyze_price_rows
    from scripts.validate_ai_overlay import validate_overlay
    from scripts.validate_ai_review_outcome import validate_review_outcome
    from scripts.validate_and_merge_ai_overlay import build_validated_merged_report
    from scripts.render_research_report import render_report
    from scripts.data_layer import CninfoFinancialReportsProvider, EastmoneyF10FinancialsProvider, HkexFinancialReportsProvider, Market, SymbolInfo, default_real_providers, _sec_submission_matches_symbol


def _get_path(payload: Any, path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _set_path(payload: Any, path: str, value: Any) -> None:
    current: Any = payload
    parts: Any = path.split(".")
    for part in parts[:-1]:
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise ValueError(f"cannot traverse mutation path: {path}")
    last: Any = parts[-1]
    if isinstance(current, list):
        current[int(last)] = value
    elif isinstance(current, dict):
        current[last] = value
    else:
        raise ValueError(f"cannot set mutation path: {path}")


def _contains_mapping(payload: Any, path: str, expected: dict[str, Any]) -> bool:
    value: Any = _get_path(payload, path)
    if not isinstance(value, list):
        return False
    for item in value:
        if isinstance(item, dict) and all(item.get(k) == v for k, v in expected.items()):
            return True
    return False


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: Any = argparse.ArgumentParser(description="Run static Serenity + Chan eval cases")
    parser.add_argument("--cases", default="evals/static_cases.json", help="JSON case file")
    args: Any = parser.parse_args(argv)

    root: Any = Path.cwd()
    cases_path: Any = root / args.cases
    cases: Any = json.loads(cases_path.read_text(encoding="utf-8"))
    failures: Any = 0

    for case in cases:
        name: str = str(case["name"])
        expect_pass: bool = bool(case["expect_pass"])
        kind: str = str(case.get("kind", "report"))
        findings: list[str] = []
        result_payload: dict[str, Any] = {}
        actual_pass: bool = False

        if kind == "report":
            report_path: Any = root / case["report"]
            result: Any = validate_text(report_path.read_text(encoding="utf-8"))
            actual_pass = result.ok
            findings = [f"{f.severity.upper()} {f.code}: {f.message}" for f in result.findings]
            result_payload = result.extracted
        elif kind == "scorecard":
            scorecard_path: Any = root / case["scorecard"]
            try:
                result_payload = score(json.loads(scorecard_path.read_text(encoding="utf-8")))
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "output_json":
            contract_path: Any = root / case["contract"]
            try:
                result_payload = validate_contract(json.loads(contract_path.read_text(encoding="utf-8")))
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "financial_validation":
            payload: Any = case.get("payload", {})
            try:
                with tempfile.TemporaryDirectory(prefix="serenity-static-financial-") as temp_dir:
                    financial_path: Any = Path(temp_dir) / "financials.json"
                    financial_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                    result = validate_financials(financial_path)
                result_payload = {
                    "status": result.status.value,
                    "rating_cap": result.rating_cap.value,
                    "warnings": result.warnings,
                    "stats": result.stats,
                }
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "valuation_inputs_validation":
            payload = case.get("payload", {})
            try:
                with tempfile.TemporaryDirectory(prefix="serenity-static-valuation-") as temp_dir:
                    valuation_path: Any = Path(temp_dir) / "valuation_inputs.json"
                    valuation_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                    result = validate_valuation_inputs(valuation_path)
                result_payload = {
                    "status": result.status.value,
                    "warnings": result.warnings,
                    "errors": result.errors,
                    "stats": result.stats,
                }
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "data_gaps":
            result_items: Any = case.get("result_items", [])
            statuses: Any = case.get("statuses", {})
            requested: Any = case.get("requested_datasets", [])
            critical: Any = case.get("critical_datasets", [])
            if not isinstance(result_items, list) or not isinstance(statuses, dict):
                raise ValueError("data_gaps static eval requires result_items array and statuses object")
            if not isinstance(requested, list) or not isinstance(critical, list):
                raise ValueError("data_gaps static eval requires requested_datasets and critical_datasets arrays")
            try:
                data_gaps: Any = _build_data_gaps(
                    [item for item in result_items if isinstance(item, dict)],
                    {str(k): str(v) for k, v in statuses.items()},
                    requested_dataset_values=[str(item) for item in requested],
                    critical_datasets=[str(item) for item in critical],
                )
                research_debt: Any = _build_research_debt(data_gaps)
                result_payload = {
                    "data_gaps": data_gaps,
                    "research_debt": research_debt,
                    "gap_count": len(data_gaps),
                    "research_debt_count": len(research_debt),
                }
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "dashboard_from_output_json":
            contract_path = root / case["contract"]
            try:
                dashboard: Any = build_from_output_contract(json.loads(contract_path.read_text(encoding="utf-8")))
                monitors: Any = dashboard.get("monitors", [])
                result_payload = {
                    "ok": True,
                    "has_valuation_monitor": any(
                        isinstance(monitor, dict) and monitor.get("category") == "valuation"
                        for monitor in monitors
                    ),
                }
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "report_kind":
            titles: Any = case.get("titles", {})
            if not isinstance(titles, dict):
                raise ValueError("report_kind static eval requires titles object")
            result_payload = {
                str(title): EastmoneyF10FinancialsProvider._report_kind(str(title))
                for title in titles
            }
            mismatches: Any = {
                title: {"expected": expected, "actual": result_payload.get(title)}
                for title, expected in titles.items()
                if result_payload.get(title) != expected
            }
            actual_pass = not mismatches
            if mismatches:
                findings = [json.dumps(mismatches, ensure_ascii=False, sort_keys=True)]
        elif kind == "official_report_selection":
            reports: Any = case.get("reports", [])
            if not isinstance(reports, list):
                raise ValueError("official_report_selection static eval requires reports array")
            selected: Any = EastmoneyF10FinancialsProvider._select_reports_for_download(
                [report for report in reports if isinstance(report, dict)],
                int(case.get("limit", 2)),
            )
            result_payload = {
                "selected_report_kinds": [str(report.get("report_kind") or "") for report in selected],
                "selected_titles": [str(report.get("title") or "") for report in selected],
            }
            actual_pass = True
        elif kind == "periodic_report_title":
            titles = case.get("titles", {})
            if not isinstance(titles, dict):
                raise ValueError("periodic_report_title static eval requires titles object")
            issuer_name: Any = str(case.get("issuer_name") or "")
            result_payload = {
                str(title): CninfoFinancialReportsProvider._is_periodic_report_title(str(title), issuer_name=issuer_name)
                for title in titles
            }
            mismatches = {
                title: {"expected": expected, "actual": result_payload.get(title)}
                for title, expected in titles.items()
                if result_payload.get(title) != expected
            }
            actual_pass = not mismatches
            if mismatches:
                findings = [json.dumps(mismatches, ensure_ascii=False, sort_keys=True)]
        elif kind == "cn_statement_start":
            pages: Any = case.get("pages", [])
            if not isinstance(pages, list):
                raise ValueError("cn_statement_start static eval requires pages array")
            index: Any = CninfoFinancialReportsProvider._cn_statement_start_index(
                [page for page in pages if isinstance(page, dict)],
                str(case.get("title", "合并资产负债表")),
                [str(signal) for signal in case.get("signals", ["资产总计", "资产合计", "负债合计", "所有者权益", "股东权益"])],
            )
            page_list: Any = [page for page in pages if isinstance(page, dict)]
            result_payload = {
                "start_index": index,
                "start_page": page_list[index].get("page_number") if index is not None and index < len(page_list) else None,
            }
            actual_pass = index is not None
        elif kind == "cninfo_english_value_extraction":
            pages = [page for page in case.get("pages", []) if isinstance(page, dict)]
            if not pages:
                raise ValueError("cninfo_english_value_extraction static eval requires pages array")
            assets: Any
            _: Any
            assets, _ = CninfoFinancialReportsProvider._extract_cn_value(
                pages,
                [["Totalassets"]],
                exclude_groups=[["liabilities", "equity"]],
            )
            liabilities: Any
            liabilities, _ = CninfoFinancialReportsProvider._extract_cn_value(
                pages,
                [["Totalliabilities"]],
                exclude_groups=[["currentliabilities"], ["non-currentliabilities"], ["liabilities&equity"]],
            )
            equity: Any
            equity, _ = CninfoFinancialReportsProvider._extract_cn_value(
                pages,
                [["Totalequity"]],
                exclude_groups=[["attributable"], ["liabilities&equity"]],
            )
            parent_equity: Any
            parent_equity, _ = CninfoFinancialReportsProvider._extract_cn_value(
                pages,
                [["Totalequityattributabletotheparentcompany"]],
            )
            result_payload = {
                "assets": assets,
                "liabilities": liabilities,
                "equity": equity,
                "parent_equity": parent_equity,
            }
            actual_pass = all(value is not None for value in result_payload.values())
        elif kind in {"bank_profile_extraction", "financial_sector_profile_extraction"}:
            pages = case.get("pages", [])
            if not isinstance(pages, list):
                raise ValueError(f"{kind} static eval requires pages array")
            profile: Any = CninfoFinancialReportsProvider._extract_financial_sector_profile(
                [page for page in pages if isinstance(page, dict)],
                unit=str(case.get("unit", "million_yuan")),
            )
            result_payload = profile if isinstance(profile, dict) else {}
            actual_pass = bool(profile)
        elif kind == "candidate_rank":
            scorecards: Any = case.get("scorecards", [])
            if not isinstance(scorecards, list) or not scorecards:
                raise ValueError("candidate_rank static eval requires scorecards array")
            try:
                payloads: Any = [
                    json.loads((root / str(path)).read_text(encoding="utf-8"))
                    for path in scorecards
                ]
                result_payload = rank_candidates(payloads)
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "technical_health":
            series: Any = case.get("series", {})
            if not isinstance(series, dict):
                raise ValueError("technical_health static eval requires series object")
            start: Any = float(series.get("start", 100.0))
            step: Any = float(series.get("step", 0.0))
            count: Any = int(series.get("count", 80))
            rows: Any = []
            for idx in range(count):
                close: Any = start + idx * step
                month: Any = 1 + idx // 28
                day: Any = 1 + idx % 28
                rows.append({
                    "date": f"2026-{month:02d}-{day:02d}",
                    "close": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "volume": 1000000 + idx,
                })
            result_payload = analyze_price_rows(rows)
            actual_pass = bool(result_payload.get("buy_point_claim_allowed")) is False
        elif kind == "financial_period_selection":
            rows = case.get("rows", [])
            if not isinstance(rows, list):
                raise ValueError("financial_period_selection static eval requires rows array")
            period_rows: Any = [row for row in rows if isinstance(row, dict)]
            annual: Any = latest_annual(period_rows, market=str(case.get("market", "")))
            q1: Any = latest_quarter(period_rows, "q1", market=str(case.get("market", "")))
            annual_meta: Any = normalize_financial_period(annual or {}, market=str(case.get("market", "")))
            q1_meta: Any = normalize_financial_period(q1 or {}, market=str(case.get("market", "")))
            result_payload = {
                "latest_annual_period": annual.get("period") if isinstance(annual, dict) else None,
                "latest_annual_selection_rule": annual_meta.get("selection_rule"),
                "latest_annual_period_type": annual_meta.get("period_type"),
                "latest_q1_period": q1.get("period") if isinstance(q1, dict) else None,
                "latest_q1_period_type": q1_meta.get("period_type"),
            }
            actual_pass = annual is not None
        elif kind == "capital_actions":
            announcements: Any = case.get("announcements", [])
            if not isinstance(announcements, list):
                raise ValueError("capital_actions static eval requires announcements array")
            result_payload = analyze_announcements({"recent_announcements": announcements})
            actual_pass = True
        elif kind == "capital_action_quantification":
            capital_actions: Any = case.get("capital_actions", {})
            if not isinstance(capital_actions, dict):
                raise ValueError("capital_action_quantification static eval requires capital_actions object")
            result_payload = quantify_capital_actions(str(case.get("symbol") or "TEST"), capital_actions)
            actual_pass = True
        elif kind == "data_consumption_ranking_validity":
            financial_payload: Any = case.get("financial_payload", {})
            financial_row: Any = case.get("financial_row", {})
            if not isinstance(financial_row, dict):
                raise ValueError("data_consumption_ranking_validity requires financial_row object")
            consumption: Any = financial_consumption_audit(
                symbol=str(case.get("symbol") or "TEST"),
                raw_status=str(case.get("raw_status") or "NOT_REQUESTED"),
                financial_payload=financial_payload,
                financial_row=financial_row,
            )
            result_payload = {
                "consumption": consumption,
                "ranking_validity": ranking_validity_from_consumption([consumption]),
            }
            actual_pass = True
        elif kind == "valuation_consumption_audit":
            valuation_payload: Any = case.get("valuation_payload", {})
            valuation_row: Any = case.get("valuation_row", {})
            growth_row: Any = case.get("growth_row", {})
            if not isinstance(valuation_row, dict) or not isinstance(growth_row, dict):
                raise ValueError("valuation_consumption_audit requires valuation_row and growth_row objects")
            result_payload = valuation_consumption_audit(
                symbol=str(case.get("symbol") or "TEST"),
                raw_status=str(case.get("raw_status") or "NOT_REQUESTED"),
                valuation_payload=valuation_payload,
                valuation_row=valuation_row,
                growth_row=growth_row,
            )
            actual_pass = True
        elif kind == "comparison_report":
            manifests = case.get("manifests", [])
            if not isinstance(manifests, list) or len(manifests) < 2:
                raise ValueError("comparison_report static eval requires at least two manifests")
            try:
                manifest_paths = [root / str(path) for path in manifests]
                if case.get("clear_manifest_research_debt"):
                    with tempfile.TemporaryDirectory(prefix="serenity-static-comparison-") as temp_dir:
                        temp_paths: Any = []
                        for manifest_path in manifest_paths:
                            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                            acquisition: Any = payload.get("data_acquisition") if isinstance(payload.get("data_acquisition"), dict) else {}
                            acquisition["research_debt"] = []
                            acquisition["manual_retrieval_tasks"] = []
                            acquisition["research_debt_count"] = 0
                            acquisition["manual_task_count"] = 0
                            payload["data_acquisition"] = acquisition
                            temp_path: Any = Path(temp_dir) / manifest_path.name
                            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                            temp_paths.append(temp_path)
                        result_payload = build_comparison_report(temp_paths)
                else:
                    result_payload = build_comparison_report(manifest_paths)
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "comparison_report_file_validation":
            manifests = case.get("manifests", [])
            if not isinstance(manifests, list) or len(manifests) < 2:
                raise ValueError("comparison_report_file_validation static eval requires at least two manifests")
            try:
                manifest_paths: list[Path] = [root / str(path) for path in manifests]
                report: dict[str, Any] = build_comparison_report(manifest_paths)
                with tempfile.TemporaryDirectory(prefix="serenity-static-comparison-validator-") as temp_dir:
                    report_path: Path = Path(temp_dir) / "comparison_report.json"
                    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                    errors: list[str] = validate_comparison_report_file(report_path)
                result_payload = {
                    "ok": not errors,
                    "error_count": len(errors),
                }
                actual_pass = not errors
                findings = errors
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "render_report_mode":
            manifests: Any = case.get("manifests", [])
            mode: str = str(case.get("mode") or "candidate_comparison")
            if not isinstance(manifests, list) or len(manifests) < 2:
                raise ValueError("render_report_mode static eval requires at least two manifests")
            try:
                manifest_paths: list[Path] = [root / str(path) for path in manifests]
                markdown: str = render_report(manifests=manifest_paths, mode=mode)
                comparison_markdown: str = render_report(manifests=manifest_paths, mode="candidate_comparison")
                result_payload = {
                    "mode": mode,
                    "line_count": len(markdown.splitlines()),
                    "candidate_line_count": len(comparison_markdown.splitlines()),
                    "differs_from_candidate_comparison": markdown != comparison_markdown,
                    "has_full_research_workbench": "# 完整研究工作台" in markdown,
                    "has_candidate_sections": "## 688019.SH" in markdown and "## 688322.SH" in markdown,
                    "has_dataset_statuses": "财报 `正常（OK）`" in markdown and "公告 `正常（OK）`" in markdown,
                    "has_gate_reasons": "门控原因：" in markdown and "门控原因：无" not in markdown,
                    "has_gate_classes": "证据门控（EVIDENCE_GATED）=证据验证（EVIDENCE_VALIDATION）" in markdown,
                }
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "action_gate_profile":
            debt_rows: Any = case.get("research_debt", [])
            if not isinstance(debt_rows, list):
                raise ValueError("action_gate_profile static eval requires research_debt array")
            try:
                result_payload = _action_gate_profile(
                    case.get("technical", {}) if isinstance(case.get("technical", {}), dict) else {},
                    case.get("capital", {}) if isinstance(case.get("capital", {}), dict) else {},
                    case.get("layer", {}) if isinstance(case.get("layer", {}), dict) else {},
                    case.get("growth", {}) if isinstance(case.get("growth", {}), dict) else {},
                    _debt_gate_profile([row for row in debt_rows if isinstance(row, dict)]),
                )
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "comparison_report_validation":
            manifests = case.get("manifests", [])
            mutations: Any = case.get("mutations", [])
            if not isinstance(manifests, list) or len(manifests) < 2:
                raise ValueError("comparison_report_validation static eval requires at least two manifests")
            if not isinstance(mutations, list):
                raise ValueError("comparison_report_validation static eval requires mutations array")
            report: Any = build_comparison_report([root / str(path) for path in manifests])
            for mutation in mutations:
                if not isinstance(mutation, dict) or "path" not in mutation:
                    raise ValueError("comparison_report_validation mutation requires path")
                _set_path(report, str(mutation["path"]), mutation.get("value"))
            errors: Any = validate_comparison_report(report)
            result_payload = {"ok": not errors, "errors": errors}
            actual_pass = not errors
            if errors:
                findings = errors
        elif kind == "comparison_report_with_overlays":
            manifests = case.get("manifests", [])
            overlays: Any = case.get("overlays", {})
            if not isinstance(manifests, list) or len(manifests) < 2:
                raise ValueError("comparison_report_with_overlays static eval requires at least two manifests")
            if not isinstance(overlays, dict):
                raise ValueError("comparison_report_with_overlays static eval requires overlays object")
            try:
                result_payload = build_comparison_report(
                    [root / str(path) for path in manifests],
                    {str(symbol): overlay for symbol, overlay in overlays.items() if isinstance(overlay, dict)},
                )
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "comparison_report_with_ai_outcomes":
            manifests = case.get("manifests", [])
            outcomes: Any = case.get("ai_review_outcomes", {})
            if not isinstance(manifests, list) or len(manifests) < 2:
                raise ValueError("comparison_report_with_ai_outcomes static eval requires at least two manifests")
            if not isinstance(outcomes, dict):
                raise ValueError("comparison_report_with_ai_outcomes static eval requires ai_review_outcomes object")
            try:
                result_payload = build_comparison_report(
                    [root / str(path) for path in manifests],
                    ai_review_outcomes={str(symbol): outcome for symbol, outcome in outcomes.items() if isinstance(outcome, dict)},
                )
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "validated_ai_merge":
            manifests = case.get("manifests", [])
            overlays: Any = case.get("overlay_values", [])
            outcomes: Any = case.get("outcome_values", [])
            if not isinstance(manifests, list) or len(manifests) < 2:
                raise ValueError("validated_ai_merge static eval requires at least two manifests")
            if not isinstance(overlays, list) or not isinstance(outcomes, list):
                raise ValueError("validated_ai_merge static eval requires overlay_values and outcome_values arrays")
            try:
                result_payload = build_validated_merged_report(
                    [root / str(path) for path in manifests],
                    [str(item) for item in overlays],
                    [str(item) for item in outcomes],
                )
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "ai_overlay_validation":
            payload = case.get("payload", {})
            if not isinstance(payload, dict):
                raise ValueError("ai_overlay_validation static eval requires payload object")
            try:
                result_payload = validate_overlay(payload)
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "ai_overlay_validation_with_context":
            payload = case.get("payload", {})
            evidence_context = case.get("evidence_context", {})
            if not isinstance(payload, dict) or not isinstance(evidence_context, dict):
                raise ValueError("ai_overlay_validation_with_context static eval requires payload and evidence_context objects")
            try:
                result_payload = validate_overlay(
                    payload,
                    evidence_context={str(key): str(value) for key, value in evidence_context.items()},
                )
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "ai_review_outcome_validation":
            payload = case.get("payload", {})
            if not isinstance(payload, dict):
                raise ValueError("ai_review_outcome_validation static eval requires payload object")
            try:
                result_payload = validate_review_outcome(payload)
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "ai_review_packet":
            manifest = case.get("manifest")
            if not isinstance(manifest, str):
                raise ValueError("ai_review_packet static eval requires manifest path")
            try:
                result_payload = build_ai_review_packet(root / manifest)
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "ai_overlay_prompt":
            manifest = case.get("manifest")
            if not isinstance(manifest, str):
                raise ValueError("ai_overlay_prompt static eval requires manifest path")
            try:
                result_payload = build_ai_overlay_prompt(root / manifest)
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "ai_committee_packet":
            manifest: Any = case.get("manifest")
            if not isinstance(manifest, str):
                raise ValueError("ai_committee_packet static eval requires manifest path")
            try:
                packet: dict[str, Any] = build_ai_committee_packet(root / manifest)
                contract: Mapping[str, Any] = packet.get("overlay_output_contract") if isinstance(packet.get("overlay_output_contract"), Mapping) else {}
                allowed_fields: set[str] = {str(item) for item in contract.get("allowed_fields", []) if str(item)}
                required_outputs: set[str] = {str(item) for item in packet.get("required_overlay_outputs", []) if str(item)}
                committee_outputs: set[str] = {str(item) for item in packet.get("committee_review_outputs", []) if str(item)}
                result_payload = {
                    **packet,
                    "overlay_contract_ok": bool(required_outputs) and required_outputs <= allowed_fields,
                    "committee_outputs_are_separate": bool(committee_outputs) and not bool(committee_outputs & allowed_fields),
                }
                actual_pass = bool(result_payload["overlay_contract_ok"] and result_payload["committee_outputs_are_separate"])
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "ai_overlay_merge":
            manifests = case.get("manifests", [])
            overlays = case.get("overlays", {})
            if not isinstance(manifests, list) or len(manifests) < 2:
                raise ValueError("ai_overlay_merge static eval requires at least two manifests")
            if not isinstance(overlays, dict):
                raise ValueError("ai_overlay_merge static eval requires overlays object")
            try:
                result_payload = build_comparison_report(
                    [root / str(path) for path in manifests],
                    {str(symbol): overlay for symbol, overlay in overlays.items() if isinstance(overlay, dict)},
                )
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "research_debt_runbook":
            manifests = case.get("manifests", [])
            overlays = case.get("overlays", {})
            if not isinstance(manifests, list) or len(manifests) < 2:
                raise ValueError("research_debt_runbook static eval requires at least two manifests")
            if not isinstance(overlays, dict):
                raise ValueError("research_debt_runbook static eval requires overlays object when supplied")
            try:
                report_payload: dict[str, Any] = build_comparison_report(
                    [root / str(path) for path in manifests],
                    {str(symbol): overlay for symbol, overlay in overlays.items() if isinstance(overlay, dict)},
                )
                runbook: list[dict[str, Any]] = build_runbook_rows(report_payload)
                result_payload = {
                    "runbook": runbook,
                    "runbook_count": len(runbook),
                    "datasets": [str(item.get("dataset") or "") for item in runbook],
                }
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "financial_amount_normalization":
            amount: Any = normalize_financial_amount(case.get("value"), case.get("unit"))
            multiplier: float = financial_unit_multiplier(case.get("unit"))
            result_payload = {
                "amount": amount,
                "multiplier": multiplier,
            }
            actual_pass = amount is not None
        elif kind == "financial_currency_resolution":
            financial_payload: Any = case.get("financial", {})
            latest_annual_payload: Any = case.get("latest_annual", {})
            if not isinstance(financial_payload, dict) or not isinstance(latest_annual_payload, dict):
                raise ValueError("financial_currency_resolution static eval requires financial and latest_annual objects")
            result_payload = {
                "financial_currency": _financial_currency(financial_payload, latest_annual_payload),
            }
            actual_pass = True
        elif kind == "currency_normalization":
            valuation_payload: Any = case.get("valuation", {})
            financial_payload: Any = case.get("financial", {})
            if not isinstance(valuation_payload, dict) or not isinstance(financial_payload, dict):
                raise ValueError("currency_normalization static eval requires valuation and financial objects")
            result_payload = normalize_valuation_payload(
                symbol=str(case.get("symbol") or valuation_payload.get("symbol") or ""),
                valuation_payload=valuation_payload,
                financial_payload=financial_payload,
                allow_network=bool(case.get("allow_network", False)),
            )
            actual_pass = True
        elif kind == "growth_hypothesis_amount_basis":
            financial: Any = case.get("financial", {})
            valuation: Any = case.get("valuation", {})
            profile: Any = case.get("profile", {})
            if not isinstance(financial, dict) or not isinstance(valuation, dict) or not isinstance(profile, dict):
                raise ValueError("growth_hypothesis_amount_basis requires financial, valuation, and profile objects")
            with tempfile.TemporaryDirectory(prefix="serenity-static-growth-unit-") as temp_dir:
                temp_root: Path = Path(temp_dir)
                valuation_path: Path = temp_root / "valuation_inputs.json"
                valuation_path.write_text(json.dumps(valuation, ensure_ascii=False), encoding="utf-8")
                manifest: dict[str, Any] = {
                    "_manifest_path": str((temp_root / "manifest.json").resolve()),
                    "symbol": {
                        "symbol": str(case.get("symbol") or valuation.get("symbol") or "0700.HK"),
                        "market": str(case.get("market") or "HK"),
                        "currency": str(valuation.get("currency") or "HKD"),
                    },
                    "data_acquisition": {
                        "status_by_dataset": {
                            "valuation_inputs": "OK",
                        },
                    },
                    "results": [
                        {
                            "dataset": "valuation_inputs",
                            "status": "OK",
                            "source": str(valuation.get("source") or "Static_Valuation_L0L2"),
                            "source_level": str(valuation.get("source_level") or "L0L2_STATIC"),
                            "as_of_date": str(valuation.get("as_of_date") or ""),
                            "data_path": str(valuation_path),
                        }
                    ],
                }
                result_payload = _growth_hypothesis(manifest, financial, profile)
            actual_pass = True
        elif kind == "provider_chain":
            symbol: Any = SymbolInfo(
                input_value=str(case.get("symbol", "NVDA")),
                symbol=str(case.get("symbol", "NVDA")),
                market=Market(str(case.get("market", "US"))),
                exchange=str(case.get("exchange", "US")),
                currency=str(case.get("currency", "USD")),
            )
            provider_names: Any = [provider.name for provider in default_real_providers(symbol)]
            result_payload = {
                "providers": provider_names,
                "has_required_providers": all(
                    str(name) in provider_names for name in case.get("required_providers", [])
                ),
            }
            actual_pass = bool(result_payload["has_required_providers"])
            if not actual_pass:
                findings = [f"provider chain missing required providers; actual={provider_names}"]
        elif kind == "hk_issued_shares_extraction":
            extracted: Any = data_layer_module.HkexValuationInputsProvider._extract_issued_shares_from_text(str(case.get("text") or ""))
            result_payload = extracted or {}
            actual_pass = extracted is not None
        elif kind == "hk_financial_summary_extraction":
            pages: Any = case.get("pages", [])
            if not isinstance(pages, list):
                raise ValueError("hk_financial_summary_extraction static eval requires pages array")
            fields: Dict[str, float]
            evidence: Dict[str, Dict[str, Any]]
            period: Optional[str]
            fields, evidence, period = HkexFinancialReportsProvider._extract_hkex_financial_summary_fields(
                [page for page in pages if isinstance(page, dict)]
            )
            result_payload = {
                "period": period,
                "fields": fields,
                "evidence_keys": sorted(evidence),
            }
            actual_pass = bool(fields)
        elif kind == "hk_valuation_quote_fallback":
            symbol = SymbolInfo(
                input_value=str(case.get("symbol", "0700.HK")),
                symbol=str(case.get("symbol", "0700.HK")),
                market=Market.HK,
                exchange="HKEX",
                currency="HKD",
            )
            provider: Any = data_layer_module.HkexValuationInputsProvider()
            provider._lookup_listing = lambda code: {"stock_id": "700", "stock_code": "00700", "stock_name": "Tencent"}  # type: ignore[method-assign]
            provider._latest_share_count_reports = lambda stock_id: []  # type: ignore[method-assign]
            provider._latest_issued_shares_from_reports = lambda reports, **kwargs: None  # type: ignore[method-assign]
            original_yahoo_provider: Any = data_layer_module.YahooChartProvider
            use_cached_quote: Any = bool(case.get("use_cached_quote"))

            class StaticYahooProvider:
                name: Any = "Yahoo_Static_L2"
                level: Any = data_layer_module.SourceLevel.L2

                def __init__(self, *args: Any, **kwargs: Any) -> None:
                    self.name = str(kwargs.get("name") or self.name)

                def fetch(self, symbol: Any, dataset: Any, **kwargs: Any) -> Any:
                    if use_cached_quote:
                        raise RuntimeError("cached quote should be consumed without refetching Yahoo")
                    return data_layer_module.DataResult(
                        True,
                        dataset,
                        symbol.symbol,
                        self.name,
                        self.level,
                        data_layer_module.utc_now(),
                        as_of_date="2026-06-23",
                        data={
                            "symbol": symbol.symbol,
                            "name": "Tencent",
                            "currency": "HKD",
                            "exchange": "HKEX",
                            "regular_market_price": 100.0,
                            "regular_market_time": 1782144000,
                            "market_cap": 1000.0,
                        },
                        currency="HKD",
                    )

            try:
                data_layer_module.YahooChartProvider = StaticYahooProvider  # type: ignore[assignment]
                provider_kwargs: dict[str, Any] = {"raw_dir": None}
                if use_cached_quote:
                    provider_kwargs["current_quote_result"] = {
                        "data": {
                            "symbol": symbol.symbol,
                            "name": "Tencent",
                            "currency": "HKD",
                            "exchange": "HKEX",
                            "regular_market_price": 100.0,
                            "regular_market_time": 1782144000,
                            "market_cap": 1000.0,
                        },
                        "as_of_date": "2026-06-23",
                        "source_name": "Yahoo_Cached_L2",
                        "source_level": data_layer_module.SourceLevel.L2.value,
                        "currency": "HKD",
                    }
                result = provider.fetch(symbol, data_layer_module.Dataset.VALUATION_INPUTS, **provider_kwargs)
            finally:
                data_layer_module.YahooChartProvider = original_yahoo_provider  # type: ignore[assignment]
            result_payload = result.data if result.ok and isinstance(result.data, dict) else {"errors": result.errors}
            actual_pass = bool(result.ok)
        elif kind == "hk_announcements_targeted_fallback":
            symbol = SymbolInfo(
                input_value=str(case.get("symbol", "0700.HK")),
                symbol=str(case.get("symbol", "0700.HK")),
                market=Market.HK,
                exchange="HKEX",
                currency="HKD",
            )
            provider = data_layer_module.HkexAnnouncementsProvider()
            provider._lookup_listing = lambda code: {"stock_id": "700", "stock_code": "00700", "stock_name": "Tencent"}  # type: ignore[method-assign]

            def fake_query(*, stock_id: str, from_date: str, to_date: str, title: str = "", row_range: int = 100) -> dict[str, Any]:
                if not title:
                    raise RuntimeError("broad search unavailable")
                if title != "Annual Report":
                    return {"recordCnt": 0, "result": "[]"}
                return {
                    "recordCnt": 1,
                    "result": json.dumps([
                        {
                            "NEWS_ID": "static-hkex-annual",
                            "DATE_TIME": "26/08/2025 16:30",
                            "STOCK_CODE": "00700",
                            "STOCK_NAME": "TENCENT",
                            "TITLE": "ANNUAL REPORT 2025",
                            "LONG_TEXT": "Financial Statements/ESG Information - Annual Report",
                            "FILE_TYPE": "PDF",
                            "FILE_INFO": "PDF",
                            "FILE_LINK": "/listedco/listconews/sehk/2025/0826/static.pdf",
                        }
                    ]),
                }

            provider._query_title_search = fake_query  # type: ignore[method-assign]
            result = provider.fetch(symbol, data_layer_module.Dataset.FILINGS, raw_dir=None)
            result_payload = result.data if result.ok and isinstance(result.data, dict) else {"errors": result.errors}
            actual_pass = bool(result.ok)
        elif kind == "hk_report_download_selection":
            reports = [dict(item) for item in case.get("reports", []) if isinstance(item, dict)]
            selected = data_layer_module.HkexFinancialReportsProvider._select_reports_for_download(
                reports,
                int(case.get("limit", 1)),
            )
            result_payload = {
                "selected_count": len(selected),
                "selected_kinds": [str(item.get("report_kind") or "") for item in selected],
                "selected_titles": [str(item.get("title") or "") for item in selected],
            }
            actual_pass = bool(selected)
        elif kind == "provider_timeout_attempt":
            class SlowProvider:
                name: Any = "Static_Slow_Provider_L2"
                level: Any = data_layer_module.SourceLevel.L2
                markets: Any = [Market.US]
                datasets: Any = [data_layer_module.Dataset.CURRENT_QUOTE]

                def fetch(self, symbol: Any, dataset: Any, **kwargs: Any) -> Any:
                    time.sleep(float(case.get("sleep_seconds", 2)))
                    return data_layer_module.DataResult(
                        True,
                        dataset,
                        symbol.symbol,
                        self.name,
                        self.level,
                        data_layer_module.utc_now(),
                        data={"regular_market_price": 1.0},
                        currency=symbol.currency,
                    )

            symbol = SymbolInfo(
                input_value=str(case.get("symbol", "NVDA")),
                symbol=str(case.get("symbol", "NVDA")),
                market=Market.US,
                exchange="US",
                currency="USD",
            )
            attempts: Any
            result, attempts = _fetch_with_attempt_ledger(
                [SlowProvider()],
                symbol,
                data_layer_module.Dataset.CURRENT_QUOTE,
                provider_timeout_seconds=int(case.get("provider_timeout_seconds", 1)),
            )
            result_payload = {
                "result_ok": result.ok,
                "attempts": attempts,
                "first_gap_type": _get_path(attempts, "0.gap_type"),
                "first_reason": _get_path(attempts, "0.reason"),
            }
            actual_pass = (
                not result.ok
                and result_payload["first_gap_type"] == "ACCESS_FAILURE"
                and "exceeded" in str(result_payload["first_reason"]).lower()
            )
        elif kind == "sec_identity_match":
            symbol = SymbolInfo(
                input_value=str(case.get("symbol", "NVDA")),
                symbol=str(case.get("symbol", "NVDA")),
                market=Market.US,
                exchange="US",
                currency="USD",
            )
            result_payload = {
                "matches": _sec_submission_matches_symbol(symbol, {
                    "tickers": case.get("submission_tickers", []),
                }),
            }
            actual_pass = bool(result_payload["matches"]) == bool(case.get("expected_match"))
            if not actual_pass:
                findings = [f"SEC identity match result was {result_payload['matches']}"]
        elif kind == "sec_cik_candidate_recovery":
            original_bootstrap: Any = data_layer_module._sec_cik_from_bootstrap
            original_exchange: Any = data_layer_module._sec_cik_from_ticker_exchange_json
            original_company: Any = data_layer_module._sec_cik_from_company_tickers_json
            original_txt: Any = data_layer_module._sec_cik_from_ticker_txt
            original_submissions: Any = data_layer_module._fetch_sec_submissions_payload
            submission_tickers: Any = {
                f"{int(str(cik)):010d}": tickers
                for cik, tickers in dict(case.get("submission_tickers_by_cik", {})).items()
            }
            try:
                data_layer_module._sec_cik_from_bootstrap = lambda ticker: case.get("bootstrap_cik")
                data_layer_module._sec_cik_from_ticker_exchange_json = lambda ticker, *, user_agent: case.get("directory_cik")
                data_layer_module._sec_cik_from_company_tickers_json = lambda ticker, *, user_agent: None
                data_layer_module._sec_cik_from_ticker_txt = lambda ticker, *, user_agent: None

                def fake_submissions(cik: str, *, user_agent: str) -> dict[str, Any]:
                    return {"tickers": submission_tickers.get(f"{int(str(cik)):010d}", [])}

                data_layer_module._fetch_sec_submissions_payload = fake_submissions
                resolved: Any = data_layer_module._sec_cik_from_ticker(str(case.get("symbol", "NVDA")), user_agent="static-eval")
            finally:
                data_layer_module._sec_cik_from_bootstrap = original_bootstrap
                data_layer_module._sec_cik_from_ticker_exchange_json = original_exchange
                data_layer_module._sec_cik_from_company_tickers_json = original_company
                data_layer_module._sec_cik_from_ticker_txt = original_txt
                data_layer_module._fetch_sec_submissions_payload = original_submissions
            result_payload = {"resolved_cik": resolved}
            actual_pass = resolved == case.get("expected_cik")
            if not actual_pass:
                findings = [f"resolved CIK {resolved!r}, expected {case.get('expected_cik')!r}"]
        elif kind == "sec_shares_outstanding":
            companyfacts: Any = case.get("companyfacts", {})
            if not isinstance(companyfacts, dict):
                raise ValueError("sec_shares_outstanding static eval requires companyfacts object")
            share_fact: Any = data_layer_module._latest_sec_shares_outstanding(companyfacts)
            result_payload = share_fact if isinstance(share_fact, dict) else {}
            actual_pass = bool(share_fact)
        elif kind == "sec_financial_period_rows":
            companyfacts = case.get("companyfacts", {})
            if not isinstance(companyfacts, dict):
                raise ValueError("sec_financial_period_rows static eval requires companyfacts object")
            rows = data_layer_module._period_rows_from_sec_facts(companyfacts)
            result_payload = {
                "row_count": len(rows),
                "latest_row": rows[-1] if rows else {},
            }
            actual_pass = bool(rows)
        elif kind == "sec_ads_ratio_text":
            extracted = data_layer_module._extract_ads_ratio_from_text(str(case.get("text") or ""))
            result_payload = extracted if isinstance(extracted, dict) else {}
            actual_pass = bool(extracted)
        elif kind == "sec_ads_ratio_required":
            symbol = SymbolInfo(
                input_value=str(case.get("symbol", "ASML")),
                symbol=str(case.get("symbol", "ASML")),
                market=Market.US,
                exchange="US",
                currency="USD",
            )
            result_payload = {
                "required": data_layer_module._ads_ratio_required(symbol, {
                    "filings": {"recent": {"form": case.get("forms", [])}},
                    "tickers": case.get("submission_tickers", []),
                }),
            }
            actual_pass = bool(result_payload["required"]) == bool(case.get("expected_required"))
            if not actual_pass:
                findings = [f"ADS ratio required result was {result_payload['required']}"]
        elif kind == "tencent_valuation_fields":
            alias: Any = str(case.get("alias", "sh688322"))
            fields: Any = [""] * int(case.get("field_count", 88))
            for key, value in (case.get("fields", {}) if isinstance(case.get("fields"), dict) else {}).items():
                fields[int(key)] = str(value)
            provider = data_layer_module.TencentQuoteKlineProvider()
            payload = (f'v_{alias}="' + "~".join(fields) + '";').encode("gb18030")
            provider._read_bytes = lambda url: payload  # type: ignore[method-assign]
            symbol = SymbolInfo(
                input_value=str(case.get("symbol", "688322")),
                symbol=str(case.get("normalized_symbol", "688322.SH")),
                market=Market.CN_A,
                exchange="SSE",
                currency="CNY",
            )
            result = provider._fetch_valuation_inputs(symbol, data_layer_module.Dataset.VALUATION_INPUTS, alias, raw_dir=None)
            result_payload = result.data if result.ok and isinstance(result.data, dict) else {"errors": result.errors}
            actual_pass = bool(result.ok)
        else:
            raise ValueError(f"unknown static eval kind: {kind}")

        expected_result: Any = case.get("expected_result", {})
        expected_contains: Any = case.get("expected_contains", [])
        result_matches: Any = all(_get_path(result_payload, k) == v for k, v in expected_result.items())
        contains_matches: Any = True
        if actual_pass and expected_contains:
            if not isinstance(expected_contains, list):
                raise ValueError("expected_contains must be an array")
            contains_matches = all(
                isinstance(item, dict)
                and isinstance(item.get("value"), dict)
                and _contains_mapping(result_payload, str(item.get("path")), item["value"])
                for item in expected_contains
            )
        passed: Any = actual_pass == expect_pass and (not actual_pass or (result_matches and contains_matches))
        marker: Any = "PASS" if passed else "FAIL"
        print(f"[{marker}] {name}: expected {'pass' if expect_pass else 'fail'}, got {'pass' if actual_pass else 'fail'}")
        if not passed:
            failures += 1
            if expected_result and actual_pass and not result_matches:
                print(f"  - expected result fields: {expected_result}")
                print(f"  - actual result fields: {result_payload}")
            if expected_contains and actual_pass and not contains_matches:
                print(f"  - expected contained fields: {expected_contains}")
                print(f"  - actual result fields: {result_payload}")
            for finding in findings:
                print(f"  - {finding}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
