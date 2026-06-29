---
name: laplace-forecast
description: Recursive forecasting and calibration engine for predicting events, markets, industries, technology adoption, geopolitics, career paths, and personal strategic choices by combining first-principles decomposition, base-rate anchoring, multi-layer human and social analysis, decision-maker modeling, live evidence, proxy design, contradiction testing, reflexivity checks, persistent ledgering, and scenario weighting. Use when the user asks what is likely to happen, what to do next, which path to choose, how trends may evolve, what could trigger a reversal, how to reason from bottom-layer drivers rather than surface narratives, or how to revisit and score earlier forecasts.
---

# Laplace Forecast

## Overview

Use this skill as a cross-layer prediction engine, not a template for hot takes. The goal is to reduce a messy future question into observable outcomes, priors, dominant variables, tested causal stories, current evidence, and decision-ready scenarios.

Do not promise certainty. Do not stop at one discipline. Do not hand-wave missing data. When evidence is incomplete, design the next-best proxy or data-acquisition path, then continue.

Read [references/first-order-lenses.md](references/first-order-lenses.md) when the topic is broad, societal, human, organizational, or otherwise shaped by multiple systems at once. Read [references/evidence-loop.md](references/evidence-loop.md) when direct data is thin, contradictory, requires proxy design, needs base-rate anchoring, or calls for iterative updating. Read [references/ledger-schema.md](references/ledger-schema.md) when using the forecast ledger. Use `scripts/forecast_ledger.py` when the forecast is important enough to revisit, search, score, recalibrate, or archive later.

## Non-Negotiables

- Forecast observable outcomes, not vague vibes.
- Reconstruct the user's real decision, not only the literal question.
- Model the decision-maker before giving advice.
- If the recommendation is preference-sensitive and preferences are unknown, branch the answer into `2-3` plausible decision profiles or ask one concise question.
- Anchor nontrivial forecasts with base rates or historical analogues.
- Use multiple first-order lenses on any nontrivial topic.
- Separate `observed`, `inferred`, and `judgment`.
- Use exact dates for current or unstable topics.
- If the data is weak, lower confidence and say why.
- Keep a forecast ledger outside the current response when the view changes materially or the forecast may be revisited.
- For important forecasts, store at least one structured claim with numeric probability and a resolution date.
- If a core variable is contradicted, loop back and rebuild the model instead of forcing the old answer.

## Recursive Forecast Engine

1. Frame the target.
- Rewrite the request into `object + horizon + geography + decision use`.
- Convert `Will X happen?` into one or more observable outcomes whenever possible.
- Surface the hidden decision: enter, avoid, wait, hedge, switch, hire, build, or ignore.

2. Model the decision owner.
- Identify who is acting: individual, company, investor, operator, policymaker, voter, or household.
- State the likely utility function: maximize upside, minimize downside, preserve options, avoid regret, gain status, protect cash flow, reduce uncertainty.
- State the constraint set: time, money, access, regulation, reputation, geography, switching cost, skill, obligation.
- State reversibility: easy to undo, costly to undo, or irreversible.
- If user preferences are unknown and the answer is not very preference-sensitive, make a reasonable default assumption and label it.
- If user preferences are unknown and the answer is preference-sensitive, branch the advice into profiles such as `risk-averse`, `balanced`, and `aggressive`, or ask one concise clarifying question.

3. Anchor with base rates and analogues.
- Ask what class of event this belongs to.
- Ask how similar events, transitions, or adoption curves usually behave.
- Use base rates to anchor probability before narrative adjustment.
- Use historical analogues as scaffolding, not as destiny.
- If multiple reference classes compete, choose the one with the closest causal structure, not the most dramatic story.
- Do not move far off the prior on one weak or low-quality signal.
- If no strong analogue exists, say the prior is weak and widen the range.

4. Rebuild the system from the bottom up.
- Map the problem across multiple layers:
  `economics`, `social structure`, `social psychology`, `behavior`, `institutions`, `technology`, `operations`, and when relevant `geopolitics`.
- Start from incentives, constraints, bottlenecks, and emotional drivers before discussing narratives.
- For reflexive systems, include expectation loops now, not only at the end:
  who sees the trend, who adapts, who front-runs, who blocks, and who copies.
- Use the minimum number of lenses needed for rigor, but never rely on a single lens for a complex forecast.

5. Extract the dominant variables.
- Collapse the full map into the `3-7` variables that actually move the outcome.
- For each variable, assign:
  `type`, `role`, `direction`, `speed`, and `observability`.
- Good roles are: `tailwind`, `headwind`, `bottleneck`, `trigger`, `absorber`, `tripwire`.
- If you cannot name the dominant variables, you are not ready to forecast.

6. Arbitrate across lenses.
- If lenses disagree, do not average them mechanically.
- Ask which lens sets `magnitude`, which sets `timing`, and which holds `veto power`.
- Institutions and gatekeepers often decide permission.
- Operations often decide real-world throughput.
- Social psychology often decides adoption speed when options are visible and friction is low.
- Economics often decides long-run direction if no veto player blocks it.

7. Build a thesis and a counter-thesis.
- Write the base causal story in one tight paragraph.
- Write the strongest opposing story, not a weak strawman.
- Ask what must be true for each story to win.
- If the opposite story survives too easily, widen uncertainty before giving advice.

8. Gather evidence and design proxies.
- Prefer official, primary, or otherwise authoritative sources.
- For unstable or current topics, browse and verify with dates.
- Favor hard indicators over commentary: pricing, margins, usage, hiring, deployments, filings, regulation, conflict activity, logistics, capital flows, retention, or behavior shifts.
- If direct evidence is missing, design a proxy and explain why it is informative.
- If needed, create small local tools, structured searches, or comparison workflows to collect or normalize evidence, subject to environment and policy constraints.
- State the `evidence ceiling`: what you still cannot know well today.
- State the `next evidence`: the next cheapest, highest-value observation that would most change the view.
- If no meaningful next observation exists, say so explicitly and use `[]` in the ledger instead of inventing a placeholder task.
- Never invent observed data. Synthetic estimates are allowed only for scenario modeling and must be labeled.

9. Run the contradiction loop and keep a ledger.
- Stress-test the thesis with the strongest disconfirming evidence you can find.
- Reweight variables after each important contradiction.
- Record the prior view, the new evidence, and the updated view.
- If a top variable changes sign or importance, rebuild the causal map.
- For important forecasts, log the state to an external ledger rather than keeping it only in prose.
- Continue looping until the view stabilizes or the evidence ceiling is reached.

10. Run a final reflexivity sweep.
- Ask whether the forecast itself changes behavior.
- Identify which actors can adapt once the trend becomes visible.
- Ask whether signaling, crowding, panic, policy reaction, copycat behavior, or defensive moves alter the base case.
- If reflexivity materially changes timing or probability, update the model instead of appending a footnote.

11. Collapse to action.
- Produce `base`, `upside`, and `downside` cases.
- Attach rough probabilities or confidence bands when useful.
- Name the `30/90/180-day` triggers that would materially change the forecast.
- Name the next observation, proxy, or event that should be checked next.
- State what would invalidate the view.
- Convert the forecast into the user's actual action space.

## Variable Rating

Use a compact table or bullet list when it helps. A good variable record includes:

- `Variable`
- `Why it matters`
- `Role`
- `Current direction`
- `Observability`: high, medium, low
- `Change speed`: slow, medium, fast
- `Dominant lens`
- `Confidence`: high, medium, low

If two low-observability variables dominate the answer, lower confidence sharply.

## Forecast Ledger

When the question is important, current, or likely to be revisited, keep a compact internal ledger:

- `Prior`: initial base-rate or starting odds
- `Thesis`: the initial causal story
- `Key assumptions`
- `New evidence`
- `What changed`
- `Updated odds or confidence`
- `Outcome`, once known
- `Calibration note`: hit, miss, partial, or unresolved

If the updated view differs meaningfully from the prior view, say so explicitly.

## Persistent Ledger

For forecasts likely to matter later, store them in a real ledger instead of relying only on memory.

Recommended path:

- Use a writable workspace path such as `forecast-ledger.sqlite`, or
- Use `$CODEX_HOME/memories/laplace-forecast-ledger.sqlite` when available

Do not use one infinite ledger for every topic forever. Prefer:

- one ledger per workspace, domain, or year
- active ledgers for open forecasts
- archive ledgers for resolved historical forecasts

Use `scripts/forecast_ledger.py` for:

- `add`: create a forecast record
- `update`: append evidence and view changes
- `resolve`: mark the outcome and score calibration
- `agenda`: list open forecasts and the next evidence to check
- `show`: inspect one full forecast object
- `list`: browse open or resolved forecasts quickly
- `search`: retrieve earlier forecasts by topic, object, thesis, or decision use
- `archive`: move resolved forecasts into a colder ledger
- `stats`: inspect record counts and Brier-score trends

The ledger is evented, not only snapshot-based:

- current state lives in a compact forecast snapshot
- updates and resolutions are stored as separate events
- claim probability changes are stored as separate trajectories
- agenda and search run on indexed tables instead of full-record scans

This keeps the forecast object flexible for the model while making long-term maintenance practical.

Normalize ledger timestamps to UTC when storing them so due checks and archive boundaries remain correct across time zones.
If a timestamp includes a clock time, require an explicit timezone offset. Date-only fields may be interpreted as `00:00:00+00:00`.
Treat resolved claims and fully resolved forecasts as immutable. Do not overwrite them through update or repeated resolution.

If the environment does not permit ledger writes, state that limitation explicitly.

## Decision Remapping

Different forecast types need different endings. Remap the answer to the real use case:

- Personal choice: say what to do now, what to avoid, what to watch, when to change course, and how reversibility affects the choice.
- Career or jobs: forecast tasks, bottlenecks, and demand shifts before naming winners and losers.
- Industry or market: identify where value accrues, who captures margin, and which bottleneck decides timing.
- Business or product: identify wedge, moat, distribution, switching costs, adoption friction, and which actor can veto the rollout.
- Geopolitics or conflict: identify deterrence logic, escalation ladder, red lines, alliance commitments, and miscalculation risk.

## Output Contract

Use this shape unless the user asks for something else:

- `Forecast`: the directional answer
- `Decision`: what the user should do with that answer
- `Decision model`: goals, constraints, and reversibility assumptions
- `Base rate`: prior or historical anchor
- `Why`: the core causal story
- `Dominant variables`: the few variables that matter most
- `Evidence`: observed facts, dates, and sources
- `Evidence ceiling`: what still cannot be known well
- `Next evidence`: what to check next to improve the forecast
- `Scenarios`: base, upside, downside
- `Triggers`: what would move the odds
- `Invalidation`: what would prove this wrong
- `Confidence`: high, medium, low or rough probabilities

Label statements clearly:

- `Observed`: directly supported by cited evidence
- `Inferred`: derived from observed evidence or proxies
- `Judgment`: subjective weighting or scenario probability

## Failure Modes

Avoid these common errors:

- Single-lens reductionism
- Confusing technical capability with real adoption
- Confusing attention with demand
- Ignoring institutions, gatekeepers, or regulation
- Treating social psychology as secondary
- Skipping priors and overfitting to current narrative
- Giving generic advice without modeling the decision-maker
- Failing to resolve conflict between lenses
- Ignoring reflexive feedback after the forecast becomes visible
- Treating one assumed utility profile as universal
- Updating the story without scoring the outcome later
- Using stale data for unstable topics
- Answering at the wrong resolution
- Refusing to update after contradiction

## Common Triggers

Use this skill for prompts such as:

- `预测2026年会不会...`
- `未来半年最可能发生什么`
- `这个行业接下来怎么走`
- `个人现在该怎么选`
- `什么岗位会被AI取代`
- `值不值得入场`
- `帮我做趋势判断/情景推演/概率分析`
- `按底层逻辑预测`
- `别只用单一视角，综合分析未来`
