# 01 Data-First Market Router

## 1. Purpose

任何 AI 投研在没有可靠数据时都容易“看起来很完整、实际上在猜”。本文件定义市场识别、数据源隔离、字段规范、校验规则和失败降级机制。

核心规则：

```text
先识别市场，再取对应市场的数据。
先取原始披露，再用摘要/媒体/数据库辅助。
先校验字段，再分析。
关键数据失败，评级必须降级。
```

---

## 2. Market Identification

### 2.1 A 股

常见代码：

| 类型 | 例子 | 交易所 | 标准内部格式 |
|---|---|---|---|
| 沪市主板 | 600000 / 601xxx / 603xxx / 605xxx | SSE | 600000.SH |
| 科创板 | 688019 | SSE STAR | 688019.SH |
| 深市主板 | 000001 / 002xxx | SZSE | 000001.SZ |
| 创业板 | 300750 / 301xxx | SZSE ChiNext | 300750.SZ |
| 北交所 | 43xxxx / 83xxxx / 87xxxx / 92xxxx | BSE | 920593.BJ |

A 股内部统一用 `.SH/.SZ/.BJ`。若使用 Yahoo Finance 之类辅助源，需要另设 provider alias：`.SS/.SZ`，但不能改变内部标准代码。

### 2.2 美股

常见代码：`AAPL`, `NVDA`, `MU`, `TSLA`, `BRK.B`。

内部格式：

```text
symbol = UPPERCASE_TICKER
market = US
currency = USD
filing_id = CIK, if resolvable
```

美股财报应优先 SEC EDGAR / 公司 IR。不要用 A 股 F10、东方财富国际摘要替代 SEC filing。

### 2.3 港股

常见代码：`0700.HK`, `9988.HK`, `1810.HK`。

内部格式：

```text
5-digit code + .HK
currency = HKD unless ADR or dual-counter special case
```

港股必须注意：配售、供股、可转债、南向资金、流动性、A/H 股本和货币差异。

### 2.4 Ambiguity Rules

- `688019` 可以推断为 A 股 `.SH`，但必须在输出中写明“按 A 股代码解析”。
- `700` 不能直接当腾讯，必须问清或按 `0700.HK` 标准化并标注假设。
- `BABA` 是美股 ADR；`9988.HK` 是港股；二者不能混用股价、市值、财报口径。
- 同一公司多地上市时，必须分别列示 ticker、货币、股本、价格来源。

---

## 3. Source Priority By Market

### 3.1 A 股数据源

#### L0 原始披露 / 官方源

优先：

- 巨潮资讯 CNINFO；
- 上交所 SSE；
- 深交所 SZSE；
- 北交所 BSE；
- 公司官网投资者关系；
- 招股书、定期报告、临时公告、问询函、监管函；
- 招投标、中标、环评/能评、项目备案、专利、标准。

用途：公司事实、客户、订单、产能、财报、风险、治理。

#### L1 授权/专业数据库

- Wind；
- Choice；
- CSMAR；
- iFinD；
- Tushare Pro；
- 聚源/通联等。

用途：结构化行情、财务、复权因子、股本、估值分位、行业比较。

#### L2 免费/开源辅助

- AKShare；
- BaoStock；
- yfinance/stooq 辅助行情；
- 免费数据接口或开源库。

用途：辅助抓取和交叉校验。不能替代原始公告。

#### L3 媒体/研报/F10

- 东方财富/同花顺 F10 与网页摘要；
- 内置 `Eastmoney_F10_Financials_L3` 可抓取 A 股 L3 结构化三表预检数据。

用途：线索、上下文和快速索引。关键结论必须回 L0/L1 复核。

#### L4 社媒/传闻

用途：只能当 lead，不可当证据。

---

### 3.2 美股数据源

#### L0 原始披露 / 官方源

- SEC EDGAR submissions；
- SEC Companyfacts/XBRL；
- 10-K、10-Q、8-K、S-1、S-3、20-F、6-K、Form 4；
- 公司 IR、earnings release、presentation、transcript。

使用 edgartools 时注意：Python import 包名是 `edgar`；SEC requests 需要设置身份。

#### L1/L2 行情和估值

- Polygon、IEX Cloud、Tiingo、Nasdaq Data Link、FactSet/Bloomberg/Koyfin/TIKR；
- yfinance、Stooq 只能作研究辅助；
- 期权、short interest、consensus revisions 需要专门源。

#### 美股特有检查

- S-3 / shelf registration / ATM；
- convertibles；
- SBC；
- insider Form 4；
- 10b5-1 selling；
- customer concentration；
- non-GAAP vs GAAP；
- split/ADR ratio；
- short interest / option squeeze。

---

### 3.3 港股数据源

#### L0 原始披露

- HKEXnews；
- 公司年报/中报/公告；
- 配售、供股、可转债、关连交易公告；
- 公司 IR。

#### 关键检查

- 港币口径；
- 流动性和 bid-ask spread；
- 配售/供股/CB 稀释；
- 南向通资格；
- 关联交易；
- A/H 双重上市口径；
- 内地政策和收入暴露。

---

## 4. Market Parity Contract

同一类研究任务必须经过相同判断层级，但不同市场必须使用不同主源和风险清单。

统一判断层级：

```text
代码解析
→ 当前价格与复权历史
→ 最新财报
→ 原始披露
→ 供应链证据
→ 财务质量
→ 市场隐含增长
→ 技术位置
→ 证伪条件
→ 评级上限
```

市场源路径不能互相替代：

| 分析对象 | 主披露源 | 禁止替代 |
|---|---|---|
| A 股 | 巨潮、上交所、深交所、北交所、公司 IR | SEC EDGAR、10-K/10-Q、东方财富/F10 摘要充当原文 |
| 美股 | SEC EDGAR、10-K/10-Q/8-K、公司 IR | 巨潮、A 股 F10、东方财富国际摘要充当 SEC filing |
| 港股 | HKEXnews、公司公告、年报/中报 | A 股或美股同名证券的价格、股本、货币直接套用 |

输出中的错源必须以 `forbidden source` 明确标记；未标记的错源视为数据路由失败，评级上限降到 `OBSERVE_ONLY` 或 `C`。

---

## 5. Required Data By Task Type

### 5.1 单股长线分析

必须数据：

```text
symbol resolution
latest quote
market cap / share capital
adjusted daily price history >= 250 trading days
latest annual report
latest quarterly/interim report
last 24 months announcements/filings
revenue/gross profit/net income/OCF/capex/debt/cash/shares
customer/order/capacity evidence if thesis depends on it
```

缺任意关键数据，评级上限下降。

### 5.2 主题扫描

必须数据：

```text
candidate universe by value-chain layer
market-specific source plan
source count and source quality summary
at least one hard evidence item for each final candidate
valuation/price freshness for final candidates
```

深度扫描目标：20+ 候选、25+ sources；工具/时间不足时标记 initial pass。

### 5.3 技术/缠论分析

必须数据：

```text
unadjusted latest price for actual current price
forward-adjusted daily OHLCV for recent technical analysis
at least 250 bars for 200DMA and medium-cycle context
30m or 60m data if claiming intraday Chan buy point
corporate actions adjusted consistently
```

没有复权历史数据，不输出缠论买点。

---

## 6. Price Data Validation

### 6.1 Required Columns

```text
date, open, high, low, close, volume
optional: amount, adj_factor, pre_close, turnover
```

### 6.2 Checks

- date 唯一且升序；
- close > 0；
- high >= max(open, close)；
- low <= min(open, close)；
- volume >= 0；
- 缺失值不得影响关键指标；
- 最新交易日不得过期；
- 对 A 股，节假日/周末要结合交易日判断，不要机械使用自然日。

### 6.3 Adjustment Rules

| 价格口径 | 用途 |
|---|---|
| 未复权 | 当前价、市值、成交价、成交金额 |
| 前复权 | 技术结构、均线、缠论、近期走势 |
| 后复权 | 长期收益率、历史回报 |

禁止：用后复权价格当当前成交价。

### 6.4 Cross-Source Quote Check

若有多个源：

```text
diff = abs(price_a - price_b) / median(price_a, price_b)
```

- diff <= 0.5%：OK；
- 0.5% < diff <= 2%：PARTIAL，必须说明；
- diff > 2%：FAILED，暂停估值和技术结论。

---

## 7. Financial Data Validation

### 7.1 Required Fields

```text
period, report_date, currency, unit
revenue
gross_profit or gross_margin
operating_income
net_income
operating_cash_flow
capex
cash
debt
assets
liabilities
equity
shares_outstanding
```

### 7.2 Rules

- 利润表和现金流可以算 TTM；资产负债表不能算 TTM，只用期末数。
- 元、万元、亿元必须统一。
- 货币必须明确：CNY、USD、HKD。
- 资产 ≈ 负债 + 权益，差异超过 1% 要警告。
- 收入增长必须和应收、存货、现金流匹配。
- 毛利率变化必须解释为价格、产品结构、利用率或竞争。
- 净利润与经营现金流长期背离必须降级。

---

## 8. Data Failure Rating Caps

| 失败类型 | 评级上限 |
|---|---|
| 市场/代码解析失败 | 观察，不评级 |
| 当前价格失败 | 最高 B；不能给当前买点 |
| 复权历史行情失败 | 最高 B；技术评分最高 C |
| 最新财报失败 | 最高 B；不能给 S/A 长线结论 |
| 原始公告/filing 失败 | 客户/订单最高 Medium，综合最高 B |
| 客户/订单只有传闻 | 综合最高 C |
| 股本/市值无法确认 | 不能做市值错配；最高 B |
| 多源价格差异 >2% | 最高 C，暂停估值与技术 |
| 供应链证据只有社媒 | 最高 C |
| 市场错源且未声明 forbidden | OBSERVE_ONLY 或最高 C |
| H4/H5 增长只由主题热度支持 | 最高 B |

---

## 9. Data Acquisition Contract

每次正式分析应生成或隐式维护：

```json
{
  "symbol": {
    "symbol": "688019.SH",
    "market": "CN_A",
    "currency": "CNY"
  },
  "retrieved_at": "2026-06-22T...Z",
  "requested_datasets": ["current_quote", "price_history_adjusted", "filings_announcements"],
  "full_research_required_datasets": ["current_quote", "price_history_adjusted", "financials", "filings_announcements"],
  "data_acquisition": {
    "policy": "assets/data_acquisition_policy.json",
    "status_by_dataset": {
      "current_quote": "OK",
      "price_history_adjusted": "OK",
      "financials": "NOT_REQUESTED",
      "filings_announcements": "OK"
    },
    "attempt_ledger": [
      {
        "dataset": "current_quote",
        "source_name": "Yahoo_Chart_L2",
        "source_level": "L2_FREE_API_OR_OPEN_SOURCE",
        "stage": "OPEN_STRUCTURED",
        "status": "OK",
        "attempted_at": "2026-06-22T...Z",
        "gap_type": null,
        "decision_impact": null,
        "reason": ""
      }
    ],
    "data_gaps": [
      {
        "dataset": "financials",
        "status": "NOT_REQUESTED",
        "gap_type": "SCOPE_NOT_REQUESTED",
        "decision_impact": "EVIDENCE_IMPACT",
        "rating_impact": "High-conviction long-term ratings are capped at B until primary or licensed evidence is complete.",
        "next_action": "Fetch latest official or licensed financial statements and run financial validation before scoring fundamentals."
      }
    ],
    "research_debt": [
      {
        "dataset": "financials",
        "priority": "critical",
        "gap_type": "SCOPE_NOT_REQUESTED",
        "decision_impact": "EVIDENCE_IMPACT",
        "next_action": "Fetch latest official or licensed financial statements and run financial validation before scoring fundamentals."
      }
    ],
    "manual_retrieval_tasks": [
      {
        "dataset": "financials",
        "priority": "critical",
        "target_source": "Official filing PDF/XBRL or L1 financial database",
        "objective": "Verify the latest core financial statement lines before issuing an S/A research rating."
      }
    ],
    "attempt_count": 8,
    "gap_count": 1,
    "research_debt_count": 1,
    "manual_task_count": 1,
    "full_research_ready": false
  },
  "data_quality": {
    "requested_data_rating_cap": "S",
    "full_research_rating_cap": "B",
    "rating_cap": "B"
  }
}
```

`NOT_REQUESTED` means the current scoped fetch did not ask for the dataset. For formal rating tasks, any missing critical dataset caps the full-research rating until it is fetched and validated.

L3 structured financial preflight creates a material evidence gap:

```json
{
  "dataset": "financials",
  "status": "PARTIAL",
  "gap_type": "NOT_MACHINE_READABLE",
  "decision_impact": "EVIDENCE_IMPACT",
  "rating_impact": "S/A research ratings require L0/L1 verification of key financial statements.",
  "next_action": "Reconcile revenue, profit, cash flow, assets, liabilities, and equity against official filings or an L1 database export."
}
```

---

## 10. Implementation Hooks

Use:

```bash
python scripts/data_router.py fetch NVDA --sec-user-agent "Your Name your.email@example.com"
python scripts/run_real_data_smoke.py --case-set all --sec-user-agent "Your Name your.email@example.com"
python scripts/data_router.py resolve 688019
python scripts/data_router.py plan 688019
python scripts/data_router.py validate-price prices.csv --market CN_A --adjust qfq
python scripts/data_router.py validate-financial financials.json
python scripts/serenity_chan_scorecard.py assets/scorecard_template.json --format md
python scripts/candidate_ranker.py candidate_a.json candidate_b.json
```

`fetch` is the preferred preflight entry point when the task depends on current price, adjusted history, SEC financials, SEC filings, A-share CNINFO announcements, or A-share Eastmoney F10 L3 structured financial preflight. It writes an auditable bundle with raw source payloads, hashes, `attempt_ledger.json`, `data_gaps.json`, `research_debt.json`, `manual_retrieval_tasks.json`, and `manifest.json` under `--out-dir` or `/tmp/serenity-chan-data/...`.

For US SEC JSON, pass a compliant identity with `--sec-user-agent` or `SEC_USER_AGENT`. Resolve CIK through the bundled bootstrap table first, then SEC ticker directories. Fetch filings through SEC submissions, fetch financials through SEC companyfacts, and continue through SEC companyconcepts if the larger companyfacts payload is unavailable. If network, TLS, identity, rate limit, or provider support fails after the available route is attempted, mark the dataset `FAILED` / `PENDING`, keep the failure in the data-quality section, and apply rating caps. If a scoped fetch does not request a dataset, mark it `NOT_REQUESTED` and keep the full-research cap conservative. Do not replace failed or unrequested real fetches with guessed prices, financials, or filing facts.

Eastmoney F10 financials provide A-share L3 structured preflight evidence. Keep the final research rating cap at B until key conclusions are verified against CNINFO/exchange report PDFs or L1 databases. Use the fetch manifest's `ai_review` section to explain source strength, industry reporting fit, validation warnings, and upgrade requirements.

Additional official or licensed CNINFO/Tushare/Wind/Choice/HKEX adapters can be added without changing the research logic.
