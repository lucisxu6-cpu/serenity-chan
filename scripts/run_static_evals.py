#!/usr/bin/env python3
"""Run static contract evals for serenity-chan-stock-skill."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

try:
    import data_layer as data_layer_module
    from validate_output_contract import validate_text
    from validate_output_contract_json import validate_contract
    from build_falsification_dashboard import build_from_output_contract
    from candidate_ranker import rank_candidates
    from serenity_chan_scorecard import score
    from data_layer import EastmoneyF10FinancialsProvider, Market, SymbolInfo, default_real_providers, _sec_submission_matches_symbol
except ModuleNotFoundError:  # pragma: no cover - supports python -m scripts.run_static_evals
    from scripts import data_layer as data_layer_module
    from scripts.validate_output_contract import validate_text
    from scripts.validate_output_contract_json import validate_contract
    from scripts.build_falsification_dashboard import build_from_output_contract
    from scripts.candidate_ranker import rank_candidates
    from scripts.serenity_chan_scorecard import score
    from scripts.data_layer import EastmoneyF10FinancialsProvider, Market, SymbolInfo, default_real_providers, _sec_submission_matches_symbol


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
        else:
            raise ValueError(f"unknown static eval kind: {kind}")

        expected_result = case.get("expected_result", {})
        result_matches = all(_get_path(result_payload, k) == v for k, v in expected_result.items())
        passed = actual_pass == expect_pass and (not actual_pass or result_matches)
        marker = "PASS" if passed else "FAIL"
        print(f"[{marker}] {name}: expected {'pass' if expect_pass else 'fail'}, got {'pass' if actual_pass else 'fail'}")
        if not passed:
            failures += 1
            if expected_result and actual_pass and not result_matches:
                print(f"  - expected result fields: {expected_result}")
                print(f"  - actual result fields: {result_payload}")
            for finding in findings:
                print(f"  - {finding}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
