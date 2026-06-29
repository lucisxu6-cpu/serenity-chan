# Ledger Schema

Read this reference when using `scripts/forecast_ledger.py`.

This ledger is SQLite-backed. The storage is intentionally split like this:

- flexible forecast objects stay in JSON inside the database
- agenda, claim scoring, search, and archive operations use indexed relational rows
- update history and probability drift live as append-only event streams

This keeps the reasoning structure flexible for the model while making long-term maintenance practical.

This reference describes the only supported on-disk schema for the skill's final form. Older ledger layouts are not auto-migrated in runtime.

## Record Shape

Use `add` with a JSON object like:

```json
{
  "question": "Will X happen by date Y?",
  "horizon": "6 months",
  "object": "X",
  "geography": "China",
  "decision_use": "enter or wait",
  "decision_profiles": [
    {
      "id": "balanced",
      "label": "balanced",
      "recommendation": "Enter slowly with checkpoints",
      "constraints": "Protect downside",
      "reversibility": "medium",
      "default": true
    }
  ],
  "base_rate": {
    "summary": "Events of this class usually diffuse slowly, then accelerate",
    "reference_class": "consumer adoption curves",
    "analogue": "mobile payments 2013-2016",
    "prior_probability": 0.58
  },
  "claims": [
    {
      "id": "c1",
      "statement": "X exceeds threshold Z by 2026-12-31",
      "probability": 0.62,
      "resolution_date": "2026-12-31",
      "resolution_criteria": "Official source or primary disclosure confirms threshold Z"
    }
  ],
  "scenarios": [
    {"id": "base", "name": "Base", "probability": 0.6},
    {"id": "upside", "name": "Upside", "probability": 0.25},
    {"id": "downside", "name": "Downside", "probability": 0.15}
  ],
  "current_state": {
    "thesis": "Core causal story here",
    "confidence": "medium",
    "evidence_ceiling": "Direct demand data is not public",
    "revisit_at": "2026-06-30",
    "next_evidence": [
      {
        "id": "n1",
        "signal": "Quarterly hiring mix",
        "why": "Fastest public proxy for demand shift",
        "check_by": "2026-05-15"
      }
    ]
  }
}
```

Notes:

- `resolution_date`, `revisit_at`, and `check_by` may be given as date-only strings or timezone-aware timestamps; the ledger normalizes them to UTC internally.
- If a timestamp includes a clock time, include an explicit timezone offset such as `+08:00` or `Z`.
- `next_evidence` may be `[]` when the evidence ceiling has been reached and no useful next check exists.
- Resolved claims are immutable, and a fully resolved forecast cannot be resolved again.

## Update Shape

Use `update` with a JSON object like:

```json
{
  "new_evidence": "New primary-source evidence",
  "what_changed": "Why the odds moved",
  "changed_variables": ["distribution", "regulation"],
  "updated_confidence": "medium-high",
  "updated_thesis": "Revised causal story",
  "updated_evidence_ceiling": "Still missing private cohort retention",
  "revisit_at": "2026-07-15",
  "updated_next_evidence": [
    {
      "id": "n2",
      "signal": "Second-quarter retention disclosure",
      "why": "Best next proxy",
      "check_by": "2026-08-01"
    }
  ],
  "claim_updates": [
    {"id": "c1", "probability": 0.7, "reason": "Independent confirmation"}
  ],
  "scenario_updates": [
    {"id": "base", "probability": 0.55},
    {"id": "upside", "probability": 0.3},
    {"id": "downside", "probability": 0.15}
  ]
}
```

## Resolution Shape

Use `resolve` with a JSON object like:

```json
{
  "outcome_summary": "What actually happened",
  "selected_scenario": "base",
  "claim_outcomes": [
    {
      "id": "c1",
      "outcome": true,
      "evidence": "Official release dated 2026-12-31",
      "notes": "Threshold exceeded narrowly"
    }
  ]
}
```

## Commands

- `add`: insert a structured forecast object
- `update`: revise thesis, probabilities, scenarios, and next evidence
- `resolve`: record outcomes and compute claim-level Brier scores
- `agenda`: list open forecasts and due evidence checks
- `show`: print one full forecast object
- `list`: browse records by status without printing full JSON
- `search`: full-text search across question, object, decision use, thesis, and base-rate summary
- `stats`: summarize calibration and record counts
- `archive`: move old resolved forecasts into another SQLite ledger

## Lifecycle

Use this pattern to keep the ledger maintainable:

- active ledger for current forecasts
- archive ledger for resolved, older forecasts
- split by workspace, topic, or year when useful
- use `list` and `search` to retrieve prior forecasts instead of remembering ids manually
- use `agenda --due-before ...` to focus only on forecasts that need attention now

Example:

- `ai-labor-2026.sqlite`
- `china-internet-2026.sqlite`
- `archive-2026.sqlite`

## Why This Matters

The ledger is only useful if the record is structured enough to score later. Do not store only prose if the forecast matters.

The storage is deliberately hybrid:

- the model keeps freedom in the forecast object itself
- the system keeps discipline in event logging, scoring, and retrieval

That split is what lets the ledger grow without collapsing into one giant opaque blob.
