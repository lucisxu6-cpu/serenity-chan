#!/usr/bin/env python3
"""Build a direction-level AI research packet from a theme candidate universe."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from validate_theme_candidate_universe import validate_universe
except ModuleNotFoundError:  # pragma: no cover
    from scripts.validate_theme_candidate_universe import validate_universe


def _load_json(path: Path) -> Mapping[str, Any]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _candidate_symbols(universe: Mapping[str, Any]) -> list[str]:
    symbols: list[str] = []
    for row in _as_list(universe.get("candidate_universe")):
        if not isinstance(row, Mapping):
            continue
        symbol: str = str(row.get("symbol") or "").strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def _layer_names(universe: Mapping[str, Any]) -> list[str]:
    names: list[str] = []
    for row in _as_list(universe.get("value_chain_layers")):
        if not isinstance(row, Mapping):
            continue
        layer: str = str(row.get("layer") or "").strip()
        if layer:
            names.append(layer)
    return names


def build_theme_research_packet(universe_path: Path) -> dict[str, Any]:
    universe: Mapping[str, Any] = _load_json(universe_path)
    errors: list[str] = validate_universe(universe)
    if errors:
        raise ValueError("; ".join(errors))
    theme: str = str(universe.get("theme") or "").strip()
    symbols: list[str] = _candidate_symbols(universe)
    layers: list[str] = _layer_names(universe)
    layer_text: str = "、".join(layers) if layers else "待映射层级"
    return {
        "contract_type": "serenity_theme_research_packet",
        "schema_version": "1.0",
        "theme": theme,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "universe_path": str(universe_path.resolve()),
        "candidate_count": len(symbols),
        "candidate_symbols": symbols,
        "value_chain_layers": list(_as_list(universe.get("value_chain_layers"))),
        "direction_research_questions": [
            f"{theme} 的一阶驱动是什么，当前驱动来自真实需求、政策资本开支、海外 capex，还是交易热度？",
            f"{theme} 的利润窄口位于哪些层级：{layer_text}？",
            "哪些层级已经能看到收入、订单、客户、产能或现金流兑现，哪些仍停留在叙事阶段？",
            "当前估值隐含增长与可验证证据支持增长是否匹配？",
            "如果只允许选择一个研究方向，应该先研究哪一层，为什么？",
            "哪些候选必须降级为线索跟踪，直到披露更硬的证据？",
        ],
        "macro_evidence_tasks": [
            "收集并标注影响本主题的政策、产业资本开支、需求侧预算和价格/招标/订单证据。",
            "识别海外或跨市场领先指标，并说明它们如何传导到当前候选池。",
            "区分 observed、inferred、judgment，禁止用市场热度替代证据支持增长。",
        ],
        "falsification_questions": [
            "哪一个数据会证明主题需求低于市场预期？",
            "哪一个价格、毛利率、订单或客户信号会证明公司并不控制利润窄口？",
            "哪一种估值或技术结构会让研究首位无法转成行动对象？",
        ],
        "candidate_expansion_policy": {
            "minimum_deep_scan_candidates": 20,
            "required_layer_coverage": "每个核心价值链层级至少保留 3 个真实候选；不足时必须说明缺口和扩展任务。",
            "exclusion_rule": "只有概念标签、缺少收入/订单/客户/产能/财报证据的公司只能进入 downgraded_hot_directions 或线索跟踪。",
        },
        "next_step": "Use this packet as theme context for AI dossier and overlay/outcome generation before comparing candidates or producing strategy output.",
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Build a Serenity theme research packet")
    parser.add_argument("universe", help="theme_candidate_universe.json")
    parser.add_argument("--out", help="write theme research packet JSON")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        payload: dict[str, Any] = build_theme_research_packet(Path(args.universe))
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
