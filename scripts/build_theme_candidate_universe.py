#!/usr/bin/env python3
"""Build a layer-first candidate universe for common Serenity themes."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


THEME_PACKS: dict[str, dict[str, Any]] = {
    "robotics": {
        "aliases": ["robot", "robots", "robotics", "机器人", "人形机器人", "工业机器人"],
        "display_theme": "机器人产业链",
        "layers": [
            {
                "layer": "精密减速器与传动",
                "bottleneck_question": "谁能在精密传动、寿命、良率和客户验证中形成窄口？",
                "evidence_to_seek": ["减速器收入占比", "客户验证或批量订单", "产能与良率", "价格和毛利率趋势"],
                "candidates": [
                    ("688017.SH", "绿的谐波", "谐波减速器核心供应商"),
                    ("002896.SZ", "中大力德", "减速器和传动部件"),
                    ("603728.SH", "鸣志电器", "运动控制和精密传动相关部件"),
                ],
            },
            {
                "layer": "伺服、空心杯、执行器与运动控制",
                "bottleneck_question": "谁能把电机、驱动、控制和执行器交付到可量产方案？",
                "evidence_to_seek": ["伺服/电机收入", "机器人客户", "订单和产能", "毛利率与现金流"],
                "candidates": [
                    ("300124.SZ", "汇川技术", "工业自动化和伺服系统龙头"),
                    ("002747.SZ", "埃斯顿", "机器人本体与运动控制"),
                    ("002050.SZ", "三花智控", "执行器和热管理部件"),
                ],
            },
            {
                "layer": "本体、系统集成与应用落地",
                "bottleneck_question": "谁能把部件能力转成可复制交付和行业应用？",
                "evidence_to_seek": ["本体销量", "应用场景收入", "客户集中度", "项目交付和售后能力"],
                "candidates": [
                    ("300024.SZ", "机器人", "工业机器人与系统集成"),
                    ("688165.SH", "埃夫特", "工业机器人本体和集成"),
                    ("688097.SH", "博众精工", "自动化设备与应用集成"),
                ],
            },
        ],
        "downgraded": [
            {
                "direction": "泛机器人概念股",
                "downgrade_reason": "缺少产品收入、客户验证或量产订单时只能作为线索。",
                "evidence_to_upgrade": "披露机器人相关收入、订单、客户、产能或产品验证。",
            }
        ],
    },
    "ai_compute": {
        "aliases": ["ai compute", "ai infrastructure", "算力", "国产算力", "ai算力", "cpo", "光模块", "高速互联"],
        "display_theme": "AI 算力与高速互联",
        "layers": [
            {
                "layer": "光模块、光芯片与高速互联",
                "bottleneck_question": "谁在高速率、客户认证和产能扩张中控制利润窄口？",
                "evidence_to_seek": ["800G/1.6T收入", "海外客户", "毛利率", "扩产和交付节奏"],
                "candidates": [
                    ("300308.SZ", "中际旭创", "高速光模块"),
                    ("300502.SZ", "新易盛", "高速光模块"),
                    ("300394.SZ", "天孚通信", "高速光器件与光模块配套"),
                    ("688313.SH", "仕佳光子", "PLC光分路器、AWG及光芯片相关能力"),
                ],
            },
            {
                "layer": "服务器、交换机、PCB和电源热管理",
                "bottleneck_question": "谁把算力 capex 转成硬件交付、良率和现金回款？",
                "evidence_to_seek": ["AI服务器收入", "交换机/PCB订单", "客户集中度", "存货和应收"],
                "candidates": [
                    ("002463.SZ", "沪电股份", "AI服务器和高速PCB"),
                    ("002916.SZ", "深南电路", "高速PCB、封装基板和通信设备配套"),
                    ("300476.SZ", "胜宏科技", "AI服务器PCB"),
                    ("000977.SZ", "浪潮信息", "AI服务器"),
                    ("603019.SH", "中科曙光", "服务器和算力基础设施"),
                ],
            },
            {
                "layer": "半导体设备与先进制程资本开支",
                "bottleneck_question": "国产算力扩产中谁受益于先进制程、刻蚀、薄膜沉积和封测设备资本开支？",
                "evidence_to_seek": ["设备订单", "先进制程客户", "国产替代进展", "收入兑现和回款"],
                "candidates": [
                    ("688012.SH", "中微公司", "半导体设备，受益先进制程资本开支"),
                    ("002371.SZ", "北方华创", "半导体设备平台"),
                    ("688072.SH", "拓荆科技", "薄膜沉积设备"),
                ],
            },
            {
                "layer": "云、模型和应用需求",
                "bottleneck_question": "下游需求是否足以支撑硬件订单持续兑现？",
                "evidence_to_seek": ["云厂商capex", "模型调用需求", "推理成本", "客户续单"],
                "candidates": [
                    ("688111.SH", "金山办公", "AI办公应用"),
                    ("300033.SZ", "同花顺", "AI金融应用"),
                    ("002230.SZ", "科大讯飞", "AI应用和模型"),
                ],
            },
        ],
        "downgraded": [
            {
                "direction": "只带AI标签的通用电子或软件公司",
                "downgrade_reason": "缺少算力需求向收入、订单或毛利传导的证据。",
                "evidence_to_upgrade": "披露AI相关产品收入、订单、客户或可验证使用量。",
            }
        ],
    },
    "innovative_medicine": {
        "aliases": ["创新药", "biotech", "medicine", "创新药产业链", "医药"],
        "display_theme": "创新药与生物科技",
        "layers": [
            {
                "layer": "临床管线与平台",
                "bottleneck_question": "谁拥有高质量临床数据、清晰适应症和可商业化平台？",
                "evidence_to_seek": ["临床阶段", "主要终点", "安全性", "患者规模"],
                "candidates": [
                    ("688506.SH", "百利天恒", "创新药管线和BD"),
                    ("688235.SH", "百济神州", "全球化创新药"),
                    ("688266.SH", "泽璟制药", "创新药管线"),
                ],
            },
            {
                "layer": "商业化与BD兑现",
                "bottleneck_question": "谁能把管线价值转成销售、BD现金流和海外权益？",
                "evidence_to_seek": ["销售额", "BD条款", "里程碑", "现金流和费用率"],
                "candidates": [
                    ("600276.SH", "恒瑞医药", "创新药商业化和研发平台"),
                    ("688331.SH", "荣昌生物", "ADC和自免/肿瘤管线"),
                    ("1801.HK", "信达生物", "港股创新药商业化平台"),
                ],
            },
            {
                "layer": "CXO、CDMO与服务",
                "bottleneck_question": "谁受益于研发外包和生产服务恢复？",
                "evidence_to_seek": ["订单恢复", "海外收入", "产能利用率", "价格和毛利"],
                "candidates": [
                    ("603259.SH", "药明康德", "CXO平台"),
                    ("300759.SZ", "康龙化成", "CXO服务"),
                    ("688202.SH", "美迪西", "临床前CRO"),
                ],
            },
        ],
        "downgraded": [
            {
                "direction": "早期管线高估值公司",
                "downgrade_reason": "早期数据和商业化路径不足时无法支撑高增长兑现。",
                "evidence_to_upgrade": "获得关键临床读数、监管进展、BD条款或商业化收入。",
            }
        ],
    },
    "grid_power": {
        "aliases": ["电力", "电网", "新型电力系统", "储能", "energy infrastructure", "grid"],
        "display_theme": "电网与电力设备",
        "layers": [
            {
                "layer": "主网、特高压与一次设备",
                "bottleneck_question": "谁受益于电网投资、招标份额和交付能力？",
                "evidence_to_seek": ["国网/南网中标", "订单金额", "交付周期", "毛利率和回款"],
                "candidates": [
                    ("600406.SH", "国电南瑞", "电网自动化和继保"),
                    ("000400.SZ", "许继电气", "电力设备和自动化"),
                    ("601179.SH", "中国西电", "输变电设备"),
                ],
            },
            {
                "layer": "储能、逆变器与PCS",
                "bottleneck_question": "谁把新能源波动和数据中心用电转成订单与利润？",
                "evidence_to_seek": ["储能订单", "海外认证", "价格和毛利", "应收和库存"],
                "candidates": [
                    ("300274.SZ", "阳光电源", "逆变器和储能系统"),
                    ("300750.SZ", "宁德时代", "电池和储能"),
                    ("688390.SH", "固德威", "逆变器和储能"),
                ],
            },
            {
                "layer": "用电需求和电力运营",
                "bottleneck_question": "新增负荷、并网和电价机制如何影响设备需求？",
                "evidence_to_seek": ["用电量", "电价政策", "数据中心负荷", "新能源消纳"],
                "candidates": [
                    ("600900.SH", "长江电力", "电力运营"),
                    ("003816.SZ", "中国广核", "核电运营"),
                    ("600795.SH", "国电电力", "电力运营"),
                ],
            },
        ],
        "downgraded": [
            {
                "direction": "只有政策叙事的设备公司",
                "downgrade_reason": "没有招标、订单、交付和回款证据时只保留观察。",
                "evidence_to_upgrade": "披露中标份额、订单、交付进度、毛利和现金回款。",
            }
        ],
    },
}


def _match_theme(theme: str) -> str:
    normalized: str = theme.lower().strip()
    for key, pack in THEME_PACKS.items():
        aliases: list[str] = [str(item).lower() for item in pack.get("aliases", [])]
        if normalized == key or any(alias in normalized for alias in aliases):
            return key
    raise ValueError(
        f"theme is not in curated packs: {theme}. "
        "Use assets/theme_candidate_universe.schema.json to write a real AI-built universe, "
        "then validate it with scripts/validate_theme_candidate_universe.py."
    )


def _market_from_symbol(symbol: str) -> str:
    if symbol.endswith(".HK"):
        return "HK"
    if symbol.endswith((".SH", ".SZ", ".BJ")):
        return "CN_A"
    if symbol.isalnum() and symbol.upper() == symbol:
        return "US"
    return "UNKNOWN"


def _candidate(symbol: str, name: str, layer: str, reason: str) -> dict[str, str]:
    return {
        "symbol": symbol,
        "market": _market_from_symbol(symbol),
        "name": name,
        "layer": layer,
        "why_in_universe": reason,
        "initial_evidence_need": "运行真实取数后，用公告、财报、订单、客户和产能证据验证产业链映射。",
    }


def build_universe(theme: str) -> dict[str, Any]:
    key: str = _match_theme(theme)
    pack: Mapping[str, Any] = THEME_PACKS[key]
    layers: list[dict[str, Any]] = []
    candidates: list[dict[str, str]] = []
    for layer in pack.get("layers", []):
        if not isinstance(layer, Mapping):
            continue
        layer_name: str = str(layer.get("layer") or "")
        layer_candidates: list[str] = []
        for item in layer.get("candidates", []):
            if not isinstance(item, (list, tuple)) or len(item) != 3:
                raise ValueError(f"candidate item in {layer_name} must be (symbol, name, reason)")
            symbol, name, reason = item
            symbol_text: str = str(symbol)
            layer_candidates.append(symbol_text)
            candidates.append(_candidate(symbol_text, str(name), layer_name, str(reason)))
        layers.append({
            "layer": layer_name,
            "bottleneck_question": str(layer.get("bottleneck_question") or ""),
            "evidence_to_seek": list(layer.get("evidence_to_seek") or []),
            "candidate_symbols": layer_candidates,
        })
    return {
        "contract_type": "serenity_theme_candidate_universe",
        "schema_version": "1.0",
        "theme": str(pack.get("display_theme") or theme),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "universe_source": "curated_industry_domain_pack",
        "value_chain_layers": layers,
        "candidate_universe": candidates,
        "downgraded_hot_directions": list(pack.get("downgraded") or []),
        "ai_expansion_tasks": [
            "按价值链层级扩展到至少 20 个候选，并标注每个候选的主层级、收入传导和证据来源。",
            "剔除只有概念标签、缺少收入/订单/客户/产能证据的候选。",
            "将候选代码交给 run_research_analysis.py 做真实取数、AI overlay 和候选对比。",
        ],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Build a Serenity theme candidate universe")
    parser.add_argument("theme", help="theme name or alias")
    parser.add_argument("--out", help="write universe JSON")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        payload: dict[str, Any] = build_universe(args.theme)
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
