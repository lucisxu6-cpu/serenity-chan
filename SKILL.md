---
name: serenity-chan-stock-skill
description: Use when performing data-first equity research for A-share, US, HK, or cross-market stock screening, single-company thesis challenges, theme scans, candidate comparisons, evidence/falsification dashboards, valuation work, or Chan/GF-DMA buy-point discipline. Always route market data and filings through market-specific sources before making current price, financial, rating, or entry claims.
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

本 Skill 的默认输出分类：核心长线候选、强观察对象、高弹性主题、已拥挤/高估对象、剔除或证伪对象。

**最高原则：No Data, No Guess, Exhaust Retrieval First。关键数据先按市场专属取数阶梯、取数账本和补数任务穷尽追索；自动链路仍不可得时，进入研究债务、行动门控和评级上限，不编造也不跳过。**

本 Skill 只提供研究框架、证据链、评级约束和风险边界，不提供个性化投资建议，不承诺收益，不执行交易。

---

## 1. Request Router

先识别用户请求类型，再选择工作流。

### 1.0 Mandatory Real Data Acquisition

任何涉及当前股价、财报、估值、市值、客户/订单、评级或买点的任务，必须先尝试真实取数，再输出可审计的数据包，最后才进入分析。

数据包至少包含：

- 解析后的市场、标准代码、交易所和货币。
- 每类关键数据的主源、结构化源、辅助源和 forbidden source。
- `data_acquisition.attempt_ledger`：每个数据集的逐源尝试记录。
- `data_acquisition.data_gaps`：数据缺口类型、决策影响、评级影响和下一步动作。
- `data_acquisition.research_debt`：影响评级或行动状态的待补证据。
- `data_acquisition.manual_retrieval_tasks`：自动取数无法完成时的补数任务。
- `valuation_inputs`：当前价格、总股本、总市值、货币、日期、股本口径和市值口径；流通股和流通市值在源可得时一并记录。
- `currency_normalization_matrix`：候选对比中必须显式处理估值市值币种与财报币种；币种不一致时先做 FX 归一，失败则保持估值门控。
- `financial_statement_unit` / `financial_unit_multiplier`：候选对比中必须显式处理财报金额单位；PE/PS 和市场隐含增长只使用绝对收入、绝对净利润和同币种市值。
- `data_consumption_audit`：进入对比报告后，审计已取数据是否被财务、估值、增长和排序矩阵正确消费。
- `readiness_matrix`：进入对比报告后，拆分 Fetch Status、Research Readiness、Action Readiness 和 Data Evidence Cap。
- `data_quality`：当前请求和完整研究所允许的评级上限。
- `ai_review`：需要 AI 介入判断的源强度、行业口径、warning 和升级条件。
- `ai_review_status_matrix`：候选对比中每个候选的 AI 研究执行状态；正式研究必须落到 `COMPLETED`、`FAILED_INSUFFICIENT_EVIDENCE`、`CONFLICT_WITH_DATA` 或 `SKIPPED_QUICK_AUDIT`，不能停在未说明原因的空白状态。
- `candidate_pool_semantic_coherence`：候选池是否同层、同主题不同层、跨主题诊断或无关诊断；只有同层候选可以输出正式 clear top candidate。
- `capital_action_quantification`：A 股定增、H 股上市、减持、回购、解禁、股权激励等资本动作的字段级量化结果。
- `research_debt_runbook`：开放研究债务的可执行补证清单，包含轴线、阻塞等级、首选来源、验证目标和预期决策影响。

同一分析任务在 A 股、美股和港股必须经过相同判断层级，但使用各自市场的主源路径。A 股不得用 SEC 替代巨潮/交易所公告，美股不得用 A 股 F10/摘要替代 SEC/IR，港股必须单独处理 HKEX、货币、股本和配售口径。

优先使用：

```bash
python scripts/run_research_analysis.py <symbol_a> <symbol_b> --out-dir <run_dir>
python scripts/data_router.py fetch <symbol>
python scripts/data_router.py plan <symbol>
python scripts/data_router.py resolve <symbol>
```

美股 SEC 官方接口建议显式提供身份，避免被 SEC 拒绝：

```bash
python scripts/data_router.py fetch NVDA --sec-user-agent "Your Name your.email@example.com"
```

`fetch` 能自动抓取可用的 L2 行情/复权历史、美股 SEC L0 filing 与 US-GAAP / IFRS XBRL 财务事实、A 股 Eastmoney/Tencent L2 行情/前复权 K 线、A 股 Tencent 股本/市值估值输入、A 股 CNINFO L0 公告元数据、A 股 CNINFO L0 官方报告 PDF 核心财务行抽取（中文和英文合并报表）、A 股金融行业专门 profile、A 股 Eastmoney F10 L3 结构化财务预检、港股 HKEX 年报/中报/月报/翌日披露报表股本抽取 + Yahoo HK 行情估值输入、港股 HKEXnews 公告和官方年报/中报 PDF 核心财务行抽取，并生成带 `raw_hash` / `pdf_hash` 的本地数据包。美股 CIK 解析先使用稳定 bootstrap，再尝试 SEC ticker 目录；财务从 SEC companyfacts 读取 10-K、10-Q、20-F、40-F 核心 facts，必要时继续取 SEC companyconcepts。若网络、证书、SEC 身份、源限流或市场不支持导致 fetch 失败，必须把相应字段标记为 `FAILED` / `PENDING` 并写入 `attempt_ledger`；若 scoped fetch 未请求某数据，标记 `NOT_REQUESTED` 并写入 `data_gaps`。这些状态都不能支持高评级；不得改用猜测值。数据 provider 受有界执行预算保护；官方 PDF 下载和解析必须在预算内完成或留下可审计失败原因。

内置真实取数边界：

- `Eastmoney_Quote_Kline_L2`：A 股 SH/SZ/BJ 当前行情、不复权/前复权 K 线，属于 L2 辅助行情源。
- `CNINFO_Tencent_Adjusted_Kline_L0L2`：A 股复权历史修复源；当免费行情只返回未确认日线时，必须读取 CNINFO 权益分派实施公告，解析除权除息日、现金分红和送转比例，并生成可审计前复权序列。
- `Tencent_Quote_Kline_L2`：A 股 SH/SZ/BJ 当前行情、股本/市值估值输入和日线历史补充源；不能把未确认日线直接当作前复权数据。
- `Yahoo_Chart_L2` / `Yahoo_Chart_Query2_L2`：US / HK 行情和历史，以及 A 股辅助交叉行情源。
- `SEC_Companyfacts_L0`：美股 SEC companyfacts 和 submissions，覆盖 US-GAAP / IFRS 核心财务 facts，属于 L0 官方源。
- `SEC_CompanyConcepts_L0`：美股 SEC companyconcepts 细粒度财务事实，覆盖 US-GAAP / IFRS 核心财务 facts，属于 L0 官方源。
- `CNINFO_Announcements_L0`：A 股巨潮公告元数据和 PDF 链接，属于 L0 官方公告源。
- `CNINFO_FinancialReports_L0`：A 股官方年报、季报 PDF 下载、文本解析和核心财务行抽取，属于 L0 官方报告源；普通经营企业必须抽取收入、净利润、经营现金流、资产、负债和权益，中文和英文版合并报表都要走同一字段合同；银行必须抽取净利息收入、净息差、存贷款、资产质量和资本充足率；证券公司必须抽取净资本、风险覆盖率、资本杠杆率、流动性覆盖率和净稳定资金率；保险公司必须抽取保险服务收入、保险合同负债、核心偿付能力和综合偿付能力。
- `Eastmoney_F10_Financials_L3`：A 股三表 L3 结构化财务预检，输出收入、归母净利、经营现金流、资产、负债、权益等核心字段；仅 F10 可用时必须保留财报验证债务，不能放开 S/A 研究评级。
- `HKEXnews_Announcements_L0`：港股 HKEXnews 公告元数据和 PDF 链接，属于 L0 官方公告源。
- `HKEXnews_FinancialReports_L0`：港股 HKEX 年报/中报官方 PDF 下载、文本解析和核心财务行抽取，属于 L0 官方报告源；若 PDF 文本不可解析或核心字段缺失，必须生成字段级缺口并限制评级。
- `HKEX_Yahoo_Valuation_L0L2`：港股估值输入源，从 HKEX 年报、中报、月报或翌日披露报表文本抽取已发行股本，并结合 Yahoo HK 当前价生成 HKD 总市值；股本证据来自 L0 官方披露，价格属于 L2 行情预检。
- Wind/Choice/Tushare 等授权结构化源未配置时，不能用错源替代；缺口类型必须精确限定为“授权结构化行项目/数据库缺口”。

AI 证据裁决必须介入：

1. `financials=OK` 表示最新期收入、净利润、经营现金流、资产、负债和权益均已抽取并通过校验；研究级证据强度由 source level、`source_integrity`、`data_gaps`、`research_debt`、`ai_review` 和 L0/L1 复核共同决定。
2. 读取 manifest 的 `attempt_ledger`、`data_gaps`、`research_debt`、`ai_review`、`source_level`、`warnings`、`validation.warnings` 和 `raw_path`，逐项解释是否可支撑评级升级。
3. A 股财务优先使用 `CNINFO_FinancialReports_L0` 官方报告 PDF 行级抽取；若只来自 `Eastmoney_F10_Financials_L3`，必须生成 `NOT_MACHINE_READABLE` 财报验证债务，最终研究评级上限为 B。
4. 银行、保险、券商等金融企业不得套普通经营企业三表逻辑；必须按行业报表口径单独判断，缺专门 profile 时最高 B。
5. 当 validation 把评级上限压低，即使数据状态为 `OK`，也必须生成机器可读的 `data_gaps` / `research_debt`：K 线窗口不足使用 `EVIDENCE_DEPTH_LIMIT`，复权口径未验证使用 `ADJUSTMENT_BASIS_UNVERIFIED`。
6. 现金流/净利 warning 不能机械扣分或忽略；AI 必须判断是中期累计/季节性、行业模式，还是利润质量问题。
7. 产业链层级、公司卡点、收入传导和反证判断通过 `ai_research_overlay` 进入对比报告；overlay 必须先通过 `scripts/validate_ai_overlay.py`。
8. AI 研究证据不足、与确定性数据冲突或用户明确要求快速审计时，输出 `ai_review_outcome` 并通过 `scripts/validate_ai_review_outcome.py`；该 outcome 必须合并进对比报告，不能退化成未执行。
9. AI overlay 可以更新产业层级、证据支持增长、反证和研究问题；`market_implied_growth` 由 `valuation_input_matrix`、PE/PS 和同币种财务口径生成。
10. 用户可读分析结果默认使用中文；机器字段可保留英文枚举，但必须用中文解释状态、限制和下一步。

### 1.1 Theme Scan / 主题扫描

触发词：产业链、机器人、国产算力、DeepSeek、AI 半导体、CPO、长鑫、昇腾、HBM、电力、美国 AI infrastructure、which stocks、最值得研究。

必须：

1. 先排产业链层级，再排公司。
2. 至少覆盖 3 个价值链层级；深度扫描尽量建立 20+ 候选公司。
3. 每个 top candidate 必须说明“卡住什么环节”。
4. 至少给出一个热门但降级的方向，并说明为什么。
5. 当前结论必须用最新数据源；没有数据时标记为 initial pass。
6. 如果直接列股票而不输出产业链地图、瓶颈层级排序和候选池，视为不合格输出。

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

执行顺序：

1. 首选运行 `python scripts/run_research_analysis.py <symbol...> --out-dir <run_dir>`。它会真实取数、生成确定性 baseline，并为每个候选生成 `ai_review_packet.json`、`ai_committee_packet.json` 和 `ai_overlay_prompt.json`。
2. 若输出 `AI_RESULT_REQUIRED`，AI 必须读取对应候选的 prompt、review packet、committee packet 和源文件后，为每个候选输出一个正式 AI 结果：证据足够时输出 `assets/ai_research_overlay.schema.json` 允许字段；证据不足、数据冲突或快速审计时输出 `assets/ai_review_outcome.schema.json` 允许字段。
3. overlay 单独校验时运行 `python scripts/validate_ai_overlay.py <overlay.json> --manifest <manifest.json>`；outcome 单独校验时运行 `python scripts/validate_ai_review_outcome.py <outcome.json>`。
4. 每个候选都有 overlay/outcome 后，继续运行同一个 `run_research_analysis.py` 命令并传入 `--overlay SYMBOL=<overlay.json>` 或 `--ai-outcome SYMBOL=<outcome.json>`，生成最终 JSON/Markdown。
5. 需要手工拆分时，才使用低层命令：`data_router.py fetch`、`build_comparison_report.py`、`build_ai_overlay_prompt.py`、`build_ai_review_packet.py`、`build_ai_committee_packet.py`、`validate_and_merge_ai_overlay.py`、`render_research_report.py`。
6. `validate_and_merge_ai_overlay.py` 会强制要求每个候选都有一个正式 AI 结果，并会把 overlay 的 `source_ref` 解析到对应 manifest 的真实 source artifact 或 AI review packet；无法解析的引用、与证据不一致的数字 claim 会阻断合并。
7. 用 `python scripts/render_research_report.py --comparison-report comparison_report.json --mode candidate_comparison` 输出候选对比交付稿；需要逐候选研究工作台时使用 `--mode full_research`。
8. 同一正式评级上限下，仍必须输出候选优先级差异、研究债务差异、资本动作量化差异、技术动作差异和候选池语义一致性；只有同层候选可进入正式 clear top candidate，同主题不同层、跨主题或无关诊断只能作为研究优先级或诊断集合。
9. 若当前价和股本可用，先在 `valuation_input_matrix` 暴露价格、股本、市值、货币、日期、来源、口径和 `valuation_stage`，再由 `growth_hypothesis_matrix` 引用该估值输入并输出预检级 PE/PS 和 H 档；缺少估值输入时保持 `UNKNOWN`，并生成估值补证任务。
10. 财报行若以 million、thousand、万元等单位披露，必须先归一到绝对金额，再计算 PE/PS 和市场隐含增长。
11. 若 `data_consumption_audit` 出现 `MISMATCH`，不得输出正式 top candidate；先修复数据消费链路。

### 1.4 Data Audit / 数据核验

当用户要求“确认数据”“别猜”“自动分析”时，直接启用 Data-First 模式。使用 `references/01_data_first_market_router.md` 和 `scripts/data_router.py`。
候选对比、完整研究和正式报告的 AI 阶段使用 `references/15_ai_overlay_execution_protocol.md`。

---

## 2. Mandatory Data-First Preflight

任何涉及当前股价、历史走势、估值、财报、订单、客户关系、公告、买点、评级的任务，必须先做数据预检。

输出中必须包含：

```markdown
## 数据质量与限制
- 市场与代码解析：OK / PARTIAL / FAILED
- 当前价格：OK / PARTIAL / STALE / FAILED / PENDING / NOT_REQUESTED
- 历史复权行情：OK / PARTIAL / STALE / FAILED / PENDING / NOT_REQUESTED
- 股本/市值/估值输入：OK / PARTIAL / STALE / FAILED / PENDING / NOT_REQUESTED
- 财报数据：OK / PARTIAL / STALE / FAILED / PENDING / NOT_APPLICABLE / NOT_REQUESTED
- 公告/filing：OK / PARTIAL / STALE / FAILED / PENDING / NOT_APPLICABLE / NOT_REQUESTED
- 供应链证据：OK / PARTIAL / STALE / FAILED / PENDING / NOT_REQUESTED
- 取数账本：attempt_ledger 路径或摘要
- 数据缺口：data_gaps 摘要
- 研究债务：research_debt 摘要
- 补数任务：manual_retrieval_tasks 摘要
- 无法验证字段：____
- 因数据限制，本报告评级上限：S/A/B/C/D/OBSERVE_ONLY
```

硬规则：

- 当前价格失败：不能给“当前买点”；评级最高 B。
- 复权历史行情失败：不能输出缠论买点；技术评级最高 C。
- 估值输入缺少当前价格、总股本、总市值、货币、日期或来源口径：不能输出市场隐含增长、估值赔率或核心行动；行动门控为 `VALUATION_GATED`，`primary_gate_class=DATA_ACQUISITION`。
- 估值市值币种与财报币种不一致：必须进入 `currency_normalization_matrix`；FX 归一成功后才允许计算预检 PE/PS 和市场隐含增长，失败时保持 `VALUATION_GATED`。
- 财报金额单位为 million、thousand、万元等非绝对金额：必须记录 `financial_statement_unit` 和 `financial_unit_multiplier`；增长矩阵必须使用绝对收入、绝对净利润和同币种市值计算 PE/PS。
- 财报、公告或 filing 已取得但 source level、机器可读性、口径复核不足：行动门控为 `EVIDENCE_GATED`，`primary_gate_class=EVIDENCE_VALIDATION`。
- 市场隐含增长达到 H4/H5 且证据支持不足：保留市场隐含增长判断，行动门控为 `VALUATION_GATED`，`primary_gate_class=RESEARCH_VALIDATION`。
- PE/PS 默认是 `preflight` 估值；只有经过同币种、口径、财务和股本复核后，才能升级为 `verified_l0` / `verified_l1` / `deep_valuation`。
- 最新财报失败：不能给 S/A 长线结论。
- 原始公告/filing 失败：客户、订单、产能只能算线索，不能算强证据。
- `NOT_APPLICABLE` / `NOT_REQUESTED` 不能绕过关键数据门禁；正式评级任务中视为不可用并触发评级封顶。
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

判断重点：已发生需求变化、收入/利润传导路径、供应商稀缺度、认证周期、产能扩张难度、单位经济和毛利率、市值相对需求冲击的弹性、明确证伪点。

参考 `references/02_serenity_bottleneck_workflow.md`。

### 3.2 Fundamental + Valuation Lens: 财务兑现与内在增长

Serenity 找到的是候选，基本面决定能否长线。

必须检查：收入增速和核心业务占比、毛利率与定价权、净利率与经营杠杆、经营现金流/应收/存货/合同负债、capex/在建工程/折旧/融资稀释、客户集中与议价权、市场隐含增长 vs 真实内在增长、Bull/Base/Bear 三情景估值。

H4/H5 高增长只能由原始披露、客户/订单、产能、财报兑现或多源强交叉验证支持。主题热度、FOMO、KOL、概念梳理只能提高“市场预期/估值风险”的判断，不能直接提高内在增长假设。

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
| A 股 | 688019.SH / 300750.SZ / 920593.BJ | 巨潮、上交所、深交所、北交所 | CNINFO 官方报告 PDF 行级财务抽取 → 银行等金融行业 profile → Wind/Choice/CSMAR/Tushare → Eastmoney/Tencent/AKShare/BaoStock 行情与估值输入 → Eastmoney F10 L3 结构化财务预检 | 复权口径、F10 错误、互动平台弱证据、金融企业口径错配 |
| 美股 | AAPL / NVDA / MU | SEC EDGAR、公司 IR | Polygon/FactSet/Bloomberg/Tiingo/Yahoo/Stooq；SEC XBRL 财报 | split、ADR、非 GAAP、SBC、ATM/S-3 |
| 港股 | 0700.HK / 9988.HK | HKEXnews、公司公告 | HKEXnews 年报/中报 PDF → HKEX 年报/中报/月报/翌日披露报表股本抽取 + Yahoo HK 行情估值输入 → Wind/Choice/港交所/财经数据商 | 港币、流动性、配售、A/H 差异 |

详细规则见 `references/01_data_first_market_router.md`。

---

## 5. Scoring And Rating

使用候选决策矩阵，判断标的是核心候选、强观察、候选池、数据门控、线索跟踪，还是应该剔除。高分必须同时满足数据可用、主源证据、AI 证据裁决、估值赔率、技术位置和证伪路径，并受研究债务约束。

| 维度 | 作用 |
|---|---|
| Thesis Quality | 产业链层级、公司瓶颈、财务兑现、风险控制 |
| Evidence Confidence | 主源覆盖、财报验证、声明可追溯性、交叉验证、时效 |
| Market Payoff | 估值折价、隐含增长与证据匹配、上下行赔率 |
| Technical Timing | 月/周/日结构、Chan 买点、GF-DMA、回撤位置 |
| Action Readiness | 当前价、复权历史、技术结构、数据债务、风险控制 |
| Candidate Priority | 候选优先级分数和 watchlist bucket |

若财务只来自 L3/F10 结构化预检、金融企业缺专门 profile、关键数据不可用、弱证据或 H4/H5 估值缺口存在，必须进入 `data_gaps` / `research_debt` / `blockers`。关键债务会压低证据评级、候选优先级和行动状态；高模块分数不能覆盖这些门控。

评级：S=核心长线候选，A=强观察对象，B=有潜力但有缺口，C=主题型或交易型，D=剔除或反面样本。

使用 `scripts/serenity_chan_scorecard.py` 可对 JSON scorecard 计算研究评级、证据评级、行动状态和候选优先级。使用 `scripts/candidate_ranker.py` 可对多个候选做相对排序。

候选对比报告还必须区分：

- 正式评级上限：由证据强度、数据缺口、研究债务和市场源完整度控制。
- 候选优先级：用于决定先研究谁，允许在相同评级上限下表达财务质量、资本动作、技术健康和 AI 层级判断的差距。
- 行动状态：关键研究债务未清时保持 `DATA_GATED` / `RESEARCH_GATED` / `WAIT_FOR_BUY_POINT` / `LEAD_TRACKING`。
- 行动门控：`action_gate.primary_gate` 必须精确写明 `DATA_GATED`、`EVIDENCE_GATED`、`VALUATION_GATED`、`AI_REVIEW_GATED`、`BUY_POINT_GATED` 或 `CAPITAL_ACTION_GATED`；`primary_gate_class` 必须区分 `DATA_ACQUISITION`、`EVIDENCE_VALIDATION`、`RESEARCH_VALIDATION`、`ACTION_TIMING`。

---

## 6. Output Contract

标准输出必须包含：

1. 结论先行：最值得优先研究 / 不适合追 / 仅观察。
2. 数据质量、取数账本、数据缺口和研究债务。
3. 决策矩阵：thesis quality、evidence confidence、market payoff、action readiness、candidate priority。
4. 一句话 thesis。
5. 产业链位置和卡点。
6. 证据等级：事实、推断、待验证。
7. 财务兑现与估值。
8. 缠论/技术位置。
9. 催化剂和 1-4 季度验证路径。
10. 证伪条件。
11. 行动框架：观察 / 等买点 / 小仓试错 / 核心候选 / 剔除。

模板见 `references/05_output_templates.md`。

候选对比必须包含 `comparison_output_contract` 的顶层结构块：

1. comparison_scope。
2. candidates。
3. data_acquisition_summary。
4. candidate_pool_semantic_coherence。
5. serenity_layer_matrix。
6. ai_review_status_matrix。
7. financial_quality_matrix。
8. valuation_input_matrix。
9. currency_normalization_matrix。
10. growth_hypothesis_matrix。
11. technical_timing_matrix。
12. capital_actions。
13. capital_action_quantification。
14. data_consumption_audit。
15. readiness_matrix。
16. research_debt。
17. research_debt_runbook。
18. candidate_priority_ranking；ranking 行必须包含 `research_priority_score`、`action_priority_score`、`decision_grade` 和带 `primary_gate_class` 的 `action_gate`。
19. final_decision；必须包含 `decision_mode`、`score_gap_to_runner_up`、`ranking_validity`、`candidate_pool_semantic_coherence` 和候选池数量提示。

`ranking_validity=INVALID` 时，只能输出工程诊断和补数/修复任务；用户可读报告必须显示“工程诊断排序｜非投资候选排序”，不得命名正式 top candidate，ranking 行的 `decision_grade=false`。`ranking_validity=PARTIAL` 时，可以输出研究优先级，但不能输出 `clear_top_candidate`。
`data_consumption_audit` 中 `MISMATCH` 触发 `INVALID`；`PARTIAL` / `DATA_GATED` 数据消费或 high/critical `research_debt` 触发 `PARTIAL`。

标准单股、主题扫描和数据审计使用 `output_contract` 门禁：

```bash
python scripts/validate_output_contract.py <report.md>
python scripts/validate_output_contract_json.py <contract.json>
```

候选对比使用 `comparison_output_contract` 门禁：

```bash
python scripts/validate_comparison_report.py <comparison_report.json>
python scripts/render_research_report.py --comparison-report <comparison_report.json> --mode full_research
```

门禁失败时必须修正报告；在无法取得外部数据时，报告必须降级到门禁允许的评级和动作后再交付。

长期跟踪、候选池或高估值争议对象必须把证伪条件落到 falsification dashboard，优先使用 `scripts/build_falsification_dashboard.py` 验证。

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
- `references/15_ai_overlay_execution_protocol.md` — AI 研究 overlay/outcome 执行闭环。
- `assets/scorecard_template.json` — 综合评分模板。
- `assets/scorecard.schema.json` — 评分输入 schema。
- `assets/data_acquisition_policy.json` — 取数阶梯、数据集重要性和缺口类型。
- `assets/fetch_attempt_ledger.schema.json` — 取数账本 schema。
- `assets/data_gaps.schema.json` — 数据缺口 schema。
- `assets/manual_retrieval_tasks.schema.json` — 补数任务 schema。
- `assets/valuation_inputs.schema.json` — 当前价格、total shares、total market cap 和估值口径 schema。
- `assets/ai_research_overlay.schema.json` — AI 研究覆盖层 schema。
- `assets/ai_review_outcome.schema.json` — AI 研究失败、冲突或快速审计 outcome schema。
- `assets/capital_action_quantification.schema.json` — 资本动作字段级量化 schema。
- `assets/data_consumption_audit.schema.json` — 已取数据在对比矩阵中的消费审计 schema。
- `assets/research_debt_runbook.schema.json` — 可执行研究债务 runbook schema。
- `assets/report_mode.schema.json` — 正式报告输出模式 schema。
- `assets/evidence_ledger.schema.json` — 证据台账 schema。
- `assets/falsification_dashboard.schema.json` — 证伪看板 schema。
- `assets/technical_health.schema.json` — 技术健康矩阵 schema。
- `assets/capital_actions.schema.json` — A 股资本动作 schema。
- `assets/comparison_output_contract.schema.json` — 候选对比输出合同 schema。
- `assets/analysis_request.schema.json` — 分析请求 schema。
- `assets/output_contract.schema.json` — 标准输出合同 schema。
- `assets/prompt_pack.md` — 可复制提示词。
- `scripts/data_contracts.py` — 统一市场、数据状态、缺口类型、评级上限和取数记录合同。
- `scripts/data_layer.py` — 市场路由和真实数据 provider 底层模块。
- `scripts/market_source_policy.py` — Markdown/JSON 共享的市场源隔离规则。
- `scripts/data_router.py` — 市场识别、真实数据预检、数据校验和质量报告脚手架。
- `scripts/run_research_analysis.py` — 顶层正式研究流程，串联真实取数、baseline、AI 研究包、证据校验合并和最终报告。
- `scripts/build_falsification_dashboard.py` — 证伪看板验证和渲染脚本。
- `scripts/technical_health.py` — 从复权日线计算技术健康、短均线距离和缠论动作纪律。
- `scripts/a_share_capital_actions.py` — 从 A 股公告元数据识别定增、减持、解禁、回购、股权激励等资本动作。
- `scripts/a_share_capital_action_quantifier.py` — 将资本动作拆成新增股份、发行价、锁定期、回购金额、减持比例等量化字段和补证任务。
- `scripts/build_comparison_report.py` — 从多个 manifest 汇总对比决策报告。
- `scripts/build_ai_review_packet.py` — 从 manifest 生成 AI 审阅包。
- `scripts/build_ai_committee_packet.py` — 从 manifest 生成多角色 AI 研究委员会包。
- `scripts/build_ai_overlay_prompt.py` — 从 manifest 生成 AI overlay/outcome 的可执行 prompt package。
- `scripts/build_research_debt_runbook.py` — 将开放研究债务转成可执行 runbook。
- `scripts/data_consumption.py` — 审计取数结果是否被研究矩阵正确消费。
- `scripts/financial_periods.py` — 标准化不同市场和财年口径的财报周期。
- `scripts/render_research_report.py` — 将 comparison JSON 或 manifests 渲染为完整 Markdown 报告。
- `scripts/validate_comparison_report.py` — 校验候选对比 JSON 输出合同。
- `scripts/validate_ai_overlay.py` — 校验 AI 研究覆盖层。
- `scripts/validate_ai_review_outcome.py` — 校验 AI 研究失败、冲突或快速审计 outcome。
- `scripts/validate_and_merge_ai_overlay.py` — 校验 overlay/outcome、合并候选对比并复验输出合同。
- `scripts/merge_ai_research_overlay.py` — 带 overlay/outcome 校验的安全合并 CLI。
- `scripts/serenity_chan_scorecard.py` — 候选决策矩阵评分器。
- `scripts/candidate_ranker.py` — 多候选优先级排序器。
- `scripts/validate_output_contract.py` — 标准 Markdown 输出门禁，检查数据质量、评级上限、证据、证伪和禁用措辞。
- `scripts/validate_output_contract_json.py` — 标准结构化 JSON 输出合同门禁。
- `scripts/run_static_evals.py` — 本地静态 eval runner。
- `scripts/run_real_data_smoke.py` — 可选联网真实数据 smoke runner，覆盖 NVDA、本体上游链路、A 股行情/财务/公告和港股当前数据。
- `scripts/validate_skill.py` — Skill 结构校验。
- `examples/` — A 股、美股、主题扫描输出样例。
- `evals/test_cases.md` — 行为测试。
- `evals/static_cases.json` — 可运行静态 eval 用例。


<!-- validator keywords: A 股 评级封顶 No Data, No Guess Market-Specific Data Routing -->
