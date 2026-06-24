#!/usr/bin/env python3
"""Build a compact AI review packet from a fetch manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from build_comparison_report import (
        _capital_summary,
        _data_summary,
        _financial_quality,
        _growth_hypothesis,
        _load_manifest,
        _research_debt_rows,
        _serenity_layer,
        _symbol,
        _technical_summary,
        _valuation_input_row,
        _valuation_payload,
    )
except ModuleNotFoundError:  # pragma: no cover
    from scripts.build_comparison_report import (
        _capital_summary,
        _data_summary,
        _financial_quality,
        _growth_hypothesis,
        _load_manifest,
        _research_debt_rows,
        _serenity_layer,
        _symbol,
        _technical_summary,
        _valuation_input_row,
        _valuation_payload,
    )


def _result_summaries(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in manifest.get("results", []) if isinstance(manifest.get("results"), list) else []:
        if not isinstance(item, Mapping):
            continue
        validation = item.get("validation") if isinstance(item.get("validation"), Mapping) else {}
        rows.append({
            "dataset": item.get("dataset", ""),
            "status": item.get("status", ""),
            "source": item.get("source", ""),
            "source_level": item.get("source_level", ""),
            "as_of_date": item.get("as_of_date", ""),
            "data_path": item.get("data_path", ""),
            "raw_path": item.get("raw_path", ""),
            "raw_hash": item.get("raw_hash", ""),
            "warnings": item.get("warnings", []) if isinstance(item.get("warnings", []), list) else [],
            "errors": item.get("errors", []) if isinstance(item.get("errors", []), list) else [],
            "validation_status": validation.get("status", ""),
            "validation_warnings": validation.get("warnings", []) if isinstance(validation.get("warnings", []), list) else [],
        })
    return rows


def build_ai_review_packet(manifest_path: Path) -> dict[str, Any]:
    manifest = _load_manifest(manifest_path)
    data_summary = _data_summary(manifest)
    financial = _financial_quality(manifest)
    technical = _technical_summary(manifest)
    capital = _capital_summary(manifest)
    layer_seed = _serenity_layer(manifest, {})
    growth = _growth_hypothesis(manifest, financial, {})
    research_debt = _research_debt_rows(manifest, capital, financial, technical, layer_seed, growth)
    valuation_inputs = dict(_valuation_payload(manifest))
    valuation_input_matrix_row = _valuation_input_row(manifest)
    acquisition = manifest.get("data_acquisition") if isinstance(manifest.get("data_acquisition"), Mapping) else {}
    ai_questions = [
        "Identify the value-chain layer and the concrete bottleneck this company may control.",
        "Map product, customer, order, capacity, and segment disclosures to revenue transmission.",
        "Judge whether evidence-supported growth reaches the market-implied growth tier.",
        "Name contrary evidence and falsification triggers that would downgrade the thesis.",
        "Return an overlay matching assets/ai_research_overlay.schema.json.",
    ]
    return {
        "symbol": _symbol(manifest),
        "manifest_path": str(manifest_path.resolve()),
        "as_of": manifest.get("retrieved_at", ""),
        "data_quality": manifest.get("data_quality", {}),
        "data_acquisition": {
            "status_by_dataset": acquisition.get("status_by_dataset", {}),
            "data_gaps": acquisition.get("data_gaps", []),
            "research_debt": acquisition.get("research_debt", []),
            "manual_retrieval_tasks": acquisition.get("manual_retrieval_tasks", []),
            "full_research_ready": acquisition.get("full_research_ready", False),
        },
        "source_artifacts": _result_summaries(manifest),
        "valuation_inputs": valuation_inputs,
        "valuation_input_matrix_row": valuation_input_matrix_row,
        "open_research_debt": research_debt,
        "deterministic_matrices": {
            "data_summary": data_summary,
            "financial_quality": financial,
            "valuation_input": valuation_input_matrix_row,
            "technical_timing": technical,
            "capital_actions": capital,
            "growth_hypothesis": growth,
            "open_research_debt": research_debt,
        },
        "required_ai_questions": ai_questions,
        "ai_research_questions": ai_questions,
        "expected_overlay_contract": {
            "required": [
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
            ],
            "score_scale": "serenity_fit is 0-1; layer_score/company_fit are 0-100 when supplied.",
            "growth_contract": "market_implied_growth is produced by deterministic valuation matrices; overlay supplies evidence_supported_growth when research evidence supports a growth tier.",
        },
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build an AI review packet from a Serenity + Chan fetch manifest")
    parser.add_argument("manifest", help="fetch manifest JSON path")
    parser.add_argument("--out", help="optional output JSON path")
    args = parser.parse_args(argv)
    try:
        packet = build_ai_review_packet(Path(args.manifest))
        text = json.dumps(packet, ensure_ascii=False, indent=2)
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
