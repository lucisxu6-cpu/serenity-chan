---
name: serenity-chan-stock-skill
description: Data-first equity research skill combining Serenity-style supply-chain bottleneck hunting, fundamental/valuation underwriting, and Chan/多级别 technical timing. Use for A-share, US, HK and cross-market stock screening, single-company challenge, theme scans, long-term high-conviction candidate selection, evidence/falsification dashboards, and buy-point discipline. Always route market data and filings through market-specific sources before making current claims.
version: "3.0.0"
language: zh-CN-first
license: MIT
compatibility: Codex / Claude Code / Agent Skills-compatible clients
requires_tools: web/search or filing/market-data tools for current analysis; python3 optional for scorecard/data validation
---

# Serenity + 缠论长线高胜率选股鉴股 Skill

## 0. Core Promise

把一个投资主题、股票代码或候选池，转化为一份**可核验、可证伪、可执行跟踪**的研究结论：

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

本 Skill 的默认输出不是“推荐买入”，而是：核心长线候选、强观察对象、高弹性主题、已拥挤/高估对象、剔除或证伪对象。

**最高原则：No Data, No Guess。没有取到关键数据时，必须降低评级上限，不允许编造价格、财报、客户、订单、估值或买点。**

---

## 1. Request Router

先识别用户请求类型，再选择工作流。

### 1.1 Theme Scan / 主题扫描

触发词：产业链、机器人、国产算力、DeepSeek、AI 半导体、CPO、长鑫、昇腾、HBM、电力、美国 AI infrastructure、which stocks、最值得研究。

必须：

1. 先排产业链层级，再排公司。
2. 至少覆盖 3 个价值链层级；深度扫描尽量建立 20+ 候选公司。
3. 每个 top candidate 必须说明“卡住什么环节”。
4. 至少给出一个热门但降级的方向，并说明为什么。
5. 当前结论必须用最新数据源；没有数据时标记为 initial pass。

### 1.2 Single-Company Challenge / 单股鉴股

触发词：分析某公司、是否核心供应商、长线高胜率、估值、买点、证伪。

必须：

1. 解析市场和代码，防止 A 股/美股/HK 取错源。
2. 获取当前价格、复权历史价格、最新财报、公告/filing、股本/市值。
3. 输出基本面 thesis、反方 thesis、证伪条件。
4. 用缠论/多级别技术框架判断“好公司是否有好位置”。

### 1.3 Candidate Comparison / 候选对比

比较多个公司时，不能只按涨幅或 PE 排序。必须分开：

```text
产业链卡点排序
→ 公司证据排序
→ 财务质量排序
→ 估值赔率排序
→ 技术位置排序
→ 综合优先级
```

### 1.4 Data Audit / 数据核验

当用户要求“确认数据”“别猜”“自动分析”时，直接启用 Data-First 模式。使用 `references/01_data_first_market_router.md` 和 `scripts/data_router.py`。

---

## 2. Mandatory Data-First Preflight

任何涉及当前股价、历史走势、估值、财报、订单、客户关系、公告、买点、评级的任务，必须先做数据预检。

输出中必须包含：

```markdown
## 数据质量与限制
- 市场与代码解析：OK / PARTIAL / FAILED
- 当前价格：OK / PARTIAL / STALE / FAILED
- 历史复权行情：OK / PARTIAL / STALE / FAILED
- 财报数据：OK / PARTIAL / STALE / FAILED
- 公告/filing：OK / PARTIAL / STALE / FAILED
- 供应链证据：OK / PARTIAL / STALE / FAILED
- 无法验证字段：____
- 因数据限制，本报告评级上限：S/A/B/C/观察
```

硬规则：

- 当前价格失败：不能给“当前买点”；评级最高 B。
- 复权历史行情失败：不能输出缠论买点；技术评级最高 C。
- 最新财报失败：不能给 S/A 长线结论。
- 原始公告/filing 失败：客户、订单、产能只能算线索，不能算强证据。
- 多源价格差异 > 0.5%：必须说明；> 2%：暂停估值和技术结论。
- A 股数据不得默认用美股源；美股财报不得用 A 股 F10 代替；HK 与 A/H 双重上市必须区分代码、货币、股本。

---

## 3. Core Lenses

### 3.1 Serenity Lens: 瓶颈与小市值弹性

核心问题：

```text
谁在花大钱？
钱会流到哪一层？
哪一层最窄？
谁控制窄口？
当前市场把它错当成什么？
这个错分会不会在 1-4 个季度内被财报或公告验证？
```

重点不是“热点”，而是：已发生需求变化、收入/利润传导路径、供应商稀缺度、认证周期、产能扩张难度、单位经济和毛利率、市值相对需求冲击的弹性、明确证伪点。

参考 `references/02_serenity_bottleneck_workflow.md`。

### 3.2 Fundamental + Valuation Lens: 财务兑现与内在增长

Serenity 找到的是候选，基本面决定能否长线。

必须检查：收入增速和核心业务占比、毛利率与定价权、净利率与经营杠杆、经营现金流/应收/存货/合同负债、capex/在建工程/折旧/融资稀释、客户集中与议价权、市场隐含增长 vs 真实内在增长、Bull/Base/Bear 三情景估值。

参考 `references/03_fundamental_valuation_framework.md`。

### 3.3 Chan Lens: 缠论与多级别买点纪律

缠论只负责位置，不负责证明公司好坏。

必须区分：

```text
好赛道 ≠ 好公司
好公司 ≠ 好股票
好股票 ≠ 当前好买点
```

优先买点：

- 强基本面 + 情绪回撤 + 日线/30 分钟底背驰 + 二买确认；
- 强趋势突破中枢 + 回踩不回中枢 + 三买确认。

禁止：利好当天无结构追高、周线顶背驰后当作长线买点、下跌趋势里把每次反弹都当反转、用技术买点拯救已证伪的基本面。

参考 `references/04_chan_technical_framework.md`。

---

## 4. Market-Specific Data Routing

在任何数据抓取前，先解析市场：

| 市场 | 代码例子 | 披露主源 | 行情/财务优先级 | 关键风险 |
|---|---|---|---|---|
| A 股 | 688019.SH / 300750.SZ / 920593.BJ | 巨潮、上交所、深交所、北交所 | Wind/Choice/CSMAR/Tushare → AKShare/BaoStock 辅助 | 复权口径、F10 错误、互动平台弱证据 |
| 美股 | AAPL / NVDA / MU | SEC EDGAR、公司 IR | Polygon/FactSet/Bloomberg/Tiingo/Yahoo/Stooq；SEC XBRL 财报 | split、ADR、非 GAAP、SBC、ATM/S-3 |
| 港股 | 0700.HK / 9988.HK | HKEXnews、公司公告 | Wind/Choice/港交所/财经数据商 | 港币、流动性、配售、A/H 差异 |

详细规则见 `references/01_data_first_market_router.md`。

---

## 5. Scoring And Rating

使用 100 分综合评分，但评分必须受数据质量上限约束。

| 模块 | 权重 |
|---|---:|
| 数据质量与可核验性 | 15 |
| Serenity 瓶颈强度 | 20 |
| 客户/订单/供应链证据 | 15 |
| 财务兑现质量 | 15 |
| 估值与内在增长赔率 | 15 |
| 缠论/技术位置 | 10 |
| 风险、证伪与治理 | 10 |

评级：S=核心长线候选，A=强观察对象，B=有潜力但有缺口，C=主题型或交易型，D=剔除或反面样本。

使用 `scripts/serenity_chan_scorecard.py` 可对 JSON scorecard 计算总分和数据评级上限。

---

## 6. Output Contract

标准输出必须包含：

1. 结论先行：最值得优先研究 / 不适合追 / 仅观察。
2. 数据质量与限制。
3. 一句话 thesis。
4. 产业链位置和卡点。
5. 证据等级：事实、推断、待验证。
6. 财务兑现与估值。
7. 缠论/技术位置。
8. 催化剂和 1-4 季度验证路径。
9. 证伪条件。
10. 行动框架：观察 / 等买点 / 小仓试错 / 核心候选 / 剔除。

模板见 `references/05_output_templates.md`。

---

## 7. Anti-Hallucination Rules

- 不编当前价。
- 不编市值。
- 不编客户。
- 不编订单。
- 不把互动平台/社媒当强证据。
- 不把美股 SEC 数据误用于 A 股。
- 不把 A 股 F10 摘要当公告原文。
- 不把未复权走势用于长期技术判断。
- 不把后复权价格当实际成交价格。
- 不在关键数据失败时输出高评级。

风险与合规见 `references/06_risk_compliance_no_guess.md`。

---

## 8. Bundled Resources

- `references/01_data_first_market_router.md` — 市场识别、数据源路由、A/US/HK 数据隔离。
- `references/02_serenity_bottleneck_workflow.md` — 产业链瓶颈、新闻到财报、小市值弹性。
- `references/03_fundamental_valuation_framework.md` — 财务、贝叶斯增长、TAM-Adj-PEG、三情景估值。
- `references/04_chan_technical_framework.md` — 缠论买点、多级别、DMA/ATR 辅助健康度。
- `references/05_output_templates.md` — 单股、主题、对比、数据审计模板。
- `references/06_risk_compliance_no_guess.md` — 证据等级、评级上限、合规边界。
- `assets/scorecard_template.json` — 综合评分模板。
- `assets/evidence_ledger.schema.json` — 证据台账 schema。
- `assets/analysis_request.schema.json` — 分析请求 schema。
- `assets/prompt_pack.md` — 可复制提示词。
- `scripts/data_router.py` — 市场识别、数据校验、质量报告脚手架。
- `scripts/serenity_chan_scorecard.py` — 评分器。
- `scripts/validate_skill.py` — Skill 结构校验。
- `examples/` — A 股、美股、主题扫描输出样例。
- `evals/test_cases.md` — 行为测试。


<!-- validator keywords: A 股 评级封顶 No Data, No Guess Market-Specific Data Routing -->
