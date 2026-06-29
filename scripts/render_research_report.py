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
    from report_labels import display_bool, display_label, display_list, display_mapping_pairs
except ModuleNotFoundError:  # pragma: no cover
    from scripts.build_comparison_report import build_comparison_report, to_markdown, validate_comparison_report
    from scripts.report_labels import display_bool, display_label, display_list, display_mapping_pairs


REPORT_MODES: set[str] = {"candidate_comparison", "full_research", "quick_audit"}
FORMAL_REPORT_MODES: set[str] = {"candidate_comparison", "full_research"}
FORMAL_AI_STATUSES: set[str] = {"COMPLETED", "FAILED_INSUFFICIENT_EVIDENCE", "CONFLICT_WITH_DATA"}


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
        items: list[str] = [display_label(item) for item in value if str(item).strip()]
        return ", ".join(items) if items else empty
    return display_label(value, empty)


def _ensure_formal_delivery(report: Mapping[str, Any], *, mode: str) -> None:
    if mode not in FORMAL_REPORT_MODES:
        return
    readiness: Mapping[str, Any] = report.get("report_readiness") if isinstance(report.get("report_readiness"), Mapping) else {}
    if readiness.get("stage") != "FINAL_REPORT_READY" or readiness.get("delivery_allowed") is not True:
        raise ValueError("formal report rendering requires FINAL_REPORT_READY readiness")
    blocked_statuses: list[str] = []
    for row in report.get("ai_review_status_matrix", []) if isinstance(report.get("ai_review_status_matrix"), list) else []:
        if not isinstance(row, Mapping):
            continue
        status: str = str(row.get("ai_review_status") or "")
        if status not in FORMAL_AI_STATUSES:
            blocked_statuses.append(status)
    if blocked_statuses:
        raise ValueError(f"formal report rendering blocked by AI statuses: {sorted(set(blocked_statuses))}")


def _gate_class_summary(value: Any, *, empty: str = "none") -> str:
    return display_mapping_pairs(value, empty=empty)


def _research_debt_lines(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    if not rows:
        return ["- 当前未记录高优先级研究债务。"]
    lines: list[str] = []
    for row in rows:
        dataset: str = display_label(row.get("dataset") or row.get("task_type"), "未知数据集")
        priority: str = display_label(row.get("priority"), "未知优先级")
        impact: str = display_label(row.get("decision_impact"), "未标注影响")
        action: str = _text(row.get("next_action") or row.get("objective"), "待补充验证动作")
        lines.append(f"- `{dataset}` [{priority}/{impact}]：{action}")
    return lines


def _render_full_research(report: Mapping[str, Any], *, comparison_markdown: str) -> str:
    candidates: list[Mapping[str, Any]] = _mapping_rows(report.get("candidates"))
    data_rows: dict[str, Mapping[str, Any]] = _first_by_symbol(_mapping_rows(report.get("data_acquisition_summary")))
    layer_rows: dict[str, Mapping[str, Any]] = _first_by_symbol(_mapping_rows(report.get("serenity_layer_matrix")))
    ai_review_rows: dict[str, Mapping[str, Any]] = _first_by_symbol(_mapping_rows(report.get("ai_review_status_matrix")))
    financial_rows: dict[str, Mapping[str, Any]] = _first_by_symbol(_mapping_rows(report.get("financial_quality_matrix")))
    valuation_rows: dict[str, Mapping[str, Any]] = _first_by_symbol(_mapping_rows(report.get("valuation_input_matrix")))
    currency_rows: dict[str, Mapping[str, Any]] = _first_by_symbol(_mapping_rows(report.get("currency_normalization_matrix")))
    growth_rows: dict[str, Mapping[str, Any]] = _first_by_symbol(_mapping_rows(report.get("growth_hypothesis_matrix")))
    technical_rows: dict[str, Mapping[str, Any]] = _first_by_symbol(_mapping_rows(report.get("technical_timing_matrix")))
    capital_rows: dict[str, Mapping[str, Any]] = _first_by_symbol(_mapping_rows(report.get("capital_actions")))
    capital_quantification_rows: dict[str, Mapping[str, Any]] = _first_by_symbol(_mapping_rows(report.get("capital_action_quantification")))
    ranking_rows: dict[str, Mapping[str, Any]] = _first_by_symbol(_mapping_rows(report.get("candidate_priority_ranking")))
    readiness_rows: dict[str, Mapping[str, Any]] = _first_by_symbol(_mapping_rows(report.get("readiness_matrix")))
    debt_rows: dict[str, list[Mapping[str, Any]]] = _many_by_symbol(_mapping_rows(report.get("research_debt")))
    runbook_rows: dict[str, list[Mapping[str, Any]]] = _many_by_symbol(_mapping_rows(report.get("research_debt_runbook")))

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
        ai_review: Mapping[str, Any] = ai_review_rows.get(symbol, {})
        financial: Mapping[str, Any] = financial_rows.get(symbol, {})
        valuation: Mapping[str, Any] = valuation_rows.get(symbol, {})
        currency: Mapping[str, Any] = currency_rows.get(symbol, {})
        growth: Mapping[str, Any] = growth_rows.get(symbol, {})
        technical: Mapping[str, Any] = technical_rows.get(symbol, {})
        capital: Mapping[str, Any] = capital_rows.get(symbol, {})
        capital_quantification: Mapping[str, Any] = capital_quantification_rows.get(symbol, {})
        ranking: Mapping[str, Any] = ranking_rows.get(symbol, {})
        readiness: Mapping[str, Any] = readiness_rows.get(symbol, {})
        action_gate: Mapping[str, Any] = ranking.get("action_gate") if isinstance(ranking.get("action_gate"), Mapping) else {}
        status_by_dataset: Mapping[str, Any] = data.get("status_by_dataset") if isinstance(data.get("status_by_dataset"), Mapping) else {}
        company_debt: list[Mapping[str, Any]] = debt_rows.get(symbol, [])
        company_runbook: list[Mapping[str, Any]] = runbook_rows.get(symbol, [])
        sector_fallback: Mapping[str, Any] = financial.get("financial_sector_profile_fallback") if isinstance(financial.get("financial_sector_profile_fallback"), Mapping) else {}
        runbook_lines: list[str] = [
            f"- `{display_label(row.get('dataset'), '未知数据集')}`：{row.get('next_action', '')}；验证 {display_list(row.get('validation_target', []), empty='待补充')}；优先源 {display_list(row.get('preferred_sources', []), empty='待补充')}；完成后 {row.get('expected_effect_if_resolved', '')}"
            for row in company_runbook
        ]
        if not runbook_lines:
            runbook_lines = ["- 当前未生成字段级 runbook。"]

        lines.extend([
            "",
            f"## {symbol} {name}".rstrip(),
            "",
            "### 研究状态",
            f"- 数据证据上限：`{_text(candidate.get('rating_cap'), 'OBSERVE_ONLY')}`，这是数据/证据允许的最高研究上限，不等于投资评级。",
            f"- 三层状态：数据获取 `{display_label(readiness.get('fetch_status'), 'NA')}`；研究状态 `{display_label(readiness.get('research_readiness'), 'NA')}`；行动状态 `{display_label(readiness.get('action_readiness'), 'NA')}`；可形成结论 `{display_bool(readiness.get('decision_grade'))}`",
            f"- 研究优先级：`{_text(ranking.get('research_priority_score'), 'NA')}`；行动优先级：`{_text(ranking.get('action_priority_score'), 'NA')}`；行动状态：`{display_label(ranking.get('action_readiness'), 'NA')}`",
            f"- 主门控：`{display_label(action_gate.get('primary_gate'), 'NA')}`；门控类别：`{display_label(action_gate.get('primary_gate_class'), 'NA')}`",
            f"- 门控类别明细：{_gate_class_summary(action_gate.get('gate_classes'), empty='无')}",
            f"- AI 研究状态：`{display_label(ai_review.get('ai_review_status'), 'NA')}`；Overlay 已合并 `{display_bool(ai_review.get('overlay_merged'))}`；证据数 `{_text(ai_review.get('evidence_ref_count'), '0')}`",
            f"- 数据包：`{_text(candidate.get('data_package_path'), 'NA')}`",
            "",
            "### Serenity 研究假设",
            f"- 产业链层级：{display_label(layer.get('layer'), '待价值链映射')}",
            f"- 瓶颈判断：{_text(layer.get('bottleneck_reason'), '待验证')}",
            f"- 收入传导：{_text(layer.get('revenue_transmission'), '待验证')}",
            f"- 证据缺口：{_text(layer.get('evidence_gap'), '待补证')}",
            "",
            "### 财务与估值",
            f"- 数据获取：财报 `{display_label(status_by_dataset.get('financials'), 'NA')}`；估值输入 `{display_label(status_by_dataset.get('valuation_inputs') or valuation.get('status'), 'NA')}`；公告 `{display_label(status_by_dataset.get('filings_announcements'), 'NA')}`",
            f"- 财务质量：分数 `{_text(financial.get('score'), 'NA')}`；最新年报 `{_text(financial.get('latest_annual_period'), 'NA')}`；预检标签 `{display_label(financial.get('label'), 'NA')}`",
            f"- 财务金额口径：披露单位 `{_text(financial.get('financial_statement_unit'), 'NA')}`；倍率 `{_text(financial.get('financial_unit_multiplier'), 'NA')}`；收入绝对额 `{_text(financial.get('revenue_absolute'), 'NA')}`；净利润绝对额 `{_text(financial.get('net_income_absolute'), 'NA')}`",
            f"- 金融行业专项 profile：需要 `{display_bool(financial.get('financial_sector_profile_required'))}`；状态 `{display_label(financial.get('financial_sector_profile_status'), '不适用')}`；历史 profile 可用 `{display_bool(sector_fallback.get('available'))}` `{_text(sector_fallback.get('fallback_period'), '')}`",
            f"- 估值输入：阶段 `{display_label(valuation.get('valuation_stage'), 'NA')}`；置信度 `{display_label(valuation.get('valuation_confidence'), 'NA')}`；总市值 `{_text(valuation.get('total_market_cap'), 'NA')}` `{_text(valuation.get('currency'), '')}`",
            f"- 币种归一：`{display_label(currency.get('normalization_status'), 'NA')}`；`{_text(currency.get('source_currency'), '')}` → `{_text(currency.get('target_currency'), '')}`；归一后总市值 `{_text(currency.get('normalized_total_market_cap'), 'NA')}`",
            f"- 市场隐含增长：市场 `{display_label(growth.get('market_implied_growth'), '未知')}`；证据 `{display_label(growth.get('evidence_supported_growth'), '未知')}`；缺口 `{display_label(growth.get('gap'), 'NA')}`；预检 PE `{_text(growth.get('pe_preflight'), 'NA')}`；预检 PS `{_text(growth.get('ps_preflight'), 'NA')}`",
            "",
            "### 技术与资本动作",
            f"- 技术状态：历史深度 `{display_label(technical.get('history_depth_status'), 'NA')}`；趋势 `{display_label(technical.get('trend_state'), 'NA')}`；缠论动作 `{display_label(technical.get('chan_action'), 'NA')}`；允许买点判断 `{display_bool(technical.get('buy_point_claim_allowed'))}`",
            f"- 资本动作：风险 `{display_label((capital.get('summary') or {}).get('material_risk_level') if isinstance(capital.get('summary'), Mapping) else None, 'NA')}`；动作 `{display_list((capital.get('summary') or {}).get('action_types') if isinstance(capital.get('summary'), Mapping) else [], empty='无')}`",
            f"- 资本动作量化：状态 `{display_label((capital_quantification.get('summary') or {}).get('quantification_status') if isinstance(capital_quantification.get('summary'), Mapping) else None, 'NA')}`；需量化 `{_text((capital_quantification.get('summary') or {}).get('requires_quantification_count') if isinstance(capital_quantification.get('summary'), Mapping) else None, '0')}` 项；行动影响 `{display_label((capital_quantification.get('summary') or {}).get('impact_on_action') if isinstance(capital_quantification.get('summary'), Mapping) else None, 'NA')}`",
            "",
            "### 待补证与 AI 研究任务",
            f"- 下一步关键证据：{_text(growth.get('required_next_evidence'), '待拆解')}",
            f"- 门控原因：{_joined(action_gate.get('blocking_reasons'), empty='无')}",
            *_research_debt_lines(company_debt),
            "### 执行 Runbook",
            *runbook_lines,
        ])

    return "\n".join(lines)


def _render_from_report(report: Mapping[str, Any], *, mode: str) -> str:
    errors: list[str] = validate_comparison_report(report)
    if errors:
        raise ValueError("; ".join(errors))
    _ensure_formal_delivery(report, mode=mode)
    markdown: str = to_markdown(report)
    if mode == "quick_audit":
        return "\n".join([
            "> 快速审计模式：这不是完整研究报告。",
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
