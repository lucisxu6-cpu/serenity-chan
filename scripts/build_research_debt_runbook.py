#!/usr/bin/env python3
"""Build field-level runbooks from open research debt rows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


RUNBOOK_TEMPLATES: dict[str, dict[str, Any]] = {
    "serenity_layer": {
        "axis": "AI_RESEARCH",
        "blocking_level": "rating_and_action",
        "preferred_sources": ["年报", "中报", "投资者演示材料", "官方公告"],
        "fallback_sources": ["交易所问答", "业绩会纪要", "公司官网"],
        "validation_target": ["产业链层级", "瓶颈论点", "收入传导", "反证"],
        "expected_effect_if_resolved": "AI overlay 可以用证据支持的 thesis 替换未映射层级。",
    },
    "valuation_growth": {
        "axis": "VALUATION_EVIDENCE",
        "blocking_level": "rating",
        "preferred_sources": ["年报", "季报", "分部收入", "订单披露", "产能披露"],
        "fallback_sources": ["L1 纪要", "官方投资者沟通"],
        "validation_target": ["分部收入", "客户/订单证据", "产能利用率", "毛利率持续性"],
        "expected_effect_if_resolved": "证据支持增长可以升级，或继续明确估值缺口。",
    },
    "capital_actions": {
        "axis": "CAPITAL_STRUCTURE",
        "blocking_level": "action",
        "preferred_sources": ["巨潮公告 PDF", "上市公告", "发行结果公告"],
        "fallback_sources": ["交易所公告索引", "公司公告镜像"],
        "validation_target": ["股份数量", "发行价格", "锁定期", "募资用途", "完成状态"],
        "expected_effect_if_resolved": "资本动作门控可以从未量化推进到可量化的赔率/行动影响。",
    },
    "capital_action_quantification": {
        "axis": "CAPITAL_STRUCTURE",
        "blocking_level": "action",
        "preferred_sources": ["资本动作 PDF", "发行结果公告", "回购进展公告"],
        "fallback_sources": ["交易所公告摘要"],
        "validation_target": ["缺失量化字段", "摊薄比例", "价格", "锁定期", "执行进展"],
        "expected_effect_if_resolved": "资本动作影响从泛化研究债务变成可度量字段。",
    },
    "financials": {
        "axis": "FUNDAMENTALS",
        "blocking_level": "rating_and_action",
        "preferred_sources": ["L0 财报", "审计年报"],
        "fallback_sources": ["L1 官方转写"],
        "validation_target": ["收入", "利润", "现金流", "资产", "负债", "权益"],
        "expected_effect_if_resolved": "财务质量矩阵和增长矩阵可以升级为可信输入。",
    },
    "price_history_adjusted": {
        "axis": "ACTION_TIMING",
        "blocking_level": "action",
        "preferred_sources": ["复权日线", "官方除权除息事件"],
        "fallback_sources": ["二级行情数据商"],
        "validation_target": ["250 根以上复权 K 线", "复权依据", "趋势状态", "缠论动作"],
        "expected_effect_if_resolved": "买点判断可以脱离数据门控并进入结构评估。",
    },
}


def _template(dataset: str) -> dict[str, Any]:
    return dict(RUNBOOK_TEMPLATES.get(dataset, {
        "axis": "RESEARCH",
        "blocking_level": "research_priority",
        "preferred_sources": ["L0/L1 来源"],
        "fallback_sources": ["可审计二级来源"],
        "validation_target": ["开放论点", "来源等级", "决策影响"],
        "expected_effect_if_resolved": "开放研究债务可以清除或降级。",
    }))


def build_runbook_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    debts: Any = report.get("research_debt", [])
    if not isinstance(debts, list):
        return rows
    for debt in debts:
        if not isinstance(debt, Mapping):
            continue
        dataset: str = str(debt.get("dataset") or debt.get("task_type") or "research")
        template: dict[str, Any] = _template(dataset)
        rows.append({
            "symbol": str(debt.get("symbol") or ""),
            "dataset": dataset,
            "priority": str(debt.get("priority") or "medium"),
            "gap_type": str(debt.get("gap_type") or dataset.upper()),
            "decision_impact": str(debt.get("decision_impact") or "RESEARCH_IMPACT"),
            "next_action": str(debt.get("next_action") or debt.get("objective") or ""),
            "axis": template["axis"],
            "blocking_level": template["blocking_level"],
            "preferred_sources": list(template["preferred_sources"]),
            "fallback_sources": list(template["fallback_sources"]),
            "validation_target": list(template["validation_target"]),
            "expected_effect_if_resolved": str(template["expected_effect_if_resolved"]),
        })
    return rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Build a research-debt runbook from comparison report JSON")
    parser.add_argument("comparison_report")
    parser.add_argument("--out")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        payload: Any = json.loads(Path(args.comparison_report).read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("comparison report JSON must be an object")
        rows: list[dict[str, Any]] = build_runbook_rows(payload)
        text: str = json.dumps(rows, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
