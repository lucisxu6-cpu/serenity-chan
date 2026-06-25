#!/usr/bin/env python3
"""Detect material A-share capital actions from CNINFO announcement metadata."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


ACTION_RULES = [
    {
        "action_type": "private_placement",
        "keywords": ["向特定对象发行", "非公开发行", "发行A股股票", "发行股票发行情况", "发行过程和认购对象", "验资报告"],
        "exclude_keywords": ["持续督导", "保荐总结报告书"],
        "risk_level": "medium_high",
        "market_payoff_effect": "升级赔率前必须量化稀释和募资用途。",
        "action_readiness_effect": "发行规模、价格、锁定期和募资用途复核前保持行动门控。",
        "research_debt": "阅读发行预案或发行情况报告 PDF，量化新增股份、发行价格、募资用途和锁定期。",
    },
    {
        "action_type": "convertible_bond",
        "keywords": ["可转换公司债券", "可转债"],
        "risk_level": "medium",
        "market_payoff_effect": "潜在转股稀释和债务条款需要进入估值调整。",
        "action_readiness_effect": "转股价、规模、期限和募资用途复核前不升级行动状态。",
        "research_debt": "阅读可转债文件，量化转股稀释和融资目的。",
    },
    {
        "action_type": "rights_issue",
        "keywords": ["配股"],
        "risk_level": "medium_high",
        "market_payoff_effect": "配股可能改变每股价值和短期筹码供给。",
        "action_readiness_effect": "配股比例、价格、认购条款和募资用途复核前不升级行动状态。",
        "research_debt": "阅读配股文件，量化每股稀释和融资必要性。",
    },
    {
        "action_type": "unlock",
        "keywords": ["限售股上市流通", "解除限售", "解禁"],
        "risk_level": "medium",
        "market_payoff_effect": "限售股释放可能压低事件窗口赔率。",
        "action_readiness_effect": "解禁规模、自由流通盘和近期成交额复核前不升级行动状态。",
        "research_debt": "量化解禁股份、自由流通占比和事件窗口流动性。",
    },
    {
        "action_type": "reduction",
        "keywords": ["减持", "减持股份计划"],
        "risk_level": "medium",
        "market_payoff_effect": "内部人或重要股东减持会压低近端赔率质量。",
        "action_readiness_effect": "需要复核减持方身份、计划数量、时间窗口和动机。",
        "research_debt": "量化减持规模、减持方角色、价格约束和完成进度。",
    },
    {
        "action_type": "pledge",
        "keywords": ["质押", "解除质押"],
        "risk_level": "medium",
        "market_payoff_effect": "质押风险会影响治理和被动卖出风险评估。",
        "action_readiness_effect": "需要复核质押比例、质权人、期限和补仓风险。",
        "research_debt": "从公告 PDF 量化质押比例和被动卖出风险。",
    },
    {
        "action_type": "buyback",
        "keywords": ["回购"],
        "risk_level": "supportive",
        "market_payoff_effect": "回购规模和价格纪律足够时可支撑每股价值。",
        "action_readiness_effect": "需要复核规模、价格区间、资金来源和完成进度。",
        "research_debt": "阅读回购方案和进展公告，确认规模与执行。",
    },
    {
        "action_type": "equity_incentive",
        "keywords": ["股权激励", "限制性股票", "股票激励"],
        "risk_level": "low",
        "market_payoff_effect": "激励条款可能强化执行一致性，但需要检查稀释和解锁目标。",
        "action_readiness_effect": "需要复核授予规模、解锁目标、费用影响和稀释。",
        "research_debt": "阅读股权激励方案，量化稀释、解锁门槛和费用影响。",
    },
    {
        "action_type": "h_share_listing",
        "keywords": ["发行H股", "H股股票", "香港联合交易所", "境外上市"],
        "risk_level": "medium",
        "market_payoff_effect": "境外上市可能改变资本基数、流动性和估值比较口径。",
        "action_readiness_effect": "需要复核发行规模、上市阶段、募资用途和 A/H 估值影响。",
        "research_debt": "阅读 H 股上市公告，量化资本基数和流动性影响。",
    },
]

RISK_ORDER = {"none": 0, "supportive": 0, "low": 1, "medium": 2, "medium_high": 3, "high": 4}


def _announcements(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in ("recent_announcements", "announcements", "reports"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
    return []


def analyze_announcements(payload: Any) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for announcement in _announcements(payload):
        title = str(announcement.get("title") or announcement.get("short_title") or "")
        if not title:
            continue
        for rule in ACTION_RULES:
            if not any(keyword in title for keyword in rule["keywords"]):
                continue
            if any(keyword in title for keyword in rule.get("exclude_keywords", [])):
                continue
            key = (str(rule["action_type"]), str(announcement.get("announcement_id") or title))
            if key in seen:
                continue
            seen.add(key)
            actions.append({
                "action_type": rule["action_type"],
                "title": title,
                "announcement_date": str(announcement.get("announcement_date") or ""),
                "pdf_url": str(announcement.get("pdf_url") or ""),
                "risk_level": rule["risk_level"],
                "market_payoff_effect": rule["market_payoff_effect"],
                "action_readiness_effect": rule["action_readiness_effect"],
                "research_debt": rule["research_debt"],
            })

    risk_level = "none"
    for action in actions:
        level = str(action.get("risk_level") or "none")
        if RISK_ORDER.get(level, 0) > RISK_ORDER.get(risk_level, 0):
            risk_level = level
    action_types = sorted({str(action["action_type"]) for action in actions})
    research_debt = list(dict.fromkeys(str(action["research_debt"]) for action in actions if action.get("research_debt")))
    has_dilution = any(action_type in {"private_placement", "convertible_bond", "rights_issue", "equity_incentive", "h_share_listing"} for action_type in action_types)

    return {
        "summary": {
            "action_count": len(actions),
            "material_action_count": sum(1 for action in actions if RISK_ORDER.get(str(action.get("risk_level")), 0) >= 2),
            "material_risk_level": risk_level,
            "action_types": action_types,
            "has_dilution_event": has_dilution,
        },
        "actions": actions,
        "research_debt": research_debt,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Detect A-share capital actions from announcement metadata")
    parser.add_argument("announcements_json")
    args = parser.parse_args(argv)
    try:
        payload = json.loads(Path(args.announcements_json).read_text(encoding="utf-8"))
        print(json.dumps(analyze_announcements(payload), ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
