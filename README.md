# serenity-chan-stock-skill

Language: [中文](#中文) | [English](#english)

---

## 中文

`serenity-chan-stock-skill` 是一个数据优先的股票研究 skill，用于 A 股、美股、港股和跨市场主题研究。它把主题、股票代码或候选池转化为可核验、可证伪、可跟踪的研究结论，而不是直接给出交易指令。

本 skill 只提供研究框架、证据链、评级约束和风险边界，不提供个性化投资建议，不承诺收益，不执行交易。

### 核心思想

本 skill 的核心原则是 `No Data, No Guess`：没有取到关键数据时，必须降低评级上限，不允许编造当前价格、市值、财报、客户、订单、估值或买点。

完整分析链路如下：

```text
市场/政策/技术叙事
→ 已发生需求变化
→ 产业链/供应链分层
→ 真正扩产瓶颈
→ 映射公司
→ 证据等级
→ 财务兑现
→ 内在增长与市场隐含预期
→ 技术位置与缠论买点
→ 仓位条件与证伪点
```

默认输出不是“推荐买入”，而是：核心长线候选、强观察对象、高弹性主题、已拥挤/高估对象、剔除或证伪对象。

### 工作流

1. 先识别请求类型：主题扫描、单股鉴股、候选对比或数据核验。
2. 先解析市场和代码：A 股、美股、港股不能混用披露源、行情源和财务源。
3. 先输出 `Data Fetch Plan`：明确每类关键数据的首选源、结构化源、fallback、forbidden source 和失败降级规则。
4. 再做数据预检：当前价格、复权历史、最新财报、公告/filing、供应链证据必须逐项标记状态。
5. 再进入研究判断：产业链瓶颈、公司证据、财务兑现、估值赔率、技术位置分开分析。
6. 最后输出评级、动作、证伪条件和需要继续跟踪的触发器。

### 数据质量门禁

任何涉及当前股价、历史走势、估值、财报、订单、客户关系、公告、买点或评级的任务，都必须先做数据预检。

报告必须包含：

```markdown
## 数据质量与限制
- 市场与代码解析：OK / PARTIAL / FAILED
- 当前价格：OK / PARTIAL / STALE / FAILED / PENDING
- 历史复权行情：OK / PARTIAL / STALE / FAILED / PENDING
- 财报数据：OK / PARTIAL / STALE / FAILED / PENDING
- 公告/filing：OK / PARTIAL / STALE / FAILED / PENDING
- 供应链证据：OK / PARTIAL / STALE / FAILED / PENDING
- 无法验证字段：____
- 因数据限制，本报告评级上限：S/A/B/C/D/OBSERVE_ONLY
```

硬规则：

- 当前价格失败：不能给“当前买点”；评级最高 B。
- 复权历史行情失败：不能输出缠论买点；技术评级最高 C。
- 最新财报失败：不能给 S/A 长线结论。
- 原始公告/filing 失败：客户、订单、产能只能算线索，不能算强证据。
- 多源价格差异 > 0.5%：必须说明；> 2%：暂停估值和技术结论。
- A 股数据不得默认用美股源；美股财报不得用 A 股 F10 代替；港股和 A/H 双重上市必须区分代码、货币、股本。

### 市场路由

| 市场 | 代码例子 | 披露主源 | 行情/财务优先级 | 关键风险 |
|---|---|---|---|---|
| A 股 | 688019.SH / 300750.SZ / 920593.BJ | 巨潮、上交所、深交所、北交所 | Wind/Choice/CSMAR/Tushare 到 AKShare/BaoStock 辅助 | 复权口径、F10 错误、互动平台弱证据 |
| 美股 | AAPL / NVDA / MU | SEC EDGAR、公司 IR | Polygon/FactSet/Bloomberg/Tiingo/Yahoo/Stooq；SEC XBRL 财报 | split、ADR、非 GAAP、SBC、ATM/S-3 |
| 港股 | 0700.HK / 9988.HK | HKEXnews、公司公告 | Wind/Choice/港交所/财经数据商 | 港币、流动性、配售、A/H 差异 |

### 三个分析镜头

`Serenity Lens`：寻找真正卡住产业链的瓶颈，而不是追热点。核心问题是：谁在花大钱，钱流到哪一层，哪一层最窄，谁控制窄口，市场是否错分，以及这个错分能否在 1-4 个季度内被财报或公告验证。

`Fundamental + Valuation Lens`：验证候选能否成为长线对象。重点检查收入增速、核心业务占比、毛利率、净利率、经营现金流、应收、存货、合同负债、capex、折旧、融资稀释、客户集中度、市场隐含增长和三情景估值。

`Chan Lens`：只负责位置，不负责证明公司好坏。必须区分“好赛道”“好公司”“好股票”“当前好买点”。禁止用技术买点拯救已证伪的基本面，也禁止在利好当天无结构追高。

### 评分与评级

综合评分为 100 分，但最终评级必须受数据质量上限约束。

| 模块 | 权重 |
|---|---:|
| 数据质量与可核验性 | 15 |
| Serenity 瓶颈强度 | 20 |
| 客户/订单/供应链证据 | 15 |
| 财务兑现质量 | 15 |
| 估值与内在增长赔率 | 15 |
| 缠论/技术位置 | 10 |
| 风险、证伪与治理 | 10 |

评级含义：

- S：核心长线候选。
- A：强观察对象。
- B：有潜力但存在数据、证据或估值缺口。
- C：主题型或交易型，不适合作长线核心。
- D：剔除、证伪或反面样本。
- OBSERVE_ONLY：仅观察，市场或关键数据未解析。

### 输出合同

标准报告必须包含：

1. 结论先行：最值得优先研究、不适合追、仅观察等。
2. 数据质量与限制。
3. 一句话 thesis。
4. 产业链位置和卡点。
5. 证据等级：事实、推断、待验证。
6. 财务兑现与估值。
7. 缠论/技术位置。
8. 催化剂和 1-4 季度验证路径。
9. 证伪条件。
10. 行动框架：观察、等买点、小仓试错、核心候选、剔除。

交付 Markdown 报告前必须运行：

```bash
python scripts/validate_output_contract.py <report.md>
```

### 安装

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

### 使用示例

```text
请用 serenity-chan-stock-skill 分析 A 股国产算力链，目标是筛出 1-3 个长线高胜率对象。先输出 Data Fetch Plan，再做产业链卡点排序、公司筛选、财务和缠论买点判断。
```

### 本地门禁

```bash
python scripts/validate_skill.py .
python scripts/data_router.py resolve 688019
python scripts/data_router.py plan AAPL
python scripts/serenity_chan_scorecard.py assets/scorecard_template.json --validate-only
python scripts/serenity_chan_scorecard.py assets/scorecard_template.json --format md
python scripts/validate_output_contract.py evals/fixtures/pass_no_network_buy_point.md
python scripts/run_static_evals.py
```

---

## English

`serenity-chan-stock-skill` is a data-first equity research skill for A-share, US, HK, and cross-market stock research. It converts a theme, ticker, or candidate pool into a verifiable, falsifiable, and trackable research conclusion instead of a trading instruction.

This skill provides a research framework, evidence chain, rating constraints, and risk boundaries. It does not provide personalized investment advice, promise returns, or execute trades.

### Core Idea

The core rule is `No Data, No Guess`: when critical data is missing, the rating cap must be downgraded. The agent must not invent current prices, market caps, financials, customers, orders, valuations, or buy points.

The full reasoning chain is:

```text
Market / policy / technology narrative
→ Confirmed demand inflection
→ Value-chain and supply-chain layers
→ True capacity bottleneck
→ Company mapping
→ Evidence level
→ Financial realization
→ Intrinsic growth versus implied expectations
→ Technical position and Chan buy-point discipline
→ Position conditions and falsification triggers
```

The default output is not “buy now”. It classifies ideas as long-term core candidates, strong watchlist names, high-beta themes, crowded or overvalued names, and rejected or falsified candidates.

### Workflow

1. Identify the request type: theme scan, single-company challenge, candidate comparison, or data audit.
2. Resolve market and ticker first: A-share, US, and HK sources must not be mixed.
3. Produce a `Data Fetch Plan`: list preferred sources, structured sources, fallback sources, forbidden sources, and downgrade rules for each critical dataset.
4. Run a data preflight: current price, adjusted history, latest financials, filings, and supply-chain evidence must each have a status.
5. Analyze only after preflight: separate bottleneck logic, company evidence, financial realization, valuation odds, and technical position.
6. Deliver rating, action, falsification conditions, and follow-up triggers.

### Data Quality Gate

Any task involving current price, historical trend, valuation, financials, orders, customer relationships, filings, buy points, or ratings must start with a data preflight.

The report must include:

```markdown
## Data Quality And Limits
- Market and ticker resolution: OK / PARTIAL / FAILED
- Current price: OK / PARTIAL / STALE / FAILED / PENDING
- Adjusted history: OK / PARTIAL / STALE / FAILED / PENDING
- Financial data: OK / PARTIAL / STALE / FAILED / PENDING
- Filings: OK / PARTIAL / STALE / FAILED / PENDING
- Supply-chain evidence: OK / PARTIAL / STALE / FAILED / PENDING
- Unverified fields: ____
- Rating cap from data limits: S/A/B/C/D/OBSERVE_ONLY
```

Hard rules:

- If current price is unavailable, no current buy point is allowed and the rating is capped at B.
- If adjusted historical data is unavailable, no Chan buy point is allowed and the technical rating is capped at C.
- If latest financials are unavailable, S/A long-term conclusions are not allowed.
- If primary filings are unavailable, customers, orders, and capacity can only be treated as leads, not strong evidence.
- If cross-source price differences exceed 0.5%, disclose it; if they exceed 2%, pause valuation and technical conclusions.
- Do not use US sources for A-share data, A-share F10 summaries for US financials, or mixed A/H tickers without separating code, currency, and share count.

### Market Routing

| Market | Examples | Primary disclosure sources | Price and financial priority | Key risks |
|---|---|---|---|---|
| A-share | 688019.SH / 300750.SZ / 920593.BJ | CNINFO, SSE, SZSE, BSE | Wind/Choice/CSMAR/Tushare, then AKShare/BaoStock as auxiliary sources | Adjustment basis, F10 errors, weak investor-platform evidence |
| US | AAPL / NVDA / MU | SEC EDGAR, company IR | Polygon/FactSet/Bloomberg/Tiingo/Yahoo/Stooq; SEC XBRL financials | Splits, ADRs, non-GAAP, SBC, ATM/S-3 |
| HK | 0700.HK / 9988.HK | HKEXnews, company announcements | Wind/Choice/HKEX/financial data vendors | HKD, liquidity, placings, A/H differences |

### Three Analytical Lenses

`Serenity Lens`: find the real supply-chain bottleneck instead of chasing hot narratives. Ask who is spending, where the money flows, which layer is narrowest, who controls that layer, whether the market is misclassifying it, and whether filings or financials can validate the thesis within 1-4 quarters.

`Fundamental + Valuation Lens`: decide whether a candidate can become a long-term object. Check revenue quality, core-business mix, gross margin, net margin, operating cash flow, receivables, inventory, contract liabilities, capex, depreciation, dilution, customer concentration, implied growth, and bull/base/bear valuation scenarios.

`Chan Lens`: judge position only. It does not prove company quality. Separate “good sector”, “good company”, “good stock”, and “good current entry”. Do not use technical buy points to rescue a falsified fundamental thesis, and do not chase news-day breakouts without structure.

### Scoring And Ratings

The scorecard is 100 points, but final ratings are constrained by data-quality caps.

| Module | Weight |
|---|---:|
| Data quality and verifiability | 15 |
| Serenity bottleneck strength | 20 |
| Customer/order/supply-chain evidence | 15 |
| Financial realization quality | 15 |
| Valuation and intrinsic-growth odds | 15 |
| Chan/technical position | 10 |
| Risk, falsification, and governance | 10 |

Rating meanings:

- S: long-term core candidate.
- A: strong watchlist name.
- B: promising, but with data, evidence, or valuation gaps.
- C: thematic or trading-oriented, not a long-term core object.
- D: rejected, falsified, or negative example.
- OBSERVE_ONLY: observe only because market or critical data is unresolved.

### Output Contract

A standard report must include:

1. Conclusion first: priority research, do not chase, observe only, and so on.
2. Data quality and limits.
3. One-sentence thesis.
4. Value-chain position and bottleneck.
5. Evidence levels: facts, inference, and missing proof.
6. Financial realization and valuation.
7. Chan and technical position.
8. Catalysts and 1-4 quarter validation path.
9. Falsification conditions.
10. Action framework: observe, wait for buy point, small test position, core candidate, or reject.

Before delivering a Markdown report, run:

```bash
python scripts/validate_output_contract.py <report.md>
```

### Install

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

### Example Prompt

```text
Use serenity-chan-stock-skill to analyze the A-share domestic AI compute supply chain. Find 1-3 long-term high-probability candidates. Start with a Data Fetch Plan, then rank value-chain bottlenecks, companies, financial evidence, and Chan buy-point discipline.
```

### Local Gates

```bash
python scripts/validate_skill.py .
python scripts/data_router.py resolve 688019
python scripts/data_router.py plan AAPL
python scripts/serenity_chan_scorecard.py assets/scorecard_template.json --validate-only
python scripts/serenity_chan_scorecard.py assets/scorecard_template.json --format md
python scripts/validate_output_contract.py evals/fixtures/pass_no_network_buy_point.md
python scripts/run_static_evals.py
```
