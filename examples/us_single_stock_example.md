# Example Shape: US Single Stock

Input: `Analyze NVDA using this skill.`

Expected first output:

```markdown
## Data Fetch Plan
- Canonical symbol: NVDA
- Market: US
- Source route:
  - Price: licensed market data / Nasdaq / IEX / Polygon / yfinance auxiliary
  - Filings: SEC EDGAR 10-K, 10-Q, 8-K, Form 4, S-3 if relevant
  - Financials: SEC XBRL + company IR + estimates provider
  - Technical: adjusted history for GF-DMA and Chan proxy
- Failure policy: no SEC filing → no S/A evidence; no adjusted history → no technical entry.
```
