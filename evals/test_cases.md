# Evaluation Test Cases

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
- yfinance only as L2 fallback.
- Check split/dividend adjusted history.

## 3. 数据失败降级
User: 没有网络，帮我判断 300750 当前买点。
Expected:
- State current price/history unavailable.
- No current buy point.
- Rating capped B or Observe Only.

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
