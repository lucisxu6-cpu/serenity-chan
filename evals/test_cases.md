# Evaluation Test Cases

Run the executable static cases with:

```bash
python scripts/run_static_evals.py
```

These fixtures assert that missing data, weak evidence, and unsupported current buy-point claims are downgraded or blocked before delivery.

## 1. A 股市场分流
User: 分析 688019。
Expected:
- Resolve as 688019.SH / CN_A.
- Use CNINFO/SSE path for filings.
- Do not use SEC EDGAR.
- Require qfq history before Chan analysis.

## 2. 美股市场分流
User: 分析 AAPL 当前是否高估。
Expected:
- Resolve as US ticker.
- Use SEC EDGAR / 10-K / 10-Q / 8-K path.
- yfinance only as L2 auxiliary source.
- Check split/dividend adjusted history.
- Attempt `data_router.py fetch` for quote, adjusted history, SEC financials, and filings before making current-price or buy-point claims.

## 3. 数据失败降级
User: 没有网络，帮我判断 300750 当前买点。
Expected:
- State current price/history unavailable.
- No current buy point.
- Rating capped B or Observe Only.
- A failed real fetch must remain visible in the data-quality section; do not replace it with guessed market data.

## 4. 证据弱降级
User: 某 KOL 说公司进入宇树供应链，能不能买？
Expected:
- Treat KOL as D/C lead.
- Ask for公告/招股书/年报/客户验证.
- No S/A rating.

## 5. 好公司但无买点
User: 公司基本面很好，现在追不追？
Expected:
- Separate object quality from entry.
- If technical overheat, action = Wait for 2nd/3rd Buy.

## 6. 主题扫描必须先层级后公司
User: 国产机器人产业链哪些 A 股值得研究？
Expected:
- Output value-chain map before company names.
- Rank bottleneck layers before top candidates.
- Build a candidate universe or mark initial pass.
- Include at least one popular but downgraded direction.

## 7. 市场错源必须阻断
User: 分析 688019、AAPL 或 0700.HK。
Expected:
- A 股 path must not use SEC EDGAR as primary evidence.
- US path must not use CNINFO/F10 as primary evidence.
- HK path must not use SEC, CNINFO, or A-share F10 as a substitute for HKEX/company announcements.
- Wrong-source output fails unless the source is explicitly marked forbidden.

## 8. H4/H5 增长必须有强证据
User: 某机器人概念股是否具备 H5 平台级扩张证据？
Expected:
- Market heat or FOMO cannot upgrade intrinsic growth.
- H4/H5 requires primary filings, customer/order, capacity, or financial realization.
- If market price implies H5 but evidence supports only H2/H3, rating cap is B or lower.
- A weak or L3 source manually labeled `Strong` must still fail.

## 9. 跨市场对比允许各走各源
User: 对比 NVDA 和 688019.SH。
Expected:
- NVDA may use SEC EDGAR / 10-K as US primary evidence.
- 688019.SH may use CNINFO / exchange announcements as A-share primary evidence.
- The validator should block wrong source use within a local object, not block legitimate cross-market reports.

## 10. 结构化 scorecard 必须执行决策矩阵门控
User: 一个满分 scorecard 但 market-implied growth 是 H5，证据只支持 H3。
Expected:
- H4/H5 证据缺口必须进入 `data_gaps` / `research_debt` / `blockers`。
- Research rating、candidate priority、action readiness 必须被门控到 B / DATA_GATED 或更低。
- 高模块分数不能覆盖关键证据债务。

## 11. Falsification dashboard 必须可验证
User: 把一个高估值争议对象转成长期跟踪 dashboard。
Expected:
- Dashboard includes thesis, growth gap, monitors, source requirements, status, and action if triggered.
- If market-implied growth is H4/H5 while evidence is weaker, dashboard must include a valuation monitor and rating cap B or lower.

## 12. News-to-financial 事件分析必须转成财务验证
User: 某 AI infrastructure news 是否会形成 alpha？
Expected:
- Identify observed demand instead of stopping at news heat.
- Map demand into revenue, margin, cash flow, backlog, capex, or balance-sheet lines.
- Explain small-cap elasticity or why it is absent.
- Include a 1-4 quarter validation path and falsification trigger.

## 13. 结构化 JSON 输出合同必须独立可验
User: 产出机器可读 output contract。
Expected:
- Required fields, market route, data statuses, data acquisition, research debt, decision matrix, rating cap, evidence, falsification, action, and uncertainty are all validated.
- If market-implied growth is H4/H5 while evidence is weaker, JSON validator blocks S/A cap and core-candidate action.
- OTHER/UNKNOWN markets are capped to OBSERVE_ONLY in scorecard paths.

## 14. 真实数据 smoke 必须覆盖主标的、上游链路、A 股行情/财务/公告和港股当前数据
User: 用真实数据验证 NVDA 用例和上游链路，并确认 A 股行情、财务、公告与港股当前行情可取。
Expected:
- `scripts/run_real_data_smoke.py --case-set all` covers NVDA, MU, AMD, AVGO, TSM, ASML, 688019, 300750, 300480, 600036, 600030, 601318, 920593, and 0700.HK.
- US operating companies should fetch current quote, adjusted history, SEC financials, and SEC filings when sources are available.
- ADR upstream cases must attempt SEC XBRL 20-F / 40-F facts, including IFRS taxonomy when present, before marking financials unavailable.
- A-share current quote and adjusted history must be fetched through market-appropriate A-share sources, including SH/SZ/BJ Eastmoney/Tencent quote/K-line coverage; CNINFO announcement metadata and selected annual/quarterly report PDFs must be fetched when CNINFO resolves the symbol; CNINFO L0 official report PDF line extraction must return core financial rows when readable; Eastmoney F10 remains a L3 secondary preflight and may not unlock S/A ratings by itself.
- BJ quote/history may not remain `FAILED` when a validated A-share open source resolves it; if a free source only returns unconfirmed daily rows, the smoke path must use CNINFO equity-distribution announcements to construct an auditable qfq series before accepting adjusted history.
- The real-data manifest must include `attempt_ledger`, `data_gaps`, `research_debt`, `manual_retrieval_tasks`, and `ai_review` guidance so the AI explains source level, industry reporting fit, validation warnings, and exact L0/L1 upgrade requirements.
- A-share financial-sector cases must use dedicated industry profiles. Bank financials require net interest, deposit, loan, asset-quality, provision, and capital metrics; securities firms require net capital and risk-control ratios; insurers require insurance service revenue, insurance contract liabilities, and solvency metrics from official reports before `financials=OK` can unlock S/A eligibility.
- HK current quote and adjusted history must be fetched when Yahoo L2 supports the symbol; HKEXnews announcements and annual/interim report PDFs must be fetched for HK issuers that resolve in the HKEX stock list; HK financials are `OK` when core report line items are extracted and `PARTIAL` only when PDF text extraction or core field coverage is incomplete.

## 15. 候选对比必须暴露估值输入、精确门控和结论确定性
User: 对比 688019 和 688322，并判断谁更值得优先研究。
Expected:
- `valuation_input_matrix` contains one row per candidate with price, total shares, total market cap, currency, source, basis, verification need, warnings, and errors.
- `growth_hypothesis_matrix[*].valuation_input_ref` points back to the candidate's valuation-input row.
- A `PARTIAL` valuation row keeps fetched audit fields such as price, shares, currency, source, and warnings, while market-implied growth stays `UNKNOWN` until complete valuation inputs are verified.
- Cross-symbol `valuation_input_ref` values are invalid even when they use the correct `valuation_input_matrix:` prefix.
- AI overlay supplies `evidence_supported_growth`; `market_implied_growth` is generated from valuation inputs and PE/PS.
- Missing valuation inputs create `VALUATION_GATED`; if a lower-level quote/history blocker exists, `DATA_GATED` can remain primary while `VALUATION_GATED` stays visible as a secondary gate.
- `final_decision` includes `decision_mode`, `score_gap_to_runner_up`, and candidate-count warning so close candidate clusters are not overstated as durable top picks.
- AI overlay examples under `examples/comparison_688019_688322/` must validate before they can change layer mapping, evidence-supported growth, or ranking context.
