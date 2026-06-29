# Laplace Strategy Bridge

Use this bridge when Serenity research must become a forecast, strategy, allocation, or action plan.

## Purpose

Serenity provides observed market data, evidence gates, research debt, valuation state, technical state, and candidate priority. Laplace provides scenario weighting, decision-owner modeling, dominant-variable arbitration, triggers, invalidation, and forecast ledgering.

The current AI agent performs the bridge. Deterministic scripts prepare the structured input; the AI reads the input and the companion skill, then writes the strategy judgment in Chinese.

## Repository Layout

- Companion skill: `companion-skills/laplace-forecast/`
- Strategy input schema: `assets/laplace_strategy_input.schema.json`
- Strategy input builder: `scripts/build_laplace_strategy_input.py`
- Strategy input validator: `scripts/validate_laplace_strategy_input.py`

## Required Flow

1. Produce a validated Serenity comparison report.
2. If the report still has missing AI overlay/outcome for formal research, complete the AI overlay/outcome stage first.
3. Build the strategy input:

```bash
python scripts/build_laplace_strategy_input.py comparison_final.json \
  --theme "A股机器人与AI算力" \
  --horizon "3-6个月" \
  --decision-use "watchlist allocation and action triggers" \
  --out laplace_strategy_input.json
```

4. Validate the strategy input:

```bash
python scripts/validate_laplace_strategy_input.py laplace_strategy_input.json
```

5. Read `companion-skills/laplace-forecast/SKILL.md`.
6. Read `companion-skills/laplace-forecast/references/first-order-lenses.md` when the question involves a theme, industry, market trend, or allocation.
7. Read `companion-skills/laplace-forecast/references/evidence-loop.md` when evidence is partial, contradictory, proxy-based, or decision-grade.
8. Read `companion-skills/laplace-forecast/references/ledger-schema.md` when the result should be revisited or scored later.
9. Produce the strategy answer using observed / inferred / judgment labels.

## Output Requirements

The strategy answer must include:

- `Forecast`: directional view and probability range.
- `Decision`: watch, avoid, enter slowly, rebalance, hedge, or wait.
- `Decision model`: default profile, horizon, constraints, and reversibility.
- `Observed`: facts from Serenity matrices.
- `Inferred`: implications from facts and proxies.
- `Judgment`: scenario weighting and action preference.
- `Dominant variables`: 3-7 variables that actually move the view.
- `Scenarios`: base, upside, downside.
- `Triggers`: 30 / 90 / 180 day signals.
- `Invalidation`: events that should break the thesis.
- `Next evidence`: the cheapest useful evidence to check next.
- `Action plan`: candidate buckets, position discipline, add/trim rules, and data gates.

## Guardrails

- Do not treat a Serenity ranking as a portfolio allocation by itself.
- Do not upgrade a candidate through Laplace when Serenity has a hard data gate.
- Do not erase research debt; convert it into next evidence and invalidation.
- Do not use market heat as evidence-supported growth.
- Do not output personalized financial advice, return promises, or trade execution instructions.
- Use Chinese for user-facing text.

## Ledger Policy

When the strategy affects a reusable watchlist, medium-term theme view, or allocation plan, create or update a Laplace forecast ledger record. Use the companion script:

```bash
python companion-skills/laplace-forecast/scripts/forecast_ledger.py add \
  --path forecast-ledger.sqlite \
  --record-file forecast_record.json
```

Ledger records need observable claims, numeric probabilities, resolution dates, and resolution criteria. If no useful ledger claim can be defined, explain the evidence ceiling and keep `next_evidence` explicit.
