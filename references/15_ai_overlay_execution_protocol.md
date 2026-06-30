# AI Dossier And Overlay Execution Protocol

This protocol closes the gap between deterministic data extraction and AI research judgment.

## Core Rule

When the user asks the skill to analyze, compare, or research stocks, do not stop after generating `ai_review_packet` or `ai_committee_packet`. The current AI agent is the research reviewer.

The final user-facing report must be rendered after every candidate has a validated AI research dossier plus one of these projected outcomes:

- `COMPLETED`: AI generated a dossier and overlay, both validators accepted them, and both were merged into the comparison report.
- `FAILED_INSUFFICIENT_EVIDENCE`: AI generated a dossier and validated `ai_review_outcome` because primary evidence was insufficient.
- `CONFLICT_WITH_DATA`: AI generated a dossier and validated `ai_review_outcome` because the thesis conflicted with deterministic data, source level, currency normalization, valuation, or capital-action constraints.
- `SKIPPED_QUICK_AUDIT`: AI generated a validated `ai_review_outcome` because the user explicitly requested quick audit or data-only diagnostics.

Formal reports use explicit AI execution statuses: `COMPLETED`, `FAILED_INSUFFICIENT_EVIDENCE`, or `CONFLICT_WITH_DATA`. `NOT_RUN` belongs only to internal data baselines before the agent research queue is executed. `SKIPPED_QUICK_AUDIT` belongs only to explicit diagnostic or quick-audit flows. The validated merge and delivery validators reject missing AI dossiers or missing projected AI results for formal candidate comparison.

## Required Workflow

1. Fetch real data and keep every manifest.
2. Build deterministic comparison JSON.
3. Build one `ai_review_packet` and one `ai_committee_packet` per candidate.
4. If `run_research_analysis.py` returns `AGENT_RESEARCH_QUEUE_READY`, validate `agent_research_queue.json`, build `agent_overlay_workspace.json`, then execute every `work_item`.
5. Read the workspace, packets and source artifacts that support the open debts.
6. Generate one `ai_research_dossier.json` per candidate. The dossier records source reading, research path, hypotheses, evidence tests, observed facts, inferences, judgment, claim graph, causal chain, same-layer comparison, bear case, scenarios, triggers, and action conditions.
7. Project each dossier into one AI result per candidate: `ai_overlay.json` for completed evidence-backed research, or `ai_review_outcome.json` for insufficient evidence or data conflict. Use `SKIPPED_QUICK_AUDIT` only for explicit diagnostic/quick-audit requests.
8. Validate each dossier with `scripts/validate_ai_research_dossier.py`; validate each overlay with `scripts/validate_ai_overlay.py`; validate each outcome with `scripts/validate_ai_review_outcome.py`.
9. Merge dossiers, overlays, and outcomes with `scripts/validate_and_merge_ai_overlay.py`.
10. Validate the merged comparison report and delivery gate with `scripts/validate_research_delivery.py`.
11. Render the final Chinese report from `comparison_final.json`.

## Dossier Content Rules

A dossier is the full AI research surface. It should preserve the work a strong analyst would otherwise do implicitly:

- source reading log
- research path with core question, decision use, hypotheses, evidence tests, and unresolved questions
- observed / inferred / judgment boundary
- claim graph with supporting and opposing refs
- causal chain from demand to revenue and financial realization
- same-layer comparison
- bear case
- base / upside / downside scenarios
- 30d / 90d / 180d trigger table
- action conditions and confidence dampers
- overlay projection with bounded AI deltas

The dossier may explore deeply, but it must not invent facts or override deterministic data. Its source refs must resolve to the manifest evidence context when a manifest is supplied.

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
- bounded thesis/evidence/risk deltas projected from the dossier

H4/H5 evidence-supported growth requires at least one L0/L1 evidence reference with confidence >= 0.65 and `h4_h5_evidence_bar_met=true`.

## Customer / Order / Capacity Evidence

Every formal candidate workspace includes `customer_order_capacity_evidence` when the fetch layer can produce it. The AI reviewer must use this lane before making customer, order, bid-win, capacity, backlog, or revenue-transmission claims.

- `DIRECT_EVIDENCE_FOUND` can support an overlay only after the reviewer reads the referenced filing or announcement and cites a valid `source_ref`.
- `DISCLOSURE_LEADS_ONLY` creates research questions, next evidence, and gating language; it does not support H4/H5 evidence-supported growth by itself.
- `NO_DIRECT_CUSTOMER_ORDER_CAPACITY_DISCLOSURE` keeps customer/order/capacity claims in research debt and action gates.
- `review_queue` items are triage targets. They become support only after the reviewer reads the underlying source artifact and writes a validated overlay claim.

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
python scripts/validate_ai_research_dossier.py ai_research_dossier_a.json --manifest manifest_a.json
python scripts/validate_ai_research_dossier.py ai_research_dossier_b.json --manifest manifest_b.json
python scripts/validate_ai_review_outcome.py ai_review_outcome.json
python scripts/validate_and_merge_ai_overlay.py manifest_a.json manifest_b.json \
  --dossier SYMBOL_A=ai_research_dossier_a.json \
  --dossier SYMBOL_B=ai_research_dossier_b.json \
  --overlay SYMBOL_A=ai_overlay.json \
  --ai-outcome SYMBOL_B=ai_review_outcome.json \
  --report-out comparison_final.json \
  --markdown-out comparison_final.md
python scripts/validate_research_delivery.py comparison_final.json
```

The report may remain research-gated, and it must state that AI research was attempted and why it failed.

## Mode Boundary

`diagnostic` and `quick_audit` may stop at a data-only baseline when the user explicitly asks for data quality, engineering diagnosis, or fast screening. `candidate_comparison`, `full_research`, strategy recommendation, allocation, and action-plan requests must execute or explicitly fail the overlay stage before delivery.

## Agent Research Queue

When formal mode lacks AI results, `run_research_analysis.py` writes `agent_research_queue.json`. Validate it with:

```bash
python scripts/validate_agent_research_queue.py <run_dir>/agent_research_queue.json
python scripts/build_agent_overlay_workspace.py <run_dir>/agent_research_queue.json \
  --out <run_dir>/agent_overlay_workspace.json
```

These artifacts are internal work instructions. The current AI agent must complete every `work_item` before formal delivery. Each item points to the manifest, review packet, committee packet, overlay prompt, source reference catalog, customer/order/capacity evidence, deterministic matrices, research expansion protocol, allowed result types, output paths, validation commands, and guardrails.
