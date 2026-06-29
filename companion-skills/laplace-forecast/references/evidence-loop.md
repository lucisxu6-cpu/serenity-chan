# Evidence Loop

Use this reference when direct data is incomplete, conflicting, delayed, or unavailable in one clean source.

## Base Rates and Analogues

Before building a rich narrative, ask:

- What class of event is this?
- How often do events in this class occur?
- What are the nearest historical or structural analogues?
- Which parts of the present case are normal, and which are genuinely unusual?

Use the base rate as the starting prior. Then adjust only when current evidence justifies the move.

When multiple reference classes compete:

- prefer the one with the closest causal mechanics
- use the broader class as a guardrail against overfitting
- explain why the chosen class dominates
- mention the secondary class if it materially widens uncertainty

Use analogues carefully:

- borrow structure, not superficial resemblance
- explain why the analogue fits
- explain where it breaks

If no analogue is strong, keep the prior weak and the range wider.

## Prior Update Discipline

Use a simple discipline to avoid narrative drift:

- one weak signal should move the view only slightly
- one strong primary-source signal can move the view moderately
- multiple independent strong signals can justify a large update
- contradictory high-grade evidence should force a rebuild, not a cosmetic caveat

If you cannot explain why the new evidence outweighs the prior, you have not earned the update.

## Evidence Ladder

Prefer higher rungs first:

1. Official statistics, filings, court records, regulator notices, central bank or ministry releases
2. Primary documents from the actors themselves: earnings, technical docs, policy papers, standards, speeches
3. Operational traces: pricing pages, job postings, product rollouts, outages, shipment notices, usage disclosures
4. Market behavior: margins, retention, churn, unit economics, bids, discounts, capital allocation
5. Proxy metrics: search behavior, hiring mix, channel activity, content mix, app rankings, store count, inventory turn
6. Anecdotes or commentary: use only as hypothesis fuel, not as core proof

## Proxy Design

When direct data is absent, define the proxy explicitly:

- `Target variable`: what you really want to know
- `Proxy`: what you can observe
- `Why correlated`: why the proxy should tell you something useful
- `Failure mode`: what would break the correlation

If you cannot explain the failure mode, the proxy is too weak to carry much weight.

## Data-Acquisition Design

When public data exists but is scattered or messy:

- Design a structured search plan before searching randomly
- Compare multiple primary sources instead of relying on one summary
- If useful, create a small local script or checklist to collect, normalize, or compare evidence
- Use the cheapest reliable observable that moves the target variable

Permitted mindset:

- Build a retrieval plan
- Build a comparison sheet
- Build a one-off parser or cleaner
- Build a proxy tracker

Forbidden mindset:

- Pretend you obtained data that you did not
- Smuggle speculation in as observed fact
- Hide uncertainty behind confident prose

## Contradiction Loop

After assembling an initial view:

1. Find the strongest evidence against your thesis
2. Ask which dominant variable it attacks
3. Reweight the variable
4. Recompute the scenario balance
5. Rebuild the model if a top variable flips sign or importance

Do not merely append a caveat. Update the forecast.

## Forecast Ledger

Keep a compact ledger whenever the forecast materially evolves:

- `Prior`
- `Base-rate anchor`
- `Initial thesis`
- `Key assumptions`
- `Contradicting evidence`
- `Updated interpretation`
- `Updated odds or confidence`

The purpose is not bureaucracy. The purpose is to prevent silent rationalization.

If the outcome eventually resolves, update the ledger with:

- `Outcome`
- `Calibration`: hit, partial, miss, unresolved
- `What you misweighted`, if it was wrong

## Weight Update

Use a simple internal scoring discipline when useful:

- `Importance`: 1-5
- `Confidence`: 1-5
- `Volatility`: 1-5

High-importance, low-confidence variables should push the final answer toward wider ranges and cleaner invalidation criteria.

## Label the Output

Every important claim should be mentally tagged as one of:

- `Observed`
- `Inferred`
- `Judgment`

If too much of the answer sits in `judgment`, say so and lower confidence.

## Decision-Grade Evidence

If the user needs a real decision, do not stop at external facts. Gather evidence about the actor's choice environment:

- what the decision-maker can actually afford
- what is reversible or irreversible
- what downside matters more than upside
- what waiting costs
- what switching later would cost

The same forecast can imply different actions for different constraint sets.

## Evidence Ceiling and Next Observation

For weak or uncertain forecasts, end with:

- `Evidence ceiling`: what cannot be resolved well today
- `Next observation`: the cheapest, highest-value signal that would most change the forecast

This keeps the loop alive instead of pretending the current answer is the end state.
