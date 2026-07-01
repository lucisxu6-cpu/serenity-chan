#!/usr/bin/env python3
"""Build a structured opportunity discovery plan before candidate narrowing."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from build_theme_candidate_universe import THEME_PACKS
    from validate_opportunity_discovery_plan import validate_opportunity_discovery_plan
except ModuleNotFoundError:  # pragma: no cover
    from scripts.build_theme_candidate_universe import THEME_PACKS
    from scripts.validate_opportunity_discovery_plan import validate_opportunity_discovery_plan


BROAD_THEME_KEYS: list[str] = ["grid_power", "ai_compute", "robotics", "innovative_medicine"]
BROAD_CONTEXT_TOKENS: tuple[str, ...] = ("当前", "现在", "大趋势", "形势", "大环境", "世界层面")
BROAD_NEED_TOKENS: tuple[str, ...] = ("机会", "可选", "推荐", "便宜", "方向", "行业", "入")
DEFAULT_SELECTION_ORDER: list[str] = [
    "Apply explicit market and board constraints before scoring.",
    "Use value-chain bottleneck fit before company popularity.",
    "Use real preflight data for price, financials, valuation, and disclosure evidence.",
    "Prefer candidates with direct customer/order/capacity evidence over theme labels.",
    "Only send the narrowed shortlist into formal comparison and AI research.",
]


def _has_broad_opportunity_intent(prompt: str) -> bool:
    if any(token in prompt for token in ["大趋势", "形势", "大环境", "世界层面"]):
        return True
    return any(token in prompt for token in BROAD_CONTEXT_TOKENS) and any(token in prompt for token in BROAD_NEED_TOKENS)


def _user_defined_theme_key(prompt: str) -> str:
    cleaned: str = "".join(ch.lower() if ch.isalnum() else "_" for ch in prompt.strip())
    compact: str = "_".join(part for part in cleaned.split("_") if part)
    return f"user_defined_{compact[:48]}" if compact else "user_defined_theme"


def _user_defined_theme_label(prompt: str) -> str:
    compact: str = " ".join(prompt.strip().split())
    return compact[:80] if compact else "用户定义主题"


def _pack_aliases(theme_key: str) -> list[str]:
    pack: Mapping[str, Any] = THEME_PACKS.get(theme_key, {})
    return [str(item).lower() for item in pack.get("aliases", []) if str(item).strip()]


def _requested_theme_keys(prompt: str, explicit_themes: Sequence[str]) -> list[str]:
    normalized: str = prompt.lower()
    selected: list[str] = []
    has_explicit_theme: bool = any(str(value).strip() for value in explicit_themes)
    for value in explicit_themes:
        key: str = str(value).strip()
        if key in THEME_PACKS and key not in selected:
            selected.append(key)
            continue
        for theme_key in THEME_PACKS:
            if key.lower() == theme_key or key.lower() in _pack_aliases(theme_key):
                if theme_key not in selected:
                    selected.append(theme_key)
                break
    for theme_key in THEME_PACKS:
        aliases: list[str] = _pack_aliases(theme_key)
        if theme_key in normalized or any(alias and alias in normalized for alias in aliases):
            if theme_key not in selected:
                selected.append(theme_key)
    if not selected and has_explicit_theme:
        return []
    if not selected and _has_broad_opportunity_intent(prompt):
        selected.extend([key for key in BROAD_THEME_KEYS if key in THEME_PACKS])
    return selected


def _display_theme(theme_key: str) -> str:
    pack: Mapping[str, Any] = THEME_PACKS.get(theme_key, {})
    return str(pack.get("display_theme") or theme_key)


def _layer_focus(theme_key: str) -> list[str]:
    pack: Mapping[str, Any] = THEME_PACKS.get(theme_key, {})
    layers: list[str] = []
    for row in pack.get("layers", []) if isinstance(pack.get("layers"), list) else []:
        if isinstance(row, Mapping):
            layer: str = str(row.get("layer") or "").strip()
            if layer:
                layers.append(layer)
    return layers


def _evidence_to_seek(theme_key: str) -> list[str]:
    pack: Mapping[str, Any] = THEME_PACKS.get(theme_key, {})
    evidence: list[str] = []
    for row in pack.get("layers", []) if isinstance(pack.get("layers"), list) else []:
        if not isinstance(row, Mapping):
            continue
        for item in row.get("evidence_to_seek", []) if isinstance(row.get("evidence_to_seek"), list) else []:
            text: str = str(item).strip()
            if text and text not in evidence:
                evidence.append(text)
    return evidence[:6] or ["收入传导", "客户/订单/产能证据"]


def _disconfirmation(theme_key: str) -> list[str]:
    pack: Mapping[str, Any] = THEME_PACKS.get(theme_key, {})
    downgraded: list[str] = []
    for row in pack.get("downgraded", []) if isinstance(pack.get("downgraded"), list) else []:
        if isinstance(row, Mapping):
            reason: str = str(row.get("downgrade_reason") or "").strip()
            if reason:
                downgraded.append(reason)
    return downgraded or ["主题热度无法映射到收入、订单、客户、产能或现金流。"]


def _why_now(theme_key: str) -> str:
    theme: str = _display_theme(theme_key)
    if theme_key == "grid_power":
        return "电力需求、数据中心负荷和电网投资共同决定设备与运营层的兑现节奏。"
    if theme_key == "ai_compute":
        return "AI资本开支和高速互联需求会沿硬件、PCB、光模块和算力基础设施传导。"
    if theme_key == "robotics":
        return "机器人量产预期需要通过核心部件、客户验证和产能良率转成收入证据。"
    if theme_key == "innovative_medicine":
        return "创新药机会取决于临床、商业化、BD、现金流和估值重定价的共同验证。"
    return f"{theme} 需要先确认一阶驱动，再确认哪些层级能把趋势转成财务兑现。"


def _price_style(max_price: Optional[float], prompt: str) -> str:
    if max_price is not None:
        return "explicit_max_price"
    if any(token in prompt for token in ["便宜", "低价", "价格低"]):
        return "low_nominal_price_preferred"
    return "no_explicit_price_limit"


def build_plan(
    *,
    prompt: str,
    market_scope: Sequence[str],
    excluded_boards: Sequence[str],
    horizon: str,
    risk_profile: str,
    max_price: Optional[float],
    min_price: Optional[float],
    explicit_themes: Sequence[str],
    preflight_candidate_limit: int,
    shortlist_target: int,
) -> dict[str, Any]:
    theme_keys: list[str] = _requested_theme_keys(prompt, explicit_themes)
    hypotheses: list[dict[str, Any]] = []
    for theme_key in theme_keys:
        hypotheses.append({
            "theme_key": theme_key,
            "theme_source": "curated_pack",
            "theme": _display_theme(theme_key),
            "why_now": _why_now(theme_key),
            "value_chain_focus": _layer_focus(theme_key),
            "evidence_to_seek": _evidence_to_seek(theme_key),
            "disconfirmation": _disconfirmation(theme_key),
        })
    user_theme_required: bool = not hypotheses
    if user_theme_required:
        hypotheses.append({
            "theme_key": _user_defined_theme_key(prompt),
            "theme_source": "ai_built_required",
            "theme": _user_defined_theme_label(prompt),
            "why_now": "需要先由 AI 明确主题边界、真实受益链路和可交易候选范围。",
            "value_chain_focus": ["主题边界", "需求来源", "收入传导", "候选可得性"],
            "evidence_to_seek": ["可验证需求或消费热度", "上市公司收入、订单、渠道或客户证据", "同类候选和替代方向"],
            "disconfirmation": ["无法映射到上市公司收入、订单、客户、产能、现金流或可验证数据。"],
        })
    minimum_universe: int = max(20, len(theme_keys) * 6)
    return {
        "contract_type": "serenity_opportunity_discovery_plan",
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "request": {
            "prompt": prompt,
            "market_scope": list(market_scope),
            "horizon": horizon,
            "risk_profile": risk_profile,
            "price_preference": {
                "style": _price_style(max_price, prompt),
                "min_price": min_price,
                "max_price": max_price,
            },
            "excluded_boards": list(excluded_boards),
        },
        "discovery_mode": "theme_research_required" if user_theme_required else "open_opportunity" if len(theme_keys) > 1 else "theme_opportunity",
        "trend_hypotheses": hypotheses,
        "universe_policy": {
            "minimum_universe_candidates": minimum_universe if not user_theme_required else 12,
            "preflight_candidate_limit": max(1, preflight_candidate_limit),
            "shortlist_target": max(1, shortlist_target),
            "selection_order": DEFAULT_SELECTION_ORDER,
            "evidence_floor": "Final shortlist requires real preflight data; theme labels alone can only create evidence watch rows.",
            "open_theme_research_tasks": [
                "Define the investable theme boundary in plain language.",
                "Build a real theme_candidate_universe artifact with listed candidates and value-chain layers.",
                "Validate the universe artifact before running candidate funnel and preflight.",
            ] if user_theme_required else [],
        },
        "next_step": "Build theme universes, run preflight data collection, then produce a candidate_funnel before formal comparison.",
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Build an opportunity discovery plan")
    parser.add_argument("prompt", help="user opportunity request")
    parser.add_argument("--market-scope", nargs="+", default=["CN_A"], help="allowed markets")
    parser.add_argument("--exclude-board", action="append", default=[], help="board to exclude, e.g. STAR")
    parser.add_argument("--horizon", default="3-6个月")
    parser.add_argument("--risk-profile", default="balanced")
    parser.add_argument("--max-price", type=float)
    parser.add_argument("--min-price", type=float)
    parser.add_argument("--theme", action="append", default=[], help="explicit theme key or alias")
    parser.add_argument("--preflight-candidate-limit", type=int, default=24)
    parser.add_argument("--shortlist-target", type=int, default=8)
    parser.add_argument("--out", help="write plan JSON")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        payload: dict[str, Any] = build_plan(
            prompt=args.prompt,
            market_scope=[str(item) for item in args.market_scope],
            excluded_boards=[str(item) for item in args.exclude_board],
            horizon=str(args.horizon),
            risk_profile=str(args.risk_profile),
            max_price=args.max_price,
            min_price=args.min_price,
            explicit_themes=[str(item) for item in args.theme],
            preflight_candidate_limit=int(args.preflight_candidate_limit),
            shortlist_target=int(args.shortlist_target),
        )
        errors: list[str] = validate_opportunity_discovery_plan(payload)
        if errors:
            raise ValueError("; ".join(errors))
        text: str = json.dumps(payload, ensure_ascii=False, indent=2)
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
