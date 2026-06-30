#!/usr/bin/env python3
"""Render a validated Serenity Laplace strategy judgment as Chinese Markdown."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from validate_laplace_strategy_judgment import validate_strategy_judgment
except ModuleNotFoundError:  # pragma: no cover
    from scripts.validate_laplace_strategy_judgment import validate_strategy_judgment


def _load_json(path: Path) -> Mapping[str, Any]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _bullet_lines(items: Any) -> list[str]:
    return [f"- {item}" for item in _as_list(items) if str(item).strip()]


def render_strategy_report(judgment: Mapping[str, Any], *, strategy_input_path: Optional[Path] = None) -> str:
    errors: list[str] = validate_strategy_judgment(judgment, strategy_input_path=strategy_input_path)
    if errors:
        raise ValueError("; ".join(errors))
    lines: list[str] = [
        "# 策略预测与行动计划",
        "",
        "## Forecast",
        str(judgment.get("forecast") or ""),
        "",
        "## Decision",
        str(judgment.get("decision") or ""),
        "",
        "## Decision Model",
        str(judgment.get("decision_model") or ""),
        "",
        "## Observed",
        *_bullet_lines(judgment.get("observed")),
        "",
        "## Inferred",
        *_bullet_lines(judgment.get("inferred")),
        "",
        "## Judgment",
        *_bullet_lines(judgment.get("judgment")),
        "",
        "## Dominant Variables",
        "| 变量 | 角色 | 方向 | 置信度 | 原因 |",
        "|---|---|---|---|---|",
    ]
    for row in _as_list(judgment.get("dominant_variables")):
        if isinstance(row, Mapping):
            lines.append(f"| {row.get('variable')} | {row.get('role')} | {row.get('direction')} | {row.get('confidence')} | {row.get('why')} |")
    lines.extend(["", "## Scenarios", "| 情景 | 概率 | 摘要 | 条件 |", "|---|---:|---|---|"])
    scenarios: Mapping[str, Any] = _as_mapping(judgment.get("scenarios"))
    for key, label in [("base", "Base"), ("upside", "Upside"), ("downside", "Downside")]:
        row: Mapping[str, Any] = _as_mapping(scenarios.get(key))
        conditions: str = "；".join(str(item) for item in _as_list(row.get("conditions")) if str(item).strip())
        lines.append(f"| {label} | {row.get('probability')} | {row.get('summary')} | {conditions} |")
    lines.extend(["", "## Triggers"])
    triggers: Mapping[str, Any] = _as_mapping(judgment.get("triggers"))
    for key in ["30d", "90d", "180d"]:
        lines.append(f"### {key}")
        lines.extend(_bullet_lines(triggers.get(key)))
    lines.extend(["", "## Invalidation", *_bullet_lines(judgment.get("invalidation"))])
    lines.extend(["", "## Next Evidence", *_bullet_lines(judgment.get("next_evidence"))])
    lines.extend(["", "## Action Plan", *_bullet_lines(judgment.get("action_plan"))])
    lines.extend(["", "## Confidence", f"- {judgment.get('confidence')}"])
    ledger_claims: list[Any] = _as_list(judgment.get("ledger_claims"))
    if ledger_claims:
        lines.extend(["", "## Ledger Claims", "| Claim | Probability | Resolution Date | Criteria |", "|---|---:|---|---|"])
        for row in ledger_claims:
            if isinstance(row, Mapping):
                lines.append(f"| {row.get('claim')} | {row.get('probability')} | {row.get('resolution_date')} | {row.get('resolution_criteria')} |")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Render a Laplace strategy judgment")
    parser.add_argument("judgment", help="strategy judgment JSON")
    parser.add_argument("--strategy-input", help="validated laplace_strategy_input.json")
    parser.add_argument("--out", help="write Markdown")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        markdown: str = render_strategy_report(
            _load_json(Path(args.judgment)),
            strategy_input_path=Path(args.strategy_input) if args.strategy_input else None,
        )
        if args.out:
            Path(args.out).write_text(markdown, encoding="utf-8")
        else:
            print(markdown, end="")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
