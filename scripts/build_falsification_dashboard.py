#!/usr/bin/env python3
"""Validate, render, or build a Serenity + Chan falsification dashboard."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


RATINGS = {"S", "A", "B", "C", "D", "OBSERVE_ONLY"}
MARKETS = {"CN_A", "US", "HK", "GLOBAL", "OTHER", "UNKNOWN"}
GROWTH = {"H0", "H1", "H2", "H3", "H4", "H5", "UNKNOWN"}
GROWTH_ORDER = {"H0": 0, "H1": 1, "H2": 2, "H3": 3, "H4": 4, "H5": 5, "UNKNOWN": -1}
MONITOR_STATUSES = {"OK", "WATCH", "TRIGGERED", "STALE", "UNKNOWN"}
MONITOR_CATEGORIES = {"demand", "financials", "customer", "capacity", "valuation", "technical", "governance", "source_quality"}
SOURCE_LEVELS = {"L0", "L1", "L2", "L3", "L4"}
CONFIDENCE = {"Strong", "Medium", "Weak", "Unverified"}
REQUIRED_ROOT = {"symbol", "market", "as_of_date", "thesis", "rating", "rating_cap", "monitors"}
REQUIRED_THESIS = {"summary", "market_implied_growth", "evidence_supported_growth"}
REQUIRED_MONITOR = {
    "id",
    "category",
    "claim",
    "falsification_trigger",
    "source_required",
    "check_frequency",
    "status",
    "action_if_triggered",
}


def _growth_order(value: str) -> int:
    return GROWTH_ORDER.get(value, -1)


def _has_h4_h5_valuation_gap(implied: str, supported: str, h4_h5_evidence_bar_met: Any = None) -> bool:
    """Treat any H4/H5 market-implied growth above evidence as a valuation gap."""
    implied_order = _growth_order(implied)
    supported_order = _growth_order(supported)
    if implied_order < 4:
        return False
    if supported_order < implied_order:
        return True
    return h4_h5_evidence_bar_met is False


def _load_json(path: str) -> dict[str, Any]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("dashboard JSON must be an object")
    return data


def _validate_date(value: Any, label: str) -> list[str]:
    if value == "YYYY-MM-DD":
        return []
    try:
        dt.date.fromisoformat(str(value))
        return []
    except ValueError:
        return [f"{label} must be YYYY-MM-DD"]


def _missing_required(obj: Mapping[str, Any], required: set[str], label: str) -> list[str]:
    return [f"{label} missing required key: {key}" for key in sorted(required) if key not in obj]


def _non_empty(value: Any) -> bool:
    return bool(str(value).strip())


def validate_dashboard(data: Mapping[str, Any]) -> None:
    errors: list[str] = []
    errors.extend(_missing_required(data, REQUIRED_ROOT, "dashboard"))

    if data.get("market") not in MARKETS:
        errors.append(f"market must be one of {sorted(MARKETS)}, got {data.get('market')!r}")
    if data.get("rating") not in RATINGS:
        errors.append(f"rating must be one of {sorted(RATINGS)}, got {data.get('rating')!r}")
    if data.get("rating_cap") not in RATINGS:
        errors.append(f"rating_cap must be one of {sorted(RATINGS)}, got {data.get('rating_cap')!r}")
    if "as_of_date" in data:
        errors.extend(_validate_date(data.get("as_of_date"), "as_of_date"))

    thesis = data.get("thesis", {})
    if not isinstance(thesis, Mapping):
        errors.append("thesis must be an object")
        thesis = {}
    errors.extend(_missing_required(thesis, REQUIRED_THESIS, "thesis"))
    implied = str(thesis.get("market_implied_growth", "UNKNOWN"))
    supported = str(thesis.get("evidence_supported_growth", "UNKNOWN"))
    if implied not in GROWTH:
        errors.append(f"thesis.market_implied_growth must be one of {sorted(GROWTH)}, got {implied!r}")
    if supported not in GROWTH:
        errors.append(f"thesis.evidence_supported_growth must be one of {sorted(GROWTH)}, got {supported!r}")

    monitors = data.get("monitors", [])
    if not isinstance(monitors, list) or not monitors:
        errors.append("monitors must be a non-empty array")
        monitors = []
    valuation_monitor = False
    for idx, monitor in enumerate(monitors):
        label = f"monitors[{idx}]"
        if not isinstance(monitor, Mapping):
            errors.append(f"{label} must be an object")
            continue
        errors.extend(_missing_required(monitor, REQUIRED_MONITOR, label))
        for key in REQUIRED_MONITOR:
            if key in monitor and not _non_empty(monitor.get(key)):
                errors.append(f"{label}.{key} must not be empty")
        if monitor.get("category") not in MONITOR_CATEGORIES:
            errors.append(f"{label}.category must be one of {sorted(MONITOR_CATEGORIES)}")
        if monitor.get("status") not in MONITOR_STATUSES:
            errors.append(f"{label}.status must be one of {sorted(MONITOR_STATUSES)}")
        valuation_monitor = valuation_monitor or monitor.get("category") == "valuation"

    for idx, item in enumerate(data.get("evidence_links", []) or []):
        label = f"evidence_links[{idx}]"
        if not isinstance(item, Mapping):
            errors.append(f"{label} must be an object")
            continue
        if item.get("source_level") not in SOURCE_LEVELS:
            errors.append(f"{label}.source_level must be one of {sorted(SOURCE_LEVELS)}")
        if item.get("confidence") and item.get("confidence") not in CONFIDENCE:
            errors.append(f"{label}.confidence must be one of {sorted(CONFIDENCE)}")

    if _has_h4_h5_valuation_gap(implied, supported, thesis.get("h4_h5_evidence_bar_met")):
        if data.get("rating_cap") in {"S", "A"}:
            errors.append("H4/H5 market-implied growth with weaker evidence requires rating_cap B or lower")
        if not valuation_monitor:
            errors.append("H4/H5 market-implied growth with weaker evidence requires a valuation monitor")

    if errors:
        raise ValueError("; ".join(errors))


def build_from_output_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    route = contract.get("market_route", {}) if isinstance(contract.get("market_route"), Mapping) else {}
    growth = contract.get("growth_hypothesis", {}) if isinstance(contract.get("growth_hypothesis"), Mapping) else {}
    symbol = str(route.get("normalized_symbol") or contract.get("symbol") or "UNKNOWN")
    market = str(route.get("market") or "UNKNOWN")
    implied = str(growth.get("market_implied") or "UNKNOWN")
    supported = str(growth.get("evidence_supported") or "UNKNOWN")
    h4_h5_evidence_bar_met = growth.get("h4_h5_evidence_bar_met")

    monitors: list[dict[str, Any]] = []
    if _has_h4_h5_valuation_gap(implied, supported, h4_h5_evidence_bar_met):
        monitors.append({
            "id": "F00",
            "category": "valuation",
            "claim": f"Market-implied growth is {implied}, but evidence currently supports {supported}.",
            "falsification_trigger": "Primary filings do not close the gap between market-implied growth and evidence-supported growth.",
            "source_required": "Primary filings, exchange announcements, official guidance, or audited customer/order evidence.",
            "check_frequency": "Each filing cycle and material announcement.",
            "status": "WATCH",
            "latest_check": contract.get("as_of_date", "YYYY-MM-DD"),
            "action_if_triggered": "Keep rating cap at B or lower and do not upgrade to core candidate.",
        })

    for idx, trigger in enumerate(contract.get("falsification", []) or [], start=1):
        monitors.append({
            "id": f"F{idx:02d}",
            "category": "source_quality",
            "claim": "Thesis remains valid only if this falsification point is not triggered.",
            "falsification_trigger": str(trigger),
            "source_required": "Market-specific primary disclosure or cross-source verification.",
            "check_frequency": "At each update.",
            "status": "UNKNOWN",
            "latest_check": contract.get("as_of_date", "YYYY-MM-DD"),
            "action_if_triggered": "Reassess thesis, rating cap, and action framework.",
        })

    evidence_links = []
    for item in contract.get("evidence", []) or []:
        if not isinstance(item, Mapping):
            continue
        evidence_links.append({
            "claim": str(item.get("claim", "")),
            "source": str(item.get("source", "")),
            "source_level": str(item.get("source_level", "L4")),
            "confidence": str(item.get("confidence", "Unverified")),
        })

    dashboard = {
        "symbol": symbol,
        "company": str(contract.get("company", "")),
        "market": market,
        "currency": str(route.get("currency", "")),
        "as_of_date": str(contract.get("as_of_date", "YYYY-MM-DD")),
        "rating": str(contract.get("rating", "OBSERVE_ONLY")),
        "rating_cap": str(contract.get("rating_cap", "OBSERVE_ONLY")),
        "thesis": {
            "summary": str(contract.get("thesis", "Track whether the research thesis remains supported by primary evidence.")),
            "market_implied_growth": implied,
            "evidence_supported_growth": supported,
            "h4_h5_evidence_bar_met": h4_h5_evidence_bar_met,
            "key_assumption": str(growth.get("posterior_basis", "")),
            "missing_proof": str(contract.get("uncertainty", {}).get("missing", "") if isinstance(contract.get("uncertainty"), Mapping) else ""),
        },
        "monitors": monitors or [{
            "id": "F01",
            "category": "source_quality",
            "claim": "No explicit falsification trigger was provided.",
            "falsification_trigger": "Missing falsification points prevent upgrade beyond observe-only review quality.",
            "source_required": "Explicit falsification conditions in the research output.",
            "check_frequency": "Before delivery.",
            "status": "TRIGGERED",
            "latest_check": str(contract.get("as_of_date", "YYYY-MM-DD")),
            "action_if_triggered": "Add concrete falsification points before treating the report as complete.",
        }],
        "evidence_links": evidence_links,
    }
    validate_dashboard(dashboard)
    return dashboard


def to_markdown(data: Mapping[str, Any]) -> str:
    thesis = data.get("thesis", {}) if isinstance(data.get("thesis"), Mapping) else {}
    lines = [
        f"# Falsification Dashboard: {data.get('symbol', 'UNKNOWN')}",
        "",
        f"- Market: {data.get('market', '')}",
        f"- As of: {data.get('as_of_date', '')}",
        f"- Rating: {data.get('rating', '')}",
        f"- Rating cap: {data.get('rating_cap', '')}",
        f"- Market-implied growth: {thesis.get('market_implied_growth', '')}",
        f"- Evidence-supported growth: {thesis.get('evidence_supported_growth', '')}",
        "",
        "## Thesis",
        str(thesis.get("summary", "")),
        "",
        "## Monitors",
        "| ID | Category | Status | Trigger | Action |",
        "|---|---|---|---|---|",
    ]
    for monitor in data.get("monitors", []) or []:
        if not isinstance(monitor, Mapping):
            continue
        lines.append(
            "| {id} | {category} | {status} | {trigger} | {action} |".format(
                id=monitor.get("id", ""),
                category=monitor.get("category", ""),
                status=monitor.get("status", ""),
                trigger=str(monitor.get("falsification_trigger", "")).replace("|", "\\|"),
                action=str(monitor.get("action_if_triggered", "")).replace("|", "\\|"),
            )
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build or validate a falsification dashboard")
    parser.add_argument("input", help="Dashboard JSON path, output-contract JSON path, or '-' for stdin")
    parser.add_argument("--from-output-contract", action="store_true", help="build dashboard from output contract JSON")
    parser.add_argument("--format", choices=["json", "md", "both"], default="json")
    parser.add_argument("--out", help="write normalized dashboard JSON to this path")
    parser.add_argument("--validate-only", action="store_true", help="validate and exit")
    args = parser.parse_args(argv)

    try:
        source = _load_json(args.input)
        dashboard = build_from_output_contract(source) if args.from_output_contract else source
        validate_dashboard(dashboard)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.out:
        Path(args.out).write_text(json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.validate_only:
        print("OK: falsification dashboard")
        return 0
    if args.format == "json":
        print(json.dumps(dashboard, ensure_ascii=False, indent=2))
    elif args.format == "md":
        print(to_markdown(dashboard))
    else:
        print(json.dumps(dashboard, ensure_ascii=False, indent=2))
        print("\n---\n")
        print(to_markdown(dashboard))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
