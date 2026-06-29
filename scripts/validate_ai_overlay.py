#!/usr/bin/env python3
"""Validate an AI research overlay before it can affect ranking or action gates."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


REQUIRED_FIELDS = {
    "symbol",
    "as_of_date",
    "layer",
    "bottleneck_reason",
    "revenue_transmission",
    "serenity_fit",
    "key_evidence_refs",
    "contrary_evidence",
    "research_questions",
    "ai_confidence",
}
ALLOWED_FIELDS = REQUIRED_FIELDS | {
    "layer_score",
    "company_fit",
    "evidence_supported_growth",
    "h4_h5_evidence_bar_met",
    "required_next_evidence",
    "posterior_basis",
}
SOURCE_LEVELS = {"L0", "L1", "L2", "L3", "L4"}
AI_CONFIDENCE = {"LOW", "MEDIUM", "HIGH"}
GROWTH = {"H0", "H1", "H2", "H3", "H4", "H5", "UNKNOWN"}
EVIDENCE_TEXT_LIMIT_BYTES: int = 2_000_000


def _as_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except Exception:
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any, label: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{label} must be an array")
        return []
    result: list[str] = []
    for item in value:
        if not _is_non_empty_string(item):
            errors.append(f"{label} must contain only non-empty strings")
            continue
        result.append(item.strip())
    return result


def _optional_score(payload: Mapping[str, Any], key: str, errors: list[str]) -> Optional[float]:
    if key not in payload or payload.get(key) is None:
        return None
    score = _as_float(payload.get(key))
    if score is None or score < 0 or score > 100:
        errors.append(f"{key} must be a number between 0 and 100")
        return None
    return round(float(score), 2)


def _compact_key(value: str) -> str:
    return re.sub(r"[^a-z0-9一-龥]+", "", value.lower())


def _read_text_artifact(path_text: str, manifest_dir: Optional[Path] = None) -> str:
    if not path_text:
        return ""
    path: Path = Path(path_text)
    if not path.is_absolute() and not path.exists() and manifest_dir is not None:
        path = manifest_dir / path
    if not path.exists() or not path.is_file():
        return ""
    try:
        raw: bytes = path.read_bytes()[:EVIDENCE_TEXT_LIMIT_BYTES]
        return raw.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _add_context_text(context: dict[str, str], key: str, text: Any) -> None:
    if not key or text in (None, ""):
        return
    if not isinstance(text, str):
        try:
            text = json.dumps(text, ensure_ascii=False, sort_keys=True)
        except Exception:
            text = str(text)
    normalized_key: str = _compact_key(key)
    if not normalized_key:
        return
    for context_key in dict.fromkeys([key, normalized_key]):
        existing: str = context.get(context_key, "")
        context[context_key] = f"{existing}\n{text}" if existing else str(text)


def evidence_context_from_manifest(manifest_path: Path) -> dict[str, str]:
    raw_manifest: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw_manifest, Mapping):
        raise ValueError(f"{manifest_path} must contain a JSON object")
    context: dict[str, str] = {}
    manifest_dir: Path = manifest_path.parent
    _add_context_text(context, "manifest", raw_manifest)
    for item in raw_manifest.get("results", []) if isinstance(raw_manifest.get("results"), list) else []:
        if not isinstance(item, Mapping):
            continue
        dataset: str = str(item.get("dataset") or "")
        source: str = str(item.get("source") or "")
        source_level: str = str(item.get("source_level") or "")
        aliases: list[str] = [
            dataset,
            source,
            f"{dataset}:{source}",
            f"{source} {dataset}",
            f"{source_level}:{dataset}",
        ]
        if dataset == "financials" and "cninfo" in source.lower():
            aliases.extend([
                "CNINFO annual report line extraction",
                "CNINFO financial report extraction",
                "CNINFO official financial rows",
            ])
        if dataset == "financials" and "sec" in source.lower():
            aliases.extend([
                "SEC companyfacts",
                "SEC financial facts",
                "SEC official financial rows",
            ])
        if dataset == "financials" and "hkex" in source.lower():
            aliases.extend([
                "HKEX annual report line extraction",
                "HKEX financial report extraction",
                "HKEX official financial rows",
            ])
        for path_key in ("data_path", "raw_path"):
            path_text: str = str(item.get(path_key) or "")
            if path_text:
                aliases.append(path_text)
                aliases.append(Path(path_text).name)
                text: str = _read_text_artifact(path_text, manifest_dir)
                if text:
                    for alias in aliases:
                        _add_context_text(context, alias, text)
        _add_context_text(context, f"{dataset}:result_metadata", item)
    try:
        from build_ai_review_packet import build_ai_review_packet
    except ModuleNotFoundError:  # pragma: no cover
        from scripts.build_ai_review_packet import build_ai_review_packet

    packet: dict[str, Any] = build_ai_review_packet(manifest_path)
    _add_context_text(context, "ai_review_packet", packet)
    _add_context_text(context, "deterministic_matrices", packet.get("deterministic_matrices", {}))
    _add_context_text(context, "financial quality matrix", packet.get("deterministic_matrices", {}).get("financial_quality", {}))
    _add_context_text(context, "valuation input matrix", packet.get("valuation_input_matrix_row", {}))
    _add_context_text(context, "growth hypothesis matrix", packet.get("deterministic_matrices", {}).get("growth_hypothesis", {}))
    return context


def _resolve_source_text(source_ref: str, evidence_context: Mapping[str, str]) -> str:
    ref_key: str = _compact_key(source_ref)
    if not ref_key:
        return ""
    for key, text in evidence_context.items():
        context_key: str = _compact_key(str(key))
        if ref_key == context_key or ref_key in context_key or context_key in ref_key:
            return str(text)
    ref_terms: set[str] = {term for term in re.split(r"[^a-z0-9一-龥]+", source_ref.lower()) if len(term) >= 3}
    if not ref_terms:
        return ""
    best_text: str = ""
    best_overlap: int = 0
    for key, text in evidence_context.items():
        key_terms: set[str] = {term for term in re.split(r"[^a-z0-9一-龥]+", str(key).lower()) if len(term) >= 3}
        overlap: int = len(ref_terms & key_terms)
        if overlap > best_overlap:
            best_overlap = overlap
            best_text = str(text)
    return best_text if best_overlap >= 2 else ""


def _numeric_tokens(text: str) -> list[float]:
    values: list[float] = []
    for match in re.finditer(r"(?<![A-Za-z])[-+]?\d+(?:,\d{3})*(?:\.\d+)?%?(?![A-Za-z])", text):
        raw: str = match.group(0).replace(",", "").replace("%", "")
        try:
            values.append(float(raw))
        except Exception:
            continue
    return values


def _number_supported(claim_number: float, source_numbers: Sequence[float]) -> bool:
    for source_number in source_numbers:
        tolerance: float = max(0.02, abs(claim_number) * 0.005)
        if abs(source_number - claim_number) <= tolerance:
            return True
    return False


def _claim_terms(claim: str) -> set[str]:
    english_terms: set[str] = {term for term in re.split(r"[^a-z0-9]+", claim.lower()) if len(term) >= 4 and not term.isdigit()}
    chinese_terms: set[str] = set(re.findall(r"[\u4e00-\u9fff]{2,}", claim))
    return english_terms | chinese_terms


def _validate_evidence_support(
    evidence_refs: Sequence[Any],
    evidence_context: Mapping[str, str],
    errors: list[str],
    warnings: list[str],
) -> None:
    for idx, item in enumerate(evidence_refs):
        if not isinstance(item, Mapping):
            continue
        label: str = f"key_evidence_refs[{idx}]"
        source_ref: str = str(item.get("source_ref") or "")
        claim: str = str(item.get("claim") or "")
        source_text: str = _resolve_source_text(source_ref, evidence_context)
        if not source_text:
            errors.append(f"{label}.source_ref cannot be resolved to manifest evidence: {source_ref}")
            continue
        claim_numbers: list[float] = _numeric_tokens(claim)
        if claim_numbers:
            source_numbers: list[float] = _numeric_tokens(source_text)
            unsupported_numbers: list[str] = [
                str(number)
                for number in claim_numbers
                if not _number_supported(number, source_numbers)
            ]
            if unsupported_numbers:
                errors.append(f"{label}.claim numeric value(s) not found in resolved evidence: {', '.join(unsupported_numbers)}")
                continue
        terms: set[str] = _claim_terms(claim)
        if terms:
            normalized_source: str = source_text.lower()
            overlap_count: int = sum(1 for term in terms if term.lower() in normalized_source)
            if overlap_count == 0 and not claim_numbers:
                warnings.append(f"{label}.claim has weak lexical support in resolved evidence; keep the conclusion conservative")


def validate_overlay(payload: Mapping[str, Any], evidence_context: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    missing = sorted(REQUIRED_FIELDS - set(payload))
    if missing:
        errors.append(f"overlay missing required keys: {', '.join(missing)}")
    unsupported = sorted(set(payload) - ALLOWED_FIELDS)
    if unsupported:
        errors.append(f"overlay contains unsupported keys: {', '.join(unsupported)}")

    for key in ["symbol", "as_of_date", "layer", "bottleneck_reason", "revenue_transmission"]:
        if key in payload and not _is_non_empty_string(payload.get(key)):
            errors.append(f"{key} must be a non-empty string")
    layer_value: str = str(payload.get("layer") or "").strip().upper()
    if layer_value in {"VALUE_CHAIN_UNMAPPED", "AI_REVIEW_REQUIRED"}:
        errors.append("layer must be a concrete value-chain layer for a completed overlay")
    for key in ["required_next_evidence", "posterior_basis"]:
        if key in payload and payload.get(key) is not None and not _is_non_empty_string(payload.get(key)):
            errors.append(f"{key} must be a non-empty string when supplied")

    serenity_fit = _as_float(payload.get("serenity_fit"))
    if serenity_fit is None or serenity_fit < 0 or serenity_fit > 1:
        errors.append("serenity_fit must be a number between 0 and 1")
        serenity_fit = 0.0

    ai_confidence = str(payload.get("ai_confidence") or "")
    if ai_confidence not in AI_CONFIDENCE:
        errors.append(f"ai_confidence must be one of {sorted(AI_CONFIDENCE)}")

    evidence_refs = payload.get("key_evidence_refs", [])
    if not isinstance(evidence_refs, list):
        errors.append("key_evidence_refs must be an array")
        evidence_refs = []
    if not evidence_refs:
        errors.append("key_evidence_refs must include at least one evidence reference")

    strong_primary_refs = 0
    for idx, item in enumerate(evidence_refs):
        label = f"key_evidence_refs[{idx}]"
        if not isinstance(item, Mapping):
            errors.append(f"{label} must be an object")
            continue
        for key in ["claim", "source_ref"]:
            if not _is_non_empty_string(item.get(key)):
                errors.append(f"{label}.{key} must be a non-empty string")
        level = str(item.get("source_level") or "")
        if level not in SOURCE_LEVELS:
            errors.append(f"{label}.source_level must be one of {sorted(SOURCE_LEVELS)}")
        confidence = _as_float(item.get("confidence"))
        if confidence is None or confidence < 0 or confidence > 1:
            errors.append(f"{label}.confidence must be a number between 0 and 1")
            confidence = 0.0
        if level in {"L0", "L1"} and confidence >= 0.65:
            strong_primary_refs += 1
    if evidence_context is not None:
        _validate_evidence_support(evidence_refs, evidence_context, errors, warnings)

    contrary_evidence = _string_list(payload.get("contrary_evidence", []), "contrary_evidence", errors)
    if not contrary_evidence:
        errors.append("contrary_evidence must include at least one falsifiable contrary point")
    research_questions = _string_list(payload.get("research_questions", []), "research_questions", errors)
    if len(research_questions) < 2:
        errors.append("research_questions must include at least two concrete next questions")

    if (serenity_fit >= 0.72 or ai_confidence == "HIGH") and strong_primary_refs == 0:
        errors.append("high-fit or high-confidence overlay requires at least one L0/L1 evidence reference with confidence >= 0.65")

    supported = payload.get("evidence_supported_growth")
    if supported is not None and str(supported) not in GROWTH:
        errors.append(f"evidence_supported_growth must be one of {sorted(GROWTH)}")
    if str(supported) in {"H4", "H5"}:
        if payload.get("h4_h5_evidence_bar_met") is not True:
            errors.append("H4/H5 evidence_supported_growth requires h4_h5_evidence_bar_met=true")
        if strong_primary_refs == 0:
            errors.append("H4/H5 evidence_supported_growth requires at least one L0/L1 evidence reference with confidence >= 0.65")
    if "h4_h5_evidence_bar_met" in payload and not isinstance(payload.get("h4_h5_evidence_bar_met"), bool):
        errors.append("h4_h5_evidence_bar_met must be boolean when supplied")
    layer_score = _optional_score(payload, "layer_score", errors)
    company_fit = _optional_score(payload, "company_fit", errors)

    normalized = dict(payload)
    normalized["serenity_fit"] = round(float(serenity_fit or 0.0), 4)
    if layer_score is not None:
        normalized["layer_score"] = layer_score
    elif normalized.get("layer_score") is None:
        normalized["layer_score"] = round(normalized["serenity_fit"] * 100.0, 2)
    if company_fit is not None:
        normalized["company_fit"] = company_fit
    elif normalized.get("company_fit") is None:
        normalized["company_fit"] = round(normalized["serenity_fit"] * 100.0, 2)

    if errors:
        raise ValueError("; ".join(errors))
    return {"ok": True, "warnings": warnings, "normalized_overlay": normalized}


def _load_json(path: str) -> Mapping[str, Any]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, Mapping):
        raise ValueError("overlay JSON must be an object")
    return payload


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a Serenity + Chan AI research overlay")
    parser.add_argument("overlay", help="Overlay JSON path or '-' for stdin")
    parser.add_argument("--manifest", help="optional fetch manifest used to verify source_ref evidence support")
    parser.add_argument("--json", action="store_true", help="emit machine-readable validation result")
    args = parser.parse_args(argv)
    try:
        context: Optional[dict[str, str]] = evidence_context_from_manifest(Path(args.manifest)) if args.manifest else None
        result = validate_overlay(_load_json(args.overlay), evidence_context=context)
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"FAILED: {args.overlay}")
            print(f"- ERROR: {exc}")
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"OK: {args.overlay}")
        for warning in result.get("warnings", []):
            print(f"- WARNING: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
