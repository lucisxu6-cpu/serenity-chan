# AI Overlay Execution Protocol

This protocol closes the gap between deterministic data extraction and AI research judgment.

## Core Rule

When the user asks the skill to analyze, compare, or research stocks, do not stop after generating `ai_review_packet` or `ai_committee_packet`. The current AI agent is the research reviewer.

The final user-facing report must be rendered after one of these outcomes:

- `COMPLETED`: AI generated an overlay, `validate_ai_overlay.py` accepted it, and the overlay was merged into the comparison report.
- `FAILED_INSUFFICIENT_EVIDENCE`: AI generated a validated `ai_review_outcome` because primary evidence was insufficient.
- `CONFLICT_WITH_DATA`: AI generated a validated `ai_review_outcome` because the thesis conflicted with deterministic data, source level, currency normalization, valuation, or capital-action constraints.
- `SKIPPED_QUICK_AUDIT`: AI generated a validated `ai_review_outcome` because the user explicitly requested quick audit or data-only diagnostics.

Final reports use explicit AI execution statuses: `COMPLETED`, `FAILED_INSUFFICIENT_EVIDENCE`, `CONFLICT_WITH_DATA`, `SKIPPED_QUICK_AUDIT`, or `NOT_RUN`. `NOT_RUN` is allowed only in deterministic baseline or diagnostic output before the AI merge stage; the validated merge entry point rejects missing AI results for formal candidate comparison.

## Required Workflow

1. Fetch real data and keep every manifest.
2. Build deterministic comparison JSON.
3. Build one `ai_review_packet` and one `ai_committee_packet` per candidate.
4. Read the packets and source artifacts that support the open debts.
5. Generate one AI result per candidate: `ai_overlay.json` for completed evidence-backed research, or `ai_review_outcome.json` for insufficient evidence, data conflict, or quick audit.
6. Validate each overlay with `scripts/validate_ai_overlay.py`; validate each outcome with `scripts/validate_ai_review_outcome.py`.
7. Merge overlays and outcomes with `scripts/validate_and_merge_ai_overlay.py`.
8. Validate the merged comparison report.
9. Render the final Chinese report.

## Overlay Content Rules

An overlay must never override deterministic fields:

- `market_implied_growth`
- PE / PS
- data acquisition status
- currency normalization
- source level
- valuation stage
- capital-action facts

An overlay may provide:

- value-chain layer
- bottleneck reason
- revenue transmission
- evidence-supported growth
- required next evidence
- posterior basis
- contrary evidence
- concrete research questions

H4/H5 evidence-supported growth requires at least one L0/L1 evidence reference with confidence >= 0.65 and `h4_h5_evidence_bar_met=true`.

## Outcome Handling

If AI cannot produce a valid overlay:

```json
{
  "ai_review_status": "FAILED_INSUFFICIENT_EVIDENCE",
  "reason": "No L0/L1 evidence connects the candidate to the claimed bottleneck or growth tier.",
  "required_evidence": [
    "specific filing or source",
    "specific field or claim to verify"
  ]
}
```

Validate the outcome before merging:

```bash
python scripts/validate_ai_review_outcome.py ai_review_outcome.json
python scripts/validate_and_merge_ai_overlay.py manifest_a.json manifest_b.json \
  --overlay SYMBOL_A=ai_overlay.json \
  --ai-outcome SYMBOL_B=ai_review_outcome.json \
  --report-out comparison_report.json \
  --markdown-out comparison_report.md
```

The report may remain research-gated, and it must state that AI research was attempted and why it failed.

## Mode Boundary

`quick_audit` may stop before overlay generation. `candidate_comparison` and `full_research` must execute or explicitly fail the overlay stage before delivery.
