#!/usr/bin/env python3
"""Convert detected A-share capital actions into quantified impact tasks."""

from __future__ import annotations

import argparse
import json
import re
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
SHARE_UNITS: dict[str, float] = {"股": 1.0, "万股": 10000.0, "亿股": 100000000.0}
MONEY_UNITS: dict[str, float] = {"元": 1.0, "万元": 10000.0, "亿元": 100000000.0}


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


def _dilution_pct(action: Mapping[str, Any], base_shares: Optional[float] = None) -> Optional[float]:
    explicit: Optional[float] = _as_float(_field_value(action, "dilution_pct", "max_dilution_pct"))
    if explicit is not None:
        return explicit
    new_shares: Optional[float] = _as_float(_field_value(action, "new_shares", "incentive_shares", "unlock_shares"))
    resolved_base_shares: Optional[float] = _as_float(_field_value(action, "base_shares", "total_shares")) or base_shares
    if new_shares is None or resolved_base_shares in (None, 0):
        return None
    return round(new_shares / resolved_base_shares * 100.0, 4)


def _append_text(parts: list[str], value: Any) -> None:
    if isinstance(value, str) and value.strip():
        parts.append(value.strip())
    elif isinstance(value, list):
        for item in value:
            _append_text(parts, item)
    elif isinstance(value, Mapping):
        for key in ("title", "short_title", "summary", "abstract", "text", "pdf_text", "content"):
            _append_text(parts, value.get(key))


def _action_text(action: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("title", "summary", "abstract", "text", "pdf_text", "content"):
        _append_text(parts, action.get(key))
    for key in ("source_announcement", "source_announcements"):
        _append_text(parts, action.get(key))
    return "\n".join(dict.fromkeys(parts))


def _action_body_text(action: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("summary", "abstract", "text", "pdf_text", "content"):
        _append_text(parts, action.get(key))
    for key in ("source_announcement", "source_announcements"):
        value: Any = action.get(key)
        if isinstance(value, Mapping):
            for body_key in ("summary", "abstract", "text", "pdf_text", "content"):
                _append_text(parts, value.get(body_key))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, Mapping):
                    for body_key in ("summary", "abstract", "text", "pdf_text", "content"):
                        _append_text(parts, item.get(body_key))
    return "\n".join(dict.fromkeys(parts))


def _parse_number(text: str) -> Optional[float]:
    cleaned: str = text.replace(",", "").replace("，", "")
    try:
        return float(cleaned)
    except Exception:
        return None


def _extract_scaled_value(text: str, labels: Sequence[str], units: Mapping[str, float]) -> Optional[float]:
    unit_pattern: str = "|".join(sorted((re.escape(unit) for unit in units), key=len, reverse=True))
    label_pattern: str = "|".join(re.escape(label) for label in labels)
    pattern: str = rf"(?:{label_pattern})[^0-9０-９]{{0,30}}([0-9０-９][0-9０-９,，.]*)\s*({unit_pattern})"
    match: Optional[re.Match[str]] = re.search(pattern, text)
    if not match:
        return None
    raw_number: str = match.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    number: Optional[float] = _parse_number(raw_number)
    if number is None:
        return None
    return number * float(units.get(match.group(2), 1.0))


def _extract_plain_number(text: str, labels: Sequence[str], unit: str) -> Optional[float]:
    label_pattern: str = "|".join(re.escape(label) for label in labels)
    pattern: str = rf"(?:{label_pattern})[^0-9０-９]{{0,30}}([0-9０-９][0-9０-９,，.]*)\s*{re.escape(unit)}"
    match: Optional[re.Match[str]] = re.search(pattern, text)
    if not match:
        return None
    raw_number: str = match.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    return _parse_number(raw_number)


def _extract_pct(text: str, labels: Sequence[str]) -> Optional[float]:
    label_pattern: str = "|".join(re.escape(label) for label in labels)
    match: Optional[re.Match[str]] = re.search(rf"(?:{label_pattern})[^0-9０-９]{{0,30}}([0-9０-９][0-9０-９,，.]*)\s*%", text)
    if not match:
        return None
    raw_number: str = match.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    return _parse_number(raw_number)


def _extract_use_of_proceeds(text: str) -> list[str]:
    match: Optional[re.Match[str]] = re.search(r"募集资金(?:净额|总额)?(?:拟)?(?:将)?(?:全部|主要)?用于([^。；;\n]+)", text)
    if not match:
        return []
    raw: str = match.group(1)
    parts: list[str] = [item.strip(" ，,、及和") for item in re.split(r"[、,，；;]", raw) if item.strip(" ，,、及和")]
    return parts[:5]


def _extract_completion_status(text: str) -> Optional[str]:
    if any(keyword in text for keyword in ["已完成", "实施完毕", "回购完成", "减持完成"]):
        return "completed"
    if any(keyword in text for keyword in ["进展", "累计", "已回购", "已减持", "尚未完成"]):
        return "in_progress"
    if any(keyword in text for keyword in ["方案", "计划", "预案"]):
        return "planned"
    return None


def _merge_extracted_fields(action: Mapping[str, Any], base_shares: Optional[float]) -> tuple[dict[str, Any], list[str]]:
    merged: dict[str, Any] = dict(action)
    text: str = _action_text(action)
    body_text: str = _action_body_text(action)
    action_type: str = str(action.get("action_type") or "")
    extracted: dict[str, Any] = {}
    if not text:
        return merged, []
    if action_type in {"private_placement", "rights_issue", "h_share_listing"}:
        extracted["new_shares"] = _extract_scaled_value(text, ["发行数量", "发行股数", "新增股份数量", "拟发行数量", "配售股份数量"], SHARE_UNITS)
        extracted["issue_price"] = _extract_plain_number(text, ["发行价格", "发行价", "配股价格"], "元")
        extracted["lockup_months"] = _extract_plain_number(text, ["锁定期", "限售期"], "个月")
        if action_type == "rights_issue":
            ratio: Optional[float] = _extract_pct(text, ["配股比例", "认购比例"])
            if ratio is not None:
                extracted["subscription_ratio"] = ratio
        proceeds: list[str] = _extract_use_of_proceeds(text)
        if proceeds:
            extracted["use_of_proceeds"] = proceeds
    elif action_type == "convertible_bond":
        extracted["issue_amount"] = _extract_scaled_value(text, ["发行规模", "发行总额", "募集资金总额"], MONEY_UNITS)
        extracted["conversion_price"] = _extract_plain_number(text, ["转股价格", "初始转股价格"], "元")
        extracted["maturity_years"] = _extract_plain_number(text, ["存续期限", "期限"], "年")
        proceeds = _extract_use_of_proceeds(text)
        if proceeds:
            extracted["use_of_proceeds"] = proceeds
    elif action_type == "unlock":
        extracted["unlock_shares"] = _extract_scaled_value(text, ["解禁股份数量", "上市流通数量", "解除限售股份数量"], SHARE_UNITS)
        extracted["unlock_pct"] = _extract_pct(text, ["占总股本比例", "占公司总股本"])
        date_match: Optional[re.Match[str]] = re.search(r"(20\d{2}[-年]\d{1,2}[-月]\d{1,2}日?)", text)
        if date_match:
            extracted["unlock_date"] = date_match.group(1)
    elif action_type == "reduction":
        extracted["reduction_shares"] = _extract_scaled_value(text, ["减持数量", "拟减持股份数量", "计划减持股份数量"], SHARE_UNITS)
        extracted["reduction_pct"] = _extract_pct(text, ["减持比例", "占总股本比例"])
        role_match: Optional[re.Match[str]] = re.search(r"(控股股东|实际控制人|董事|监事|高级管理人员|持股5%以上股东|股东)", text)
        if role_match:
            extracted["seller_role"] = role_match.group(1)
        status: Optional[str] = _extract_completion_status(body_text) if body_text else None
        if status:
            extracted["completion_status"] = status
    elif action_type == "pledge":
        extracted["pledged_shares"] = _extract_scaled_value(text, ["质押股份数量", "本次质押数量", "累计质押数量"], SHARE_UNITS)
        extracted["pledge_pct"] = _extract_pct(text, ["占总股本比例", "质押比例"])
        pledgee_match: Optional[re.Match[str]] = re.search(r"质权人(?:为|：|:)?([^，,。；;\n]{2,30})", text)
        if pledgee_match:
            extracted["pledgee"] = pledgee_match.group(1).strip()
        date_match: Optional[re.Match[str]] = re.search(r"(20\d{2}[-年]\d{1,2}[-月]\d{1,2}日?)", text)
        if date_match:
            extracted["maturity_date"] = date_match.group(1)
    elif action_type == "buyback":
        extracted["buyback_amount"] = _extract_scaled_value(text, ["回购资金总额", "回购金额", "回购资金", "资金总额"], MONEY_UNITS)
        extracted["buyback_price_ceiling"] = _extract_plain_number(text, ["回购价格不超过", "回购价格上限", "不超过"], "元")
        status: Optional[str] = _extract_completion_status(body_text) if body_text else None
        if status:
            extracted["execution_progress"] = status
    elif action_type == "equity_incentive":
        extracted["incentive_shares"] = _extract_scaled_value(text, ["授予数量", "拟授予数量", "限制性股票数量"], SHARE_UNITS)
        extracted["dilution_pct"] = _extract_pct(text, ["占总股本比例", "摊薄比例"])
        vesting_match: Optional[re.Match[str]] = re.search(r"(解除限售条件|归属条件|业绩考核目标)[：:，,]?([^。；;\n]{2,80})", text)
        if vesting_match:
            extracted["vesting_conditions"] = vesting_match.group(2).strip()
        expense: Optional[float] = _extract_scaled_value(text, ["股份支付费用", "费用影响", "摊销费用"], MONEY_UNITS)
        if expense is not None:
            extracted["expense_impact"] = expense
    if base_shares is not None and merged.get("base_shares") in (None, "") and merged.get("total_shares") in (None, ""):
        merged["base_shares"] = base_shares
    applied: list[str] = []
    for key, value in extracted.items():
        if value in (None, "", []):
            continue
        if merged.get(key) in (None, "", []):
            merged[key] = value
            applied.append(key)
    return merged, sorted(applied)


def quantify_capital_actions(symbol: str, capital_actions: Mapping[str, Any], *, base_shares: Optional[float] = None) -> dict[str, Any]:
    raw_actions: Any = capital_actions.get("actions", [])
    actions: list[Mapping[str, Any]] = [item for item in raw_actions if isinstance(item, Mapping)] if isinstance(raw_actions, list) else []
    quantified: list[dict[str, Any]] = []
    for action in actions:
        enriched_action: dict[str, Any]
        extracted_fields: list[str]
        enriched_action, extracted_fields = _merge_extracted_fields(action, base_shares)
        action_type: str = str(enriched_action.get("action_type") or "")
        required_fields: list[str] = ACTION_REQUIRED_FIELDS.get(action_type, [])
        title: str = str(enriched_action.get("title") or "")
        missing_fields: list[str] = []
        for field in required_fields:
            value: Any = _field_value(enriched_action, field)
            if field == "use_of_proceeds":
                present: bool = bool(_list_value(value))
            else:
                present = value not in (None, "")
            if not present:
                missing_fields.append(field)

        dilution_pct: Optional[float] = _dilution_pct(enriched_action, base_shares=base_shares)
        new_shares: Optional[float] = _as_float(_field_value(enriched_action, "new_shares"))
        issue_price: Optional[float] = _as_float(_field_value(enriched_action, "issue_price"))
        lockup_months: Optional[float] = _as_float(_field_value(enriched_action, "lockup_months"))
        buyback_amount: Optional[float] = _as_float(_field_value(enriched_action, "buyback_amount"))
        reduction_pct: Optional[float] = _as_float(_field_value(enriched_action, "reduction_pct"))
        quantification_status: str
        if not required_fields:
            quantification_status = "NOT_REQUIRED"
        elif not missing_fields:
            quantification_status = "QUANTIFIED"
        elif len(missing_fields) < len(required_fields):
            quantification_status = "PARTIAL"
        else:
            quantification_status = "NEEDS_PDF_EXTRACTION"

        source_ref: str = str(enriched_action.get("pdf_url") or enriched_action.get("source_ref") or title)
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
            "event_id": str(enriched_action.get("event_id") or ""),
            "event_theme": str(enriched_action.get("event_theme") or ""),
            "source_count": int(enriched_action.get("source_count") or 1),
            "announcement_date": str(enriched_action.get("announcement_date") or ""),
            "source_ref": source_ref,
            "risk_level": str(enriched_action.get("risk_level") or ""),
            "new_shares": new_shares,
            "base_shares": _as_float(_field_value(enriched_action, "base_shares", "total_shares")) or base_shares,
            "dilution_pct": dilution_pct,
            "issue_price": issue_price,
            "lockup_months": lockup_months,
            "buyback_amount": buyback_amount,
            "buyback_price_ceiling": _as_float(_field_value(enriched_action, "buyback_price_ceiling")),
            "reduction_shares": _as_float(_field_value(enriched_action, "reduction_shares")),
            "reduction_pct": reduction_pct,
            "use_of_proceeds": _list_value(_field_value(enriched_action, "use_of_proceeds")),
            "extraction_status": "FIELD_EXTRACTED" if extracted_fields else "NO_TEXT_FIELD_EXTRACTED",
            "extracted_fields": extracted_fields,
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
