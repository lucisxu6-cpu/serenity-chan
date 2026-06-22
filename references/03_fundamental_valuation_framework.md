# 03 Fundamental And Valuation Framework

## 1. Purpose

Serenity 负责找到“可能被市场低估的瓶颈”，但长线高胜率还需要回答：

```text
这个瓶颈能不能持续进入收入？
收入能不能变成利润？
利润能不能变成现金流？
当前市值隐含了什么未来？
```

---

## 2. Financial Statement Deep Dive

### 2.1 Revenue Quality

检查：

- 总收入增速；
- 核心业务收入增速；
- 新业务收入占比；
- segment disclosure 是否足够清楚；
- 收入增长来自量、价、mix 还是并表；
- 是否低毛利贸易/集成拉高收入。

红旗：

- 收入增长但核心 segment 不披露；
- 收入增长主要来自关联交易；
- 收入增长但毛利率明显下降；
- 收入增长但经营现金流恶化。

### 2.2 Margin And Pricing Power

瓶颈应体现为：

- 毛利率稳定或上升；
- 高端产品 mix 提升；
- 利用率提高；
- ASP 稳定或上涨；
- 客户接受更高价格以保障供应。

若公司宣称稀缺但毛利率持续下滑，必须降级。

### 2.3 Cash Flow And Working Capital

重点看：

```text
OCF / net income
receivables growth vs revenue growth
inventory growth vs orders / cycle
contract liabilities / backlog
capex / construction in progress
```

强状态：收入增长 + OCF 改善 + 存货与订单匹配。

弱状态：收入增长 + 应收暴涨 + 存货暴涨 + OCF 为负。

### 2.4 Balance Sheet And Dilution

美股特别看：

- SBC；
- S-3 shelf；
- ATM；
- converts；
- debt maturity；
- share count dilution。

A 股特别看：

- 定增；
- 可转债；
- 股权质押；
- 商誉；
- 关联交易；
- 政府补助占比；
- 募投项目投产和转固。

---

## 3. Bayesian Intrinsic Growth Lens

不要简单说利好/利空。把信息更新为未来 3-5 年收入 CAGR 的概率。

| 假设 | 标签 | 3-5Y revenue CAGR |
|---|---|---:|
| H0 | 收缩 | <0% |
| H1 | 成熟低速 | 0-5% |
| H2 | 稳定成长 | 5-12% |
| H3 | 高景气成长 | 12-25% |
| H4 | 结构性爆发 | 25-50% |
| H5 | 平台级扩张 | >50% |

工作流：

1. 根据历史增长、行业周期、TAM、竞争位置建立 prior。
2. 把新信息分到变量：收入、margin、TAM、市占率、现金流、估值倍数、FOMO。
3. 判断新信息在 H0-H5 下的相容性。
4. 更新 posterior。
5. 计算加权内在增长。
6. 对比当前市值隐含增长。

硬规则：

- 情绪热度只能更新 FOMO 和估值倍数风险，不能直接更新内在增长。
- 单季度订单不能机械外推 5 年。
- 周期品要分开结构性需求与周期价格。

---

## 4. TAM-Adjusted PEG Lens

适用于成长股、有利润或可估正常化利润的公司。

公式：

```text
TAM-Adj-PEG = Forward PE / (EPS CAGR × TAM Runway Factor × Quality Factor)
```

### 4.1 TAM Runway Factor

| 高速增长持续时间 | Factor |
|---:|---:|
| 2 年 | 0.6 |
| 3 年 | 0.75 |
| 5 年 | 1.0 |
| 8 年 | 1.25 |
| 10 年 | 1.4 |
| 15 年 | 1.7 |
| 20+ 年 | 2.0 cap |

### 4.2 Quality Factor

| 类型 | Factor |
|---|---:|
| 未验证、亏损、稀释风险高 | 0.3-0.5 |
| 周期、客户集中、执行风险高 | 0.5-0.7 |
| 高成长但竞争激烈 | 0.7-0.9 |
| 正常高质量成长 | 0.9-1.1 |
| 强护城河、定价权、客户粘性 | 1.1-1.3 |
| 近似垄断或平台型 | 1.3-1.5 |

问题：

1. TAM 增长是否真的流到这家公司？
2. 公司是否有定价权？
3. 客户集中是护城河还是压价风险？
4. 技术迭代是否要求反复认证？
5. 毛利率和 EBIT margin 是否可持续？
6. 增长是否依赖重 capex？
7. 竞争者能否快速第二供？
8. 是否依赖融资和股本稀释？
9. AI/技术变化是否压缩利润池？

---

## 5. Scenario Valuation

### 5.1 Bull/Base/Bear

每个公司至少做三情景：

| 情景 | 概率 | 收入假设 | 毛利率 | 净利率/FCF | 估值倍数 | 合理市值 | 触发条件 |
|---|---:|---|---|---|---|---|---|
| Bear | | | | | | | |
| Base | | | | | | | |
| Bull | | | | | | | |

### 5.2 Reverse-Engineer Market Implied Growth

问：

```text
当前市值要合理，需要未来几年达到什么收入/利润？
这个路径与产能、客户、TAM、margin 是否匹配？
```

如果当前市值已隐含 H4/H5，而证据只有送样/概念，则高风险。

---

## 6. Position-Type Classification

| 类型 | 特征 | 仓位框架 |
|---|---|---|
| Core compounder | 高质量、长 runway、估值合理 | 可作为核心候选，等买点 |
| High-beta growth | 高成长、高估值、高波动 | 中仓或交易型，严控买点 |
| Turnaround | 当前差但成功情景改善大 | 小仓、里程碑驱动 |
| Option-like | 亏损/早期/大 TAM | 小仓或观察，接受二元风险 |
| Cyclical | 低 PE 但周期强 | 交易供需周期，不线性外推 |
| Hype-only | 证据弱、股价强 | 观察或剔除 |

---

## 7. Falsification Metrics

每季度必须复核：

- revenue growth；
- core segment mix；
- gross margin；
- OCF/net income；
- receivables/revenue；
- inventory/revenue；
- contract liabilities/backlog；
- capex progress；
- share count；
- customer disclosure；
- guidance and revisions；
- valuation percentile。
