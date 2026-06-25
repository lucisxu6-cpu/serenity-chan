#!/usr/bin/env python3
"""Render decision-grade Serenity + Chan research reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from build_comparison_report import build_comparison_report, to_markdown, validate_comparison_report
except ModuleNotFoundError:  # pragma: no cover
    from scripts.build_comparison_report import build_comparison_report, to_markdown, validate_comparison_report


REPORT_MODES: set[str] = {"candidate_comparison", "full_research", "quick_audit"}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _mapping_rows(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _first_by_symbol(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        symbol: str = str(row.get("symbol") or "")
        if symbol and symbol not in indexed:
            indexed[symbol] = row
    return indexed


def _many_by_symbol(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    indexed: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        symbol: str = str(row.get("symbol") or "")
        if symbol:
            indexed.setdefault(symbol, []).append(row)
    return indexed


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text: str = str(value).strip()
    return text if text else default


def _joined(value: Any, *, empty: str = "") -> str:
    if isinstance(value, list):
        items: list[str] = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(items) if items else empty
    return _text(value, empty)


def _gate_class_summary(value: Any, *, empty: str = "none") -> str:
    if not isinstance(value, Mapping):
        return empty
    items: list[str] = [f"{gate}={gate_class}" for gate, gate_class in value.items()]
    return ", ".join(items) if items else empty


def _research_debt_lines(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    if not rows:
        return ["- 当前未记录高优先级研究债务。"]
    lines: list[str] = []
    for row in rows:
        dataset: str = _text(row.get("dataset") or row.get("task_type"), "unknown")
        priority: str = _text(row.get("priority"), "unknown")
        impact: str = _text(row.get("decision_impact"), "unknown")
        action: str = _text(row.get("next_action") or row.get("objective"), "待补充验证动作")
        lines.append(f"- `{dataset}` [{priority}/{impact}]：{action}")
    return lines


def _render_full_research(report: Mapping[str, Any], *, comparison_markdown: str) -> str:
    candidates: list[Mapping[str, Any]] = _mapping_rows(report.get("candidates"))
    data_rows: dict[str, Mapping[str, Any]] = _first_by_symbol(_mapping_rows(report.get("data_acquisition_summary")))
    layer_rows: dict[str, Mapping[str, Any]] = _first_by_symbol(_mapping_rows(report.get("serenity_layer_matrix")))
    financial_rows: dict[str, Mapping[str, Any]] = _first_by_symbol(_mapping_rows(report.get("financial_quality_matrix")))
    valuation_rows: dict[str, Mapping[str, Any]] = _first_by_symbol(_mapping_rows(report.get("valuation_input_matrix")))
    growth_rows: dict[str, Mapping[str, Any]] = _first_by_symbol(_mapping_rows(report.get("growth_hypothesis_matrix")))
    technical_rows: dict[str, Mapping[str, Any]] = _first_by_symbol(_mapping_rows(report.get("technical_timing_matrix")))
    capital_rows: dict[str, Mapping[str, Any]] = _first_by_symbol(_mapping_rows(report.get("capital_actions")))
    ranking_rows: dict[str, Mapping[str, Any]] = _first_by_symbol(_mapping_rows(report.get("candidate_priority_ranking")))
    debt_rows: dict[str, list[Mapping[str, Any]]] = _many_by_symbol(_mapping_rows(report.get("research_debt")))

    lines: list[str] = [
        comparison_markdown,
        "",
        "# 完整研究工作台",
        "",
        "本节把候选对比拆成逐公司研究任务。若排序可信度为 `PARTIAL` 或 `INVALID`，以下内容用于研究推进和补证，不用于正式投资结论。",
    ]

    for candidate in candidates:
        symbol: str = _text(candidate.get("symbol"), "UNKNOWN")
        name: str = _text(candidate.get("name"), "")
        data: Mapping[str, Any] = data_rows.get(symbol, {})
        layer: Mapping[str, Any] = layer_rows.get(symbol, {})
        financial: Mapping[str, Any] = financial_rows.get(symbol, {})
        valuation: Mapping[str, Any] = valuation_rows.get(symbol, {})
        growth: Mapping[str, Any] = growth_rows.get(symbol, {})
        technical: Mapping[str, Any] = technical_rows.get(symbol, {})
        capital: Mapping[str, Any] = capital_rows.get(symbol, {})
        ranking: Mapping[str, Any] = ranking_rows.get(symbol, {})
        action_gate: Mapping[str, Any] = ranking.get("action_gate") if isinstance(ranking.get("action_gate"), Mapping) else {}
        status_by_dataset: Mapping[str, Any] = data.get("status_by_dataset") if isinstance(data.get("status_by_dataset"), Mapping) else {}
        company_debt: list[Mapping[str, Any]] = debt_rows.get(symbol, [])

        lines.extend([
            "",
            f"## {symbol} {name}".rstrip(),
            "",
            "### 研究状态",
            f"- Rating Cap：`{_text(candidate.get('rating_cap'), 'OBSERVE_ONLY')}`",
            f"- Research Priority：`{_text(ranking.get('research_priority_score'), 'NA')}`；Action Priority：`{_text(ranking.get('action_priority_score'), 'NA')}`；Action Readiness：`{_text(ranking.get('action_readiness'), 'NA')}`",
            f"- Primary Gate：`{_text(action_gate.get('primary_gate'), 'NA')}`；Gate Class：`{_text(action_gate.get('primary_gate_class'), 'NA')}`",
            f"- Gate Classes：{_gate_class_summary(action_gate.get('gate_classes'))}",
            f"- 数据包：`{_text(candidate.get('data_package_path'), 'NA')}`",
            "",
            "### Serenity Thesis",
            f"- Layer：{_text(layer.get('layer'), '待 AI/domain review')}",
            f"- Bottleneck：{_text(layer.get('bottleneck_reason'), '待验证')}",
            f"- Revenue Transmission：{_text(layer.get('revenue_transmission'), '待验证')}",
            f"- Evidence Gap：{_text(layer.get('evidence_gap'), '待补证')}",
            "",
            "### 财务与估值",
            f"- Data Acquisition：财报 `{_text(status_by_dataset.get('financials'), 'NA')}`；估值输入 `{_text(status_by_dataset.get('valuation_inputs') or valuation.get('status'), 'NA')}`；公告 `{_text(status_by_dataset.get('filings_announcements'), 'NA')}`",
            f"- Financial Quality：score `{_text(financial.get('score'), 'NA')}`；latest annual `{_text(financial.get('latest_annual_period'), 'NA')}`；label `{_text(financial.get('label'), 'NA')}`",
            f"- Valuation Inputs：stage `{_text(valuation.get('valuation_stage'), 'NA')}`；confidence `{_text(valuation.get('valuation_confidence'), 'NA')}`；market cap `{_text(valuation.get('total_market_cap'), 'NA')}` `{_text(valuation.get('currency'), '')}`",
            f"- Market Growth：implied `{_text(growth.get('market_implied_growth'), 'UNKNOWN')}`；evidence `{_text(growth.get('evidence_supported_growth'), 'UNKNOWN')}`；gap `{_text(growth.get('gap'), 'NA')}`",
            "",
            "### 技术与资本动作",
            f"- Technical：trend `{_text(technical.get('trend_state'), 'NA')}`；Chan action `{_text(technical.get('chan_action'), 'NA')}`；buy-point claim allowed `{_text(technical.get('buy_point_claim_allowed'), 'NA')}`",
            f"- Capital Actions：risk `{_text((capital.get('summary') or {}).get('material_risk_level') if isinstance(capital.get('summary'), Mapping) else None, 'NA')}`；actions `{_joined((capital.get('summary') or {}).get('action_types') if isinstance(capital.get('summary'), Mapping) else [], empty='none')}`",
            "",
            "### 待补证与 AI 研究任务",
            f"- Required Next Evidence：{_text(growth.get('required_next_evidence'), '待拆解')}",
            f"- Gate Reasons：{_joined(action_gate.get('blocking_reasons'), empty='none')}",
            *_research_debt_lines(company_debt),
        ])

    return "\n".join(lines)


def _render_from_report(report: Mapping[str, Any], *, mode: str) -> str:
    errors: list[str] = validate_comparison_report(report)
    if errors:
        raise ValueError("; ".join(errors))
    markdown: str = to_markdown(report)
    if mode == "quick_audit":
        return "\n".join([
            "> Quick audit mode: this is not a complete research report.",
            "",
            markdown,
        ])
    if mode == "full_research":
        return _render_full_research(report, comparison_markdown=markdown)
    return markdown


def render_report(
    *,
    manifests: Sequence[Path],
    comparison_report: Optional[Path] = None,
    mode: str = "candidate_comparison",
) -> str:
    if mode not in REPORT_MODES:
        raise ValueError(f"unknown report mode: {mode}")
    if comparison_report:
        loaded: Any = _load_json(comparison_report)
        if not isinstance(loaded, Mapping):
            raise ValueError("comparison report JSON must be an object")
        return _render_from_report(loaded, mode=mode)
    if len(manifests) < 2:
        raise ValueError("candidate comparison rendering requires at least two manifests")
    report: dict[str, Any] = build_comparison_report(manifests)
    return _render_from_report(report, mode=mode)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Render a full Serenity + Chan research report")
    parser.add_argument("manifests", nargs="*", help="manifest paths for candidate comparison")
    parser.add_argument("--comparison-report", help="existing comparison report JSON")
    parser.add_argument("--mode", choices=sorted(REPORT_MODES), default="candidate_comparison")
    parser.add_argument("--out", help="write Markdown to this path instead of stdout")
    args: argparse.Namespace = parser.parse_args(argv)

    try:
        manifest_paths: list[Path] = [Path(path) for path in args.manifests]
        report_path: Optional[Path] = Path(args.comparison_report) if args.comparison_report else None
        markdown: str = render_report(
            manifests=manifest_paths,
            comparison_report=report_path,
            mode=args.mode,
        )
        if args.out:
            Path(args.out).write_text(markdown + "\n", encoding="utf-8")
        else:
            print(markdown)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
