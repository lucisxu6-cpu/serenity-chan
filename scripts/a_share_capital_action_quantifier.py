#!/usr/bin/env python3
"""Convert detected A-share capital actions into quantified impact tasks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


ACTION_REQUIRED_FIELDS: dict[str, list[str]] = {
    "private_placement": ["new_shares", "issue_price", "lockup_months", "use_of_proceeds"],
    "convertible_bond": ["issue_amount", "conversion_price", "maturity_years", "use_of_proceeds"],
    "rights_issue": ["new_shares", "issue_price", "subscription_ratio", "use_of_proceeds"],
    "unlock": ["unlock_shares", "unlock_pct", "unlock_date"],
    "reduction": ["reduction_shares", "reduction_pct", "seller_role", "completion_status"],
    "pledge": ["pledged_shares", "pledge_pct", "pledgee", "maturity_date"],
    "buyback": ["buyback_amount", "buyback_price_ceiling", "execution_progress"],
    "equity_incentive": ["incentive_shares", "dilution_pct", "vesting_conditions", "expense_impact"],
    "h_share_listing": ["new_shares", "issue_price", "listing_stage", "use_of_proceeds"],
}

DILUTION_ACTIONS: set[str] = {"private_placement", "convertible_bond", "rights_issue", "equity_incentive", "h_share_listing"}


def _as_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        number: float = float(str(value).replace(",", "").replace("%", ""))
    except Exception:
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _list_value(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value in (None, ""):
        return []
    return [str(value).strip()]


def _field_value(action: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if action.get(name) not in (None, ""):
            return action.get(name)
    return None


def _dilution_pct(action: Mapping[str, Any]) -> Optional[float]:
    explicit: Optional[float] = _as_float(_field_value(action, "dilution_pct", "max_dilution_pct"))
    if explicit is not None:
        return explicit
    new_shares: Optional[float] = _as_float(_field_value(action, "new_shares", "incentive_shares", "unlock_shares"))
    base_shares: Optional[float] = _as_float(_field_value(action, "base_shares", "total_shares"))
    if new_shares is None or base_shares in (None, 0):
        return None
    return round(new_shares / base_shares * 100.0, 4)


def quantify_capital_actions(symbol: str, capital_actions: Mapping[str, Any]) -> dict[str, Any]:
    raw_actions: Any = capital_actions.get("actions", [])
    actions: list[Mapping[str, Any]] = [item for item in raw_actions if isinstance(item, Mapping)] if isinstance(raw_actions, list) else []
    quantified: list[dict[str, Any]] = []
    for action in actions:
        action_type: str = str(action.get("action_type") or "")
        required_fields: list[str] = ACTION_REQUIRED_FIELDS.get(action_type, [])
        title: str = str(action.get("title") or "")
        missing_fields: list[str] = []
        for field in required_fields:
            value: Any = _field_value(action, field)
            if field == "use_of_proceeds":
                present: bool = bool(_list_value(value))
            else:
                present = value not in (None, "")
            if not present:
                missing_fields.append(field)

        dilution_pct: Optional[float] = _dilution_pct(action)
        new_shares: Optional[float] = _as_float(_field_value(action, "new_shares"))
        issue_price: Optional[float] = _as_float(_field_value(action, "issue_price"))
        lockup_months: Optional[float] = _as_float(_field_value(action, "lockup_months"))
        buyback_amount: Optional[float] = _as_float(_field_value(action, "buyback_amount"))
        reduction_pct: Optional[float] = _as_float(_field_value(action, "reduction_pct"))
        quantification_status: str
        if not required_fields:
            quantification_status = "NOT_REQUIRED"
        elif not missing_fields:
            quantification_status = "QUANTIFIED"
        elif len(missing_fields) < len(required_fields):
            quantification_status = "PARTIAL"
        else:
            quantification_status = "NEEDS_PDF_EXTRACTION"

        source_ref: str = str(action.get("pdf_url") or action.get("source_ref") or title)
        next_verification: str = (
            f"从公告 PDF 抽取 {', '.join(missing_fields)}。"
            if missing_fields
            else "复核字段与公告正文、股本基数和后续进展是否一致。"
        )
        impact_on_action: str = "CAPITAL_ACTION_GATED" if quantification_status in {"PARTIAL", "NEEDS_PDF_EXTRACTION"} else "RESEARCH_VALIDATION"
        impact_on_payoff: str = (
            "dilution_or_liquidity_unknown"
            if action_type in DILUTION_ACTIONS and quantification_status != "QUANTIFIED"
            else "support_or_supply_effect_quantified"
            if quantification_status == "QUANTIFIED"
            else "monitor_required"
        )
        quantified.append({
            "symbol": symbol,
            "action_type": action_type,
            "title": title,
            "announcement_date": str(action.get("announcement_date") or ""),
            "source_ref": source_ref,
            "risk_level": str(action.get("risk_level") or ""),
            "new_shares": new_shares,
            "base_shares": _as_float(_field_value(action, "base_shares", "total_shares")),
            "dilution_pct": dilution_pct,
            "issue_price": issue_price,
            "lockup_months": lockup_months,
            "buyback_amount": buyback_amount,
            "buyback_price_ceiling": _as_float(_field_value(action, "buyback_price_ceiling")),
            "reduction_shares": _as_float(_field_value(action, "reduction_shares")),
            "reduction_pct": reduction_pct,
            "use_of_proceeds": _list_value(_field_value(action, "use_of_proceeds")),
            "quantification_status": quantification_status,
            "missing_fields": missing_fields,
            "impact_on_payoff": impact_on_payoff,
            "impact_on_action": impact_on_action,
            "next_verification": next_verification,
        })

    quantified_count: int = sum(1 for item in quantified if item.get("quantification_status") == "QUANTIFIED")
    requires_count: int = sum(1 for item in quantified if item.get("quantification_status") in {"PARTIAL", "NEEDS_PDF_EXTRACTION"})
    dilution_values: list[float] = [
        float(item["dilution_pct"])
        for item in quantified
        if item.get("dilution_pct") is not None
    ]
    summary: dict[str, Any] = {
        "symbol": symbol,
        "action_count": len(quantified),
        "quantified_action_count": quantified_count,
        "requires_quantification_count": requires_count,
        "max_dilution_pct": max(dilution_values) if dilution_values else None,
        "quantification_status": "OK" if requires_count == 0 else "PARTIAL",
        "impact_on_action": "CAPITAL_ACTION_GATED" if requires_count else "RESEARCH_VALIDATION",
    }
    return {
        "symbol": symbol,
        "summary": summary,
        "actions": quantified,
        "research_debt": [
            item["next_verification"]
            for item in quantified
            if item.get("quantification_status") in {"PARTIAL", "NEEDS_PDF_EXTRACTION"}
        ],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Quantify detected A-share capital actions")
    parser.add_argument("capital_actions_json")
    parser.add_argument("--symbol", default="")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        payload: Any = json.loads(Path(args.capital_actions_json).read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("capital actions JSON must be an object")
        symbol: str = args.symbol or str(payload.get("symbol") or "")
        print(json.dumps(quantify_capital_actions(symbol, payload), ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
