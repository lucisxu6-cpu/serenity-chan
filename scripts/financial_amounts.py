#!/usr/bin/env python3
"""Normalize reported financial statement units into absolute currency amounts."""

from __future__ import annotations

from typing import Any, Optional


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number: float = float(str(value).replace(",", ""))
    except Exception:
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def financial_unit_multiplier(unit: Any) -> float:
    """Return the amount multiplier implied by a reporting unit label."""

    text: str = str(unit or "").strip().lower()
    if not text:
        return 1.0

    normalized: str = (
        text.replace("（", "(")
        .replace("）", ")")
        .replace("，", ",")
        .replace(" ", "")
        .replace("_", "")
        .replace("-", "")
    )

    if any(token in normalized for token in ["十亿元", "billion", "billions"]):
        return 1_000_000_000.0
    if any(token in normalized for token in ["亿元", "亿人民币", "亿港元", "亿美元", "hundredmillion"]):
        return 100_000_000.0
    if any(token in normalized for token in ["百万元", "百万", "million", "millions"]):
        return 1_000_000.0
    if any(token in normalized for token in ["千元", "thousand", "thousands", "'000", "000s"]):
        return 1_000.0
    if any(token in normalized for token in ["万元", "万人民币", "万港元", "万美元"]):
        return 10_000.0
    return 1.0


def normalize_financial_amount(value: Any, unit: Any) -> Optional[float]:
    number: Optional[float] = _as_float(value)
    if number is None:
        return None
    return number * financial_unit_multiplier(unit)
