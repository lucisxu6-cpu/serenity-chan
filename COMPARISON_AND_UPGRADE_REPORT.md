# Serenity Skill 外部仓库对比与 v3 优化报告

## 1. 对比对象

- muxuuu/serenity-skill：单一完整 Serenity 供应链瓶颈研究 skill，带 references、assets、scripts、examples、evals。
- haskaomni/serenity-skill：多 skill 组合，包含 serenity-alpha、bayesian-intrinsic-growth-valuation、gf-dma-health-index、tam-adj-peg、buy-side-equity-research-memo。
- 我们的 v2：Serenity + 缠论 + Data-First 数据获取与证据校验。

## 2. muxuuu 仓库优点

1. **Skill 工程结构完整**：`SKILL.md + references + assets + scripts + examples + evals`，适合 Codex/Agent Skills 安装。
2. **触发描述清楚**：明确适合 supply-chain bottleneck research、A-share/HK/US screening、thesis stress tests。
3. **研究流程连贯**：从 market story 到 system change、required parts、scarce constraints、public companies、evidence、failure conditions。
4. **深度研究标准明确**：theme scan 要先排层级，再排公司；工具允许时构建 20+ 候选、25+ 来源。
5. **证据阶梯清晰**：primary/medium/weak，强调 filing、announcement、exchange document、patent、standard 等强证据。
6. **市场源 playbook 有跨市场意识**：A 股、港股、美股、台日韩欧都有不同 source path。
7. **有本地评分脚本**：`serenity_scorecard.py` 能把主观评分落到 JSON/Markdown。
8. **有 eval test cases**：可以测试是否真的触发 skill 行为。

## 3. muxuuu 仓库不足

1. **没有真正的数据层 contract**：说要用 live sources，但没有明确 provider interface、data manifest、validation、cross-check、rating cap。
2. **评分维度偏 Serenity 单一**：缺基本面速度、估值隐含增长、技术买点、数据质量 cap。
3. **技术面缺失**：没有缠论、DMA、ATR、趋势健康评分。
4. **A 股/美股源隔离不够硬**：有 source path，但没有 resolver 防止 ticker 混淆。
5. **没有“缺数据后停止/降级”的可执行机制**：容易在工具失败时继续生成完整结论。

## 4. haskaomni 仓库优点

1. **模块化非常强**：把 Serenity Alpha、Bayesian valuation、GF-DMA、TAM-Adj-PEG、Buy-side Memo 拆成独立 skill。
2. **新闻转财务能力好**：serenity-alpha 的 `news -> observed demand -> revenue/profit transmission -> small-cap elasticity -> validation path` 很适合找二阶机会。
3. **贝叶斯估值框架好**：用 H0-H5 概率更新判断未来 3-5 年增长，并与市场隐含增长比较。
4. **GF-DMA 技术健康评分可量化**：结合基本面速度、20/50/100/200DMA、ATR、预期修正，适合判断趋势是否过热。
5. **TAM-Adj-PEG 有助于避免简单 PE/PEG**：把增长持续时间和质量因子纳入估值。
6. **Buy-side memo 输出很接近机构研究流程**：先给 investment view，再做行业链、财务、估值、风险、监控看板。
7. **SEC/edgartools 用法写得具体**：对美股财报取数非常有参考价值。

## 5. haskaomni 仓库不足

1. **整体偏美股**：SEC/edgartools 很强，但 A 股官方公告、交易所问询、互动易、复权行情和财报口径没有同等深度。
2. **多个 skill 之间缺统一总控**：模块强，但没有统一总评分和统一数据质量 cap。
3. **没有 Data Manifest / Evidence Ledger**：证据与字段来源不够结构化，不利于复盘和自动化。
4. **没有市场 resolver**：跨 A 股、美股、港股使用时容易 ticker 和数据源混淆。
5. **没有缠论结构**：GF-DMA 是趋势健康，但不是中枢/级别/买点体系。
6. **没有本地数据验证脚本**：公式写得清楚，但 provider、validation、fallback 不完整。

## 6. 我们 v2 的优点

1. **Data-First 意识最强**：明确 No Data, No Guess。
2. **A 股适配更细**：公告、财报、问询、互动、招投标、环评、应收、存货、合同负债、解禁、定增等。
3. **缠论买点纪律独特**：把好公司和好买点分开。
4. **证据评级和评级上限机制明确**：缺数据就降级。
5. **已经有 Python 数据层脚手架**：比纯文本 skill 更落地。

## 7. 我们 v2 的不足

1. **结构过于单文件**：不如 muxuuu 适合 Agent Skills 分层加载。
2. **估值模块不够强**：缺 haskaomni 的 H0-H5、市场隐含增长、TAM-Adj-PEG。
3. **技术模块不够可计算**：缠论有思想，但缺 GF-DMA 这样的可量化过热/健康评分。
4. **数据层缺市场 resolver 和强隔离**：需要防止 688019 被当作 US ticker、700 被误当 A 股等。
5. **缺 examples/evals/scripts 的完整工程闭环**。
6. **评分系统还没把数据质量、贝叶斯估值、技术买点统一进总分。**

## 8. v3 取长补短改动

### 从 muxuuu 吸收

- Agent skill 标准结构：`SKILL.md / references / assets / scripts / examples / evals`。
- 深度扫描最低标准：先层级、再公司；广泛主题先构建候选池。
- Evidence ladder 与 market source playbook。
- 本地 scorecard 脚本与 eval cases。
- Plain-language output，不写成券商报告。

### 从 haskaomni 吸收

- Serenity Alpha：新闻转需求、需求转财务、小市值弹性、1-4 季度验证。
- Bayesian Intrinsic Growth：H0-H5 概率、内在增长 vs 市场隐含增长。
- GF-DMA：基本面速度 × 均线速度 × ATR 背离 × 预期上修。
- TAM-Adj-PEG：估值不只看 PE，而看增长持续时间与质量因子。
- Buy-side memo：先给 investment view，再做财务、估值、风险和监控看板。

### 我们新增强化

- A 股/美股/港股 Market Resolver。
- 数据源隔离：A 股走 CNINFO/SSE/SZSE/BSE；美股走 SEC/IR；港股走 HKEX。
- Data Manifest 和 Evidence Ledger。
- 数据校验脚本：价格、复权、财报一致性、多源价格差异。
- 数据质量评级上限：数据失败直接限制 S/A/B/C。
- 缠论 + GF-DMA 双技术模块：一个判断结构，一个判断趋势健康。

## 9. v3 使用建议

- 宽主题扫描：先用 `SKILL.md` + `serenity-workflow.md`。
- 当前价格/买点：必须调用 `serenity_chan_data_layer_v3.py` 获取或验证复权行情。
- 单股深度：用 `scorecard_schema_v3.json` 填分，再跑 `serenity_chan_scorecard_v3.py`。
- 美股：优先实现 SEC/edgartools + yfinance/Polygon provider。
- A 股：优先实现 CNINFO/SSE/SZSE/BSE 公告下载 + Tushare/AKShare/BaoStock 行情 provider。
- 任何报告最后必须包含数据质量与限制。
