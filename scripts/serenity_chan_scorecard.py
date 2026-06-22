#!/usr/bin/env python3
"""
Serenity + Chan scorecard for v3 skill.

Input: JSON scorecard matching assets/scorecard_template.json.
All factor ratings are 0-5. Output JSON or Markdown.
Rating is capped by critical data failures and red flags.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


class Rating(str, Enum):
    S = "S"
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    OBSERVE_ONLY = "OBSERVE_ONLY"


RATING_ORDER = [Rating.D, Rating.C, Rating.B, Rating.A, Rating.S]


MODULE_WEIGHTS = {
    "data_quality": 15,
    "serenity": 20,
    "evidence": 15,
    "fundamentals": 15,
    "valuation": 15,
    "technical": 10,
    "risk": 10,
}

REQUIRED_TOP_LEVEL = set(MODULE_WEIGHTS) | {"ticker", "market", "as_of_date", "penalties"}
ALLOWED_MARKETS = {"CN_A", "US", "HK", "OTHER"}


PENALTY_CAPS = {
    "only_weak_evidence": Rating.C,
    "missing_latest_quote": Rating.B,
    "missing_price_history": Rating.B,
    "missing_latest_financials": Rating.B,
    "unverified_customer_claim": Rating.C,
    "price_source_conflict_gt_2pct": Rating.C,
    "major_dilution_or_governance_red_flag": Rating.B,
    "technical_escape_overheat": Rating.A,
    "fundamental_falsified": Rating.D,
}


def _rating_0_5(value: Any, label: str) -> float:
    try:
        v = float(value)
    except Exception as exc:
        raise ValueError(f"{label} must be numeric 0-5") from exc
    if v < 0 or v > 5:
        raise ValueError(f"{label} must be in 0-5, got {v}")
    return v


def _module_score(module: Mapping[str, Any], weight: float, label: str) -> Tuple[float, Dict[str, Any]]:
    if not module:
        return 0.0, {"average_rating": 0, "points": 0, "warning": f"{label} missing"}
    ratings = []
    details = {}
    for k, v in module.items():
        r = _rating_0_5(v, f"{label}.{k}")
        ratings.append(r)
        details[k] = r
    avg = sum(ratings) / len(ratings)
    points = avg / 5.0 * weight
    return points, {"average_rating": round(avg, 2), "points": round(points, 2), "details": details}


def _rating_from_score(score: float) -> Rating:
    if score >= 85:
        return Rating.S
    if score >= 75:
        return Rating.A
    if score >= 65:
        return Rating.B
    if score >= 50:
        return Rating.C
    return Rating.D


def _apply_cap(rating: Rating, cap: Rating) -> Rating:
    if rating == Rating.OBSERVE_ONLY or cap == Rating.OBSERVE_ONLY:
        return Rating.OBSERVE_ONLY
    if rating == Rating.D or cap == Rating.D:
        return Rating.D
    return rating if RATING_ORDER.index(rating) <= RATING_ORDER.index(cap) else cap


def score(data: Dict[str, Any]) -> Dict[str, Any]:
    validate_input_shape(data)
    module_results: Dict[str, Any] = {}
    total = 0.0
    for module, weight in MODULE_WEIGHTS.items():
        points, detail = _module_score(data.get(module, {}), weight, module)
        module_results[module] = detail
        total += points

    raw_rating = _rating_from_score(total)
    cap = Rating.S
    active_caps: List[Dict[str, str]] = []
    penalties = data.get("penalties", {}) or {}
    for name, active in penalties.items():
        if bool(active) and name in PENALTY_CAPS:
            new_cap = PENALTY_CAPS[name]
            cap = _apply_cap(cap, new_cap)
            active_caps.append({"penalty": name, "cap": new_cap.value})

    if data.get("market") == "OTHER":
        cap = _apply_cap(cap, Rating.OBSERVE_ONLY)
        active_caps.append({"penalty": "market_unresolved", "cap": "OBSERVE_ONLY"})
    final_rating = _apply_cap(raw_rating, cap)

    verdict_map = {
        Rating.S: "核心长线候选：买点出现后可重点研究",
        Rating.A: "强观察对象：等待关键验证或买点",
        Rating.B: "有潜力但存在证据/估值/数据缺口",
        Rating.C: "主题型或交易型，不适合作长线核心",
        Rating.D: "剔除/证伪/仅作反面样本",
        Rating.OBSERVE_ONLY: "仅观察：市场或关键数据未解析",
    }

    return {
        "ticker": data.get("ticker", ""),
        "company": data.get("company", ""),
        "market": data.get("market", ""),
        "as_of_date": data.get("as_of_date", ""),
        "raw_score": round(total, 2),
        "raw_rating": raw_rating.value,
        "rating_cap": cap.value,
        "final_rating": final_rating.value,
        "verdict": verdict_map[final_rating],
        "module_results": module_results,
        "active_caps": active_caps,
        "evidence_notes": data.get("evidence_notes", []),
        "falsification_points": [x for x in data.get("falsification_points", []) if str(x).strip()],
    }


def validate_input_shape(data: Mapping[str, Any]) -> None:
    missing = sorted(k for k in REQUIRED_TOP_LEVEL if k not in data)
    if missing:
        raise ValueError(f"scorecard missing required top-level keys: {', '.join(missing)}")
    for module in MODULE_WEIGHTS:
        value = data.get(module)
        if not isinstance(value, Mapping) or not value:
            raise ValueError(f"{module} must be a non-empty object of 0-5 ratings")
        for key, rating in value.items():
            _rating_0_5(rating, f"{module}.{key}")
    penalties = data.get("penalties")
    if not isinstance(penalties, Mapping):
        raise ValueError("penalties must be an object of boolean flags")
    market = data.get("market")
    if market not in ALLOWED_MARKETS:
        raise ValueError(f"market must be one of {sorted(ALLOWED_MARKETS)}, got {market!r}")
    as_of_date = data.get("as_of_date")
    if as_of_date != "YYYY-MM-DD":
        try:
            dt.date.fromisoformat(str(as_of_date))
        except ValueError as exc:
            raise ValueError("as_of_date must be YYYY-MM-DD") from exc
    unknown_penalties = sorted(set(penalties) - set(PENALTY_CAPS))
    if unknown_penalties:
        raise ValueError(f"unknown penalty flags: {', '.join(unknown_penalties)}")
    non_bool_penalties = sorted(k for k, v in penalties.items() if not isinstance(v, bool))
    if non_bool_penalties:
        raise ValueError(f"penalty flags must be boolean: {', '.join(non_bool_penalties)}")


def to_markdown(result: Dict[str, Any]) -> str:
    title = f"{result.get('ticker') or 'UNKNOWN'}"
    if result.get("company"):
        title += f" ({result['company']})"
    lines = [
        f"# Serenity + Chan Scorecard: {title}",
        "",
        f"- Market: {result.get('market', '')}",
        f"- As of: {result.get('as_of_date', '')}",
        f"- Raw score: **{result['raw_score']} / 100**",
        f"- Raw rating: **{result['raw_rating']}**",
        f"- Rating cap: **{result['rating_cap']}**",
        f"- Final rating: **{result['final_rating']}**",
        f"- Verdict: {result['verdict']}",
        "",
        "## Module Scores",
        "| Module | Avg Rating | Points |",
        "|---|---:|---:|",
    ]
    for name, detail in result["module_results"].items():
        lines.append(f"| {name} | {detail.get('average_rating', 0)} | {detail.get('points', 0)} |")
    if result.get("active_caps"):
        lines.extend(["", "## Active Rating Caps", "| Penalty | Cap |", "|---|---|"])
        for item in result["active_caps"]:
            lines.append(f"| {item['penalty']} | {item['cap']} |")
    if result.get("falsification_points"):
        lines.extend(["", "## Falsification Points"])
        for p in result["falsification_points"]:
            lines.append(f"- {p}")
    if result.get("evidence_notes"):
        lines.extend(["", "## Evidence Notes"])
        for ev in result["evidence_notes"]:
            if not isinstance(ev, dict):
                continue
            claim = ev.get("claim", "")
            src = ev.get("source", "")
            lvl = ev.get("source_level", "")
            conf = ev.get("confidence", "")
            if claim or src:
                lines.append(f"- [{lvl}/{conf}] {claim} — {src}")
    lines.append("")
    return "\n".join(lines)


def load_json(path: str) -> Dict[str, Any]:
    if path == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise SystemExit("Input JSON must be an object.")
    return data


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Score a Serenity + Chan stock thesis")
    parser.add_argument("input", help="Scorecard JSON path or '-' for stdin")
    parser.add_argument("--format", choices=["json", "md", "both"], default="json")
    parser.add_argument("--validate-only", action="store_true", help="validate input shape and exit")
    args = parser.parse_args(argv)
    data = load_json(args.input)
    try:
        validate_input_shape(data)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.validate_only:
        print("OK: scorecard input")
        return 0
    result = score(data)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.format == "md":
        print(to_markdown(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("\n---\n")
        print(to_markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
