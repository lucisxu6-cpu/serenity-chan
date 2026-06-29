#!/usr/bin/env python3
"""Validate a Serenity theme candidate universe."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


def _load_json(path: Path) -> Mapping[str, Any]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _as_list(value: Any, label: str, errors: list[str]) -> list[Any]:
    if isinstance(value, list):
        return value
    errors.append(f"{label} must be an array")
    return []


def _as_mapping(value: Any, label: str, errors: list[str]) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    errors.append(f"{label} must be an object")
    return {}


def validate_universe(payload: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("contract_type") != "serenity_theme_candidate_universe":
        errors.append("contract_type must be serenity_theme_candidate_universe")
    if payload.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    for key in ["theme", "generated_at", "universe_source"]:
        if not str(payload.get(key) or "").strip():
            errors.append(f"{key} must not be empty")
    layers: list[Any] = _as_list(payload.get("value_chain_layers"), "value_chain_layers", errors)
    if len(layers) < 3:
        errors.append("value_chain_layers must contain at least 3 layers")
    candidate_symbols: set[str] = set()
    duplicated_layer_symbols: set[str] = set()
    for index, item in enumerate(layers):
        row: Mapping[str, Any] = _as_mapping(item, f"value_chain_layers[{index}]", errors)
        for key in ["layer", "bottleneck_question", "evidence_to_seek", "candidate_symbols"]:
            if key not in row:
                errors.append(f"value_chain_layers[{index}] missing {key}")
        for symbol in _as_list(row.get("candidate_symbols"), f"value_chain_layers[{index}].candidate_symbols", errors):
            if str(symbol or "").strip():
                symbol_text: str = str(symbol)
                if symbol_text in candidate_symbols:
                    duplicated_layer_symbols.add(symbol_text)
                candidate_symbols.add(symbol_text)
    if duplicated_layer_symbols:
        errors.append(f"value_chain_layers duplicate candidate symbols: {sorted(duplicated_layer_symbols)}")
    candidates: list[Any] = _as_list(payload.get("candidate_universe"), "candidate_universe", errors)
    if len(candidates) < 9:
        errors.append("candidate_universe must contain at least 9 candidates")
    universe_symbols: set[str] = set()
    duplicated_universe_symbols: set[str] = set()
    for index, item in enumerate(candidates):
        row = _as_mapping(item, f"candidate_universe[{index}]", errors)
        for key in ["symbol", "market", "name", "layer", "why_in_universe", "initial_evidence_need"]:
            if not str(row.get(key) or "").strip():
                errors.append(f"candidate_universe[{index}].{key} must not be empty")
        market: str = str(row.get("market") or "")
        if market not in {"CN_A", "US", "HK", "GLOBAL", "OTHER"}:
            errors.append(f"candidate_universe[{index}].market is unknown: {market}")
        if str(row.get("symbol") or "").strip():
            symbol_text = str(row.get("symbol"))
            if symbol_text in universe_symbols:
                duplicated_universe_symbols.add(symbol_text)
            universe_symbols.add(symbol_text)
    if duplicated_universe_symbols:
        errors.append(f"candidate_universe duplicate symbols: {sorted(duplicated_universe_symbols)}")
    missing: set[str] = candidate_symbols - universe_symbols
    if missing:
        errors.append(f"layer candidate_symbols missing from candidate_universe: {sorted(missing)}")
    downgraded: list[Any] = _as_list(payload.get("downgraded_hot_directions"), "downgraded_hot_directions", errors)
    if not downgraded:
        errors.append("downgraded_hot_directions must not be empty")
    tasks: list[Any] = _as_list(payload.get("ai_expansion_tasks"), "ai_expansion_tasks", errors)
    if not tasks:
        errors.append("ai_expansion_tasks must not be empty")
    return errors


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Validate theme candidate universe JSON")
    parser.add_argument("universe")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        errors: list[str] = validate_universe(_load_json(Path(args.universe)))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("OK: theme candidate universe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
