#!/usr/bin/env python3
"""Structured forecast ledger with event log, search, and calibration."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def normalize_iso_utc(value: str, field_name: str) -> str:
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SystemExit(f"invalid {field_name}: {value}") from exc
    if parsed.tzinfo is None:
        if "T" in value:
            raise SystemExit(f"{field_name} must include an explicit timezone offset")
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.replace(microsecond=0).isoformat()


def parse_probability(value: Any, field_name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"{field_name} must be numeric") from exc
    if not 0.0 <= parsed <= 1.0:
        raise SystemExit(f"{field_name} must be between 0 and 1")
    return parsed


def load_json_payload(json_arg: str | None, file_arg: str | None) -> dict[str, Any]:
    if bool(json_arg) == bool(file_arg):
        raise SystemExit("provide exactly one of --*-json or --*-file")
    text = Path(file_arg).read_text(encoding="utf-8") if file_arg else (json_arg or "")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid json payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("payload must be a JSON object")
    return payload


def dumps_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def deep_clone(obj: Any) -> Any:
    return json.loads(json.dumps(obj))


def require_keys(obj: dict[str, Any], keys: list[str], label: str) -> None:
    for key in keys:
        if key not in obj:
            raise SystemExit(f"{label} missing required key: {key}")


def validate_decision_profiles(profiles: Any) -> list[dict[str, Any]]:
    if not isinstance(profiles, list) or not profiles:
        raise SystemExit("decision_profiles must be a non-empty list")
    normalized = []
    seen_ids: set[str] = set()
    default_count = 0
    for index, profile in enumerate(profiles, start=1):
        if not isinstance(profile, dict):
            raise SystemExit(f"decision_profile #{index} must be an object")
        require_keys(profile, ["id", "label", "recommendation"], f"decision_profile #{index}")
        profile_id = str(profile["id"])
        if profile_id in seen_ids:
            raise SystemExit("decision_profile ids must be unique")
        seen_ids.add(profile_id)
        is_default = bool(profile.get("default", False))
        default_count += 1 if is_default else 0
        normalized.append(
            {
                "id": profile_id,
                "label": str(profile["label"]),
                "recommendation": str(profile["recommendation"]),
                "constraints": str(profile.get("constraints", "")),
                "reversibility": str(profile.get("reversibility", "")),
                "default": is_default,
            }
        )
    if default_count > 1:
        raise SystemExit("at most one decision_profile may be marked default")
    return normalized


def validate_base_rate(base_rate: Any) -> dict[str, Any]:
    if not isinstance(base_rate, dict):
        raise SystemExit("base_rate must be an object")
    require_keys(base_rate, ["summary", "reference_class"], "base_rate")
    normalized = {
        "summary": str(base_rate["summary"]),
        "reference_class": str(base_rate["reference_class"]),
        "analogue": str(base_rate.get("analogue", "")),
        "notes": str(base_rate.get("notes", "")),
        "prior_probability": None,
    }
    if "prior_probability" in base_rate and base_rate["prior_probability"] not in (None, ""):
        normalized["prior_probability"] = parse_probability(
            base_rate["prior_probability"], "base_rate.prior_probability"
        )
    return normalized


def normalize_claim(claim: dict[str, Any]) -> dict[str, Any]:
    require_keys(
        claim,
        ["id", "statement", "probability", "resolution_date", "resolution_criteria"],
        "claim",
    )
    probability = parse_probability(claim["probability"], f"claim {claim['id']} probability")
    resolution_date = normalize_iso_utc(
        str(claim["resolution_date"]), f"claim {claim['id']} resolution_date"
    )
    return {
        "id": str(claim["id"]),
        "statement": str(claim["statement"]),
        "probability": probability,
        "resolution_date": resolution_date,
        "resolution_criteria": str(claim["resolution_criteria"]),
        "status": "open",
        "outcome": None,
        "resolution_notes": "",
        "resolution_evidence": "",
    }


def validate_claims(claims: Any) -> list[dict[str, Any]]:
    if not isinstance(claims, list) or not claims:
        raise SystemExit("claims must be a non-empty list")
    normalized = [normalize_claim(claim) for claim in claims]
    ids = [claim["id"] for claim in normalized]
    if len(ids) != len(set(ids)):
        raise SystemExit("claim ids must be unique")
    return normalized


def validate_scenarios(scenarios: Any) -> list[dict[str, Any]]:
    if scenarios in (None, []):
        return []
    if not isinstance(scenarios, list):
        raise SystemExit("scenarios must be a list")
    normalized = []
    for index, scenario in enumerate(scenarios, start=1):
        if not isinstance(scenario, dict):
            raise SystemExit(f"scenario #{index} must be an object")
        require_keys(scenario, ["id", "name", "probability"], f"scenario #{index}")
        normalized.append(
            {
                "id": str(scenario["id"]),
                "name": str(scenario["name"]),
                "probability": parse_probability(
                    scenario["probability"], f"scenario {scenario['id']} probability"
                ),
                "description": str(scenario.get("description", "")),
            }
        )
    total = sum(item["probability"] for item in normalized)
    if abs(total - 1.0) > 0.01:
        raise SystemExit(f"scenario probabilities must sum to 1.0 (+/-0.01), got {total}")
    ids = [scenario["id"] for scenario in normalized]
    if len(ids) != len(set(ids)):
        raise SystemExit("scenario ids must be unique")
    return normalized


def validate_next_evidence(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        raise SystemExit("current_state.next_evidence must be a list")
    normalized = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise SystemExit(f"next_evidence #{index} must be an object")
        require_keys(item, ["id", "signal", "why"], f"next_evidence #{index}")
        check_by = item.get("check_by")
        normalized_check_by = ""
        if check_by:
            normalized_check_by = normalize_iso_utc(
                str(check_by), f"next_evidence {item['id']} check_by"
            )
        normalized.append(
            {
                "id": str(item["id"]),
                "signal": str(item["signal"]),
                "why": str(item["why"]),
                "check_by": normalized_check_by,
                "status": str(item.get("status", "open")),
            }
        )
    ids = [item["id"] for item in normalized]
    if len(ids) != len(set(ids)):
        raise SystemExit("next_evidence ids must be unique")
    return normalized


def validate_current_state(current_state: Any) -> dict[str, Any]:
    if not isinstance(current_state, dict):
        raise SystemExit("current_state must be an object")
    require_keys(
        current_state,
        ["thesis", "confidence", "evidence_ceiling", "next_evidence"],
        "current_state",
    )
    revisit_at = current_state.get("revisit_at")
    normalized_revisit_at = ""
    if revisit_at:
        normalized_revisit_at = normalize_iso_utc(
            str(revisit_at), "current_state.revisit_at"
        )
    return {
        "thesis": str(current_state["thesis"]),
        "confidence": str(current_state["confidence"]),
        "evidence_ceiling": str(current_state["evidence_ceiling"]),
        "next_evidence": validate_next_evidence(current_state["next_evidence"]),
        "revisit_at": normalized_revisit_at,
    }


def validate_record(payload: dict[str, Any]) -> dict[str, Any]:
    require_keys(
        payload,
        ["question", "horizon", "decision_profiles", "base_rate", "claims", "current_state"],
        "record",
    )
    created_at = utc_now()
    return {
        "id": str(payload.get("id") or uuid.uuid4().hex[:12]),
        "created_at": created_at,
        "updated_at": created_at,
        "revision": 1,
        "status": "open",
        "question": str(payload["question"]),
        "horizon": str(payload["horizon"]),
        "object": str(payload.get("object", "")),
        "geography": str(payload.get("geography", "")),
        "decision_use": str(payload.get("decision_use", "")),
        "decision_profiles": validate_decision_profiles(payload["decision_profiles"]),
        "base_rate": validate_base_rate(payload["base_rate"]),
        "claims": validate_claims(payload["claims"]),
        "scenarios": validate_scenarios(payload.get("scenarios")),
        "current_state": validate_current_state(payload["current_state"]),
        "notes": str(payload.get("notes", "")),
        "resolution_summary": None,
    }


def validate_update_payload(payload: dict[str, Any]) -> dict[str, Any]:
    require_keys(payload, ["new_evidence"], "update")
    normalized_revisit_at = ""
    if "revisit_at" in payload and payload["revisit_at"]:
        normalized_revisit_at = normalize_iso_utc(
            str(payload["revisit_at"]), "update.revisit_at"
        )
    return {
        "new_evidence": str(payload["new_evidence"]),
        "what_changed": str(payload.get("what_changed", "")),
        "updated_thesis": str(payload.get("updated_thesis", "")),
        "updated_confidence": str(payload.get("updated_confidence", "")),
        "updated_evidence_ceiling": str(payload.get("updated_evidence_ceiling", "")),
        "notes": str(payload.get("notes", "")),
        "changed_variables": [str(item) for item in payload.get("changed_variables", [])],
        "revisit_at": normalized_revisit_at,
        "updated_next_evidence": payload.get("updated_next_evidence"),
        "claim_updates": payload.get("claim_updates", []),
        "scenario_updates": payload.get("scenario_updates", []),
    }


def validate_resolution_payload(payload: dict[str, Any]) -> dict[str, Any]:
    require_keys(payload, ["claim_outcomes", "outcome_summary"], "resolution")
    claim_outcomes = payload["claim_outcomes"]
    if not isinstance(claim_outcomes, list) or not claim_outcomes:
        raise SystemExit("claim_outcomes must be a non-empty list")
    normalized_claims = []
    for item in claim_outcomes:
        if not isinstance(item, dict):
            raise SystemExit("claim_outcomes entries must be objects")
        require_keys(item, ["id", "outcome"], "claim_outcome")
        if not isinstance(item["outcome"], bool):
            raise SystemExit(f"claim_outcome {item['id']} outcome must be true or false")
        normalized_claims.append(
            {
                "id": str(item["id"]),
                "outcome": item["outcome"],
                "evidence": str(item.get("evidence", "")),
                "notes": str(item.get("notes", "")),
            }
        )
    return {
        "claim_outcomes": normalized_claims,
        "selected_scenario": str(payload.get("selected_scenario", "")),
        "outcome_summary": str(payload["outcome_summary"]),
        "notes": str(payload.get("notes", "")),
    }


def snapshot_state(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "claim_probabilities": {claim["id"]: claim["probability"] for claim in record["claims"]},
        "scenario_probabilities": {
            scenario["id"]: scenario["probability"] for scenario in record.get("scenarios", [])
        },
        "confidence": record["current_state"]["confidence"],
        "evidence_ceiling": record["current_state"]["evidence_ceiling"],
        "next_evidence": deep_clone(record["current_state"]["next_evidence"]),
        "revisit_at": record["current_state"].get("revisit_at", ""),
        "thesis": record["current_state"]["thesis"],
    }


def claim_map(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {claim["id"]: claim for claim in record["claims"]}


def scenario_map(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {scenario["id"]: scenario for scenario in record.get("scenarios", [])}


def strip_derived_fields(record: dict[str, Any]) -> dict[str, Any]:
    clean = deep_clone(record)
    clean.pop("schema_version", None)
    clean.pop("history", None)
    clean.pop("event_log", None)
    for claim in clean.get("claims", []):
        claim.pop("probability_history", None)
        claim.pop("scores", None)
        claim.pop("initial_probability", None)
    return clean


def apply_update(
    record: dict[str, Any], payload: dict[str, Any]
) -> tuple[str, dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    before = snapshot_state(record)
    if payload["updated_thesis"]:
        record["current_state"]["thesis"] = payload["updated_thesis"]
    if payload["updated_confidence"]:
        record["current_state"]["confidence"] = payload["updated_confidence"]
    if payload["updated_evidence_ceiling"]:
        record["current_state"]["evidence_ceiling"] = payload["updated_evidence_ceiling"]
    if payload["revisit_at"]:
        record["current_state"]["revisit_at"] = payload["revisit_at"]
    if payload["updated_next_evidence"] not in (None, ""):
        record["current_state"]["next_evidence"] = validate_next_evidence(
            payload["updated_next_evidence"]
        )

    claims = claim_map(record)
    updated_at = utc_now()
    claim_events = []
    for item in payload["claim_updates"]:
        if not isinstance(item, dict):
            raise SystemExit("claim_updates entries must be objects")
        require_keys(item, ["id", "probability"], "claim_update")
        claim = claims.get(str(item["id"]))
        if not claim:
            raise SystemExit(f"unknown claim id in update: {item['id']}")
        if claim.get("status") == "resolved":
            raise SystemExit(f"cannot update resolved claim: {item['id']}")
        probability = parse_probability(item["probability"], f"claim_update {item['id']} probability")
        claim["probability"] = probability
        claim_events.append(
            {
                "claim_id": claim["id"],
                "probability": probability,
                "reason": str(item.get("reason", payload["new_evidence"])),
                "at": updated_at,
            }
        )

    scenarios = scenario_map(record)
    if payload["scenario_updates"]:
        for item in payload["scenario_updates"]:
            if not isinstance(item, dict):
                raise SystemExit("scenario_updates entries must be objects")
            require_keys(item, ["id", "probability"], "scenario_update")
            scenario = scenarios.get(str(item["id"]))
            if not scenario:
                raise SystemExit(f"unknown scenario id in update: {item['id']}")
            scenario["probability"] = parse_probability(
                item["probability"], f"scenario_update {item['id']} probability"
            )
        total = sum(scenario["probability"] for scenario in record.get("scenarios", []))
        if abs(total - 1.0) > 0.01:
            raise SystemExit(f"updated scenario probabilities must sum to 1.0 (+/-0.01), got {total}")

    after = snapshot_state(record)
    record["updated_at"] = updated_at
    record["revision"] = int(record.get("revision", 1)) + 1
    return updated_at, before, after, claim_events


def next_event_seq(conn: sqlite3.Connection, forecast_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(event_seq), 0) AS seq FROM forecast_events WHERE forecast_id = ?",
        (forecast_id,),
    ).fetchone()
    return int(row["seq"]) + 1


def next_claim_probability_seq(conn: sqlite3.Connection, forecast_id: str, claim_id: str) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(MAX(seq), 0) AS seq
        FROM claim_probability_events
        WHERE forecast_id = ? AND claim_id = ?
        """,
        (forecast_id, claim_id),
    ).fetchone()
    return int(row["seq"]) + 1


def lookup_initial_probability(
    conn: sqlite3.Connection, forecast_id: str, claim_id: str, fallback: float
) -> float:
    row = conn.execute(
        """
        SELECT probability
        FROM claim_probability_events
        WHERE forecast_id = ? AND claim_id = ?
        ORDER BY seq
        LIMIT 1
        """,
        (forecast_id, claim_id),
    ).fetchone()
    return float(row["probability"]) if row is not None else fallback


def rebuild_search_row(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    conn.execute("DELETE FROM forecast_search WHERE forecast_id = ?", (record["id"],))
    conn.execute(
        """
        INSERT INTO forecast_search (
            forecast_id, question, object, decision_use, thesis, base_rate_summary
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            record["id"],
            record["question"],
            record.get("object", ""),
            record.get("decision_use", ""),
            record["current_state"]["thesis"],
            record["base_rate"]["summary"],
        ),
    )


def refresh_record_indexes(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    conn.execute("DELETE FROM claims WHERE forecast_id = ?", (record["id"],))
    for claim in record["claims"]:
        scores = claim.get("scores", {})
        initial_probability = claim.get("initial_probability")
        if initial_probability is None:
            initial_probability = lookup_initial_probability(
                conn, record["id"], claim["id"], float(claim["probability"])
            )
        conn.execute(
            """
            INSERT INTO claims (
                forecast_id, claim_id, statement, probability, resolution_date,
                resolution_criteria, status, outcome, resolution_notes,
                resolution_evidence, initial_probability, final_brier, initial_brier
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["id"],
                claim["id"],
                claim["statement"],
                claim["probability"],
                claim["resolution_date"],
                claim["resolution_criteria"],
                claim["status"],
                None if claim.get("outcome") is None else int(bool(claim["outcome"])),
                claim.get("resolution_notes", ""),
                claim.get("resolution_evidence", ""),
                initial_probability,
                scores.get("final_brier"),
                scores.get("initial_brier"),
            ),
        )

    conn.execute("DELETE FROM next_evidence WHERE forecast_id = ?", (record["id"],))
    for item in record["current_state"]["next_evidence"]:
        conn.execute(
            """
            INSERT INTO next_evidence (
                forecast_id, evidence_id, signal, why, check_by, status
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                record["id"],
                item["id"],
                item["signal"],
                item["why"],
                item.get("check_by", ""),
                item.get("status", "open"),
            ),
        )

    rebuild_search_row(conn, record)


def insert_record(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    stored_record = strip_derived_fields(record)
    conn.execute(
        """
        INSERT INTO forecasts (
            id, created_at, updated_at, revision, status, question, horizon, object,
            geography, decision_use, confidence, revisit_at, record_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record["id"],
            record["created_at"],
            record["updated_at"],
            int(record.get("revision", 1)),
            record["status"],
            record["question"],
            record["horizon"],
            record.get("object", ""),
            record.get("geography", ""),
            record.get("decision_use", ""),
            record["current_state"]["confidence"],
            record["current_state"].get("revisit_at", ""),
            dumps_json(stored_record),
        ),
    )
    refresh_record_indexes(conn, record)


def replace_record(conn: sqlite3.Connection, record: dict[str, Any], expected_revision: int) -> None:
    stored_record = strip_derived_fields(record)
    cursor = conn.execute(
        """
        UPDATE forecasts
        SET updated_at = ?,
            revision = ?,
            status = ?,
            question = ?,
            horizon = ?,
            object = ?,
            geography = ?,
            decision_use = ?,
            confidence = ?,
            revisit_at = ?,
            record_json = ?
        WHERE id = ? AND revision = ?
        """,
        (
            record["updated_at"],
            int(record.get("revision", 1)),
            record["status"],
            record["question"],
            record["horizon"],
            record.get("object", ""),
            record.get("geography", ""),
            record.get("decision_use", ""),
            record["current_state"]["confidence"],
            record["current_state"].get("revisit_at", ""),
            dumps_json(stored_record),
            record["id"],
            expected_revision,
        ),
    )
    if cursor.rowcount != 1:
        raise SystemExit("entry changed concurrently; reload and retry")
    refresh_record_indexes(conn, record)


def insert_forecast_event(
    conn: sqlite3.Connection,
    forecast_id: str,
    event_seq: int,
    event_type: str,
    event_at: str,
    summary: str,
    payload: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO forecast_events (
            forecast_id, event_seq, event_type, event_at, summary, event_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (forecast_id, event_seq, event_type, event_at, summary, dumps_json(payload)),
    )


def insert_claim_probability_event(
    conn: sqlite3.Connection,
    forecast_id: str,
    claim_id: str,
    seq: int,
    event_at: str,
    probability: float,
    reason: str,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO claim_probability_events (
            forecast_id, claim_id, seq, event_at, probability, reason
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (forecast_id, claim_id, seq, event_at, probability, reason),
    )


def append_initial_artifacts(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    insert_forecast_event(
        conn,
        record["id"],
        1,
        "add",
        record["created_at"],
        "initial forecast",
        {
            "question": record["question"],
            "thesis": record["current_state"]["thesis"],
            "base_rate": record["base_rate"],
            "decision_profiles": record["decision_profiles"],
        },
    )
    for claim in record["claims"]:
        insert_claim_probability_event(
            conn,
            record["id"],
            claim["id"],
            1,
            record["created_at"],
            float(claim["probability"]),
            "initial",
        )


def append_update_artifacts(
    conn: sqlite3.Connection,
    record: dict[str, Any],
    payload: dict[str, Any],
    updated_at: str,
    before: dict[str, Any],
    after: dict[str, Any],
    claim_events: list[dict[str, Any]],
) -> None:
    insert_forecast_event(
        conn,
        record["id"],
        next_event_seq(conn, record["id"]),
        "update",
        updated_at,
        payload["new_evidence"],
        {
            "updated_at": updated_at,
            "new_evidence": payload["new_evidence"],
            "what_changed": payload["what_changed"],
            "changed_variables": payload["changed_variables"],
            "notes": payload["notes"],
            "before": before,
            "after": after,
        },
    )
    for item in claim_events:
        insert_claim_probability_event(
            conn,
            record["id"],
            item["claim_id"],
            next_claim_probability_seq(conn, record["id"], item["claim_id"]),
            item["at"],
            float(item["probability"]),
            item["reason"],
        )


def compute_claim_scores(conn: sqlite3.Connection, record: dict[str, Any]) -> dict[str, float]:
    final_scores = []
    initial_scores = []
    for claim in record["claims"]:
        if claim.get("status") != "resolved" or claim.get("outcome") is None:
            continue
        target = 1.0 if claim["outcome"] else 0.0
        initial_probability = lookup_initial_probability(
            conn, record["id"], claim["id"], float(claim["probability"])
        )
        final_score = (float(claim["probability"]) - target) ** 2
        initial_score = (float(initial_probability) - target) ** 2
        claim["initial_probability"] = initial_probability
        claim["scores"] = {
            "final_brier": final_score,
            "initial_brier": initial_score,
        }
        final_scores.append(final_score)
        initial_scores.append(initial_score)
    return {
        "resolved_claims": float(len(final_scores)),
        "avg_final_brier": sum(final_scores) / len(final_scores) if final_scores else 0.0,
        "avg_initial_brier": sum(initial_scores) / len(initial_scores) if initial_scores else 0.0,
    }


def apply_resolution(
    conn: sqlite3.Connection, record: dict[str, Any], payload: dict[str, Any]
) -> str:
    claims = claim_map(record)
    scenarios = scenario_map(record)
    selected_scenario = payload["selected_scenario"]
    if selected_scenario and selected_scenario not in scenarios:
        raise SystemExit(f"unknown selected_scenario: {selected_scenario}")
    for item in payload["claim_outcomes"]:
        claim = claims.get(item["id"])
        if not claim:
            raise SystemExit(f"unknown claim id in resolution: {item['id']}")
        if claim.get("status") == "resolved":
            raise SystemExit(f"claim already resolved: {item['id']}")
        claim["status"] = "resolved"
        claim["outcome"] = item["outcome"]
        claim["resolution_notes"] = item["notes"]
        claim["resolution_evidence"] = item["evidence"]

    metrics = compute_claim_scores(conn, record)
    all_resolved = all(claim.get("status") == "resolved" for claim in record["claims"])
    updated_at = utc_now()
    record["status"] = "resolved" if all_resolved else "open"
    record["updated_at"] = updated_at
    record["revision"] = int(record.get("revision", 1)) + 1
    if all_resolved:
        record["current_state"]["revisit_at"] = ""
        for item in record["current_state"]["next_evidence"]:
            item["status"] = "done"
    record["resolution_summary"] = {
        "updated_at": updated_at,
        "selected_scenario": payload["selected_scenario"],
        "outcome_summary": payload["outcome_summary"],
        "notes": payload["notes"],
        "resolved_claims": int(metrics["resolved_claims"]),
        "avg_final_brier": metrics["avg_final_brier"],
        "avg_initial_brier": metrics["avg_initial_brier"],
    }
    return updated_at


def append_resolution_artifacts(
    conn: sqlite3.Connection, record: dict[str, Any], payload: dict[str, Any], updated_at: str
) -> None:
    insert_forecast_event(
        conn,
        record["id"],
        next_event_seq(conn, record["id"]),
        "resolve",
        updated_at,
        payload["outcome_summary"],
        {
            "updated_at": updated_at,
            "selected_scenario": payload["selected_scenario"],
            "outcome_summary": payload["outcome_summary"],
            "notes": payload["notes"],
            "claim_outcomes": payload["claim_outcomes"],
        },
    )


def load_record(conn: sqlite3.Connection, entry_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT record_json, revision FROM forecasts WHERE id = ?",
        (entry_id,),
    ).fetchone()
    if row is None:
        raise SystemExit(f"entry not found: {entry_id}")
    record = json.loads(row["record_json"])
    record["revision"] = int(row["revision"])
    return record


def load_working_record(conn: sqlite3.Connection, entry_id: str) -> dict[str, Any]:
    return hydrate_record(conn, load_record(conn, entry_id))


def hydrate_record(conn: sqlite3.Connection, record: dict[str, Any]) -> dict[str, Any]:
    hydrated = deep_clone(record)
    claim_rows = conn.execute(
        """
        SELECT claim_id, initial_probability, final_brier, initial_brier
        FROM claims
        WHERE forecast_id = ?
        ORDER BY claim_id
        """,
        (record["id"],),
    ).fetchall()
    claim_meta = {row["claim_id"]: row for row in claim_rows}
    probability_rows = conn.execute(
        """
        SELECT claim_id, seq, event_at, probability, reason
        FROM claim_probability_events
        WHERE forecast_id = ?
        ORDER BY claim_id, seq
        """,
        (record["id"],),
    ).fetchall()
    probability_history: dict[str, list[dict[str, Any]]] = {}
    for row in probability_rows:
        probability_history.setdefault(row["claim_id"], []).append(
            {
                "at": row["event_at"],
                "probability": float(row["probability"]),
                "reason": row["reason"],
            }
        )

    for claim in hydrated["claims"]:
        row = claim_meta.get(claim["id"])
        claim["probability_history"] = probability_history.get(claim["id"], [])
        if row is not None:
            claim["initial_probability"] = row["initial_probability"]
            claim["scores"] = {
                "final_brier": row["final_brier"],
                "initial_brier": row["initial_brier"],
            }

    event_rows = conn.execute(
        """
        SELECT event_seq, event_type, event_at, summary, event_json
        FROM forecast_events
        WHERE forecast_id = ?
        ORDER BY event_seq
        """,
        (record["id"],),
    ).fetchall()
    event_log = []
    history = []
    for row in event_rows:
        details = json.loads(row["event_json"])
        event = {
            "seq": int(row["event_seq"]),
            "type": row["event_type"],
            "at": row["event_at"],
            "summary": row["summary"],
            "details": details,
        }
        event_log.append(event)
        if row["event_type"] == "update":
            history.append(details)
    hydrated["event_log"] = event_log
    hydrated["history"] = history
    return hydrated


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS forecasts (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL,
            question TEXT NOT NULL,
            horizon TEXT NOT NULL,
            object TEXT NOT NULL,
            geography TEXT NOT NULL,
            decision_use TEXT NOT NULL,
            confidence TEXT NOT NULL,
            revisit_at TEXT,
            record_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS claims (
            forecast_id TEXT NOT NULL REFERENCES forecasts(id) ON DELETE CASCADE,
            claim_id TEXT NOT NULL,
            statement TEXT NOT NULL,
            probability REAL NOT NULL,
            resolution_date TEXT NOT NULL,
            resolution_criteria TEXT NOT NULL,
            status TEXT NOT NULL,
            outcome INTEGER,
            resolution_notes TEXT NOT NULL DEFAULT '',
            resolution_evidence TEXT NOT NULL DEFAULT '',
            initial_probability REAL,
            final_brier REAL,
            initial_brier REAL,
            PRIMARY KEY (forecast_id, claim_id)
        );

        CREATE TABLE IF NOT EXISTS next_evidence (
            forecast_id TEXT NOT NULL REFERENCES forecasts(id) ON DELETE CASCADE,
            evidence_id TEXT NOT NULL,
            signal TEXT NOT NULL,
            why TEXT NOT NULL,
            check_by TEXT,
            status TEXT NOT NULL,
            PRIMARY KEY (forecast_id, evidence_id)
        );

        CREATE TABLE IF NOT EXISTS forecast_events (
            forecast_id TEXT NOT NULL REFERENCES forecasts(id) ON DELETE CASCADE,
            event_seq INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            event_at TEXT NOT NULL,
            summary TEXT NOT NULL,
            event_json TEXT NOT NULL,
            PRIMARY KEY (forecast_id, event_seq)
        );

        CREATE TABLE IF NOT EXISTS claim_probability_events (
            forecast_id TEXT NOT NULL REFERENCES forecasts(id) ON DELETE CASCADE,
            claim_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            event_at TEXT NOT NULL,
            probability REAL NOT NULL,
            reason TEXT NOT NULL,
            PRIMARY KEY (forecast_id, claim_id, seq)
        );

        CREATE INDEX IF NOT EXISTS idx_forecasts_status_revisit
            ON forecasts(status, revisit_at);
        CREATE INDEX IF NOT EXISTS idx_forecasts_updated
            ON forecasts(updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_claims_status_resolution
            ON claims(status, resolution_date);
        CREATE INDEX IF NOT EXISTS idx_next_evidence_status_checkby
            ON next_evidence(status, check_by);
        CREATE INDEX IF NOT EXISTS idx_events_forecast_type
            ON forecast_events(forecast_id, event_type, event_at);
        CREATE INDEX IF NOT EXISTS idx_claim_probability_events_lookup
            ON claim_probability_events(forecast_id, claim_id, seq);
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS forecast_search
        USING fts5(
            forecast_id UNINDEXED,
            question,
            object,
            decision_use,
            thesis,
            base_rate_summary
        )
        """
    )


def connect_db(path: Path) -> sqlite3.Connection:
    ensure_parent(path)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    init_schema(conn)
    return conn


def command_add(args: argparse.Namespace) -> int:
    path = Path(args.path)
    record = validate_record(load_json_payload(args.record_json, args.record_file))
    conn = connect_db(path)
    try:
        with conn:
            try:
                insert_record(conn, record)
            except sqlite3.IntegrityError as exc:
                raise SystemExit(f"entry id already exists: {record['id']}") from exc
            append_initial_artifacts(conn, record)
    finally:
        conn.close()
    print(record["id"])
    return 0


def command_update(args: argparse.Namespace) -> int:
    path = Path(args.path)
    payload = validate_update_payload(load_json_payload(args.update_json, args.update_file))
    conn = connect_db(path)
    try:
        with conn:
            record = load_working_record(conn, args.id)
            if record.get("status") == "resolved":
                raise SystemExit("cannot update a fully resolved forecast")
            expected_revision = int(record["revision"])
            updated_at, before, after, claim_events = apply_update(record, payload)
            replace_record(conn, record, expected_revision)
            append_update_artifacts(conn, record, payload, updated_at, before, after, claim_events)
    finally:
        conn.close()
    print(args.id)
    return 0


def command_resolve(args: argparse.Namespace) -> int:
    path = Path(args.path)
    payload = validate_resolution_payload(load_json_payload(args.resolution_json, args.resolution_file))
    conn = connect_db(path)
    try:
        with conn:
            record = load_working_record(conn, args.id)
            if record.get("status") == "resolved":
                raise SystemExit("cannot resolve a fully resolved forecast")
            expected_revision = int(record["revision"])
            updated_at = apply_resolution(conn, record, payload)
            replace_record(conn, record, expected_revision)
            append_resolution_artifacts(conn, record, payload, updated_at)
    finally:
        conn.close()
    print(args.id)
    return 0


def command_stats(args: argparse.Namespace) -> int:
    now = utc_now()
    conn = connect_db(Path(args.path))
    try:
        total_records = conn.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0]
        open_records = conn.execute("SELECT COUNT(*) FROM forecasts WHERE status != 'resolved'").fetchone()[0]
        resolved_records = conn.execute(
            "SELECT COUNT(*) FROM forecasts WHERE status = 'resolved'"
        ).fetchone()[0]
        total_claims = conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        resolved_claims = conn.execute(
            "SELECT COUNT(*) FROM claims WHERE status = 'resolved'"
        ).fetchone()[0]
        total_events = conn.execute("SELECT COUNT(*) FROM forecast_events").fetchone()[0]
        avg_revision = conn.execute("SELECT AVG(revision) FROM forecasts").fetchone()[0]
        overdue_revisits = conn.execute(
            """
            SELECT COUNT(*)
            FROM forecasts
            WHERE status != 'resolved'
              AND NULLIF(revisit_at, '') IS NOT NULL
              AND revisit_at <= ?
            """,
            (now,),
        ).fetchone()[0]
        overdue_evidence = conn.execute(
            """
            SELECT COUNT(*)
            FROM next_evidence AS ne
            JOIN forecasts AS f ON f.id = ne.forecast_id
            WHERE f.status != 'resolved'
              AND ne.status != 'done'
              AND NULLIF(ne.check_by, '') IS NOT NULL
              AND ne.check_by <= ?
            """,
            (now,),
        ).fetchone()[0]
        avg_final = conn.execute(
            "SELECT AVG(final_brier) FROM claims WHERE status = 'resolved' AND final_brier IS NOT NULL"
        ).fetchone()[0]
        avg_initial = conn.execute(
            "SELECT AVG(initial_brier) FROM claims WHERE status = 'resolved' AND initial_brier IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    print(f"total_records={total_records}")
    print(f"open_records={open_records}")
    print(f"resolved_records={resolved_records}")
    print(f"total_claims={total_claims}")
    print(f"resolved_claims={resolved_claims}")
    print(f"total_events={total_events}")
    if avg_revision is not None:
        print(f"avg_revision={avg_revision:.2f}")
    print(f"overdue_revisits={overdue_revisits}")
    print(f"overdue_evidence_checks={overdue_evidence}")
    if avg_final is not None:
        print(f"avg_final_brier={avg_final:.4f}")
    if avg_initial is not None:
        print(f"avg_initial_brier={avg_initial:.4f}")
    return 0


def command_agenda(args: argparse.Namespace) -> int:
    due_before = args.due_before or ""
    if due_before:
        due_before = normalize_iso_utc(due_before, "agenda.due_before")
    limit_clause = "" if args.limit <= 0 else f"LIMIT {int(args.limit)}"
    conn = connect_db(Path(args.path))
    try:
        rows = conn.execute(
            f"""
            SELECT id, revisit_at, question, revision
            FROM forecasts AS f
            WHERE status != 'resolved'
              AND (
                    ? = ''
                    OR (
                        NULLIF(revisit_at, '') IS NOT NULL
                        AND revisit_at <= ?
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM next_evidence AS ne
                        WHERE ne.forecast_id = f.id
                          AND ne.status != 'done'
                          AND NULLIF(ne.check_by, '') IS NOT NULL
                          AND ne.check_by <= ?
                    )
                )
            ORDER BY COALESCE(NULLIF(revisit_at, ''), '9999-12-31T00:00:00+00:00'), id
            {limit_clause}
            """,
            (due_before, due_before, due_before),
        ).fetchall()
        for row in rows:
            print(
                f"{row['id']}\trev={row['revision']}\t{row['revisit_at'] or '-'}\t{row['question']}"
            )
            evidence_rows = conn.execute(
                """
                SELECT evidence_id, signal, check_by, status
                FROM next_evidence
                WHERE forecast_id = ? AND status != 'done'
                ORDER BY COALESCE(NULLIF(check_by, ''), '9999-12-31T00:00:00+00:00'), evidence_id
                """,
                (row["id"],),
            ).fetchall()
            for evidence in evidence_rows:
                print(
                    f"  - [{evidence['evidence_id']}] due={evidence['check_by'] or '-'} "
                    f"status={evidence['status']} signal={evidence['signal']}"
                )
    finally:
        conn.close()
    return 0


def command_show(args: argparse.Namespace) -> int:
    conn = connect_db(Path(args.path))
    try:
        record = hydrate_record(conn, load_record(conn, args.id))
    finally:
        conn.close()
    print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def command_list(args: argparse.Namespace) -> int:
    limit_clause = "" if args.limit <= 0 else f"LIMIT {int(args.limit)}"
    filters = []
    params: list[Any] = []
    if args.status:
        filters.append("status = ?")
        params.append(args.status)
    where_clause = "" if not filters else "WHERE " + " AND ".join(filters)
    conn = connect_db(Path(args.path))
    try:
        rows = conn.execute(
            f"""
            SELECT id, status, revision, updated_at, question
            FROM forecasts
            {where_clause}
            ORDER BY updated_at DESC, id
            {limit_clause}
            """,
            params,
        ).fetchall()
        for row in rows:
            print(
                f"{row['id']}\t{row['status']}\trev={row['revision']}\t"
                f"{row['updated_at']}\t{row['question']}"
            )
    finally:
        conn.close()
    return 0


def command_search(args: argparse.Namespace) -> int:
    conn = connect_db(Path(args.path))
    try:
        query = args.query.strip()
        if not query:
            raise SystemExit("search query must not be empty")
        sql = """
            SELECT f.id, f.status, f.revision, f.updated_at, f.question
            FROM forecast_search
            JOIN forecasts AS f ON f.id = forecast_search.forecast_id
            WHERE forecast_search MATCH ?
            ORDER BY bm25(forecast_search), f.updated_at DESC
            LIMIT ?
            """
        try:
            rows = conn.execute(sql, (query, int(args.limit))).fetchall()
        except sqlite3.OperationalError:
            literal_query = '"' + query.replace('"', '""') + '"'
            try:
                rows = conn.execute(sql, (literal_query, int(args.limit))).fetchall()
            except sqlite3.OperationalError as exc:
                raise SystemExit(f"invalid search query: {query}") from exc
        for row in rows:
            print(
                f"{row['id']}\t{row['status']}\trev={row['revision']}\t"
                f"{row['updated_at']}\t{row['question']}"
            )
    finally:
        conn.close()
    return 0


def command_archive(args: argparse.Namespace) -> int:
    source_path = Path(args.path)
    output_path = Path(args.output)
    if source_path.resolve() == output_path.resolve():
        raise SystemExit("--output must be different from --path")
    before = normalize_iso_utc(args.before, "archive.before")

    source = connect_db(source_path)
    target = connect_db(output_path)
    moved = 0
    try:
        ids = [
            row["id"]
            for row in source.execute(
                """
                SELECT id
                FROM forecasts
                WHERE status = 'resolved' AND updated_at < ?
                ORDER BY updated_at, id
                """,
                (before,),
            ).fetchall()
        ]
        with target:
            for forecast_id in ids:
                existing = target.execute(
                    "SELECT 1 FROM forecasts WHERE id = ?",
                    (forecast_id,),
                ).fetchone()
                if existing:
                    raise SystemExit(f"archive target already contains entry: {forecast_id}")
                record = load_working_record(source, forecast_id)
                insert_record(target, record)
                event_rows = source.execute(
                    """
                    SELECT event_seq, event_type, event_at, summary, event_json
                    FROM forecast_events
                    WHERE forecast_id = ?
                    ORDER BY event_seq
                    """,
                    (forecast_id,),
                ).fetchall()
                for row in event_rows:
                    target.execute(
                        """
                        INSERT OR REPLACE INTO forecast_events (
                            forecast_id, event_seq, event_type, event_at, summary, event_json
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            forecast_id,
                            row["event_seq"],
                            row["event_type"],
                            row["event_at"],
                            row["summary"],
                            row["event_json"],
                        ),
                    )
                probability_rows = source.execute(
                    """
                    SELECT claim_id, seq, event_at, probability, reason
                    FROM claim_probability_events
                    WHERE forecast_id = ?
                    ORDER BY claim_id, seq
                    """,
                    (forecast_id,),
                ).fetchall()
                for row in probability_rows:
                    target.execute(
                        """
                        INSERT OR REPLACE INTO claim_probability_events (
                            forecast_id, claim_id, seq, event_at, probability, reason
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            forecast_id,
                            row["claim_id"],
                            row["seq"],
                            row["event_at"],
                            row["probability"],
                            row["reason"],
                        ),
                    )
                moved += 1
        if moved:
            with source:
                source.executemany(
                    "DELETE FROM forecast_search WHERE forecast_id = ?",
                    [(forecast_id,) for forecast_id in ids],
                )
                source.execute(
                    "DELETE FROM forecasts WHERE status = 'resolved' AND updated_at < ?",
                    (before,),
                )
            if args.vacuum:
                source.execute("VACUUM")
    finally:
        source.close()
        target.close()
    print(f"archived={moved}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Maintain a structured forecast ledger with event log and search."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser("add", help="Add a structured forecast record.")
    add.add_argument("--path", required=True)
    add.add_argument("--record-json")
    add.add_argument("--record-file")
    add.set_defaults(func=command_add)

    update = subparsers.add_parser("update", help="Update an existing forecast.")
    update.add_argument("--path", required=True)
    update.add_argument("--id", required=True)
    update.add_argument("--update-json")
    update.add_argument("--update-file")
    update.set_defaults(func=command_update)

    resolve = subparsers.add_parser("resolve", help="Resolve one or more forecast claims.")
    resolve.add_argument("--path", required=True)
    resolve.add_argument("--id", required=True)
    resolve.add_argument("--resolution-json")
    resolve.add_argument("--resolution-file")
    resolve.set_defaults(func=command_resolve)

    stats = subparsers.add_parser("stats", help="Show calibration statistics.")
    stats.add_argument("--path", required=True)
    stats.set_defaults(func=command_stats)

    agenda = subparsers.add_parser("agenda", help="List open forecasts and due evidence checks.")
    agenda.add_argument("--path", required=True)
    agenda.add_argument("--due-before")
    agenda.add_argument("--limit", type=int, default=0)
    agenda.set_defaults(func=command_agenda)

    show = subparsers.add_parser("show", help="Print one hydrated record as JSON.")
    show.add_argument("--path", required=True)
    show.add_argument("--id", required=True)
    show.set_defaults(func=command_show)

    list_parser = subparsers.add_parser("list", help="List forecast records.")
    list_parser.add_argument("--path", required=True)
    list_parser.add_argument("--status", choices=["open", "resolved"])
    list_parser.add_argument("--limit", type=int, default=50)
    list_parser.set_defaults(func=command_list)

    search = subparsers.add_parser("search", help="Full-text search across forecast records.")
    search.add_argument("--path", required=True)
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=20)
    search.set_defaults(func=command_search)

    archive = subparsers.add_parser(
        "archive", help="Move old resolved forecasts into another ledger."
    )
    archive.add_argument("--path", required=True)
    archive.add_argument("--output", required=True)
    archive.add_argument("--before", required=True)
    archive.add_argument("--vacuum", action="store_true")
    archive.set_defaults(func=command_archive)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        sys.exit(1)
