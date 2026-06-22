# Example Shape: A-share Single Stock

Input: `分析 688019 是否是长线高胜率对象。`

Expected first output:

```markdown
## Data Fetch Plan
- 解析后证券：688019.SH
- 市场：CN_A
- 交易所：SH
- 数据源路线：
  - 价格：Tushare/Wind/Choice + AKShare/BaoStock 交叉
  - 财报：巨潮/上交所原始公告 + 结构化数据库
  - 公告：巨潮/上交所最近 24 个月
  - 证据：年报、调研纪要、客户/产能/专利/项目资料
- 失败降级：无复权行情则不输出缠论买点；无原始公告则证据最高 B。
```

Then the agent may proceed to analysis only after data status is known.
