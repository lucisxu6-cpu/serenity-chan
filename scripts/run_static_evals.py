#!/usr/bin/env python3
"""Run static contract evals for serenity-chan-stock-skill."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional, Sequence

try:
    import data_layer as data_layer_module
    from validate_output_contract import validate_text
    from validate_output_contract_json import validate_contract
    from data_router import validate_financials, validate_valuation_inputs, _build_data_gaps, _build_research_debt
    from build_falsification_dashboard import build_from_output_contract
    from a_share_capital_actions import analyze_announcements
    from build_comparison_report import build_comparison_report, validate_comparison_report, _action_gate_profile, _debt_gate_profile
    from build_ai_review_packet import build_ai_review_packet
    from candidate_ranker import rank_candidates
    from serenity_chan_scorecard import score
    from technical_health import analyze_price_rows
    from validate_ai_overlay import validate_overlay
    from data_layer import CninfoFinancialReportsProvider, EastmoneyF10FinancialsProvider, Market, SymbolInfo, default_real_providers, _sec_submission_matches_symbol
except ModuleNotFoundError:  # pragma: no cover - supports python -m scripts.run_static_evals
    from scripts import data_layer as data_layer_module
    from scripts.validate_output_contract import validate_text
    from scripts.validate_output_contract_json import validate_contract
    from scripts.data_router import validate_financials, validate_valuation_inputs, _build_data_gaps, _build_research_debt
    from scripts.build_falsification_dashboard import build_from_output_contract
    from scripts.a_share_capital_actions import analyze_announcements
    from scripts.build_comparison_report import build_comparison_report, validate_comparison_report, _action_gate_profile, _debt_gate_profile
    from scripts.build_ai_review_packet import build_ai_review_packet
    from scripts.candidate_ranker import rank_candidates
    from scripts.serenity_chan_scorecard import score
    from scripts.technical_health import analyze_price_rows
    from scripts.validate_ai_overlay import validate_overlay
    from scripts.data_layer import CninfoFinancialReportsProvider, EastmoneyF10FinancialsProvider, Market, SymbolInfo, default_real_providers, _sec_submission_matches_symbol


def _get_path(payload: Any, path: str) -> Any:
    current = payload
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
    current = payload
    parts = path.split(".")
    for part in parts[:-1]:
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise ValueError(f"cannot traverse mutation path: {path}")
    last = parts[-1]
    if isinstance(current, list):
        current[int(last)] = value
    elif isinstance(current, dict):
        current[last] = value
    else:
        raise ValueError(f"cannot set mutation path: {path}")


def _contains_mapping(payload: Any, path: str, expected: dict[str, Any]) -> bool:
    value = _get_path(payload, path)
    if not isinstance(value, list):
        return False
    for item in value:
        if isinstance(item, dict) and all(item.get(k) == v for k, v in expected.items()):
            return True
    return False


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run static Serenity + Chan eval cases")
    parser.add_argument("--cases", default="evals/static_cases.json", help="JSON case file")
    args = parser.parse_args(argv)

    root = Path.cwd()
    cases_path = root / args.cases
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    failures = 0

    for case in cases:
        name = case["name"]
        expect_pass = bool(case["expect_pass"])
        kind = case.get("kind", "report")
        findings: list[str] = []
        result_payload: dict[str, Any] = {}

        if kind == "report":
            report_path = root / case["report"]
            result = validate_text(report_path.read_text(encoding="utf-8"))
            actual_pass = result.ok
            findings = [f"{f.severity.upper()} {f.code}: {f.message}" for f in result.findings]
            result_payload = result.extracted
        elif kind == "scorecard":
            scorecard_path = root / case["scorecard"]
            try:
                result_payload = score(json.loads(scorecard_path.read_text(encoding="utf-8")))
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "output_json":
            contract_path = root / case["contract"]
            try:
                result_payload = validate_contract(json.loads(contract_path.read_text(encoding="utf-8")))
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "financial_validation":
            payload = case.get("payload", {})
            try:
                with tempfile.TemporaryDirectory(prefix="serenity-static-financial-") as temp_dir:
                    financial_path = Path(temp_dir) / "financials.json"
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
                    valuation_path = Path(temp_dir) / "valuation_inputs.json"
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
            result_items = case.get("result_items", [])
            statuses = case.get("statuses", {})
            requested = case.get("requested_datasets", [])
            critical = case.get("critical_datasets", [])
            if not isinstance(result_items, list) or not isinstance(statuses, dict):
                raise ValueError("data_gaps static eval requires result_items array and statuses object")
            if not isinstance(requested, list) or not isinstance(critical, list):
                raise ValueError("data_gaps static eval requires requested_datasets and critical_datasets arrays")
            try:
                data_gaps = _build_data_gaps(
                    [item for item in result_items if isinstance(item, dict)],
                    {str(k): str(v) for k, v in statuses.items()},
                    requested_dataset_values=[str(item) for item in requested],
                    critical_datasets=[str(item) for item in critical],
                )
                research_debt = _build_research_debt(data_gaps)
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
                dashboard = build_from_output_contract(json.loads(contract_path.read_text(encoding="utf-8")))
                monitors = dashboard.get("monitors", [])
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
            titles = case.get("titles", {})
            if not isinstance(titles, dict):
                raise ValueError("report_kind static eval requires titles object")
            result_payload = {
                str(title): EastmoneyF10FinancialsProvider._report_kind(str(title))
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
        elif kind == "official_report_selection":
            reports = case.get("reports", [])
            if not isinstance(reports, list):
                raise ValueError("official_report_selection static eval requires reports array")
            selected = EastmoneyF10FinancialsProvider._select_reports_for_download(
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
            issuer_name = str(case.get("issuer_name") or "")
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
            pages = case.get("pages", [])
            if not isinstance(pages, list):
                raise ValueError("cn_statement_start static eval requires pages array")
            index = CninfoFinancialReportsProvider._cn_statement_start_index(
                [page for page in pages if isinstance(page, dict)],
                str(case.get("title", "合并资产负债表")),
                [str(signal) for signal in case.get("signals", ["资产总计", "资产合计", "负债合计", "所有者权益", "股东权益"])],
            )
            page_list = [page for page in pages if isinstance(page, dict)]
            result_payload = {
                "start_index": index,
                "start_page": page_list[index].get("page_number") if index is not None and index < len(page_list) else None,
            }
            actual_pass = index is not None
        elif kind in {"bank_profile_extraction", "financial_sector_profile_extraction"}:
            pages = case.get("pages", [])
            if not isinstance(pages, list):
                raise ValueError(f"{kind} static eval requires pages array")
            profile = CninfoFinancialReportsProvider._extract_financial_sector_profile(
                [page for page in pages if isinstance(page, dict)],
                unit=str(case.get("unit", "million_yuan")),
            )
            result_payload = profile if isinstance(profile, dict) else {}
            actual_pass = bool(profile)
        elif kind == "candidate_rank":
            scorecards = case.get("scorecards", [])
            if not isinstance(scorecards, list) or not scorecards:
                raise ValueError("candidate_rank static eval requires scorecards array")
            try:
                payloads = [
                    json.loads((root / str(path)).read_text(encoding="utf-8"))
                    for path in scorecards
                ]
                result_payload = rank_candidates(payloads)
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "technical_health":
            series = case.get("series", {})
            if not isinstance(series, dict):
                raise ValueError("technical_health static eval requires series object")
            start = float(series.get("start", 100.0))
            step = float(series.get("step", 0.0))
            count = int(series.get("count", 80))
            rows = []
            for idx in range(count):
                close = start + idx * step
                month = 1 + idx // 28
                day = 1 + idx % 28
                rows.append({
                    "date": f"2026-{month:02d}-{day:02d}",
                    "close": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "volume": 1000000 + idx,
                })
            result_payload = analyze_price_rows(rows)
            actual_pass = bool(result_payload.get("buy_point_claim_allowed")) is False
        elif kind == "capital_actions":
            announcements = case.get("announcements", [])
            if not isinstance(announcements, list):
                raise ValueError("capital_actions static eval requires announcements array")
            result_payload = analyze_announcements({"recent_announcements": announcements})
            actual_pass = True
        elif kind == "comparison_report":
            manifests = case.get("manifests", [])
            if not isinstance(manifests, list) or len(manifests) < 2:
                raise ValueError("comparison_report static eval requires at least two manifests")
            try:
                manifest_paths = [root / str(path) for path in manifests]
                if case.get("clear_manifest_research_debt"):
                    with tempfile.TemporaryDirectory(prefix="serenity-static-comparison-") as temp_dir:
                        temp_paths = []
                        for manifest_path in manifest_paths:
                            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                            acquisition = payload.get("data_acquisition") if isinstance(payload.get("data_acquisition"), dict) else {}
                            acquisition["research_debt"] = []
                            acquisition["manual_retrieval_tasks"] = []
                            acquisition["research_debt_count"] = 0
                            acquisition["manual_task_count"] = 0
                            payload["data_acquisition"] = acquisition
                            temp_path = Path(temp_dir) / manifest_path.name
                            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                            temp_paths.append(temp_path)
                        result_payload = build_comparison_report(temp_paths)
                else:
                    result_payload = build_comparison_report(manifest_paths)
                actual_pass = True
            except Exception as exc:
                actual_pass = False
                findings = [f"{type(exc).__name__}: {exc}"]
        elif kind == "action_gate_profile":
            debt_rows = case.get("research_debt", [])
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
            mutations = case.get("mutations", [])
            if not isinstance(manifests, list) or len(manifests) < 2:
                raise ValueError("comparison_report_validation static eval requires at least two manifests")
            if not isinstance(mutations, list):
                raise ValueError("comparison_report_validation static eval requires mutations array")
            report = build_comparison_report([root / str(path) for path in manifests])
            for mutation in mutations:
                if not isinstance(mutation, dict) or "path" not in mutation:
                    raise ValueError("comparison_report_validation mutation requires path")
                _set_path(report, str(mutation["path"]), mutation.get("value"))
            errors = validate_comparison_report(report)
            result_payload = {"ok": not errors, "errors": errors}
            actual_pass = not errors
            if errors:
                findings = errors
        elif kind == "comparison_report_with_overlays":
            manifests = case.get("manifests", [])
            overlays = case.get("overlays", {})
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
        elif kind == "provider_chain":
            symbol = SymbolInfo(
                input_value=str(case.get("symbol", "NVDA")),
                symbol=str(case.get("symbol", "NVDA")),
                market=Market(str(case.get("market", "US"))),
                exchange=str(case.get("exchange", "US")),
                currency=str(case.get("currency", "USD")),
            )
            provider_names = [provider.name for provider in default_real_providers(symbol)]
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
            extracted = data_layer_module.HkexValuationInputsProvider._extract_issued_shares_from_text(str(case.get("text") or ""))
            result_payload = extracted or {}
            actual_pass = extracted is not None
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
            original_bootstrap = data_layer_module._sec_cik_from_bootstrap
            original_exchange = data_layer_module._sec_cik_from_ticker_exchange_json
            original_company = data_layer_module._sec_cik_from_company_tickers_json
            original_txt = data_layer_module._sec_cik_from_ticker_txt
            original_submissions = data_layer_module._fetch_sec_submissions_payload
            submission_tickers = {
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
                resolved = data_layer_module._sec_cik_from_ticker(str(case.get("symbol", "NVDA")), user_agent="static-eval")
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
            companyfacts = case.get("companyfacts", {})
            if not isinstance(companyfacts, dict):
                raise ValueError("sec_shares_outstanding static eval requires companyfacts object")
            share_fact = data_layer_module._latest_sec_shares_outstanding(companyfacts)
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
            alias = str(case.get("alias", "sh688322"))
            fields = [""] * int(case.get("field_count", 88))
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

        expected_result = case.get("expected_result", {})
        expected_contains = case.get("expected_contains", [])
        result_matches = all(_get_path(result_payload, k) == v for k, v in expected_result.items())
        contains_matches = True
        if actual_pass and expected_contains:
            if not isinstance(expected_contains, list):
                raise ValueError("expected_contains must be an array")
            contains_matches = all(
                isinstance(item, dict)
                and isinstance(item.get("value"), dict)
                and _contains_mapping(result_payload, str(item.get("path")), item["value"])
                for item in expected_contains
            )
        passed = actual_pass == expect_pass and (not actual_pass or (result_matches and contains_matches))
        marker = "PASS" if passed else "FAIL"
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
