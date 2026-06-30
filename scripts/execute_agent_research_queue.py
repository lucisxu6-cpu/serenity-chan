#!/usr/bin/env python3
"""Execute and close Serenity formal AI research queues.

The script does not generate investment judgment by itself. It organizes the
queue for the current AI reviewer, validates reviewer-written artifacts, and
merges them once every candidate has a complete validated AI package.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from build_agent_overlay_workspace import build_workspace
    from build_comparison_report import to_markdown
    from validate_agent_research_queue import validate_agent_research_queue
    from validate_ai_overlay import evidence_context_from_manifest, validate_overlay
    from validate_ai_research_dossier import validate_dossier
    from validate_ai_review_outcome import validate_review_outcome
    from validate_and_merge_ai_overlay import build_validated_merged_report
    from validate_research_delivery import validate_delivery_payload
except ModuleNotFoundError:  # pragma: no cover
    from scripts.build_agent_overlay_workspace import build_workspace
    from scripts.build_comparison_report import to_markdown
    from scripts.validate_agent_research_queue import validate_agent_research_queue
    from scripts.validate_ai_overlay import evidence_context_from_manifest, validate_overlay
    from scripts.validate_ai_research_dossier import validate_dossier
    from scripts.validate_ai_review_outcome import validate_review_outcome
    from scripts.validate_and_merge_ai_overlay import build_validated_merged_report
    from scripts.validate_research_delivery import validate_delivery_payload


FINAL_STATUSES: set[str] = {"COMPLETED", "FAILED_INSUFFICIENT_EVIDENCE", "CONFLICT_WITH_DATA"}


def _load_json(path: Path) -> Mapping[str, Any]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _queue_payload(queue_path: Path) -> Mapping[str, Any]:
    payload: Mapping[str, Any] = _load_json(queue_path)
    errors: list[str] = validate_agent_research_queue(payload)
    if errors:
        raise ValueError("; ".join(errors))
    return payload


def _manifest_paths(queue: Mapping[str, Any]) -> list[Path]:
    paths: list[Path] = []
    for summary in queue.get("fetch_summaries", []) if isinstance(queue.get("fetch_summaries"), list) else []:
        if not isinstance(summary, Mapping):
            continue
        manifest_path: Path = Path(str(summary.get("manifest") or ""))
        if manifest_path and manifest_path not in paths:
            paths.append(manifest_path)
    if paths:
        return paths
    for item in queue.get("work_items", []) if isinstance(queue.get("work_items"), list) else []:
        if not isinstance(item, Mapping):
            continue
        manifest_path: Path = Path(str(item.get("manifest_path") or ""))
        if manifest_path and manifest_path not in paths:
            paths.append(manifest_path)
    return paths


def _manifest_by_symbol(queue: Mapping[str, Any]) -> dict[str, Path]:
    by_symbol: dict[str, Path] = {}
    for summary in queue.get("fetch_summaries", []) if isinstance(queue.get("fetch_summaries"), list) else []:
        if not isinstance(summary, Mapping):
            continue
        symbol: str = str(summary.get("symbol") or "").strip()
        manifest_path: Path = Path(str(summary.get("manifest") or ""))
        if symbol and manifest_path:
            by_symbol[symbol] = manifest_path
    for item in queue.get("work_items", []) if isinstance(queue.get("work_items"), list) else []:
        if not isinstance(item, Mapping):
            continue
        symbol = str(item.get("symbol") or "").strip()
        manifest_path = Path(str(item.get("manifest_path") or ""))
        if symbol and manifest_path and symbol not in by_symbol:
            by_symbol[symbol] = manifest_path
    return by_symbol


def _fetch_summary_symbols(queue: Mapping[str, Any]) -> set[str]:
    symbols: set[str] = set()
    for summary in queue.get("fetch_summaries", []) if isinstance(queue.get("fetch_summaries"), list) else []:
        if not isinstance(summary, Mapping):
            continue
        symbol: str = str(summary.get("symbol") or "").strip()
        if symbol:
            symbols.add(symbol)
    return symbols


def _validate_json_file(path: Path, label: str) -> Mapping[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")
    return _load_json(path)


def _candidate_result_status(work_item: Mapping[str, Any]) -> dict[str, Any]:
    symbol: str = str(work_item.get("symbol") or "")
    manifest_path: Path = Path(str(work_item.get("manifest_path") or ""))
    dossier_path: Path = Path(str(work_item.get("dossier_output_path") or ""))
    overlay_path: Path = Path(str(work_item.get("overlay_output_path") or ""))
    outcome_path: Path = Path(str(work_item.get("outcome_output_path") or ""))
    row: dict[str, Any] = {
        "symbol": symbol,
        "manifest_path": str(manifest_path),
        "dossier_path": str(dossier_path),
        "overlay_path": str(overlay_path),
        "outcome_path": str(outcome_path),
        "dossier_valid": False,
        "result_valid": False,
        "result_type": "",
        "research_status": "WAITING_FOR_DOSSIER",
        "merge_assignment": "",
        "errors": [],
        "next_action": "",
    }

    if not manifest_path.is_file():
        row["errors"].append(f"manifest not found: {manifest_path}")
        row["next_action"] = "Repair the fetch manifest path before AI research can be validated."
        return row

    try:
        evidence_context: Mapping[str, str] = evidence_context_from_manifest(manifest_path)
    except Exception as exc:
        row["errors"].append(f"manifest invalid: {exc}")
        row["next_action"] = "Repair the fetch manifest JSON before AI research can be validated."
        return row
    try:
        dossier_payload: Mapping[str, Any] = _validate_json_file(dossier_path, "AI research dossier")
        dossier_result: dict[str, Any] = validate_dossier(dossier_payload, evidence_context=evidence_context)
        dossier: Mapping[str, Any] = dossier_result["normalized_dossier"]
        dossier_status: str = str(dossier.get("research_status") or "")
        row["dossier_valid"] = True
        row["research_status"] = dossier_status
    except FileNotFoundError:
        row["next_action"] = "Read the workspace and write ai_research_dossier.json for this candidate."
        return row
    except Exception as exc:
        row["errors"].append(f"dossier invalid: {exc}")
        row["next_action"] = "Repair ai_research_dossier.json until validate_ai_research_dossier.py passes."
        return row

    overlay_exists: bool = overlay_path.exists()
    outcome_exists: bool = outcome_path.exists()
    if overlay_exists and outcome_exists:
        row["errors"].append("overlay and ai_review_outcome both exist; keep exactly one projected result")
        row["next_action"] = "Keep either the completed overlay or the failure outcome, then rerun validation."
        return row
    if not overlay_exists and not outcome_exists:
        row["next_action"] = "Project the dossier into ai_research_overlay.json or ai_review_outcome.json."
        return row

    try:
        if overlay_exists:
            overlay_payload: Mapping[str, Any] = _load_json(overlay_path)
            overlay_result: dict[str, Any] = validate_overlay(overlay_payload, evidence_context=evidence_context)
            overlay: Mapping[str, Any] = overlay_result["normalized_overlay"]
            if str(overlay.get("symbol") or "") != symbol:
                raise ValueError(f"overlay.symbol must be {symbol}")
            if row["research_status"] != "COMPLETED":
                raise ValueError("overlay requires dossier research_status=COMPLETED")
            row["result_type"] = "overlay"
            row["merge_assignment"] = f"{symbol}={overlay_path}"
        else:
            outcome_payload: Mapping[str, Any] = _load_json(outcome_path)
            outcome_result: dict[str, Any] = validate_review_outcome(outcome_payload)
            outcome: Mapping[str, Any] = outcome_result["normalized_outcome"]
            outcome_status: str = str(outcome.get("ai_review_status") or "")
            if str(outcome.get("symbol") or "") != symbol:
                raise ValueError(f"ai_review_outcome.symbol must be {symbol}")
            if outcome_status not in FINAL_STATUSES:
                raise ValueError("formal queues accept only final AI review statuses")
            if row["research_status"] != outcome_status:
                raise ValueError(f"dossier research_status must match outcome status {outcome_status}")
            row["result_type"] = "ai_outcome"
            row["merge_assignment"] = f"{symbol}={outcome_path}"
        row["result_valid"] = True
        row["next_action"] = "AI research package is validated for merge."
    except Exception as exc:
        row["errors"].append(f"projected result invalid: {exc}")
        row["next_action"] = "Repair the projected AI result until its validator passes."
    return row


def _existing_package_status(package: Mapping[str, Any], manifest_by_symbol: Mapping[str, Path]) -> dict[str, Any]:
    symbol: str = str(package.get("symbol") or "")
    manifest_path: Optional[Path] = manifest_by_symbol.get(symbol)
    dossier_path: Path = Path(str(package.get("dossier_path") or ""))
    result_path: Path = Path(str(package.get("result_path") or ""))
    result_type: str = str(package.get("result_type") or "")
    row: dict[str, Any] = {
        "symbol": symbol,
        "manifest_path": str(manifest_path) if manifest_path else "",
        "dossier_path": str(dossier_path),
        "result_type": result_type,
        "result_path": str(result_path),
        "package_valid": False,
        "research_status": "",
        "errors": [],
        "next_action": "",
    }
    if manifest_path is None or not manifest_path.is_file():
        row["errors"].append(f"manifest not found for existing AI package: {symbol}")
        row["next_action"] = "Regenerate the queue with this candidate in fetch_summaries, or rebuild the AI package from the current manifest."
        return row
    try:
        evidence_context: Mapping[str, str] = evidence_context_from_manifest(manifest_path)
        dossier_result: dict[str, Any] = validate_dossier(_load_json(dossier_path), evidence_context=evidence_context)
        dossier: Mapping[str, Any] = dossier_result["normalized_dossier"]
        dossier_status: str = str(dossier.get("research_status") or "")
        row["research_status"] = dossier_status
        if str(dossier.get("symbol") or "") != symbol:
            raise ValueError(f"dossier.symbol must be {symbol}")
        if result_type == "overlay":
            overlay_result: dict[str, Any] = validate_overlay(_load_json(result_path), evidence_context=evidence_context)
            overlay: Mapping[str, Any] = overlay_result["normalized_overlay"]
            if str(overlay.get("symbol") or "") != symbol:
                raise ValueError(f"overlay.symbol must be {symbol}")
            if dossier_status != "COMPLETED":
                raise ValueError("overlay requires dossier research_status=COMPLETED")
        elif result_type == "ai_outcome":
            outcome_result: dict[str, Any] = validate_review_outcome(_load_json(result_path))
            outcome: Mapping[str, Any] = outcome_result["normalized_outcome"]
            outcome_status: str = str(outcome.get("ai_review_status") or "")
            if str(outcome.get("symbol") or "") != symbol:
                raise ValueError(f"ai_review_outcome.symbol must be {symbol}")
            if outcome_status not in FINAL_STATUSES:
                raise ValueError("formal queues accept only final AI review statuses")
            if dossier_status != outcome_status:
                raise ValueError(f"dossier research_status must match outcome status {outcome_status}")
        else:
            raise ValueError(f"unknown result_type: {result_type}")
        row["package_valid"] = True
        row["next_action"] = "Existing AI research package is validated for merge."
    except Exception as exc:
        row["errors"].append(f"existing AI package invalid: {exc}")
        row["next_action"] = "Refresh the existing dossier and projected result against the current manifest source refs, then rerun validation."
    return row


def build_execution_status(queue_path: Path) -> dict[str, Any]:
    queue: Mapping[str, Any] = _queue_payload(queue_path)
    manifest_map: dict[str, Path] = _manifest_by_symbol(queue)
    rows: list[dict[str, Any]] = [
        _candidate_result_status(item)
        for item in queue.get("work_items", []) if isinstance(item, Mapping)
    ]
    existing_rows: list[dict[str, Any]] = [
        _existing_package_status(item, manifest_map)
        for item in queue.get("existing_ai_packages", []) if isinstance(item, Mapping)
    ]
    complete: bool = (
        all(row.get("dossier_valid") and row.get("result_valid") for row in rows)
        and all(row.get("package_valid") for row in existing_rows)
    )
    errors: list[str] = [
        f"{row.get('symbol')}: {error}"
        for row in [*rows, *existing_rows]
        for error in row.get("errors", []) if error
    ]
    next_actions: list[str] = [
        f"{row.get('symbol')}: {row.get('next_action')}"
        for row in [*rows, *existing_rows]
        if row.get("next_action")
        and not (row.get("dossier_valid") and row.get("result_valid"))
        and not row.get("package_valid")
    ]
    return {
        "contract_type": "serenity_agent_research_execution_status",
        "schema_version": "1.0",
        "queue_path": str(queue_path.resolve()),
        "workflow_status": "READY_TO_MERGE" if complete else "AGENT_RESEARCH_REQUIRED",
        "delivery_allowed": False,
        "existing_ai_package_status": existing_rows,
        "candidate_status": rows,
        "errors": errors,
        "next_actions": next_actions,
    }


def render_taskbook(workspace: Mapping[str, Any]) -> str:
    lines: list[str] = [
        "# Serenity AI Research Taskbook",
        "",
        "This taskbook is for the current AI reviewer. Complete every candidate before formal delivery.",
        "",
        "## Execution Rule",
        "",
        str(workspace.get("execution_rule") or ""),
    ]
    for item in workspace.get("workspaces", []) if isinstance(workspace.get("workspaces"), list) else []:
        if not isinstance(item, Mapping):
            continue
        lines.extend([
            "",
            f"## {item.get('symbol')}",
            "",
            f"- Manifest: `{item.get('manifest_path')}`",
            f"- Review packet: `{item.get('review_packet_path')}`",
            f"- Committee packet: `{item.get('committee_packet_path')}`",
            f"- Overlay prompt: `{item.get('overlay_prompt_path')}`",
            f"- Dossier output: `{item.get('dossier_output_path')}`",
            f"- Overlay output: `{item.get('overlay_output_path')}`",
            f"- Outcome output: `{item.get('outcome_output_path')}`",
            "",
            "### Priority Research Tasks",
        ])
        tasks: list[Any] = list(item.get("priority_research_tasks") or []) if isinstance(item.get("priority_research_tasks"), list) else []
        lines.extend([f"- {task}" for task in tasks] or ["- Read the deterministic matrices and define the core research question."])
        lines.extend(["", "### Execution Sequence"])
        sequence: list[Any] = list(item.get("execution_sequence") or []) if isinstance(item.get("execution_sequence"), list) else []
        lines.extend([f"- {step}" for step in sequence])
        lines.extend(["", "### Validation Commands"])
        commands: list[Any] = list(item.get("validation_commands") or []) if isinstance(item.get("validation_commands"), list) else []
        lines.extend([f"- `{command}`" for command in commands])
    return "\n".join(lines)


def prepare_queue(queue_path: Path, *, workspace_out: Optional[Path], taskbook_out: Optional[Path], status_out: Optional[Path]) -> dict[str, Any]:
    workspace: dict[str, Any] = build_workspace(queue_path)
    status: dict[str, Any] = build_execution_status(queue_path)
    if workspace_out:
        _write_json(workspace_out, workspace)
    if taskbook_out:
        _write_text(taskbook_out, render_taskbook(workspace))
    if status_out:
        _write_json(status_out, status)
    return {
        "contract_type": "serenity_agent_research_prepare_summary",
        "schema_version": "1.0",
        "workspace": str(workspace_out) if workspace_out else "",
        "taskbook": str(taskbook_out) if taskbook_out else "",
        "status": status,
    }


def merge_queue(queue_path: Path, *, report_out: Path, markdown_out: Optional[Path]) -> dict[str, Any]:
    queue: Mapping[str, Any] = _queue_payload(queue_path)
    status: dict[str, Any] = build_execution_status(queue_path)
    if status.get("workflow_status") != "READY_TO_MERGE":
        raise ValueError("; ".join(status.get("next_actions") or status.get("errors") or ["AI research package is incomplete"]))
    overlay_values: list[str] = []
    outcome_values: list[str] = []
    dossier_values: list[str] = []
    fetched_symbols: set[str] = _fetch_summary_symbols(queue)
    package_symbols: set[str] = set()
    for row in status.get("existing_ai_package_status", []) if isinstance(status.get("existing_ai_package_status"), list) else []:
        if not isinstance(row, Mapping):
            continue
        symbol: str = str(row.get("symbol") or "")
        if symbol:
            package_symbols.add(symbol)
            dossier_values.append(f"{symbol}={row.get('dossier_path')}")
            assignment: str = f"{symbol}={row.get('result_path')}"
            if row.get("result_type") == "overlay":
                overlay_values.append(assignment)
            elif row.get("result_type") == "ai_outcome":
                outcome_values.append(assignment)
    for row in status.get("candidate_status", []) if isinstance(status.get("candidate_status"), list) else []:
        if not isinstance(row, Mapping):
            continue
        symbol = str(row.get("symbol") or "")
        if symbol:
            package_symbols.add(symbol)
        dossier_values.append(f"{symbol}={row.get('dossier_path')}")
        assignment: str = str(row.get("merge_assignment") or "")
        if row.get("result_type") == "overlay":
            overlay_values.append(assignment)
        elif row.get("result_type") == "ai_outcome":
            outcome_values.append(assignment)
    if fetched_symbols and package_symbols != fetched_symbols:
        missing_from_queue: set[str] = fetched_symbols - package_symbols
        raise ValueError(
            "AI research packages do not cover the full candidate set; "
            f"missing packages for formal delivery: {', '.join(sorted(missing_from_queue))}"
        )
    report: dict[str, Any] = build_validated_merged_report(_manifest_paths(queue), overlay_values, outcome_values, dossier_values)
    delivery_errors: list[str] = validate_delivery_payload(report)
    if delivery_errors:
        raise ValueError("; ".join(delivery_errors))
    _write_json(report_out, report)
    if markdown_out:
        _write_text(markdown_out, to_markdown(report))
    return {
        "contract_type": "serenity_agent_research_merge_summary",
        "schema_version": "1.0",
        "workflow_status": "FINAL_REPORT_READY",
        "delivery_allowed": True,
        "final_report": str(report_out),
        "final_markdown": str(markdown_out) if markdown_out else "",
    }


def run_queue(
    queue_path: Path,
    *,
    workspace_out: Optional[Path],
    taskbook_out: Optional[Path],
    status_out: Optional[Path],
    report_out: Path,
    markdown_out: Optional[Path],
) -> dict[str, Any]:
    prepare_summary: dict[str, Any] = prepare_queue(
        queue_path,
        workspace_out=workspace_out,
        taskbook_out=taskbook_out,
        status_out=status_out,
    )
    status: Mapping[str, Any] = prepare_summary["status"] if isinstance(prepare_summary.get("status"), Mapping) else {}
    if status.get("workflow_status") != "READY_TO_MERGE":
        return {
            "contract_type": "serenity_agent_research_run_summary",
            "schema_version": "1.0",
            "workflow_status": "AGENT_RESEARCH_REQUIRED",
            "delivery_allowed": False,
            "workspace": prepare_summary.get("workspace", ""),
            "taskbook": prepare_summary.get("taskbook", ""),
            "status": status,
            "next_phase": "current_ai_reviewer_writes_dossier_and_projected_result",
        }
    merge_summary: dict[str, Any] = merge_queue(queue_path, report_out=report_out, markdown_out=markdown_out)
    return {
        "contract_type": "serenity_agent_research_run_summary",
        "schema_version": "1.0",
        "workflow_status": "FINAL_REPORT_READY",
        "delivery_allowed": True,
        "merge_summary": merge_summary,
        "final_report": merge_summary.get("final_report", ""),
        "final_markdown": merge_summary.get("final_markdown", ""),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Execute Serenity formal AI research queues")
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser] = parser.add_subparsers(dest="command", required=True)

    prepare_parser: argparse.ArgumentParser = subparsers.add_parser("prepare", help="build workspace, taskbook, and status")
    prepare_parser.add_argument("queue")
    prepare_parser.add_argument("--workspace-out")
    prepare_parser.add_argument("--taskbook-out")
    prepare_parser.add_argument("--status-out")

    status_parser: argparse.ArgumentParser = subparsers.add_parser("status", help="inspect AI package completion")
    status_parser.add_argument("queue")

    merge_parser: argparse.ArgumentParser = subparsers.add_parser("merge", help="merge validated AI packages into a formal report")
    merge_parser.add_argument("queue")
    merge_parser.add_argument("--report-out", required=True)
    merge_parser.add_argument("--markdown-out")

    run_parser: argparse.ArgumentParser = subparsers.add_parser("run", help="prepare, inspect, and merge when complete")
    run_parser.add_argument("queue")
    run_parser.add_argument("--workspace-out")
    run_parser.add_argument("--taskbook-out")
    run_parser.add_argument("--status-out")
    run_parser.add_argument("--report-out", required=True)
    run_parser.add_argument("--markdown-out")

    args: argparse.Namespace = parser.parse_args(argv)
    try:
        queue_path: Path = Path(args.queue)
        if args.command == "prepare":
            result: dict[str, Any] = prepare_queue(
                queue_path,
                workspace_out=Path(args.workspace_out) if args.workspace_out else None,
                taskbook_out=Path(args.taskbook_out) if args.taskbook_out else None,
                status_out=Path(args.status_out) if args.status_out else None,
            )
        elif args.command == "status":
            result = build_execution_status(queue_path)
        elif args.command == "merge":
            result = merge_queue(
                queue_path,
                report_out=Path(args.report_out),
                markdown_out=Path(args.markdown_out) if args.markdown_out else None,
            )
        else:
            result = run_queue(
                queue_path,
                workspace_out=Path(args.workspace_out) if args.workspace_out else None,
                taskbook_out=Path(args.taskbook_out) if args.taskbook_out else None,
                status_out=Path(args.status_out) if args.status_out else None,
                report_out=Path(args.report_out),
                markdown_out=Path(args.markdown_out) if args.markdown_out else None,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
