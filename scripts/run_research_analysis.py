#!/usr/bin/env python3
"""Run the top-level Serenity + Chan research workflow."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from build_ai_committee_packet import build_ai_committee_packet
    from build_ai_overlay_prompt import build_ai_overlay_prompt
    from build_ai_review_packet import build_ai_review_packet
    from build_comparison_report import build_comparison_report, to_markdown
    from build_laplace_strategy_input import build_strategy_input, infer_geography
    from build_laplace_strategy_prompt import build_strategy_prompt
    from data_router import fetch_real_data
    from validate_and_merge_ai_overlay import build_validated_merged_report
    from validate_candidate_funnel import validate_candidate_funnel
except ModuleNotFoundError:  # pragma: no cover
    from scripts.build_ai_committee_packet import build_ai_committee_packet
    from scripts.build_ai_overlay_prompt import build_ai_overlay_prompt
    from scripts.build_ai_review_packet import build_ai_review_packet
    from scripts.build_comparison_report import build_comparison_report, to_markdown
    from scripts.build_laplace_strategy_input import build_strategy_input, infer_geography
    from scripts.build_laplace_strategy_prompt import build_strategy_prompt
    from scripts.data_router import fetch_real_data
    from scripts.validate_and_merge_ai_overlay import build_validated_merged_report
    from scripts.validate_candidate_funnel import validate_candidate_funnel


DEFAULT_DATASETS: list[str] = [
    "current_quote",
    "price_history_adjusted",
    "financials",
    "filings_announcements",
    "customer_order_capacity_evidence",
    "valuation_inputs",
]
RESEARCH_MODES: set[str] = {"formal", "diagnostic"}


def _safe_name(value: str) -> str:
    cleaned: str = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned or "candidate"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _load_json(path: Path) -> Mapping[str, Any]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _manifest_symbol(path: Path) -> str:
    payload: Mapping[str, Any] = _load_json(path)
    symbol: Any = payload.get("symbol")
    if isinstance(symbol, Mapping):
        return str(symbol.get("symbol") or "")
    return str(symbol or "")


def _validate_candidate_funnel_scope(candidate_funnel: str, symbols: Sequence[str]) -> None:
    if not candidate_funnel:
        return
    funnel_path: Path = Path(candidate_funnel)
    payload: Mapping[str, Any] = _load_json(funnel_path)
    errors: list[str] = validate_candidate_funnel(payload)
    if errors:
        raise ValueError(f"candidate_funnel is invalid: {'; '.join(errors)}")
    shortlist: set[str] = {str(item).strip() for item in _as_list(payload.get("shortlist_symbols")) if str(item).strip()}
    if not shortlist:
        raise ValueError("candidate_funnel.shortlist_symbols is empty; repair discovery before formal research")
    requested: set[str] = {str(symbol).strip() for symbol in symbols if str(symbol).strip()}
    outside: list[str] = sorted(requested - shortlist)
    if outside:
        raise ValueError(f"symbols outside candidate_funnel shortlist: {', '.join(outside)}")


def _assigned_symbols(values: Sequence[str]) -> set[str]:
    symbols: set[str] = set()
    for value in values:
        if "=" not in value:
            raise ValueError("AI result assignments must use SYMBOL=path")
        symbol: str = value.split("=", 1)[0]
        symbols.add(symbol)
    return symbols


def _assigned_paths(values: Sequence[str], *, label: str) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"{label} assignments must use SYMBOL=path")
        symbol: str
        path_text: str
        symbol, path_text = value.split("=", 1)
        if symbol in assignments:
            raise ValueError(f"duplicate {label} assignment for {symbol}")
        assignments[symbol] = path_text
    return assignments


def _existing_ai_packages(
    *,
    overlay_values: Sequence[str],
    outcome_values: Sequence[str],
    dossier_values: Sequence[str],
    completed_symbols: Sequence[str],
) -> list[dict[str, str]]:
    overlay_paths: dict[str, str] = _assigned_paths(overlay_values, label="overlay")
    outcome_paths: dict[str, str] = _assigned_paths(outcome_values, label="ai-outcome")
    dossier_paths: dict[str, str] = _assigned_paths(dossier_values, label="dossier")
    packages: list[dict[str, str]] = []
    for symbol in sorted(completed_symbols):
        if symbol in overlay_paths and symbol in outcome_paths:
            raise ValueError(f"candidate cannot have both overlay and ai-outcome assignments: {symbol}")
        if symbol not in dossier_paths:
            continue
        if symbol in overlay_paths:
            packages.append({
                "symbol": symbol,
                "dossier_path": dossier_paths[symbol],
                "result_type": "overlay",
                "result_path": overlay_paths[symbol],
            })
        elif symbol in outcome_paths:
            packages.append({
                "symbol": symbol,
                "dossier_path": dossier_paths[symbol],
                "result_type": "ai_outcome",
                "result_path": outcome_paths[symbol],
            })
    return packages


def _agent_research_work_items(
    ai_artifacts: Sequence[Mapping[str, str]],
    fetch_summaries: Sequence[Mapping[str, Any]],
    missing_symbols: Sequence[str],
) -> list[dict[str, Any]]:
    missing: set[str] = set(missing_symbols)
    manifest_by_symbol: dict[str, str] = {
        str(summary.get("symbol") or ""): str(summary.get("manifest") or "")
        for summary in fetch_summaries
        if str(summary.get("symbol") or "")
    }
    work_items: list[dict[str, Any]] = []
    for artifact in ai_artifacts:
        symbol: str = str(artifact.get("symbol") or "")
        if symbol not in missing:
            continue
        result_dir: Path = Path(str(artifact.get("ai_overlay_prompt") or ".")).parent
        work_items.append({
            "symbol": symbol,
            "required_action": "produce_validated_ai_research_package",
            "manifest_path": manifest_by_symbol.get(symbol, ""),
            "review_packet": artifact.get("ai_review_packet", ""),
            "committee_packet": artifact.get("ai_committee_packet", ""),
            "overlay_prompt": artifact.get("ai_overlay_prompt", ""),
            "theme_universe": artifact.get("theme_universe", ""),
            "theme_research_packet": artifact.get("theme_research_packet", ""),
            "candidate_funnel": artifact.get("candidate_funnel", ""),
            "dossier_schema": "assets/ai_research_dossier.schema.json",
            "overlay_schema": "assets/ai_research_overlay.schema.json",
            "outcome_schema": "assets/ai_review_outcome.schema.json",
            "dossier_output_path": str(result_dir / "ai_research_dossier.json"),
            "overlay_output_path": str(result_dir / "ai_research_overlay.json"),
            "outcome_output_path": str(result_dir / "ai_review_outcome.json"),
            "allowed_results": [
                "COMPLETED via ai_research_dossier plus ai_research_overlay",
                "FAILED_INSUFFICIENT_EVIDENCE via ai_research_dossier plus ai_review_outcome",
                "CONFLICT_WITH_DATA via ai_research_dossier plus ai_review_outcome",
            ],
            "research_expansion_protocol": [
                "Frame the core decision question before scoring the candidate.",
                "Write at least two competing or complementary hypotheses, including one that can reduce the thesis.",
                "Turn each major uncertainty into an evidence test with method, current result, evidence status, and source refs.",
                "Separate observed facts, inferences, and judgment before writing final judgment.",
                "Use scenarios, triggers, and action conditions to convert research into an executable decision path.",
            ],
            "validation_commands": [
                "python scripts/validate_ai_research_dossier.py <dossier.json> --manifest <manifest.json>",
                "python scripts/validate_ai_overlay.py <overlay.json> --manifest <manifest.json>",
                "python scripts/validate_ai_review_outcome.py <ai_review_outcome.json>",
            ],
            "guardrails": [
                "First write the full AI research dossier, then project it into one validated overlay or one validated review outcome.",
                "Do not invent customers, orders, revenue split, or current data.",
                "Do not override deterministic PE/PS, market_implied_growth, valuation_stage, data status, or capital-action facts.",
                "Formal mode accepts COMPLETED, FAILED_INSUFFICIENT_EVIDENCE, or CONFLICT_WITH_DATA as final AI statuses.",
            ],
        })
    return work_items


def _agent_research_queue_summary(
    *,
    out_dir: Path,
    fetch_summaries: Sequence[Mapping[str, Any]],
    ai_artifacts: Sequence[Mapping[str, str]],
    internal_baseline_report_path: Path,
    missing_ai_symbols: Sequence[str],
    research_mode: str,
    existing_ai_packages: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    return {
        "contract_type": "serenity_agent_research_queue",
        "schema_version": "1.0",
        "workflow_status": "AGENT_RESEARCH_QUEUE_READY",
        "research_mode": research_mode,
        "artifact_role": "internal_agent_work_queue",
        "terminal": False,
        "delivery_allowed": False,
        "next_phase": "execute_agent_research",
        "out_dir": str(out_dir),
        "fetch_summaries": list(fetch_summaries),
        "ai_artifacts": list(ai_artifacts),
        "internal_baseline_report": str(internal_baseline_report_path),
        "missing_ai_result_symbols": list(missing_ai_symbols),
        "existing_ai_packages": [dict(item) for item in existing_ai_packages],
        "work_items": _agent_research_work_items(ai_artifacts, fetch_summaries, missing_ai_symbols),
        "execution_policy": {
            "current_agent_executes_work_items": True,
            "internal_baseline_role": "data_diagnostics_only",
            "quick_audit_allowed": False,
            "forbidden": [
                "Do not present internal baseline artifacts as final research.",
                "Do not treat missing AI work as a terminal formal result.",
                "Do not use market heat, theme labels, or unverified claims to upgrade evidence-supported growth.",
            ],
        },
    }


def _diagnostic_summary(
    *,
    out_dir: Path,
    fetch_summaries: Sequence[Mapping[str, Any]],
    ai_artifacts: Sequence[Mapping[str, str]],
    baseline_report_path: Optional[Path],
    baseline_markdown_path: Optional[Path],
    missing_ai_symbols: Sequence[str],
) -> dict[str, Any]:
    return {
        "contract_type": "serenity_diagnostic_baseline",
        "schema_version": "1.0",
        "workflow_status": "DATA_BASELINE_READY",
        "research_mode": "diagnostic",
        "artifact_role": "internal_data_baseline",
        "terminal": True,
        "delivery_allowed": True,
        "next_phase": "deliver",
        "out_dir": str(out_dir),
        "fetch_summaries": list(fetch_summaries),
        "ai_artifacts": list(ai_artifacts),
        "diagnostic_baseline_report": str(baseline_report_path) if baseline_report_path else "",
        "diagnostic_baseline_markdown": str(baseline_markdown_path) if baseline_markdown_path else "",
        "missing_ai_result_symbols": list(missing_ai_symbols),
        "execution_policy": {
            "current_agent_executes_work_items": False,
            "internal_baseline_role": "diagnostic_only",
            "quick_audit_allowed": True,
            "forbidden": [
                "Do not call this diagnostic baseline a formal candidate ranking.",
                "Do not produce a formal decision object from NOT_RUN AI research rows.",
            ],
        },
    }


def run_analysis(
    symbols: Sequence[str],
    *,
    out_dir: Path,
    datasets: Sequence[str],
    chart_range: str,
    interval: str,
    min_bars: int,
    sec_user_agent: str,
    overlay_values: Sequence[str],
    outcome_values: Sequence[str],
    dossier_values: Sequence[str],
    strategy_theme: str,
    strategy_horizon: str,
    strategy_geography: str,
    strategy_decision_use: str,
    strategy_profile: str,
    research_mode: str,
    theme_universe: str,
    theme_research_packet: str,
    candidate_funnel: str,
) -> dict[str, Any]:
    if research_mode not in RESEARCH_MODES:
        raise ValueError(f"research_mode must be one of: {', '.join(sorted(RESEARCH_MODES))}")
    out_dir.mkdir(parents=True, exist_ok=True)
    _validate_candidate_funnel_scope(candidate_funnel, symbols)
    if sec_user_agent:
        os.environ["SEC_USER_AGENT"] = sec_user_agent

    manifest_paths: list[Path] = []
    fetch_summaries: list[dict[str, Any]] = []
    for symbol in symbols:
        candidate_dir: Path = out_dir / "data" / _safe_name(symbol)
        manifest: Mapping[str, Any] = fetch_real_data(
            symbol,
            datasets=list(datasets),
            out_dir=str(candidate_dir),
            chart_range=chart_range,
            interval=interval,
            min_bars=min_bars,
        )
        manifest_path: Path = candidate_dir / "manifest.json"
        manifest_paths.append(manifest_path)
        acquisition: Mapping[str, Any] = manifest.get("data_acquisition", {}) if isinstance(manifest.get("data_acquisition"), Mapping) else {}
        fetch_summaries.append({
            "input": symbol,
            "symbol": _manifest_symbol(manifest_path),
            "manifest": str(manifest_path),
            "status_by_dataset": acquisition.get("status_by_dataset", {}),
            "full_research_ready": acquisition.get("full_research_ready", False),
        })

    ai_artifacts: list[dict[str, str]] = []
    for manifest_path in manifest_paths:
        symbol: str = _manifest_symbol(manifest_path)
        artifact_dir: Path = out_dir / "ai_research" / _safe_name(symbol)
        review_packet: dict[str, Any] = build_ai_review_packet(manifest_path)
        committee_packet: dict[str, Any] = build_ai_committee_packet(manifest_path)
        overlay_prompt: dict[str, Any] = build_ai_overlay_prompt(
            manifest_path,
            theme_universe_path=Path(theme_universe) if theme_universe else None,
            theme_research_packet_path=Path(theme_research_packet) if theme_research_packet else None,
            candidate_funnel_path=Path(candidate_funnel) if candidate_funnel else None,
        )
        review_path: Path = artifact_dir / "ai_review_packet.json"
        committee_path: Path = artifact_dir / "ai_committee_packet.json"
        prompt_path: Path = artifact_dir / "ai_overlay_prompt.json"
        _write_json(review_path, review_packet)
        _write_json(committee_path, committee_packet)
        _write_json(prompt_path, overlay_prompt)
        ai_artifacts.append({
            "symbol": symbol,
            "ai_review_packet": str(review_path),
            "ai_committee_packet": str(committee_path),
            "ai_overlay_prompt": str(prompt_path),
            "theme_universe": theme_universe,
            "theme_research_packet": theme_research_packet,
            "candidate_funnel": candidate_funnel,
        })

    baseline_report_path: Optional[Path] = None
    baseline_markdown_path: Optional[Path] = None
    if len(manifest_paths) >= 2:
        baseline_report: dict[str, Any] = build_comparison_report(manifest_paths, {}, {})
        if research_mode == "diagnostic":
            baseline_report_path = out_dir / "comparison_diagnostic_baseline.json"
            baseline_markdown_path = out_dir / "comparison_diagnostic_baseline.md"
        else:
            baseline_report_path = out_dir / "comparison_internal_baseline.json"
        _write_json(baseline_report_path, baseline_report)
        if baseline_markdown_path:
            _write_text(baseline_markdown_path, to_markdown(baseline_report))

    candidate_symbols: set[str] = {_manifest_symbol(path) for path in manifest_paths}
    supplied_result_symbols: set[str] = _assigned_symbols(overlay_values) | _assigned_symbols(outcome_values)
    supplied_dossier_symbols: set[str] = _assigned_symbols(dossier_values)
    completed_ai_package_symbols: set[str] = supplied_result_symbols & supplied_dossier_symbols
    missing_ai_symbols: list[str] = sorted(candidate_symbols - completed_ai_package_symbols)
    existing_ai_packages: list[dict[str, str]] = _existing_ai_packages(
        overlay_values=overlay_values,
        outcome_values=outcome_values,
        dossier_values=dossier_values,
        completed_symbols=sorted(completed_ai_package_symbols),
    )
    if missing_ai_symbols:
        if research_mode == "diagnostic":
            summary: dict[str, Any] = _diagnostic_summary(
                out_dir=out_dir,
                fetch_summaries=fetch_summaries,
                ai_artifacts=ai_artifacts,
                baseline_report_path=baseline_report_path,
                baseline_markdown_path=baseline_markdown_path,
                missing_ai_symbols=missing_ai_symbols,
            )
            _write_json(out_dir / "diagnostic_baseline.json", summary)
            return summary
        if baseline_report_path is None:
            raise ValueError("formal research queue requires an internal baseline report")
        summary = _agent_research_queue_summary(
            out_dir=out_dir,
            fetch_summaries=fetch_summaries,
            ai_artifacts=ai_artifacts,
            internal_baseline_report_path=baseline_report_path,
            missing_ai_symbols=missing_ai_symbols,
            research_mode=research_mode,
            existing_ai_packages=existing_ai_packages,
        )
        _write_json(out_dir / "agent_research_queue.json", summary)
        return summary

    final_report: dict[str, Any] = build_validated_merged_report(manifest_paths, overlay_values, outcome_values, dossier_values)
    final_report_path: Path = out_dir / "comparison_final.json"
    final_markdown_path: Path = out_dir / "comparison_final.md"
    _write_json(final_report_path, final_report)
    _write_text(final_markdown_path, to_markdown(final_report))
    strategy_input: dict[str, Any] = build_strategy_input(
        final_report,
        source_report_path=final_report_path,
        theme=strategy_theme,
        horizon=strategy_horizon,
        geography=strategy_geography or infer_geography(final_report),
        decision_use=strategy_decision_use,
        default_profile=strategy_profile,
        candidate_funnel_path=Path(candidate_funnel) if candidate_funnel else None,
    )
    strategy_input_path: Path = out_dir / "laplace_strategy_input.json"
    _write_json(strategy_input_path, strategy_input)
    strategy_prompt: dict[str, Any] = build_strategy_prompt(strategy_input_path)
    strategy_prompt_path: Path = out_dir / "laplace_strategy_prompt.json"
    _write_json(strategy_prompt_path, strategy_prompt)
    summary = {
        "contract_type": "serenity_research_workflow_summary",
        "schema_version": "1.0",
        "workflow_status": "FINAL_REPORT_READY",
        "research_mode": research_mode,
        "artifact_role": "formal_research_report",
        "terminal": True,
        "delivery_allowed": True,
        "next_phase": "build_strategy_input",
        "out_dir": str(out_dir),
        "fetch_summaries": fetch_summaries,
        "ai_artifacts": ai_artifacts,
        "internal_baseline_report": str(baseline_report_path) if baseline_report_path else "",
        "missing_ai_result_symbols": [],
        "final_report": str(final_report_path),
        "final_markdown": str(final_markdown_path),
        "candidate_funnel": candidate_funnel,
        "laplace_strategy_input": str(strategy_input_path),
        "laplace_strategy_prompt": str(strategy_prompt_path),
    }
    _write_json(out_dir / "workflow_summary.json", summary)
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Run Serenity + Chan research analysis")
    parser.add_argument("symbols", nargs="+", help="symbols to fetch and analyze")
    parser.add_argument("--out-dir", required=True, help="workflow output directory")
    parser.add_argument("--datasets", nargs="+", default=DEFAULT_DATASETS, help="datasets passed to data_router fetch")
    parser.add_argument("--range", dest="chart_range", default="2y", help="chart range for price history")
    parser.add_argument("--interval", default="1d", help="chart interval")
    parser.add_argument("--min-bars", type=int, default=250)
    parser.add_argument("--sec-user-agent", default="", help="SEC-compliant User-Agent for US symbols")
    parser.add_argument("--overlay", action="append", default=[], help="SYMBOL=overlay.json")
    parser.add_argument("--ai-outcome", action="append", default=[], help="SYMBOL=ai_review_outcome.json")
    parser.add_argument("--dossier", action="append", default=[], help="SYMBOL=ai_research_dossier.json")
    parser.add_argument("--strategy-theme", default="", help="theme or strategy object for laplace_strategy_input.json")
    parser.add_argument("--strategy-horizon", default="3-6个月", help="strategy horizon for laplace_strategy_input.json")
    parser.add_argument("--strategy-geography", default="", help="strategy geography; defaults to inferred candidate markets")
    parser.add_argument("--strategy-decision-use", default="watchlist allocation, action triggers, and invalidation")
    parser.add_argument("--strategy-profile", default="balanced")
    parser.add_argument("--research-mode", choices=sorted(RESEARCH_MODES), default="formal", help="formal blocks delivery until AI research is merged; diagnostic emits data-only baseline")
    parser.add_argument("--theme-universe", default="", help="optional theme_candidate_universe.json passed into AI overlay prompts")
    parser.add_argument("--theme-research-packet", default="", help="optional theme_research_packet.json passed into AI overlay prompts")
    parser.add_argument("--candidate-funnel", default="", help="optional candidate_funnel.json passed into AI overlay prompts and strategy input")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        summary: dict[str, Any] = run_analysis(
            args.symbols,
            out_dir=Path(args.out_dir),
            datasets=args.datasets,
            chart_range=args.chart_range,
            interval=args.interval,
            min_bars=args.min_bars,
            sec_user_agent=args.sec_user_agent,
            overlay_values=args.overlay,
            outcome_values=args.ai_outcome,
            dossier_values=args.dossier,
            strategy_theme=args.strategy_theme,
            strategy_horizon=args.strategy_horizon,
            strategy_geography=args.strategy_geography,
            strategy_decision_use=args.strategy_decision_use,
            strategy_profile=args.strategy_profile,
            research_mode=args.research_mode,
            theme_universe=args.theme_universe,
            theme_research_packet=args.theme_research_packet,
            candidate_funnel=args.candidate_funnel,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
