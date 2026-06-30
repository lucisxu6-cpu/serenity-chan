#!/usr/bin/env python3
"""Build a structured workspace for executing Serenity AI overlay work items."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from validate_ai_overlay import evidence_context_from_manifest
    from validate_agent_research_queue import validate_agent_research_queue
except ModuleNotFoundError:  # pragma: no cover
    from scripts.validate_ai_overlay import evidence_context_from_manifest
    from scripts.validate_agent_research_queue import validate_agent_research_queue


def _load_json(path: Path) -> Mapping[str, Any]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _maybe_load_json(path_text: Any) -> Mapping[str, Any]:
    path: Path = Path(str(path_text or ""))
    if not str(path_text or "").strip() or not path.exists():
        return {}
    return _load_json(path)


def _source_ref_catalog(manifest_path: Path) -> list[dict[str, Any]]:
    context: Mapping[str, str] = evidence_context_from_manifest(manifest_path)
    rows: list[dict[str, Any]] = []
    for key in sorted(context):
        key_text: str = str(key)
        if not key_text.strip():
            continue
        text: str = str(context.get(key) or "")
        rows.append({
            "source_ref": key_text,
            "text_available": bool(text.strip()),
            "text_bytes": len(text.encode("utf-8")),
        })
    return rows


def _customer_evidence_workspace(review_packet: Mapping[str, Any]) -> dict[str, Any]:
    row_value: Any = review_packet.get("customer_order_capacity_evidence", {})
    row: Mapping[str, Any] = row_value if isinstance(row_value, Mapping) else {}
    return {
        "summary": {
            "status": str(row.get("status") or ""),
            "evidence_status": str(row.get("evidence_status") or ""),
            "score": row.get("score"),
            "direct_evidence_count": row.get("direct_evidence_count"),
            "lead_evidence_count": row.get("lead_evidence_count"),
            "review_queue_count": row.get("review_queue_count"),
            "loaded_record_count": row.get("loaded_record_count"),
        },
        "matrix_row": dict(row),
        "usage_rule": "direct evidence requires reading the referenced source before support; leads and review queue items become research questions, next evidence, invalidation, and gates.",
    }


def _workspace_item(work_item: Mapping[str, Any]) -> dict[str, Any]:
    symbol: str = str(work_item.get("symbol") or "")
    manifest_path: Path = Path(str(work_item.get("manifest_path") or ""))
    review_packet: Mapping[str, Any] = _maybe_load_json(work_item.get("review_packet"))
    committee_packet: Mapping[str, Any] = _maybe_load_json(work_item.get("committee_packet"))
    overlay_prompt: Mapping[str, Any] = _maybe_load_json(work_item.get("overlay_prompt"))
    return {
        "symbol": symbol,
        "required_action": str(work_item.get("required_action") or "produce_validated_ai_overlay_or_outcome"),
        "manifest_path": str(manifest_path),
        "review_packet_path": str(work_item.get("review_packet") or ""),
        "committee_packet_path": str(work_item.get("committee_packet") or ""),
        "overlay_prompt_path": str(work_item.get("overlay_prompt") or ""),
        "overlay_output_path": str(work_item.get("overlay_output_path") or ""),
        "outcome_output_path": str(work_item.get("outcome_output_path") or ""),
        "allowed_results": list(work_item.get("allowed_results") or []),
        "validation_commands": list(work_item.get("validation_commands") or []),
        "source_ref_catalog": _source_ref_catalog(manifest_path) if manifest_path.exists() else [],
        "customer_order_capacity_evidence": _customer_evidence_workspace(review_packet),
        "deterministic_matrices": review_packet.get("deterministic_matrices", {}),
        "committee_roles": committee_packet.get("committee_roles", []),
        "overlay_contract": overlay_prompt.get("expected_output", {}),
        "hard_constraints": overlay_prompt.get("hard_constraints", []),
    }


def build_workspace(agent_queue_path: Path) -> dict[str, Any]:
    queue: Mapping[str, Any] = _load_json(agent_queue_path)
    errors: list[str] = validate_agent_research_queue(queue)
    if errors:
        raise ValueError("; ".join(errors))
    work_items: list[Any] = list(queue.get("work_items") or []) if isinstance(queue.get("work_items"), list) else []
    return {
        "contract_type": "serenity_agent_overlay_workspace",
        "schema_version": "1.0",
        "agent_queue_path": str(agent_queue_path.resolve()),
        "workflow_status": str(queue.get("workflow_status") or ""),
        "work_item_count": len(work_items),
        "workspaces": [
            _workspace_item(item)
            for item in work_items
            if isinstance(item, Mapping)
        ],
        "execution_rule": "The AI reviewer writes one validated overlay or one validated review outcome per workspace; this file only organizes evidence and never creates research judgment.",
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Build an AI overlay execution workspace from an agent research queue")
    parser.add_argument("agent_queue", help="agent_research_queue.json")
    parser.add_argument("--out", help="write workspace JSON")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        payload: dict[str, Any] = build_workspace(Path(args.agent_queue))
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
