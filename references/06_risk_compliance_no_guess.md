# 06 Risk, Compliance, And No-Guess Rules

## 1. Research Boundary

本 Skill 只提供研究框架、优先级、证据链和风险边界，不提供个性化投资建议，不承诺收益，不执行交易。

输出时优先使用：

- “优先研究”；
- “观察”；
- “等待买点”；
- “小仓试错条件”；
- “降级/证伪”；
- “不适合作核心”。

避免：

- “必涨”；
- “马上梭哈”；
- “无风险”；
- “确定翻倍”；
- “内幕/未公开信息”。

---

## 2. Evidence Rules

| Evidence | Allowed Conclusion |
|---|---|
| 原始公告/filing/年报/招股书/合同 | 可支持 Strong claim |
| 公司 IR/电话会/产业会议/专利/权威媒体 | 可支持 Medium claim |
| 互动平台/F10/普通媒体/券商概念梳理 | 只能支持 Weak lead |
| KOL/社媒/截图/群聊 | 不能支持投资结论 |

强结论必须有 Strong 或多个 Medium 交叉验证。

---

## 3. Claim Labels

每个核心事实应标注：

- Confirmed fact：已被原始资料支持；
- Management claim：管理层说法；
- Third-party estimate：第三方估计；
- Analyst inference：分析推断；
- Unverified lead：待验证线索。

不要把推断写成事实。

---

## 4. Rating Caps

评级上限必须受数据质量约束，见 `01_data_first_market_router.md`。

额外规则：

- 只有送样，没有订单：最高 B；
- 只有互动平台，没有公告：最高 C；
- 客户匿名且收入影响不明：最高 B；
- 公司亏损且融资依赖严重：最高 B，除非用户明确接受 option-like；
- 市值已隐含 H5，但证据只到 H2/H3：最高 B；
- 周线技术高位逃逸：即便基本面 S，当前动作也不能是“追买”。

---

## 5. Market-Specific Risk Checks

### A 股

- 关联交易；
- 大股东质押；
- 定增/可转债；
- 商誉减值；
- 政府补助占利润比例；
- 应收/存货；
- 互动平台过度营销；
- 主题炒作后的估值压力；
- 限售股解禁；
- 退市/监管函/问询函。

### 美股

- SBC；
- ATM / S-3 shelf；
- convertibles；
- GAAP vs non-GAAP；
- customer concentration；
- insider selling；
- short interest/option squeeze；
- tariff/export controls；
- ADR/VIE risk if Chinese ADR。

### 港股

- 流动性；
- 配售/供股/CB；
- 关联交易；
- 南向持仓变化；
- A/H 估值差；
- 大股东质押；
- auditor/governance risk。

---

## 6. Technical Risk Rules

- 周线顶背驰 + 基本面无上修：不能当长线新买点。
- 高位放量滞涨：降低追涨意愿。
- 三买失败变三卖：技术降级。
- 跌破大级别中枢且反抽不过：趋势破坏。
- 低位一买仅适合试错，不能重仓。

---

## 7. Final Answer Must State Uncertainty

合格结论应包含：

```text
我确定的事实是____。
我的推断是____。
还缺的关键证据是____。
如果____发生，这个 thesis 应该降级。
```
