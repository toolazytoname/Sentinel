# P1.1 / P1.3 — S2 动量轮动数据与回测报告

**日期**: 2026-08-09
**结论**: 数据下载与工程验证完成；S2 默认参数在留出期亏损，**禁止进入 dry-run**。

---

## 1. 数据与研究宇宙

候选集按 2026-08-09 的 CoinGecko 市值排序取得，剔除稳定币后，与 Binance `exchangeInfo` 中可交易的 USDT 现货取交集，保留前 15 个：

`BTC, ETH, BNB, XRP, SOL, TRX, DOGE, ZEC, ADA, LINK, XLM, BCH, GRAM, LTC, HBAR`

来源：
- [CoinGecko markets API](https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=60&page=1&sparkline=false)
- [Binance exchangeInfo](https://api.binance.com/api/v3/exchangeInfo)

本地已下载全部 15 个 pair 的 `1d` 和 `4h` Binance 现货 OHLCV：

| 分组 | 数据范围 | 完整性结果 |
|---|---|---|
| BTC/ETH/BNB/XRP/TRX/DOGE/ZEC/ADA/LINK/XLM/BCH/LTC/HBAR | 2020-01-01 至 2026-08-08（1d）/ 2026-08-09（4h） | 1d 连续；4h 均只有同一个源端缺口 |
| SOL | 2020-08-11（Binance 上市）至今 | 1d、4h 连续 |
| GRAM | 2026-07-02（Binance 上市）至今 | 1d、4h 连续；历史回测窗口中自然无数据 |

13 个早期上市 pair 的 4h 数据都缺少 `2020-02-19 12:00 UTC`，下一根为 `16:00 UTC`。直接查询 Binance Kline API 得到相同结果，因此这是交易所源数据的同步缺口，不做人工插值或伪造 K 线。除上市日期和该源端缺口外，未发现重复时间戳或异常间隔。

> [!warning]
> 这套候选集使用“今天仍在市值前列”的币去回测历史，存在明显的幸存者偏差；它适合工程验证，不能单独作为历史收益可信证据。严肃验证需要 point-in-time 历史成分数据。

---

## 2. S2 实现口径

- 周一 UTC 收盘时再平衡，其余日期延续上周持仓。
- 先按过去 30 日成交额选流动性前 10，再按过去 30 日收益率持有前 3。
- 仅做现货多头；单 pair 配置 stake 为 50 USDT，最多同时 3 仓。
- `-10%` 交易所止损底线；`MaxDrawdown`、`StoplossGuard`、`CooldownPeriod` 三项保护保持启用。
- 横截面数据只读取原始 OHLCV，不读取其他 pair 的 analyzed dataframe。
- `rotation_pairs` 在研究配置中显式固定，保证完整回测与切片验证使用同一研究宇宙。

费用按 Binance 回测识别的最差费率 `0.1%` 计，未额外假设成交滑点。

---

## 3. 回测结果

默认参数：`universe_size=10`、`hold_top_k=3`、`momentum_lookback=30`。本轮没有根据留出期调参。

| 区间 | 交易数 | 总收益 | CAGR | 最大回撤 | Profit factor | 胜率 |
|---|---:|---:|---:|---:|---:|---:|
| 全期 2020-03-01 → 2026-08-08 | 438 | +58.19% | +7.38% | 11.49% | 1.47 | 33.8% |
| 训练期 2020-03-01 → 2023-12-31 | 251 | +64.23% | +13.81% | 11.58%（账户水下） | 1.94 | — |
| 留出期 2024-01-01 → 2026-08-08 | 191 | **-3.46%** | **-1.34%** | 17.85% | 0.94 | 28.3% |

全期 CAGR 低于 ADR-004 的 8%–15% 目标下限；更重要的是，留出期 CAGR 为负，未达到 P1.4“留出段年化为正且回撤 <25%”的完整门槛。训练期与留出期方向相反，不能解释为小幅统计噪声后直接上线。

---

## 4. 前视偏差验证

第一次 `lookahead-analysis` 报告 30 个抽样交易中有 25 个入场、26 个出场偏差。复核 Freqtrade 2026.6 的分析流程后确认：切片回测会把 `pair_whitelist` 缩成当前交易 pair，而原实现直接用运行时 whitelist 做横截面排名，导致完整回测按 15 币排名、切片按 1 币排名。

修复方式是把研究宇宙显式写入 `rotation_pairs`；策略仍逐时间点使用向后滚动数据，没有改变交易规则。重跑结果：

| total_signals | biased_entry_signals | biased_exit_signals | biased_indicators | 结论 |
|---:|---:|---:|---|---|
| 30 | 0 | 0 | 空 | **No bias detected** |

---

## 5. 决策与下一步

1. S2 的工程实现、单元测试和前视偏差闸已通过，可以保留为研究策略。
2. S2 的投资闸未通过，不写入 `dry-run.json`，当前 dry-run 继续只运行 S1。
3. 不允许看过留出结果后直接在 2024–2026 区间调参并再次称其为“留出验证”；该区间从现在起已是开发信息。
4. 下一条可检验假设是加入“绝对动量 ≤ 0 时持币为现金”的风险状态过滤，但应先在训练期做滚动 walk-forward，再用新的时间段、point-in-time 成分或另一独立数据切分验证。
5. 进入资金阶段前仍需完成 P1.5 的 Jesse/Monte Carlo 交叉验证。

---

## 6. 复现命令

```bash
# 全期 / 训练期 / 留出期只替换 --timerange
docker exec sentinel-freqtrade freqtrade backtesting \
  --config /freqtrade/user_data/config/hyperopt.json \
  --strategy S2MomentumRotation \
  --timerange 20200301-20260809 \
  --enable-protections \
  --export none

docker exec sentinel-freqtrade freqtrade lookahead-analysis \
  --config /freqtrade/user_data/config/hyperopt.json \
  --config /freqtrade/user_data/config/lookahead.json \
  --strategy S2MomentumRotation \
  --timerange 20240101-20241231 \
  --minimum-trade-amount 20 \
  --targeted-trade-amount 30 \
  --export none
```

`lookahead-analysis` 会在自己的临时分析进程中强制市场单、无限持仓数、大额测试钱包并关闭 exchange stoploss；这些是分析器内部覆盖项，不会修改 Sentinel 的运行时配置。
