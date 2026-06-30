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
            "text_excerpt": text.strip().replace("\n", " ")[:1200],
        })
    return rows


def _safe_source_ref_catalog(manifest_path: Path) -> list[dict[str, Any]]:
    if not manifest_path.is_file():
        return []
    try:
        return _source_ref_catalog(manifest_path)
    except Exception as exc:
        return [{
            "source_ref": "MANIFEST_ERROR",
            "text_available": False,
            "text_bytes": 0,
            "text_excerpt": f"Manifest could not be read: {exc}",
        }]


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


def _priority_research_tasks(review_packet: Mapping[str, Any]) -> list[str]:
    tasks: list[str] = []
    for row in review_packet.get("open_research_debt", []) if isinstance(review_packet.get("open_research_debt"), list) else []:
        if not isinstance(row, Mapping):
            continue
        action: str = str(row.get("next_action") or row.get("objective") or "").strip()
        if action and action not in tasks:
            tasks.append(action)
    customer_value: Any = review_packet.get("customer_order_capacity_evidence")
    customer: Mapping[str, Any] = customer_value if isinstance(customer_value, Mapping) else {}
    next_evidence: str = str(customer.get("required_next_evidence") or "").strip()
    if next_evidence and next_evidence not in tasks:
        tasks.append(next_evidence)
    return tasks


def _research_expansion_protocol(work_item: Mapping[str, Any], overlay_prompt: Mapping[str, Any]) -> list[str]:
    protocol: list[str] = []
    for source in [work_item.get("research_expansion_protocol", []), overlay_prompt.get("research_expansion_protocol", [])]:
        rows: list[Any] = list(source) if isinstance(source, list) else []
        for item in rows:
            text: str = str(item or "").strip()
            if text and text not in protocol:
                protocol.append(text)
    return protocol


def _workspace_item(work_item: Mapping[str, Any]) -> dict[str, Any]:
    symbol: str = str(work_item.get("symbol") or "")
    manifest_path: Path = Path(str(work_item.get("manifest_path") or ""))
    review_packet: Mapping[str, Any] = _maybe_load_json(work_item.get("review_packet"))
    committee_packet: Mapping[str, Any] = _maybe_load_json(work_item.get("committee_packet"))
    overlay_prompt: Mapping[str, Any] = _maybe_load_json(work_item.get("overlay_prompt"))
    return {
        "symbol": symbol,
        "required_action": str(work_item.get("required_action") or "produce_validated_ai_research_package"),
        "manifest_path": str(manifest_path),
        "review_packet_path": str(work_item.get("review_packet") or ""),
        "committee_packet_path": str(work_item.get("committee_packet") or ""),
        "overlay_prompt_path": str(work_item.get("overlay_prompt") or ""),
        "dossier_output_path": str(work_item.get("dossier_output_path") or ""),
        "overlay_output_path": str(work_item.get("overlay_output_path") or ""),
        "outcome_output_path": str(work_item.get("outcome_output_path") or ""),
        "dossier_schema": str(work_item.get("dossier_schema") or ""),
        "allowed_results": list(work_item.get("allowed_results") or []),
        "validation_commands": list(work_item.get("validation_commands") or []),
        "source_ref_catalog": _safe_source_ref_catalog(manifest_path),
        "priority_research_tasks": _priority_research_tasks(review_packet),
        "customer_order_capacity_evidence": _customer_evidence_workspace(review_packet),
        "deterministic_matrices": review_packet.get("deterministic_matrices", {}),
        "committee_roles": committee_packet.get("committee_roles", []),
        "overlay_contract": overlay_prompt.get("expected_output", {}),
        "research_expansion_protocol": _research_expansion_protocol(work_item, overlay_prompt),
        "hard_constraints": overlay_prompt.get("hard_constraints", []),
        "execution_sequence": [
            "Read source_ref_catalog and deterministic_matrices.",
            "Frame research_path with core question, hypotheses, evidence tests, and unresolved questions.",
            "Write and validate ai_research_dossier.json.",
            "Project the dossier into either ai_research_overlay.json or ai_review_outcome.json.",
            "Run all validation_commands before considering the work item complete.",
        ],
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
        "execution_rule": "The AI reviewer writes one validated research dossier and then one validated overlay or review outcome per workspace; this file organizes evidence and does not replace research judgment.",
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
