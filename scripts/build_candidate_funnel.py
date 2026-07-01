#!/usr/bin/env python3
"""Build an auditable candidate funnel from an opportunity discovery plan."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from build_theme_candidate_universe import THEME_PACKS, build_universe
    from validate_candidate_funnel import validate_candidate_funnel
    from validate_opportunity_discovery_plan import validate_opportunity_discovery_plan
    from validate_theme_candidate_universe import validate_universe
except ModuleNotFoundError:  # pragma: no cover
    from scripts.build_theme_candidate_universe import THEME_PACKS, build_universe
    from scripts.validate_candidate_funnel import validate_candidate_funnel
    from scripts.validate_opportunity_discovery_plan import validate_opportunity_discovery_plan
    from scripts.validate_theme_candidate_universe import validate_universe


RATING_CAP_SCORE: dict[str, float] = {"S": 24.0, "A": 20.0, "B": 14.0, "C": 8.0, "D": 3.0, "OBSERVE_ONLY": 2.0}
DATASET_SCORE: dict[str, float] = {"OK": 4.0, "PARTIAL": 2.0, "STALE": 1.5, "PENDING": 1.0, "FAILED": 0.0, "NOT_REQUESTED": 0.0}
CORE_PREFLIGHT_DATASETS: tuple[str, ...] = ("current_quote", "financials", "filings_announcements", "valuation_inputs")
ACCEPTABLE_PREFLIGHT_STATUSES: set[str] = {"OK", "PARTIAL", "STALE"}


def _load_json(path: Path) -> Mapping[str, Any]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _safe_name(value: str) -> str:
    cleaned: str = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned or "candidate"


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _market_allowed(market: str, market_scope: Sequence[str]) -> bool:
    return "GLOBAL" in market_scope or market in market_scope


def _is_star_board(symbol: str) -> bool:
    code: str = symbol.split(".", 1)[0]
    return code.startswith(("688", "689"))


def _excluded_by_board(symbol: str, excluded_boards: Sequence[str]) -> bool:
    normalized: set[str] = {str(item).lower() for item in excluded_boards}
    return bool({"star", "科创板", "sci-tech", "sci_tech"} & normalized and _is_star_board(symbol))


def _candidate_theme_rows(plan: Mapping[str, Any], universe_paths: Sequence[Path]) -> tuple[list[dict[str, Any]], list[str], list[dict[str, str]]]:
    path_texts: list[str] = []
    excluded_directions: list[dict[str, str]] = []
    rows_by_symbol: dict[str, dict[str, Any]] = {}
    universes: list[Mapping[str, Any]] = []
    if universe_paths:
        for path in universe_paths:
            universe: Mapping[str, Any] = _load_json(path)
            errors: list[str] = validate_universe(universe)
            if errors:
                raise ValueError(f"{path}: " + "; ".join(errors))
            universes.append(universe)
            path_texts.append(str(path.resolve()))
    else:
        for hypothesis in _as_list(plan.get("trend_hypotheses")):
            if not isinstance(hypothesis, Mapping):
                continue
            if _text(hypothesis.get("theme_source") or "curated_pack") != "curated_pack":
                continue
            theme_key: str = _text(hypothesis.get("theme_key"))
            if not theme_key or theme_key not in THEME_PACKS:
                continue
            universe_payload: dict[str, Any] = build_universe(theme_key)
            universes.append(universe_payload)
            path_texts.append(f"generated:{theme_key}")
    theme_by_key: dict[str, str] = {
        _text(item.get("theme_key")): _text(item.get("theme"))
        for item in _as_list(plan.get("trend_hypotheses"))
        if isinstance(item, Mapping)
    }
    for universe in universes:
        theme: str = _text(universe.get("theme"))
        for row in _as_list(universe.get("candidate_universe")):
            if not isinstance(row, Mapping):
                continue
            symbol: str = _text(row.get("symbol"))
            if not symbol:
                continue
            current: dict[str, Any] = rows_by_symbol.get(symbol, {})
            rows_by_symbol[symbol] = {
                "symbol": symbol,
                "name": _text(row.get("name")),
                "market": _text(row.get("market")),
                "theme": current.get("theme") or theme or theme_by_key.get(_text(row.get("theme_key")), ""),
                "layer": current.get("layer") or _text(row.get("layer")),
                "why_in_universe": current.get("why_in_universe") or _text(row.get("why_in_universe")),
                "initial_evidence_need": current.get("initial_evidence_need") or _text(row.get("initial_evidence_need")),
                "universe_order": min(int(current.get("universe_order", 10_000)), len(rows_by_symbol)),
            }
        for direction in _as_list(universe.get("downgraded_hot_directions")):
            if not isinstance(direction, Mapping):
                continue
            excluded_directions.append({
                "direction": _text(direction.get("direction")) or theme,
                "reason": _text(direction.get("downgrade_reason")) or "缺少可验证收入、订单、客户或产能证据。",
                "revisit_trigger": _text(direction.get("evidence_to_upgrade")) or "披露可追溯证据后重新纳入漏斗。",
            })
    return list(rows_by_symbol.values()), path_texts, excluded_directions


def _snapshot_by_symbol(snapshot_path: Optional[Path]) -> Mapping[str, Any]:
    if snapshot_path is None:
        return {}
    payload: Mapping[str, Any] = _load_json(snapshot_path)
    symbols: Any = payload.get("symbols")
    if isinstance(symbols, Mapping):
        return symbols
    return payload


def _manifest_path(preflight_root: Optional[Path], symbol: str) -> Optional[Path]:
    if preflight_root is None:
        return None
    candidates: list[Path] = [
        preflight_root / "data" / _safe_name(symbol) / "manifest.json",
        preflight_root / _safe_name(symbol) / "manifest.json",
        preflight_root / symbol / "manifest.json",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def _result_path(manifest: Mapping[str, Any], dataset: str) -> Optional[Path]:
    for row in _as_list(manifest.get("results")):
        if not isinstance(row, Mapping):
            continue
        if _text(row.get("dataset")) == dataset and _text(row.get("data_path")):
            return Path(_text(row.get("data_path")))
    return None


def _preflight_from_manifest(preflight_root: Optional[Path], symbol: str) -> dict[str, Any]:
    path: Optional[Path] = _manifest_path(preflight_root, symbol)
    if path is None:
        return {}
    manifest: Mapping[str, Any] = _load_json(path)
    acquisition: Mapping[str, Any] = _as_mapping(manifest.get("data_acquisition"))
    quality: Mapping[str, Any] = _as_mapping(manifest.get("data_quality"))
    row: dict[str, Any] = {
        "manifest_path": str(path),
        "status_by_dataset": dict(_as_mapping(acquisition.get("status_by_dataset"))),
        "rating_cap": _text(quality.get("rating_cap") or quality.get("full_research_rating_cap")),
    }
    quote_path: Optional[Path] = _result_path(manifest, "current_quote")
    if quote_path and quote_path.is_file():
        quote: Mapping[str, Any] = _load_json(quote_path)
        row["price"] = _safe_float(quote.get("regular_market_price"))
        row["currency"] = _text(quote.get("currency"))
        row["quote_time"] = _text(quote.get("regular_market_time"))
    return row


def _preflight_row(
    symbol: str,
    *,
    preflight_root: Optional[Path],
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    snap: Any = snapshot.get(symbol)
    if isinstance(snap, Mapping):
        row: dict[str, Any] = dict(snap)
        if "status_by_dataset" not in row:
            row["status_by_dataset"] = {}
        return row
    return _preflight_from_manifest(preflight_root, symbol)


def _data_score(preflight: Mapping[str, Any]) -> float:
    statuses: Mapping[str, Any] = _as_mapping(preflight.get("status_by_dataset"))
    score: float = 0.0
    for dataset in ["current_quote", "financials", "filings_announcements", "customer_order_capacity_evidence", "valuation_inputs"]:
        score += DATASET_SCORE.get(_text(statuses.get(dataset)), 0.0)
    rating_cap: str = _text(preflight.get("rating_cap"))
    score += RATING_CAP_SCORE.get(rating_cap, 0.0)
    return min(44.0, score)


def _preflight_ready(preflight: Mapping[str, Any]) -> tuple[bool, list[str]]:
    statuses: Mapping[str, Any] = _as_mapping(preflight.get("status_by_dataset"))
    blockers: list[str] = []
    for dataset in CORE_PREFLIGHT_DATASETS:
        status: str = _text(statuses.get(dataset))
        if status not in ACCEPTABLE_PREFLIGHT_STATUSES:
            blockers.append(f"{dataset} 状态为 {status or 'MISSING'}，不能进入正式 shortlist。")
    return not blockers, blockers


def _price_pass(preflight: Mapping[str, Any], price_preference: Mapping[str, Any]) -> tuple[bool, str]:
    price: Optional[float] = _safe_float(preflight.get("price"))
    min_price: Optional[float] = _safe_float(price_preference.get("min_price"))
    max_price: Optional[float] = _safe_float(price_preference.get("max_price"))
    if price is None:
        return False, "缺少当前价格 preflight，不能确认价格约束。"
    if min_price is not None and price < min_price:
        return False, f"当前价格 {price:.2f} 低于最小价格约束 {min_price:.2f}。"
    if max_price is not None and price > max_price:
        return False, f"当前价格 {price:.2f} 高于最高价格约束 {max_price:.2f}。"
    return True, f"当前价格 {price:.2f} 满足价格约束。"


def _price_preference_score(preflight: Mapping[str, Any], price_preference: Mapping[str, Any]) -> tuple[float, str]:
    price: Optional[float] = _safe_float(preflight.get("price"))
    if price is None:
        return 0.0, ""
    style: str = _text(price_preference.get("style"))
    max_price: Optional[float] = _safe_float(price_preference.get("max_price"))
    if style == "low_nominal_price_preferred":
        if price <= 5:
            return 8.0, "价格偏好加分：当前价格处于低价区间。"
        if price <= 10:
            return 6.0, "价格偏好加分：当前价格处于较低区间。"
        if price <= 20:
            return 4.0, "价格偏好加分：当前价格符合低价偏好。"
        if price <= 30:
            return 2.0, "价格偏好加分：当前价格接近低价偏好上沿。"
    if max_price is not None and max_price > 0 and price <= max_price:
        headroom: float = max(0.0, min(1.0, (max_price - price) / max_price))
        return round(headroom * 4.0, 2), "价格偏好加分：当前价格低于上限且保留约束空间。"
    return 0.0, ""


def _structural_score(row: Mapping[str, Any]) -> float:
    order: int = int(row.get("universe_order") or 0)
    base: float = max(18.0, 36.0 - min(order, 18) * 1.0)
    layer: str = _text(row.get("layer"))
    if any(token in layer for token in ["主网", "特高压", "光模块", "减速器", "伺服", "传动", "临床", "商业化"]):
        base += 8.0
    return min(44.0, base)


def _evidence_tasks(row: Mapping[str, Any]) -> list[str]:
    name: str = _text(row.get("name")) or _text(row.get("symbol"))
    layer: str = _text(row.get("layer")) or "待映射层级"
    return [
        f"验证 {name} 在「{layer}」中的收入、订单、客户、产能或现金流证据。",
        "读取最新年报、季报和近24个月公告，区分直接证据、披露线索和主题叙事。",
        "复核当前价格、估值输入、股本、市值和技术结构后再进入正式行动判断。",
    ]


def build_candidate_funnel(
    *,
    plan_path: Path,
    universe_paths: Sequence[Path],
    preflight_root: Optional[Path],
    preflight_snapshot_path: Optional[Path],
) -> dict[str, Any]:
    plan: Mapping[str, Any] = _load_json(plan_path)
    plan_errors: list[str] = validate_opportunity_discovery_plan(plan)
    if plan_errors:
        raise ValueError("; ".join(plan_errors))
    request: Mapping[str, Any] = _as_mapping(plan.get("request"))
    policy: Mapping[str, Any] = _as_mapping(plan.get("universe_policy"))
    market_scope: list[str] = [_text(item) for item in _as_list(request.get("market_scope")) if _text(item)]
    excluded_boards: list[str] = [_text(item) for item in _as_list(request.get("excluded_boards")) if _text(item)]
    price_preference: Mapping[str, Any] = _as_mapping(request.get("price_preference"))
    shortlist_target: int = int(policy.get("shortlist_target") or 8)
    raw_rows: list[dict[str, Any]]
    source_universe_paths: list[str]
    excluded_directions: list[dict[str, str]]
    raw_rows, source_universe_paths, excluded_directions = _candidate_theme_rows(plan, universe_paths)
    if not raw_rows:
        raise ValueError("candidate funnel requires at least one curated universe or one validated AI-built universe")
    snapshot: Mapping[str, Any] = _snapshot_by_symbol(preflight_snapshot_path)

    rows: list[dict[str, Any]] = []
    static_pass_count: int = 0
    data_pass_count: int = 0
    for raw in raw_rows:
        symbol: str = _text(raw.get("symbol"))
        market: str = _text(raw.get("market"))
        reasons: list[str] = []
        selected: bool = False
        status: str = "DEFERRED"
        bucket: str = "evidence_watch"
        score: float = _structural_score(raw)
        if not _market_allowed(market, market_scope):
            status = "FILTERED_OUT"
            bucket = "constraint_excluded"
            reasons.append(f"市场 {market} 不在允许范围 {', '.join(market_scope)}。")
        elif _excluded_by_board(symbol, excluded_boards):
            status = "FILTERED_OUT"
            bucket = "constraint_excluded"
            reasons.append("命中排除板块约束。")
        else:
            static_pass_count += 1
            preflight: dict[str, Any] = _preflight_row(symbol, preflight_root=preflight_root, snapshot=snapshot)
            if not preflight:
                bucket = "data_preflight_needed"
                reasons.append("缺少 preflight 数据，不能进入正式 shortlist。")
            else:
                preflight_ok: bool
                preflight_blockers: list[str]
                preflight_ok, preflight_blockers = _preflight_ready(preflight)
                if not preflight_ok:
                    bucket = "data_preflight_needed"
                    reasons.extend(preflight_blockers)
                else:
                    price_ok: bool
                    price_reason: str
                    price_ok, price_reason = _price_pass(preflight, price_preference)
                    reasons.append(price_reason)
                    score += _data_score(preflight)
                    if not price_ok:
                        status = "FILTERED_OUT"
                        bucket = "constraint_excluded"
                    else:
                        price_score: float
                        price_score_reason: str
                        price_score, price_score_reason = _price_preference_score(preflight, price_preference)
                        score += price_score
                        if price_score_reason:
                            reasons.append(price_score_reason)
                        data_pass_count += 1
                        reasons.append("核心 preflight 数据已到位，可进入缩圈评分。")
        rows.append({
            **raw,
            "stage_status": status,
            "final_bucket": bucket,
            "selected_for_formal": selected,
            "score": round(max(0.0, min(100.0, score)), 2),
            "preflight": _preflight_row(symbol, preflight_root=preflight_root, snapshot=snapshot),
            "reasons": reasons or ["进入主题候选宇宙，等待约束和证据筛选。"],
            "evidence_tasks": _evidence_tasks(raw),
        })

    eligible_indexes: list[int] = [
        index for index, row in enumerate(rows)
        if row["stage_status"] == "DEFERRED" and row["final_bucket"] == "evidence_watch"
    ]
    eligible_indexes.sort(key=lambda index: float(rows[index]["score"]), reverse=True)
    shortlist_indexes: set[int] = set(eligible_indexes[:shortlist_target])
    for index, row in enumerate(rows):
        if index in shortlist_indexes:
            row["stage_status"] = "IN_SHORTLIST"
            row["final_bucket"] = "formal_shortlist"
            row["selected_for_formal"] = True
            row["reasons"].append("进入正式 shortlist，下一步运行 formal comparison 和 AI research。")
        elif row["stage_status"] == "DEFERRED" and row["final_bucket"] == "evidence_watch":
            row["reasons"].append("未进入本轮 shortlist，保留为证据观察池。")
    rows.sort(key=lambda item: (0 if item["selected_for_formal"] else 1, -float(item["score"]), _text(item.get("symbol"))))
    shortlist_symbols: list[str] = [_text(row.get("symbol")) for row in rows if row.get("selected_for_formal") is True]
    return {
        "contract_type": "serenity_candidate_funnel",
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_plan_path": str(plan_path.resolve()),
        "source_universe_paths": source_universe_paths,
        "constraints": {
            "market_scope": market_scope,
            "excluded_boards": excluded_boards,
            "price_preference": dict(price_preference),
            "shortlist_target": shortlist_target,
        },
        "stage_summary": [
            {
                "stage": "theme_universe",
                "input_count": len(raw_rows),
                "output_count": len(raw_rows),
                "rule": "Build a multi-theme, layer-first universe before naming formal candidates.",
            },
            {
                "stage": "hard_constraints",
                "input_count": len(raw_rows),
                "output_count": static_pass_count,
                "rule": "Apply market and board exclusions before any formal comparison.",
            },
            {
                "stage": "data_preflight",
                "input_count": static_pass_count,
                "output_count": data_pass_count,
                "rule": "Use real preflight data or an explicit snapshot before shortlist selection.",
            },
            {
                "stage": "formal_shortlist",
                "input_count": data_pass_count,
                "output_count": len(shortlist_symbols),
                "rule": "Only shortlisted symbols are eligible for formal comparison and AI research.",
            },
        ],
        "candidate_rows": rows,
        "shortlist_symbols": shortlist_symbols,
        "excluded_directions": excluded_directions,
        "next_step": "Run formal research only on shortlist_symbols and pass this funnel as context to AI research and strategy handoff.",
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Build candidate funnel JSON")
    parser.add_argument("plan", help="opportunity_discovery_plan.json")
    parser.add_argument("--universe", action="append", default=[], help="optional theme_candidate_universe.json")
    parser.add_argument("--preflight-root", help="root containing data/<symbol>/manifest.json preflight data")
    parser.add_argument("--preflight-snapshot", help="static preflight snapshot JSON for tests or offline review")
    parser.add_argument("--out", help="write candidate funnel JSON")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        payload: dict[str, Any] = build_candidate_funnel(
            plan_path=Path(args.plan),
            universe_paths=[Path(item) for item in args.universe],
            preflight_root=Path(args.preflight_root) if args.preflight_root else None,
            preflight_snapshot_path=Path(args.preflight_snapshot) if args.preflight_snapshot else None,
        )
        errors: list[str] = validate_candidate_funnel(payload)
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
