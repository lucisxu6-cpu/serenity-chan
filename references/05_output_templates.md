# 05 Output Templates

## 1. Single Company Memo

```markdown
# [公司 / 代码] Serenity + 缠论长线研究

## 0. 结论先行
- 评级：S/A/B/C/D
- 类型：核心长线候选 / 强观察 / 高弹性主题 / 已拥挤 / 剔除
- 当前动作：等待买点 / 小仓试错 / 继续持有 / 降级 / 不参与
- 一句话：____

## 1. 数据质量与限制
- 市场与代码解析：OK / PARTIAL / FAILED
- 标准代码/市场/货币：____
- 主披露源：____
- forbidden source：____
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
- 因数据限制，本报告评级上限：____

`NOT_APPLICABLE` 和 `NOT_REQUESTED` 只说明当前 scope 或适用性，不能支持高评级；正式评级任务中仍按关键数据不可用处理。
A 股财报优先使用 `CNINFO_FinancialReports_L0` 官方报告 PDF 行级抽取，中文和英文版合并报表都按同一字段合同消费。普通经营企业需说明收入、利润、经营现金流、资产、负债、权益的抽取状态；银行、证券、保险等金融企业还需说明专门 profile 是否覆盖行业核心指标。若财报只来自 `Eastmoney_F10_Financials_L3`，只能作为 L3 结构化预检，必须保留财报验证债务，最终研究评级上限为 `B`。
估值输入必须说明当前价格、总股本、总市值、货币、日期、股本口径和市值口径；流通股和流通市值在源可得时记录。缺少估值输入时，市场隐含增长、估值赔率和核心行动保持门控。
必须读取 fetch manifest 的 `attempt_ledger`、`data_gaps`、`research_debt`、`manual_retrieval_tasks` 和 `ai_review`，并说明是否存在金融行业特殊口径、现金流质量 warning、行情/公告缺口，以及升级到 A/S 需要补哪类 L0/L1 证据。
如果数据状态为 `OK` 但 validation 已限制评级上限，必须说明对应的结构化缺口；K 线窗口或证据窗口不足使用 `EVIDENCE_DEPTH_LIMIT`，复权口径未知或未验证使用 `ADJUSTMENT_BASIS_UNVERIFIED`。

## 1.1 决策矩阵
| 维度 | 分数/状态 | 说明 |
|---|---:|---|
| Thesis Quality | /100 | |
| Evidence Confidence | /100 | |
| Market Payoff | /100 | |
| Technical Timing | /100 | |
| Action Readiness | CORE_CANDIDATE / STRONG_OBSERVE / CANDIDATE_POOL / WAIT_FOR_BUY_POINT / DATA_GATED / RESEARCH_GATED / LEAD_TRACKING / ELIMINATE / OBSERVE_ONLY | |
| Action Gate | DATA_GATED / EVIDENCE_GATED / VALUATION_GATED / AI_REVIEW_GATED / BUY_POINT_GATED / CAPITAL_ACTION_GATED / NONE | |
| Action Gate Class | DATA_ACQUISITION / EVIDENCE_VALIDATION / RESEARCH_VALIDATION / ACTION_TIMING / NONE | |
| Candidate Priority | /100 | |
| Watchlist Bucket | ____ | |

## 2. 一句话 Thesis
这家公司控制/接近的瓶颈是：____。
它受益的下游趋势是：____。
当前市场最大分歧是：____。

## 3. 产业链位置
```text
终端需求 → 系统/OEM → 模块 → 零部件/材料/设备 → 公司产品
```

- 下游客户：____
- 直接竞争者：____
- 替代方案：____
- 是否真瓶颈：强 / 中 / 弱 / 非瓶颈

## 4. Serenity 瓶颈判断
| 维度 | 评分 | 说明 |
|---|---:|---|
| 需求真实性 | /10 | |
| 供应商集中 | /10 | |
| 认证周期 | /10 | |
| 产能扩张难度 | /10 | |
| 客户质量 | /10 | |
| 定价权 | /10 | |
| 小市值弹性 | /10 | |
| 市场误分类 | /10 | |

## 5. 证据台账
| Claim | Source | Level | Supports | Missing Proof | Confidence |
|---|---|---|---|---|---|
| | | Strong/Medium/Weak | | | |

## 6. 财务兑现
- 收入：____
- 核心业务占比：____
- 毛利率：____
- 净利率：____
- OCF / Net income：____
- 应收/收入：____
- 存货/收入：____
- 负债与稀释：____
- 结论：优 / 良 / 一般 / 差

## 7. 估值与内在增长
- 当前市值：____
- 市场隐含增长：____
- Bayesian H0-H5 posterior：____
- H4/H5 证据门槛是否满足：是 / 否 / 不适用；理由____
- Bull/Base/Bear 合理市值：____
- 当前赔率：高 / 中 / 低 / 数据不足

## 8. 技术位置 / 缠论
- 月线：____
- 周线：____
- 日线：____
- 30m/60m：____
- 当前买点：一买 / 二买 / 三买 / 无买点 / 数据不足
- 等待条件：____
- 技术证伪：____

## 9. 催化剂和验证路径
| 时间 | 事件 | 要看什么 | 影响 |
|---|---|---|---|
| 1Q | | | |
| 2Q | | | |
| 4Q | | | |

## 10. 证伪条件
- 基本面证伪：____
- 财务证伪：____
- 估值证伪：____
- 技术证伪：____

## 11. 最终动作
观察 / 等待二买 / 等待三买 / 小仓试错 / 核心候选 / 剔除。
```

---

## 2. Theme Scan Template

```markdown
# [主题] 产业链瓶颈扫描

## 0. 结论先行
我会先看这几层：____、____、____。
最值得优先研究的公司是：____。
暂时降级的热门方向是：____，因为____。

## 1. 数据质量与限制
- 扫描范围：____
- 市场：A 股 / 美股 / 港股 / 全球
- 市场源路径：____
- forbidden source：____
- Source count：____
- 评级上限：____

## 2. 需求变化
- 已发生需求：____
- 仍是叙事：____
- 需求会进入的财务项目：____

## 3. 产业链地图
| 层级 | 代表公司 | 稀缺程度 | 证据 |
|---|---|---|---|
| 终端/OEM | | | |
| 模块/系统 | | | |
| 核心部件 | | | |
| 设备/材料 | | | |
| 基础设施 | | | |

## 4. 瓶颈层级排序
1. ____：原因____。
2. ____：原因____。
3. ____：原因____。

## 5. 候选公司池
| 公司 | 市场 | 层级 | 是否瓶颈 | 证据等级 | 主要风险 |
|---|---|---|---|---|---|

## 6. Top Candidates
对每家公司按单股模板简写。
每个 top candidate 必须回答：

- 它卡住哪一层；
- 证据是 Strong / Medium / Weak；
- 财务报表会在哪一项体现；
- 当前市场是否已隐含 H4/H5；
- 缺什么证据会导致降级。

## 7. 下一步验证
- 查____公告；
- 查____财报字段；
- 查____客户交叉验证；
- 等____事件。
```

---

## 3. Candidate Comparison Template

```markdown
# 候选公司对比

## 0. 结论
排序：1) ____ 2) ____ 3) ____。
优先研究：____。
决策模式：clear_top_candidate / tentative_top_candidate / candidate_cluster / comparison_not_decision_grade。
排序可信度：VALID / PARTIAL / INVALID；理由____。
与第二名分差：____。
候选池数量提示：____。
候选池一致性：SAME_LAYER / SAME_THEME_DIFFERENT_LAYERS / CROSS_THEME_DIAGNOSTIC / UNRELATED_DIAGNOSTIC / UNREVIEWED；约束____。
当前动作：____。

## 1. 数据质量与取数追索
| 公司 | 当前价 | 财报 | 公告 | 技术数据 | Attempt Count | Research Debt | 评级上限 |
|---|---|---|---|---|---:|---:|---|

## 2. 产业链层级矩阵
| 公司 | 层级 | 卡点原因 | Layer Score | Company Fit | Revenue Transmission | Evidence Gap |
|---|---|---|---:|---:|---|---|

## 3. AI 研究状态
| 公司 | AI 状态 | Overlay 已合并 | 产业层级已映射 | 证据数 | 问题数 | 阻断说明 |
|---|---|---|---|---:|---:|---|

正式候选对比必须为每个候选生成 `ai_research_overlay` 或 `ai_review_outcome`，并通过 `validate_and_merge_ai_overlay.py` 合并。`NOT_RUN` 只用于 AI 合并前的 deterministic baseline 或诊断输出。

## 4. 财务质量矩阵
| 公司 | 收入增速 | 净利增速 | 毛利率 | 净利率 | OCF/净利 | 应收/收入 | 存货/收入 | 负债率 | 研发/收入 | 结论 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|

## 5. 估值输入矩阵
| 公司 | 状态 | Stage | 当前价 | 总股本 | 总市值 | 货币 | 来源 | 股本口径 | 市值口径 | 验证需求 |
|---|---|---|---:|---:|---:|---|---|---|---|---|

## 6. 市场隐含增长 vs 证据支持增长
| 公司 | 估值输入引用 | 市场隐含增长 | 证据支持增长 | Gap | H4/H5 证据门槛 | 下一步证据 |
|---|---|---|---|---|---|---|

## 7. 技术健康与缠论动作
| 公司 | Trend State | Chan Action | 是否允许买点结论 | 等待条件 |
|---|---|---|---|---|

## 8. 数据消费审计
| 公司 | Dataset | Raw Status | Rows | Consumption | Selected | Rule | Warnings |
|---|---|---|---:|---|---|---|---|

若任何行是 `MISMATCH`，`ranking_validity` 必须为 `INVALID`，不能输出正式 top candidate。
若任何行是 `PARTIAL` 或 `DATA_GATED`，或存在 high/critical `research_debt`，`ranking_validity` 必须为 `PARTIAL`，不能输出 `clear_top_candidate`。
若 `candidate_pool_semantic_coherence` 不是 `SAME_LAYER`，只能输出研究优先级或诊断集合，不能输出正式 `clear_top_candidate`。

## 9. 资本动作与稀释风险
| 公司 | 动作类型 | 风险级别 | 影响 | Research Debt |
|---|---|---|---|---|

## 10. 资本动作量化
| 公司 | 状态 | 需量化动作数 | 最大摊薄 | 行动影响 | 字段级缺口 |
|---|---|---:|---:|---|---|

## 11. 研究债务 Runbook
| 公司 | Dataset | Axis | Blocking Level | Next Action | Validation Target | Expected Effect |
|---|---|---|---|---|---|---|

## 12. 候选优先级
| Rank | 公司 | Research Score | Action Score | Priority Score | Rating Cap | Action Gate | Action Readiness | 主要原因 |
|---:|---|---:|---:|---:|---|---|---|---|

## 13. 行动门控
| 公司 | Primary Gate | Primary Class | Secondary Gates | Gate Classes | Blocking Datasets | Reasons |
|---|---|---|---|---|---|---|

## 11. 下一步补证据
- 公司 A：____
- 公司 B：____
```

---

## 4. Data Audit Template

```markdown
# 数据审计报告

## 1. Market Resolution
- Input：____
- Normalized：____
- Market：____
- Exchange：____
- Currency：____
- Possible ambiguity：____

## 2. Data Manifest
| Dataset | Status | Source | Level | As-of | Warnings |
|---|---|---|---|---|---|

## 3. Data Gaps And Research Debt
| Dataset | Gap Type | Decision Impact | Rating Impact | Next Action |
|---|---|---|---|---|

## 4. Manual Retrieval Tasks
| Dataset | Priority | Target Source | Objective |
|---|---|---|---|

## 5. Validation Errors
- ____

## 6. Rating Cap
由于____，评级上限为____。
```
