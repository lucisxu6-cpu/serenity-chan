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
    from data_router import fetch_real_data
    from validate_and_merge_ai_overlay import build_validated_merged_report
except ModuleNotFoundError:  # pragma: no cover
    from scripts.build_ai_committee_packet import build_ai_committee_packet
    from scripts.build_ai_overlay_prompt import build_ai_overlay_prompt
    from scripts.build_ai_review_packet import build_ai_review_packet
    from scripts.build_comparison_report import build_comparison_report, to_markdown
    from scripts.data_router import fetch_real_data
    from scripts.validate_and_merge_ai_overlay import build_validated_merged_report


DEFAULT_DATASETS: list[str] = [
    "current_quote",
    "price_history_adjusted",
    "financials",
    "filings_announcements",
    "valuation_inputs",
]


def _safe_name(value: str) -> str:
    cleaned: str = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned or "candidate"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _manifest_symbol(path: Path) -> str:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    symbol: Any = payload.get("symbol")
    if isinstance(symbol, Mapping):
        return str(symbol.get("symbol") or "")
    return str(symbol or "")


def _assigned_symbols(values: Sequence[str]) -> set[str]:
    symbols: set[str] = set()
    for value in values:
        if "=" not in value:
            raise ValueError("AI result assignments must use SYMBOL=path")
        symbol: str = value.split("=", 1)[0]
        symbols.add(symbol)
    return symbols


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
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
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
        overlay_prompt: dict[str, Any] = build_ai_overlay_prompt(manifest_path)
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
        })

    baseline_report_path: Optional[Path] = None
    baseline_markdown_path: Optional[Path] = None
    if len(manifest_paths) >= 2:
        baseline_report: dict[str, Any] = build_comparison_report(manifest_paths, {}, {})
        baseline_report_path = out_dir / "comparison_baseline.json"
        baseline_markdown_path = out_dir / "comparison_baseline.md"
        _write_json(baseline_report_path, baseline_report)
        _write_text(baseline_markdown_path, to_markdown(baseline_report))

    candidate_symbols: set[str] = {_manifest_symbol(path) for path in manifest_paths}
    supplied_symbols: set[str] = _assigned_symbols(overlay_values) | _assigned_symbols(outcome_values)
    missing_ai_symbols: list[str] = sorted(candidate_symbols - supplied_symbols)
    summary: dict[str, Any] = {
        "workflow_status": "AI_RESULT_REQUIRED" if missing_ai_symbols else "COMPLETED",
        "out_dir": str(out_dir),
        "fetch_summaries": fetch_summaries,
        "ai_artifacts": ai_artifacts,
        "baseline_report": str(baseline_report_path) if baseline_report_path else "",
        "baseline_markdown": str(baseline_markdown_path) if baseline_markdown_path else "",
        "missing_ai_result_symbols": missing_ai_symbols,
    }
    if missing_ai_symbols:
        summary["next_action"] = "读取每个候选的 ai_overlay_prompt.json，由 AI 研究后输出 overlay 或 ai_review_outcome，再用同一命令传入 --overlay/--ai-outcome 生成最终报告。"
        _write_json(out_dir / "ai_result_required.json", summary)
        return summary

    final_report: dict[str, Any] = build_validated_merged_report(manifest_paths, overlay_values, outcome_values)
    final_report_path: Path = out_dir / "comparison_final.json"
    final_markdown_path: Path = out_dir / "comparison_final.md"
    _write_json(final_report_path, final_report)
    _write_text(final_markdown_path, to_markdown(final_report))
    summary["final_report"] = str(final_report_path)
    summary["final_markdown"] = str(final_markdown_path)
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
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
