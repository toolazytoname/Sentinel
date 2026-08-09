# P1.2 / P1.4 — S1 扩展币种回测报告

**日期**: 2026-08-09

**结论**: S1 扩展到 15 个现货 pair 后，交易样本从 BTC/ETH 版本的 3 笔增加到全期 40 笔；策略实现通过前视偏差检查。但留出期 CAGR 仅 5.58%，全期收益又被一笔 DOGE 极端行情主导，当前仍不具备扩大 dry-run 宇宙或进入资金阶段的证据。

---

## 1. 实验边界

- 策略代码和默认参数不变：`adx_entry=25`、`adx_exit=18`、`hard_stop=8%`、`trailing_stop=5%`。
- 使用 `08-s2-backtest-results.md` 中相同的 15-pair 当前市值候选集。
- 1d K 线、Binance 现货、0.1% 费率、每单 50 USDT、最多同时 3 仓。
- 三项 Freqtrade Protections 启用。
- 未根据留出结果修改规则或参数。

当前市值候选集用于历史回测存在幸存者偏差，因此本实验主要回答“扩大币种能否提高信号密度”，不等同于无偏收益估计。

---

## 2. 回测结果

| 区间 | 交易数 | 总收益 | CAGR | 最大回撤（平仓） | 最大水下（钱包） | Profit factor | 胜率 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 全期 2020-08-08 → 2026-08-08 | 40 | +105.38% | +12.74% | 4.56% | **22.96%** | 8.70 | 20.0% |
| 训练期 2020-08-08 → 2024-01-01 | 25 | +91.01% | +20.97% | 4.56% | **22.96%** | 11.04 | 20.0% |
| 留出期 2024-01-01 → 2026-08-08 | 16 | +15.19% | **+5.58%** | 2.62% | 9.23% | 4.10 | 18.8% |

训练期与全期的高收益主要来自一笔 DOGE 交易：单笔收益率 `+1718.20%`，DOGE 对账户总收益贡献 `+93.20%`。去掉或削弱这一极端赢家后，表面的高 Profit factor 和 CAGR 会显著下降。因此不能把全期 +105% 解读为稳定的年化能力。

留出期由原 BTC/ETH 的 0 笔提高到 16 笔，证明扩大币种能够缓解信号稀疏；但它仍有三项不足：

1. CAGR 5.58% 低于 ADR-004 的 8%–15% 目标。
2. 16 笔仍低于 ADR-005 单阶段升级所需的 30 笔。
3. 当前成分回测带有幸存者偏差，不能据此把 dry-run 从 BTC/ETH 直接扩到 15 币。

---

## 3. 前视偏差检查

Freqtrade 2026.6 `lookahead-analysis` 使用全历史窗口抽样 30 个交易：

| total_signals | biased_entry_signals | biased_exit_signals | biased_indicators | 结论 |
|---:|---:|---:|---|---|
| 30 | 0 | 0 | 空 | **No bias detected** |

这补齐了 P1.2 的工程验收项：回测包含 2022 熊市，指标函数有单测，前视检查通过。

---

## 4. 决策

- P1.2“实现正确”可以完成；这不代表策略通过投资验收。
- P1.4 继续保持未完成：15-pair 版本尚未做严格的训练期 hyperopt / rolling walk-forward，且留出 CAGR 低于系统目标。
- 当前 `dry-run.json` 保持 BTC/ETH + S1，不扩大币种，不启用 S2。
- 下一轮研究优先补 point-in-time 历史成分或独立切分，再做稳健性/Monte Carlo；不围绕 DOGE 极端赢家追参。

---

## 5. 复现命令

```bash
# 全期；训练/留出仅替换 --timerange
docker exec sentinel-freqtrade freqtrade backtesting \
  --config /freqtrade/user_data/config/hyperopt.json \
  --strategy S1TrendFollow \
  --timerange 20200301-20260809 \
  --enable-protections \
  --cache none \
  --export trades

docker exec sentinel-freqtrade freqtrade lookahead-analysis \
  --config /freqtrade/user_data/config/hyperopt.json \
  --config /freqtrade/user_data/config/lookahead.json \
  --strategy S1TrendFollow \
  --timerange 20200301-20260809 \
  --minimum-trade-amount 20 \
  --targeted-trade-amount 30 \
  --export none
```
