#!/usr/bin/env python3
"""Chinese-first labels for human-facing Serenity + Chan reports."""

from __future__ import annotations

from typing import Any, Mapping


DISPLAY_LABELS: dict[str, str] = {
    "ACCESS_FAILURE": "访问失败",
    "ACTION_TIMING": "行动时机",
    "AI_REVIEW_GATED": "AI 研究门控",
    "BASE_BUILDING_WATCH": "筑底观察",
    "BUY_POINT_GATED": "买点门控",
    "CANDIDATE_POOL": "候选池",
    "CAPITAL_ACTION_GATED": "资本动作门控",
    "CAPITAL_ACTIONS": "资本动作",
    "CLEAR_TOP_CANDIDATE": "明确优先候选",
    "COMPARISON_NOT_DECISION_GRADE": "对比未达决策级",
    "CONSTRUCTIVE_PREFLIGHT": "建设性预检",
    "CORE_CANDIDATE": "核心候选",
    "CURRENCY_MISMATCH": "币种不一致",
    "CURRENCY_MISSING": "币种缺失",
    "DATA_ACQUISITION": "数据获取",
    "DATA_GATED": "数据门控",
    "DECISION_GRADE": "决策级",
    "EVIDENCE_GATED": "证据门控",
    "EVIDENCE_VALIDATION": "证据验证",
    "EQUITY_INCENTIVE": "股权激励",
    "FAILED": "失败",
    "FALSE": "否",
    "FINANCIAL_CONSUMPTION_PARTIAL": "财务消费部分完成",
    "FINANCIAL_ROWS_NOT_CONSUMED": "财务行未被消费",
    "FINANCIALS": "财务数据",
    "FULL_250_PLUS": "250 日以上完整历史",
    "FX_RATE_UNAVAILABLE": "汇率不可用",
    "H0": "H0 低增长/修复",
    "H1": "H1 温和增长",
    "H2": "H2 明确增长",
    "H3": "H3 高增长",
    "H4": "H4 强增长",
    "H5": "H5 极高增长",
    "HIGH": "高",
    "H_SHARE_LISTING": "H 股上市",
    "INVALID": "无效",
    "LEAD_TRACKING": "线索跟踪",
    "LOW": "低",
    "MARKET_AHEAD_OF_EVIDENCE": "市场预期领先于证据",
    "MARKET_CAP_MISSING": "总市值缺失",
    "MEDIUM": "中",
    "MEDIUM_HIGH": "中高",
    "NO_BUY_POINT": "无买点",
    "NONE": "无",
    "NOT_ACTIONABLE": "暂不可行动",
    "NOT_APPLICABLE": "不适用",
    "NOT_MACHINE_READABLE": "不可机器读取",
    "NOT_PROVIDED": "未提供",
    "NOT_REQUIRED": "无需处理",
    "NOT_REQUESTED": "未请求",
    "OBSERVE_ONLY": "仅观察",
    "OK": "正常",
    "PARTIAL": "部分可信",
    "PASS": "通过",
    "PENDING": "等待中",
    "PERIOD_SELECTION_MISMATCH": "期间选择错配",
    "PREFLIGHT": "预检",
    "PRICE_HISTORY_ADJUSTED": "复权价格历史",
    "REDUCTION": "减持",
    "RESEARCH_GATED": "研究门控",
    "RESEARCH_VALIDATION": "研究验证",
    "RAW_STATUS_PARTIAL": "原始状态部分可用",
    "ROUGHLY_MATCHED": "基本匹配",
    "SCOPE_NOT_REQUESTED": "本轮未请求",
    "SERENITY_LAYER": "Serenity 层级",
    "SINGLE_CANDIDATE": "单候选",
    "SOURCE_DATA_UNAVAILABLE": "源数据不可用",
    "STALE": "过期",
    "STRONG_EXTENDED_WATCH": "强趋势延伸观察",
    "STRONG_OBSERVE": "强观察",
    "STRONG_PREFLIGHT": "强预检",
    "TENTATIVE_TOP_CANDIDATE": "暂定优先候选",
    "TRUE": "是",
    "UNKNOWN": "未知",
    "UNAVAILABLE": "不可用",
    "VALID": "有效",
    "VALUATION_CURRENCY_RECONCILIATION_REQUIRED": "需要估值/财报币种归一",
    "VALUATION_CONSUMPTION_PARTIAL": "估值消费部分完成",
    "VALUATION_GATED": "估值门控",
    "VALUATION_GROWTH": "估值增长缺口",
    "VALUATION_INPUT_REQUIRED": "需要补齐估值输入",
    "VALUATION_INPUT_NOT_CONSUMED": "估值输入未被消费",
    "VALUATION_INPUTS": "估值输入",
    "WAIT_FOR_BUY_POINT": "等待买点",
    "WAIT_FOR_SECOND_BUY": "等待二买/三买确认",
    "WAIT_FOR_STRUCTURE_CONFIRMATION": "等待结构确认",
    "WATCH_PREFLIGHT": "观察预检",
    "WEAK_OR_DOWNTREND": "弱势或下行趋势",
    "WEAK_PREFLIGHT": "弱预检",
}


DISPLAY_LABELS.update({
    "AI_RESEARCH": "AI 研究",
    "BASE_SHARES": "基准股本",
    "BLOCKING_LEVEL": "阻断级别",
    "BUYBACK": "回购",
    "CANDIDATE_POOL_SEMANTIC_COHERENCE": "候选池语义一致性",
    "CAPITAL_ACTION_QUANTIFICATION": "资本动作量化",
    "CAPITAL_STRUCTURE": "资本结构",
    "COMPLETED": "已完成",
    "COMPLETE_LAYER_MAPPING_REQUIRED": "需要完成产业层级映射",
    "CONFLICT_WITH_DATA": "与确定性数据冲突",
    "CONSTRUCTIVE_PULLBACK_WATCH": "建设性回踩观察",
    "CROSS_THEME_DIAGNOSTIC": "跨主题诊断",
    "FAILED_INSUFFICIENT_EVIDENCE": "证据不足，AI 研究失败",
    "FUNDAMENTALS": "基本面",
    "ISSUE_PRICE": "发行价",
    "LISTING_STAGE": "上市阶段",
    "LOCKUP_MONTHS": "锁定期（月）",
    "NEW_SHARES": "新增股份",
    "NO_CLEAR_TOP_CANDIDATE": "不得输出明确优先候选",
    "NOT_RUN": "未执行",
    "PARTIAL_RESEARCH_ONLY": "仅部分研究可用",
    "PRIVATE_PLACEMENT": "定增",
    "QUANTIFIED": "已量化",
    "RESEARCH": "研究",
    "RESEARCH_IMPACT": "研究影响",
    "RESEARCH_PRIORITY": "研究优先级",
    "RESEARCH_PRIORITY_ONLY": "仅作研究优先级",
    "SAME_LAYER": "同一产业层级",
    "SAME_THEME_DIFFERENT_LAYERS": "同主题不同层级",
    "SKIPPED_QUICK_AUDIT": "快速审计跳过",
    "SUBSCRIPTION_RATIO": "配售比例",
    "UNRELATED_DIAGNOSTIC": "无关诊断",
    "UNREVIEWED": "未完成 AI 研究",
    "USE_OF_PROCEEDS": "募资用途",
    "VALUATION_EVIDENCE": "估值证据",
    "VALUE_CHAIN_UNMAPPED": "产业链未映射",
})


def display_label(value: Any, default: str = "", *, include_code: bool = True) -> str:
    if value is None:
        return default
    text: str = str(value).strip()
    if not text:
        return default
    key: str = text.upper()
    label: str = DISPLAY_LABELS.get(key, "")
    if not label:
        return text
    return f"{label}（{text}）" if include_code and label != text else label


def display_bool(value: Any) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    text: str = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return "是"
    if text in {"false", "0", "no", "n", "none", ""}:
        return "否"
    return "是" if bool(value) else "否"


def display_list(value: Any, *, empty: str = "无") -> str:
    if not isinstance(value, list):
        return display_label(value, empty)
    items: list[str] = [display_label(item) for item in value if str(item).strip()]
    return ", ".join(items) if items else empty


def display_mapping_pairs(value: Any, *, empty: str = "无") -> str:
    if not isinstance(value, Mapping):
        return empty
    items: list[str] = [
        f"{display_label(key)}={display_label(label)}"
        for key, label in value.items()
    ]
    return ", ".join(items) if items else empty
