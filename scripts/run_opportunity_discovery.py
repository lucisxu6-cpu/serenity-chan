#!/usr/bin/env python3
"""Run opportunity discovery through plan, universe, preflight, and funnel."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from build_candidate_funnel import build_candidate_funnel
    from build_opportunity_discovery_plan import build_plan
    from build_theme_candidate_universe import build_universe
    from data_router import fetch_real_data
    from validate_candidate_funnel import validate_candidate_funnel
    from validate_opportunity_discovery_plan import validate_opportunity_discovery_plan
    from validate_theme_candidate_universe import validate_universe
except ModuleNotFoundError:  # pragma: no cover
    from scripts.build_candidate_funnel import build_candidate_funnel
    from scripts.build_opportunity_discovery_plan import build_plan
    from scripts.build_theme_candidate_universe import build_universe
    from scripts.data_router import fetch_real_data
    from scripts.validate_candidate_funnel import validate_candidate_funnel
    from scripts.validate_opportunity_discovery_plan import validate_opportunity_discovery_plan
    from scripts.validate_theme_candidate_universe import validate_universe


DEFAULT_PREFLIGHT_DATASETS: list[str] = [
    "current_quote",
    "price_history_adjusted",
    "financials",
    "filings_announcements",
    "customer_order_capacity_evidence",
    "valuation_inputs",
]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value.strip()) or "candidate"


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _theme_keys(plan: Mapping[str, Any]) -> list[str]:
    keys: list[str] = []
    for row in _as_list(plan.get("trend_hypotheses")):
        if not isinstance(row, Mapping):
            continue
        if _text(row.get("theme_source") or "curated_pack") != "curated_pack":
            continue
        key: str = _text(row.get("theme_key"))
        if key and key not in keys:
            keys.append(key)
    return keys


def _open_theme_tasks(plan: Mapping[str, Any]) -> list[str]:
    policy: Any = plan.get("universe_policy")
    if not isinstance(policy, Mapping):
        return []
    tasks: list[str] = []
    for item in _as_list(policy.get("open_theme_research_tasks")):
        text: str = _text(item)
        if text:
            tasks.append(text)
    return tasks


def _preflight_symbols(initial_funnel: Mapping[str, Any], limit: int) -> list[str]:
    symbols: list[str] = []
    for row in _as_list(initial_funnel.get("candidate_rows")):
        if not isinstance(row, Mapping):
            continue
        if _text(row.get("final_bucket")) == "constraint_excluded":
            continue
        symbol: str = _text(row.get("symbol"))
        if symbol and symbol not in symbols:
            symbols.append(symbol)
        if len(symbols) >= limit:
            break
    return symbols


def run_discovery(
    *,
    prompt: str,
    out_dir: Path,
    external_universe_paths: Sequence[Path],
    market_scope: Sequence[str],
    excluded_boards: Sequence[str],
    horizon: str,
    risk_profile: str,
    max_price: Optional[float],
    min_price: Optional[float],
    themes: Sequence[str],
    preflight_candidate_limit: int,
    shortlist_target: int,
    datasets: Sequence[str],
    chart_range: str,
    interval: str,
    min_bars: int,
    sec_user_agent: str,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    if sec_user_agent:
        os.environ["SEC_USER_AGENT"] = sec_user_agent
    plan: dict[str, Any] = build_plan(
        prompt=prompt,
        market_scope=market_scope,
        excluded_boards=excluded_boards,
        horizon=horizon,
        risk_profile=risk_profile,
        max_price=max_price,
        min_price=min_price,
        explicit_themes=themes,
        preflight_candidate_limit=preflight_candidate_limit,
        shortlist_target=shortlist_target,
    )
    plan_errors: list[str] = validate_opportunity_discovery_plan(plan)
    if plan_errors:
        raise ValueError("; ".join(plan_errors))
    plan_path: Path = out_dir / "opportunity_discovery_plan.json"
    _write_json(plan_path, plan)

    universe_paths: list[Path] = []
    for universe_path in external_universe_paths:
        raw_universe_payload: Any = json.loads(universe_path.read_text(encoding="utf-8"))
        if not isinstance(raw_universe_payload, Mapping):
            raise ValueError(f"{universe_path} must contain a JSON object")
        universe_payload: Mapping[str, Any] = raw_universe_payload
        universe_errors: list[str] = validate_universe(universe_payload)
        if universe_errors:
            raise ValueError(f"{universe_path}: " + "; ".join(universe_errors))
        universe_paths.append(universe_path)
    theme_keys: list[str] = _theme_keys(plan)
    if not theme_keys and not universe_paths:
        summary: dict[str, Any] = {
            "contract_type": "serenity_opportunity_discovery_workflow_summary",
            "schema_version": "1.0",
            "workflow_status": "THEME_UNIVERSE_RESEARCH_REQUIRED",
            "terminal": True,
            "delivery_allowed": True,
            "next_phase": "build_ai_theme_candidate_universe",
            "out_dir": str(out_dir),
            "opportunity_discovery_plan": str(plan_path),
            "theme_candidate_universes": [str(path) for path in universe_paths],
            "initial_candidate_funnel": "",
            "candidate_funnel": "",
            "preflight_symbols": [],
            "fetch_summaries": [],
            "shortlist_symbols": [],
            "research_tasks": _open_theme_tasks(plan),
        }
        _write_json(out_dir / "opportunity_discovery_summary.json", summary)
        return summary

    for theme_key in theme_keys:
        universe: dict[str, Any] = build_universe(theme_key)
        universe_errors: list[str] = validate_universe(universe)
        if universe_errors:
            raise ValueError(f"{theme_key}: " + "; ".join(universe_errors))
        universe_path: Path = out_dir / f"theme_candidate_universe_{theme_key}.json"
        _write_json(universe_path, universe)
        universe_paths.append(universe_path)

    initial_funnel: dict[str, Any] = build_candidate_funnel(
        plan_path=plan_path,
        universe_paths=universe_paths,
        preflight_root=None,
        preflight_snapshot_path=None,
    )
    initial_funnel_path: Path = out_dir / "candidate_funnel_initial.json"
    _write_json(initial_funnel_path, initial_funnel)
    symbols: list[str] = _preflight_symbols(initial_funnel, preflight_candidate_limit)
    preflight_root: Path = out_dir / "preflight"
    fetch_summaries: list[dict[str, Any]] = []
    for symbol in symbols:
        candidate_dir: Path = preflight_root / "data" / _safe_name(symbol)
        manifest: Mapping[str, Any] = fetch_real_data(
            symbol,
            datasets=list(datasets),
            out_dir=str(candidate_dir),
            chart_range=chart_range,
            interval=interval,
            min_bars=min_bars,
        )
        acquisition: Mapping[str, Any] = manifest.get("data_acquisition") if isinstance(manifest.get("data_acquisition"), Mapping) else {}
        fetch_summaries.append({
            "symbol": symbol,
            "manifest": str(candidate_dir / "manifest.json"),
            "status_by_dataset": acquisition.get("status_by_dataset", {}),
            "full_research_ready": acquisition.get("full_research_ready", False),
        })

    final_funnel: dict[str, Any] = build_candidate_funnel(
        plan_path=plan_path,
        universe_paths=universe_paths,
        preflight_root=preflight_root,
        preflight_snapshot_path=None,
    )
    funnel_errors: list[str] = validate_candidate_funnel(final_funnel)
    if funnel_errors:
        raise ValueError("; ".join(funnel_errors))
    final_funnel_path: Path = out_dir / "candidate_funnel.json"
    _write_json(final_funnel_path, final_funnel)
    shortlist_symbols: list[str] = [str(item) for item in _as_list(final_funnel.get("shortlist_symbols")) if str(item).strip()]
    has_shortlist: bool = bool(shortlist_symbols)
    summary: dict[str, Any] = {
        "contract_type": "serenity_opportunity_discovery_workflow_summary",
        "schema_version": "1.0",
        "workflow_status": "CANDIDATE_FUNNEL_READY" if has_shortlist else "CANDIDATE_FUNNEL_EMPTY",
        "terminal": True,
        "delivery_allowed": True,
        "next_phase": "run_formal_research_on_shortlist" if has_shortlist else "expand_universe_or_repair_preflight",
        "out_dir": str(out_dir),
        "opportunity_discovery_plan": str(plan_path),
        "theme_candidate_universes": [str(path) for path in universe_paths],
        "initial_candidate_funnel": str(initial_funnel_path),
        "candidate_funnel": str(final_funnel_path),
        "preflight_symbols": symbols,
        "fetch_summaries": fetch_summaries,
        "shortlist_symbols": shortlist_symbols,
    }
    _write_json(out_dir / "opportunity_discovery_summary.json", summary)
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Run Serenity opportunity discovery")
    parser.add_argument("prompt", help="open opportunity request")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--market-scope", nargs="+", default=["CN_A"])
    parser.add_argument("--exclude-board", action="append", default=[])
    parser.add_argument("--horizon", default="3-6个月")
    parser.add_argument("--risk-profile", default="balanced")
    parser.add_argument("--max-price", type=float)
    parser.add_argument("--min-price", type=float)
    parser.add_argument("--theme", action="append", default=[])
    parser.add_argument("--universe", action="append", default=[], help="validated AI-built theme_candidate_universe.json")
    parser.add_argument("--preflight-candidate-limit", type=int, default=24)
    parser.add_argument("--shortlist-target", type=int, default=8)
    parser.add_argument("--datasets", nargs="+", default=DEFAULT_PREFLIGHT_DATASETS)
    parser.add_argument("--range", dest="chart_range", default="2y")
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--min-bars", type=int, default=250)
    parser.add_argument("--sec-user-agent", default="")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        summary: dict[str, Any] = run_discovery(
            prompt=str(args.prompt),
            out_dir=Path(args.out_dir),
            external_universe_paths=[Path(item) for item in args.universe],
            market_scope=[str(item) for item in args.market_scope],
            excluded_boards=[str(item) for item in args.exclude_board],
            horizon=str(args.horizon),
            risk_profile=str(args.risk_profile),
            max_price=args.max_price,
            min_price=args.min_price,
            themes=[str(item) for item in args.theme],
            preflight_candidate_limit=int(args.preflight_candidate_limit),
            shortlist_target=int(args.shortlist_target),
            datasets=[str(item) for item in args.datasets],
            chart_range=str(args.chart_range),
            interval=str(args.interval),
            min_bars=int(args.min_bars),
            sec_user_agent=str(args.sec_user_agent),
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
