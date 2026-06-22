# serenity-chan-stock-skill v3

Data-first equity research skill combining:

- Serenity-style supply-chain bottleneck hunting
- Fundamental verification and falsification
- Bayesian growth / TAM-adjusted valuation
- GF-DMA trend health
- 缠论 multi-level entry discipline
- A-share / US / HK market-aware data-source routing

## Install

Codex / Agent Skills-compatible clients:

```bash
SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/serenity-chan-stock-skill"
mkdir -p "$SKILL_DIR"
cp -R SKILL.md references assets scripts examples evals agents "$SKILL_DIR"/
```

Claude Code:

```bash
SKILL_DIR="$HOME/.claude/skills/serenity-chan-stock-skill"
mkdir -p "$SKILL_DIR"
cp -R SKILL.md references assets scripts examples evals agents "$SKILL_DIR"/
```

## Core use

```text
请用 serenity-chan-stock-skill 分析 A 股国产算力链，目标是筛出 1-3 个长线高胜率对象。先输出 Data Fetch Plan，再做产业链卡点排序、公司筛选、财务和缠论买点判断。
```

## Local helpers

```bash
python scripts/validate_skill.py .
python scripts/data_layer_v3.py 688019 --plan
python scripts/data_router.py resolve 688019
python scripts/serenity_chan_scorecard.py assets/scorecard_template.json --validate-only
python scripts/serenity_chan_scorecard.py assets/scorecard_template.json --format md
python scripts/validate_output_contract.py evals/fixtures/pass_no_network_buy_point.md
python scripts/run_static_evals.py
```

## Data rule

If current price, adjusted history, latest financials, or primary filings cannot be obtained, the report must cap its rating and say what remains unverified. Do not guess.

## Reliability gates

Before publishing a generated report, run the output-contract validator against the Markdown file. Before changing skill logic, run the static eval suite so data failures, weak evidence, and unsupported buy-point claims remain downgraded instead of upgraded.
