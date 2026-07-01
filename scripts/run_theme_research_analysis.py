#!/usr/bin/env python3
"""Run the layer-first theme research workflow."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from build_theme_candidate_universe import build_universe
    from build_theme_research_packet import build_theme_research_packet
    from run_research_analysis import DEFAULT_DATASETS, run_analysis
    from validate_theme_candidate_universe import validate_universe
    from validate_theme_research_packet import validate_theme_research_packet
except ModuleNotFoundError:  # pragma: no cover
    from scripts.build_theme_candidate_universe import build_universe
    from scripts.build_theme_research_packet import build_theme_research_packet
    from scripts.run_research_analysis import DEFAULT_DATASETS, run_analysis
    from scripts.validate_theme_candidate_universe import validate_universe
    from scripts.validate_theme_research_packet import validate_theme_research_packet


def _load_json(path: Path) -> Mapping[str, Any]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _candidate_symbols(universe: Mapping[str, Any]) -> list[str]:
    symbols: list[str] = []
    candidates: Any = universe.get("candidate_universe")
    for row in candidates if isinstance(candidates, list) else []:
        if not isinstance(row, Mapping):
            continue
        symbol: str = str(row.get("symbol") or "").strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def _layer_symbol_groups(universe: Mapping[str, Any]) -> list[list[str]]:
    groups: list[list[str]] = []
    layers: Any = universe.get("value_chain_layers")
    for row in layers if isinstance(layers, list) else []:
        if not isinstance(row, Mapping):
            continue
        symbols: list[str] = []
        for symbol in row.get("candidate_symbols", []) if isinstance(row.get("candidate_symbols"), list) else []:
            symbol_text: str = str(symbol).strip()
            if symbol_text and symbol_text not in symbols:
                symbols.append(symbol_text)
        if symbols:
            groups.append(symbols)
    return groups


def _append_unique(target: list[str], symbols: Sequence[str], *, limit: int) -> None:
    for symbol in symbols:
        if limit > 0 and len(target) >= limit:
            return
        if symbol not in target:
            target.append(symbol)


def _select_candidate_symbols(universe: Mapping[str, Any], *, max_candidates: int, selection_policy: str) -> list[str]:
    all_symbols: list[str] = _candidate_symbols(universe)
    limit: int = max_candidates if max_candidates > 0 else 0
    if selection_policy == "theme-cluster":
        return all_symbols if limit <= 0 else all_symbols[:limit]

    groups: list[list[str]] = _layer_symbol_groups(universe)
    selected: list[str] = []
    if selection_policy == "same-layer":
        primary_group: list[str] = max(groups, key=len, default=[])
        _append_unique(selected, primary_group, limit=limit)
        return selected

    if selection_policy == "balanced-layers":
        max_group_size: int = max((len(group) for group in groups), default=0)
        for index in range(max_group_size):
            for group in groups:
                if index < len(group):
                    _append_unique(selected, [group[index]], limit=limit)
                if limit > 0 and len(selected) >= limit:
                    return selected
        return selected

    raise ValueError("selection_policy must be one of: same-layer, balanced-layers, theme-cluster")


def _prepare_universe(theme: str, universe_path: str, out_dir: Path) -> tuple[Mapping[str, Any], Path]:
    if universe_path:
        source_path: Path = Path(universe_path)
        universe: Mapping[str, Any] = _load_json(source_path)
        errors: list[str] = validate_universe(universe)
        if errors:
            raise ValueError("; ".join(errors))
        target_path: Path = out_dir / "theme_candidate_universe.json"
        _write_json(target_path, universe)
        return universe, target_path
    universe_payload: dict[str, Any] = build_universe(theme)
    target_path = out_dir / "theme_candidate_universe.json"
    _write_json(target_path, universe_payload)
    errors = validate_universe(universe_payload)
    if errors:
        raise ValueError("; ".join(errors))
    return universe_payload, target_path


def run_theme_analysis(
    *,
    theme: str,
    out_dir: Path,
    universe_path: str,
    symbols: Sequence[str],
    max_candidates: int,
    datasets: Sequence[str],
    chart_range: str,
    interval: str,
    min_bars: int,
    sec_user_agent: str,
    overlay_values: Sequence[str],
    outcome_values: Sequence[str],
    dossier_values: Sequence[str],
    strategy_horizon: str,
    strategy_geography: str,
    strategy_decision_use: str,
    strategy_profile: str,
    research_mode: str,
    selection_policy: str,
    candidate_funnel: str,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    if sec_user_agent:
        os.environ["SEC_USER_AGENT"] = sec_user_agent
    universe, prepared_universe_path = _prepare_universe(theme, universe_path, out_dir)
    theme_packet: dict[str, Any] = build_theme_research_packet(prepared_universe_path)
    packet_errors: list[str] = validate_theme_research_packet(theme_packet)
    if packet_errors:
        raise ValueError("; ".join(packet_errors))
    theme_packet_path: Path = out_dir / "theme_research_packet.json"
    _write_json(theme_packet_path, theme_packet)

    selected_symbols: list[str] = [str(symbol).strip() for symbol in symbols if str(symbol).strip()]
    if not selected_symbols:
        selected_symbols = _select_candidate_symbols(universe, max_candidates=max_candidates, selection_policy=selection_policy)
    if len(selected_symbols) < 2:
        raise ValueError("theme research requires at least two selected symbols")

    research_summary: dict[str, Any] = run_analysis(
        selected_symbols,
        out_dir=out_dir / "research",
        datasets=datasets,
        chart_range=chart_range,
        interval=interval,
        min_bars=min_bars,
        sec_user_agent=sec_user_agent,
        overlay_values=overlay_values,
        outcome_values=outcome_values,
        dossier_values=dossier_values,
        strategy_theme=theme,
        strategy_horizon=strategy_horizon,
        strategy_geography=strategy_geography,
        strategy_decision_use=strategy_decision_use,
        strategy_profile=strategy_profile,
        research_mode=research_mode,
        theme_universe=str(prepared_universe_path),
        theme_research_packet=str(theme_packet_path),
        candidate_funnel=candidate_funnel,
    )
    summary: dict[str, Any] = {
        "contract_type": "serenity_theme_research_workflow_summary",
        "schema_version": "1.0",
        "theme": theme or str(universe.get("theme") or ""),
        "theme_candidate_universe": str(prepared_universe_path),
        "theme_research_packet": str(theme_packet_path),
        "selected_symbols": selected_symbols,
        "selection_policy": selection_policy,
        "research_summary": research_summary,
    }
    _write_json(out_dir / "theme_workflow_summary.json", summary)
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Run Serenity theme research analysis")
    parser.add_argument("theme", help="theme name or alias")
    parser.add_argument("--out-dir", required=True, help="workflow output directory")
    parser.add_argument("--universe", default="", help="optional prebuilt theme_candidate_universe.json")
    parser.add_argument("--symbols", nargs="*", default=[], help="explicit symbols to analyze; defaults to universe candidates")
    parser.add_argument("--max-candidates", type=int, default=12, help="maximum universe candidates to analyze by default; use 0 for all")
    parser.add_argument("--selection-policy", choices=["same-layer", "balanced-layers", "theme-cluster"], default="theme-cluster", help="how to select default candidates from the theme universe")
    parser.add_argument("--datasets", nargs="+", default=DEFAULT_DATASETS, help="datasets passed to data_router fetch")
    parser.add_argument("--range", dest="chart_range", default="2y", help="chart range for price history")
    parser.add_argument("--interval", default="1d", help="chart interval")
    parser.add_argument("--min-bars", type=int, default=250)
    parser.add_argument("--sec-user-agent", default="", help="SEC-compliant User-Agent for US symbols")
    parser.add_argument("--overlay", action="append", default=[], help="SYMBOL=overlay.json")
    parser.add_argument("--ai-outcome", action="append", default=[], help="SYMBOL=ai_review_outcome.json")
    parser.add_argument("--dossier", action="append", default=[], help="SYMBOL=ai_research_dossier.json")
    parser.add_argument("--strategy-horizon", default="3-6个月")
    parser.add_argument("--strategy-geography", default="")
    parser.add_argument("--strategy-decision-use", default="watchlist allocation, action triggers, and invalidation")
    parser.add_argument("--strategy-profile", default="balanced")
    parser.add_argument("--research-mode", choices=["formal", "diagnostic"], default="formal")
    parser.add_argument("--candidate-funnel", default="", help="optional candidate_funnel.json passed into AI research and strategy input")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        summary: dict[str, Any] = run_theme_analysis(
            theme=args.theme,
            out_dir=Path(args.out_dir),
            universe_path=args.universe,
            symbols=args.symbols,
            max_candidates=args.max_candidates,
            datasets=args.datasets,
            chart_range=args.chart_range,
            interval=args.interval,
            min_bars=args.min_bars,
            sec_user_agent=args.sec_user_agent,
            overlay_values=args.overlay,
            outcome_values=args.ai_outcome,
            dossier_values=args.dossier,
            strategy_horizon=args.strategy_horizon,
            strategy_geography=args.strategy_geography,
            strategy_decision_use=args.strategy_decision_use,
            strategy_profile=args.strategy_profile,
            research_mode=args.research_mode,
            selection_policy=args.selection_policy,
            candidate_funnel=args.candidate_funnel,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
