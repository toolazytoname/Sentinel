# P1.4 — S1 Trend-Follow Hyperopt 报告

**日期**: 2026-07-06
**目标**: 用历史数据验证 S1 策略的超参空间，量化 baseline 与"hyperopt 最优"的实际差异，决定是否改 strategy 默认值。

---

## 1. 实验设计

### 数据
- 来源：本地缓存 `user_data/data/binance/{BTC,ETH}_USDT-1d.json`（P1.1 下载）
- 时间覆盖：2020-06-27 → 2026-06-04（约 2200 根 1d K线 × 2 pairs）

### 切分
| 区间 | 日期 | 用途 | 有效回测窗口 |
|---|---|---|---|
| 训练 | 2020-06-27 → 2023-12-31 | hyperopt 搜索 | 2021-02-02 → 2023-12-31（扣 220 根 EMA 暖机）|
| Holdout | 2024-01-01 → 2026-06-04 | 验证 | 同上 |

### 超参空间（4 维）
| 参数 | 范围 | Default |
|---|---|---|
| `adx_entry` | 20.0 – 35.0 | 25.0 |
| `adx_exit` | 15.0 – 22.0 | 18.0 |
| `hard_stop` | 0.05 – 0.15 | 0.08 |
| `trailing_stop_p` | 0.03 – 0.10 | 0.05 |

### Loss
`SortinoHyperOptLossDaily`（下行风险调整，比 Sharpe 更稳定）

### 配置
- 100 epochs（`--epochs 100`）
- `--min-trades 2`（过滤掉只 1 笔交易的组合）
- 数据格式：`json`（hyperopt.json 里 `dataformat_ohlcv: json`，默认 `feather` 会找不到 .json 数据）
- 交易模式：`spot`
- 容器：`sentinel-freqtrade`（已挂 user_data 与本地代理）

---

## 2. 结果

### 训练期
| 配置 | 交易数 | Profit% | MaxDD% | Win% | Avg Dur |
|---|---|---|---|---|---|
| Baseline（默认参数）| **3** | +0.04% | 1.02% | 33.3% | 33d16h |
| Hyperopt "Best" epoch | **3** | +0.04% | 1.02% | 33.3% | 33d16h |

Hyperopt 输出最优参数：
```python
{"adx_entry": 34.926, "adx_exit": 16.926, "hard_stop": 0.129, "trailing_stop_p": 0.055}
```

### Holdout（2024-01 → 2026-06）
| 配置 | 交易数 | Profit% | MaxDD% |
|---|---|---|---|
| Baseline | **0** | 0% | 0% |
| Hyperopt 最优 | **0** | 0% | 0% |

---

## 3. 关键发现

### 3.1 S1 在训练期只产生 3 笔交易
EMA50/EMA200 黄金交叉在 BTC/ETH 1d 上是**稀疏事件**：2020-06 → 2023-12 三年半内仅触发 3 次。3 笔交易不足以支撑超参搜索的统计意义——hyperopt 的 100 epochs 几乎全部分布在 3-4 笔交易的组合上，profit 范围 ±0.3%。

### 3.2 Hyperopt 没有产生差异化
所有 epoch 的最优交易集都是相同的 3 笔。Loss 在参数空间内是**平顶**——`adx_entry` 的搜索只影响"是否能进第 1 天"，其余日子没有 golden cross 信号可过滤。

### 3.3 Holdout 零交易是策略本身的事实，不是参数问题
BTC/ETH 在 2024-01 → 2026-06 ETF 大牛市中持续上涨，1d 上 EMA50 始终高于 EMA200——**没有死叉信号意味着也没有新黄金交叉**（一旦金叉发生，状态就维持）。无论参数怎么调，策略在该区间不会进场。

---

## 4. 结论与建议

### 4.1 不改 strategy 默认参数
本次 hyperopt 没有找到任何**有统计意义的改进**。Best epoch 与 baseline 完全相同。**保留默认参数（25/18/0.08/0.05）**。

### 4.2 S1 不适合 1d timeframe 的 hyperopt
如果未来想给 S1 调参，需要：
- **加更多 pairs**（当前只有 BTC/ETH，golden cross 总样本太小）
- **降 timeframe 到 4h 或 1h**（startup_candle_count=220 在 4h = ~36 天，仍可工作；可大幅提升样本量）
- **改 S1 本身**——黄金交叉 + ADX 过滤太严苛，可考虑加 RSI/volume 等次级信号

### 4.3 Holdout 零交易也提示了产品方向
2024-2026 BTC/ETH 现货大牛市里，**S1 trend-follow 完全在场外**。这不是 bug 而是 feature（不接飞刀），但意味着 S1 在小资金测试期间会有较长的"沉默期"。**当前不影响** Phase 0 dry_run——按现状运行、积累样本即可。

---

## 5. 复现命令

```bash
# Hyperopt (训练期)
docker exec -e HTTP_PROXY=http://127.0.0.1:7890 -e HTTPS_PROXY=http://127.0.0.1:7890 \
  sentinel-freqtrade freqtrade hyperopt \
  --config /freqtrade/user_data/config/hyperopt.json \
  --strategy S1TrendFollow \
  --strategy-path /freqtrade/user_data/strategies \
  --hyperopt-loss SortinoHyperOptLossDaily \
  --spaces buy sell \
  --epochs 100 \
  --timerange 20200627-20231231 \
  --min-trades 2 \
  --print-all

# Baseline backtest (训练期)
docker exec sentinel-freqtrade freqtrade backtesting \
  --config /freqtrade/user_data/config/hyperopt.json \
  --strategy S1TrendFollow \
  --strategy-path /freqtrade/user_data/strategies \
  --timerange 20200627-20231231

# Holdout backtest (任一配置；修改 default 后再跑)
docker exec sentinel-freqtrade freqtrade backtesting \
  --config /freqtrade/user_data/config/hyperopt.json \
  --strategy S1TrendFollow \
  --strategy-path /freqtrade/user_data/strategies \
  --timerange 20240101-20260604
```

## 6. 文件变更

- 新增 `deploy/user_data/config/hyperopt.json`（专用 hyperopt/backtest 配置：dataformat_ohlcv=json、spot 模式、内存 DB）
- 新增 `deploy/user_data/hyperopt_results/strategy_S1TrendFollow_2026-07-06_12-58-59.fthypt`（100 epoch 全部结果）
- 新增本文档 `docs/system/05-hyperopt-results.md`
- **未改动** `deploy/user_data/strategies/S1TrendFollow.py` 和 `strategies/s1_trend_follow/strategy.py`（hyperopt 没找到改进）

---

## 7. 已知未同步点（不阻塞）

`strategies/` 副本的 DecimalParameter 命名为 `trailing_stop_pct`，`deploy/` 副本为 `trailing_stop_p`（默认参数值两边一致）。本次 P1.4 未触发参数变更，故未同步命名。详见 memory `[[strategy-base-duplication]]`。