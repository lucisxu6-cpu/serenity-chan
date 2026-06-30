#!/usr/bin/env python3
"""Build a strategy judgment prompt package from a validated Laplace input."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from validate_laplace_strategy_input import validate_strategy_input
except ModuleNotFoundError:  # pragma: no cover
    from scripts.validate_laplace_strategy_input import validate_strategy_input


def _load_json(path: Path) -> Mapping[str, Any]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def build_strategy_prompt(strategy_input_path: Path) -> dict[str, Any]:
    strategy_input: Mapping[str, Any] = _load_json(strategy_input_path)
    errors: list[str] = validate_strategy_input(strategy_input)
    if errors:
        raise ValueError("; ".join(errors))
    return {
        "packet_type": "serenity_laplace_strategy_prompt",
        "schema_version": "1.0",
        "source_strategy_input_path": str(strategy_input_path.resolve()),
        "instruction": (
            "Use the validated Serenity strategy input and the bundled laplace-forecast companion skill to produce one JSON "
            "object matching assets/laplace_strategy_judgment.schema.json. Write user-facing fields in Chinese. Keep "
            "Observed, Inferred, and Judgment separate. Do not override Serenity hard gates, ranking validity, data debt, "
            "or candidate-pool coherence. Convert research debt into next evidence, triggers, invalidation, and action discipline."
        ),
        "required_reads": [
            "references/16_laplace_strategy_bridge.md",
            "companion-skills/laplace-forecast/SKILL.md",
            "companion-skills/laplace-forecast/references/first-order-lenses.md",
            "companion-skills/laplace-forecast/references/evidence-loop.md",
        ],
        "expected_output": {
            "json_only": True,
            "schema_path": "assets/laplace_strategy_judgment.schema.json",
            "validate_with": "python scripts/validate_laplace_strategy_judgment.py <judgment.json> --strategy-input <laplace_strategy_input.json>",
            "render_with": "python scripts/render_strategy_report.py <judgment.json> --strategy-input <laplace_strategy_input.json>",
        },
        "hard_constraints": [
            "Use Serenity observed_facts for observed statements.",
            "Keep inferred implications separate from judgment weights and scenario probabilities.",
            "Do not turn a research lead into an action candidate when Serenity gates are active.",
            "If ranking_validity is PARTIAL or INVALID, action_plan must remain gated and evidence-first.",
            "Ledger claims must be observable and resolvable; use an empty array only when no useful claim can be defined.",
        ],
        "strategy_input": dict(strategy_input),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Build a Laplace strategy judgment prompt")
    parser.add_argument("strategy_input", help="laplace_strategy_input.json")
    parser.add_argument("--out", help="write prompt JSON")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        payload: dict[str, Any] = build_strategy_prompt(Path(args.strategy_input))
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
