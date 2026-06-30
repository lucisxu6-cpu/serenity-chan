---
name: serenity-chan-stock-skill
description: Use when performing data-first equity research for A-share, US, HK, or cross-market stock screening, single-company thesis challenges, theme scans, candidate comparisons, evidence/falsification dashboards, valuation work, Chan/GF-DMA buy-point discipline, or strategy/forecast follow-through. Always route market data and filings through market-specific sources before making current price, financial, rating, entry, allocation, or forecast claims.
---

# Serenity Chan Stock Skill

## Core Contract

Turn a stock, theme, candidate pool, or strategy question into research that is grounded in real data, AI investigation, evidence constraints, action conditions, and reviewable follow-up.

Use this operating sequence for every research task:

1. Parse the request into one route.
2. Acquire real data before making current price, valuation, financial, rating, entry, allocation, or forecast claims.
3. Keep market-specific source routing: A-share, US, and HK must use their own disclosure and market context.
4. Separate observed facts, inference, and judgment.
5. Generate AI research work before formal delivery when the task asks for analysis, comparison, recommendations, strategy, or action.
6. Validate every structured artifact before using it in a user-facing answer.
7. Deliver in Chinese by default while preserving machine enum fields in English.

The skill supports research assistance, evidence organization, candidate comparison, action framing, and forecast review. Users remain responsible for investment decisions.

## Route Selection

Choose exactly one primary route. Read only the references needed for that route.

| User Intent | Route | Read First | Primary Commands |
|---|---|---|---|
| Current price, market, data availability, source check | Data audit | `references/01_data_first_market_router.md` | `python scripts/data_router.py resolve <symbol>` then `python scripts/data_router.py fetch <symbol>` |
| Single stock analysis, thesis challenge, valuation, buy point | Single company | `references/01_data_first_market_router.md`, `references/02_serenity_bottleneck_workflow.md`, `references/03_fundamental_valuation_framework.md`, `references/04_chan_technical_framework.md`, `references/06_risk_compliance_no_guess.md` | `python scripts/data_router.py fetch <symbol>` then build/validate the relevant output contract |
| Theme scan, industry chain, candidate discovery | Theme scan | `references/17_industry_domain_packs.md`, `references/01_data_first_market_router.md` | `python scripts/build_theme_candidate_universe.py <theme> --out <universe.json>` then `python scripts/run_theme_research_analysis.py <theme> --out-dir <run_dir> --research-mode formal` |
| Multiple candidate comparison | Candidate comparison | `references/01_data_first_market_router.md`, `references/02_serenity_bottleneck_workflow.md`, `references/03_fundamental_valuation_framework.md`, `references/04_chan_technical_framework.md`, `references/15_ai_overlay_execution_protocol.md` | `python scripts/run_research_analysis.py <symbol...> --out-dir <run_dir> --research-mode formal` |
| Recommendation, allocation, action plan, trend forecast | Strategy forecast | `references/16_laplace_strategy_bridge.md`, `companion-skills/laplace-forecast/SKILL.md` | Build `laplace_strategy_input.json`, `laplace_strategy_prompt.json`, `laplace_strategy_judgment.json`, then render strategy report |
| Report rendering or delivery validation | Delivery | `references/05_output_templates.md`, `references/06_risk_compliance_no_guess.md` | Formal: `python scripts/validate_research_delivery.py <comparison_final.json>` then `python scripts/render_research_report.py --comparison-report <comparison_final.json>`; research progress: `python scripts/render_research_report.py --comparison-report <baseline.json> --mode research_brief` |

When a task crosses routes, complete the earlier evidence route first. For example, a strategy recommendation built from stocks must complete candidate comparison before entering strategy forecast.

## Data-First Rules

Before analysis, obtain or explicitly fail the relevant data package:

- Market identity, normalized symbol, exchange, and currency.
- Current quote and adjusted price history when price, entry, or technical timing is requested.
- Financials, filings/announcements, valuation inputs, total shares, total market cap, and source level.
- Customer, order, bid-win, capacity, and revenue-transmission evidence when the thesis depends on commercial adoption.
- Attempt ledger, data gaps, manual retrieval tasks, research debt, and data consumption audit.

Never invent current price, market cap, customers, orders, financial rows, source strength, or buy points. A failed or unrequested critical dataset constrains rating and action state until the data path is repaired or explicitly scoped out by the user.

Market routing is mandatory:

- A-share uses CNINFO/SSE/SZSE/BSE disclosure context, A-share quote and valuation sources, and A-share capital-action parsing.
- US uses SEC/IR disclosure context and US quote/filing conventions.
- HK uses HKEXnews, HK quote conventions, HKD valuation, share-count disclosures, placement, monthly return, and next-day disclosure context.

Use `references/01_data_first_market_router.md` for source ladders, forbidden source substitutions, adapter boundaries, and data-gap semantics.

## Formal AI Research Loop

Formal analysis and comparison must include AI research execution. Use `references/15_ai_overlay_execution_protocol.md` for details.

Required loop:

1. Run the formal research command.
2. If it returns `AGENT_RESEARCH_QUEUE_READY`, immediately enter the execution workspace:

```bash
python scripts/execute_agent_research_queue.py run <run_dir>/agent_research_queue.json \
  --workspace-out <run_dir>/agent_overlay_workspace.json \
  --taskbook-out <run_dir>/agent_research_taskbook.md \
  --status-out <run_dir>/agent_research_execution_status.json \
  --report-out <run_dir>/comparison_final.json \
  --markdown-out <run_dir>/comparison_final.md
```

3. If the execution status is `AGENT_RESEARCH_REQUIRED`, the current AI reviewer must complete every candidate work item before user-facing formal delivery. Read the workspace, taskbook, manifest, review packet, committee packet, source catalog, customer/order/capacity evidence, deterministic matrices, and prompt package.
4. Write one `ai_research_dossier.json` per candidate using `assets/ai_research_dossier.schema.json`.
5. Project the dossier into exactly one result:

- `ai_research_overlay.json` when evidence is sufficient.
- `ai_review_outcome.json` when evidence is insufficient or conflicts with deterministic data.

6. Validate every dossier and projected result:

```bash
python scripts/validate_ai_research_dossier.py <dossier.json> --manifest <manifest.json>
python scripts/validate_ai_overlay.py <overlay.json> --manifest <manifest.json>
python scripts/validate_ai_review_outcome.py <outcome.json>
```

7. Rerun the execution command. It will merge and validate when every AI package is complete. If a package is incomplete or invalid, repair the exact candidate artifact named by `agent_research_execution_status.json`.

Manual merge remains available when paths are supplied directly:

```bash
python scripts/validate_and_merge_ai_overlay.py <manifest...> \
  --dossier SYMBOL=<dossier.json> \
  --overlay SYMBOL=<overlay.json> \
  --ai-outcome SYMBOL=<outcome.json> \
  --report-out <comparison_final.json> \
  --markdown-out <comparison_final.md>
python scripts/validate_research_delivery.py <comparison_final.json>
```

Formal delivery requires every candidate to have a validated dossier plus one validated projected result. Internal baselines, queues, diagnostic artifacts, and unexecuted AI work stay inside the execution workspace.

## Candidate Comparison Logic

Compare candidates in layers:

```text
candidate pool coherence
→ value-chain layer and bottleneck fit
→ evidence confidence
→ financial quality
→ valuation payoff
→ technical timing
→ research priority
→ action readiness
```

Only same-layer candidates with sufficient evidence and open action conditions may produce a formal decision candidate. Mixed-layer, cross-theme, or data-diagnostic pools produce research priority, next evidence, and scope boundaries.

Use `assets/comparison_output_contract.schema.json` and `scripts/validate_comparison_report.py` as the final report contract. Final reports must include AI review status, AI dossier consumption, data acquisition summary, customer evidence matrix, valuation matrix, currency normalization, growth hypothesis, technical timing, capital actions, data consumption audit, readiness matrix, research debt, runbook, ranking, report readiness, and final decision.

## Strategy Forecast Loop

Use this route when the user asks what to do, how to allocate, which direction is actionable, what can change the thesis, or how the next 30/90/180 days may evolve.

Required loop:

```bash
python scripts/build_laplace_strategy_input.py <comparison_final.json> --out <laplace_strategy_input.json>
python scripts/validate_laplace_strategy_input.py <laplace_strategy_input.json>
python scripts/build_laplace_strategy_prompt.py <laplace_strategy_input.json> --out <laplace_strategy_prompt.json>
python scripts/validate_laplace_strategy_judgment.py <laplace_strategy_judgment.json> --strategy-input <laplace_strategy_input.json>
python scripts/render_strategy_report.py <laplace_strategy_judgment.json> --strategy-input <laplace_strategy_input.json> --out <strategy_report.md>
```

Read `references/16_laplace_strategy_bridge.md` and the companion Laplace skill before writing strategy judgment. Strategy output must preserve Serenity evidence constraints, open research debt, triggers, invalidation, scenarios, action plan, and review cadence.

## Output Requirements

Every user-facing report should make these answers clear:

- What is known from real data.
- What is inferred by AI research.
- What judgment follows from the evidence.
- Which candidate or layer deserves research first.
- Whether action conditions are present.
- Which evidence blocks rating or action.
- What would upgrade, delay, reduce, or invalidate the thesis.
- What to check in 30, 90, and 180 days.

Use `references/05_output_templates.md` for report modes and `references/06_risk_compliance_no_guess.md` for evidence, rating, and compliance boundaries.

## Validation Gates

Run the narrowest applicable gate before delivery:

```bash
python scripts/validate_output_contract.py <report.md>
python scripts/validate_output_contract_json.py <contract.json>
python scripts/validate_comparison_report.py <comparison_final.json>
python scripts/validate_research_delivery.py <comparison_final.json>
python scripts/validate_laplace_strategy_input.py <laplace_strategy_input.json>
python scripts/validate_laplace_strategy_judgment.py <laplace_strategy_judgment.json> --strategy-input <laplace_strategy_input.json>
python scripts/validate_skill.py .
python scripts/run_static_evals.py
```

When a gate fails, repair the artifact or lower the claim to the validated evidence boundary.

## Reference Map

- `references/01_data_first_market_router.md`: market routing, source ladders, forbidden substitutions, data gaps.
- `references/02_serenity_bottleneck_workflow.md`: value-chain bottleneck logic and Serenity thesis formation.
- `references/03_fundamental_valuation_framework.md`: financial realization, valuation, growth tiers, scenario reasoning.
- `references/04_chan_technical_framework.md`: Chan/GF-DMA technical timing and buy-point discipline.
- `references/05_output_templates.md`: user-facing report modes.
- `references/06_risk_compliance_no_guess.md`: evidence levels, rating caps, compliance boundaries.
- `references/15_ai_overlay_execution_protocol.md`: dossier, overlay, outcome, merge, delivery loop.
- `references/16_laplace_strategy_bridge.md`: Serenity-to-Laplace strategy handoff.
- `references/17_industry_domain_packs.md`: built-in industry routes and candidate universe construction.

<!-- validator keywords: A 股 评级封顶 No Data, No Guess Market-Specific Data Routing -->
