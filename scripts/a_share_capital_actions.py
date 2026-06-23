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
        "market_payoff_effect": "Dilution and use-of-proceeds must be quantified before upgrading payoff.",
        "action_readiness_effect": "Keep action readiness gated until issuance size, price, lock-up, and proceeds use are reviewed.",
        "research_debt": "Read the issuance plan or issuance report PDF and quantify share increase, issue price, proceeds use, and lock-up terms.",
    },
    {
        "action_type": "convertible_bond",
        "keywords": ["可转换公司债券", "可转债"],
        "risk_level": "medium",
        "market_payoff_effect": "Potential dilution and debt-service terms require valuation adjustment.",
        "action_readiness_effect": "Review conversion price, scale, maturity, and use of proceeds before action upgrade.",
        "research_debt": "Read the convertible-bond documents and quantify conversion dilution and financing purpose.",
    },
    {
        "action_type": "rights_issue",
        "keywords": ["配股"],
        "risk_level": "medium_high",
        "market_payoff_effect": "Rights issue may change per-share value and near-term supply.",
        "action_readiness_effect": "Review issue ratio, price, subscription terms, and proceeds use.",
        "research_debt": "Read the rights-issue documents and quantify per-share dilution and financing need.",
    },
    {
        "action_type": "unlock",
        "keywords": ["限售股上市流通", "解除限售", "解禁"],
        "risk_level": "medium",
        "market_payoff_effect": "Supply release can pressure payoff during the event window.",
        "action_readiness_effect": "Map unlock scale to free-float and recent turnover before action upgrade.",
        "research_debt": "Quantify unlocked shares, free-float share, and event-window liquidity.",
    },
    {
        "action_type": "reduction",
        "keywords": ["减持", "减持股份计划"],
        "risk_level": "medium",
        "market_payoff_effect": "Insider or shareholder selling can lower near-term payoff quality.",
        "action_readiness_effect": "Review seller identity, planned amount, timing, and motive.",
        "research_debt": "Quantify reduction scale, seller role, price constraints, and completion progress.",
    },
    {
        "action_type": "pledge",
        "keywords": ["质押", "解除质押"],
        "risk_level": "medium",
        "market_payoff_effect": "Pledge risk affects governance and forced-selling risk assessment.",
        "action_readiness_effect": "Review pledge ratio, pledgee, maturity, and margin-call exposure.",
        "research_debt": "Quantify pledge ratio and forced-selling risk from the announcement PDF.",
    },
    {
        "action_type": "buyback",
        "keywords": ["回购"],
        "risk_level": "supportive",
        "market_payoff_effect": "Buyback can support per-share value if scale and price discipline are meaningful.",
        "action_readiness_effect": "Verify scale, price range, funding source, and completion progress.",
        "research_debt": "Read the buyback plan and completion announcements to confirm scale and execution.",
    },
    {
        "action_type": "equity_incentive",
        "keywords": ["股权激励", "限制性股票", "股票激励"],
        "risk_level": "low",
        "market_payoff_effect": "Incentive terms can align execution, while dilution and exercise targets must be checked.",
        "action_readiness_effect": "Review grant size, vesting targets, expense impact, and dilution.",
        "research_debt": "Read the incentive plan and quantify dilution, vesting hurdles, and expense impact.",
    },
    {
        "action_type": "h_share_listing",
        "keywords": ["发行H股", "H股股票", "香港联合交易所", "境外上市"],
        "risk_level": "medium",
        "market_payoff_effect": "Offshore listing may change capital base, liquidity, and valuation comparison.",
        "action_readiness_effect": "Review issuance scale, listing stage, use of proceeds, and A/H valuation effects.",
        "research_debt": "Read the H-share listing announcement and quantify capital-base and liquidity effects.",
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
