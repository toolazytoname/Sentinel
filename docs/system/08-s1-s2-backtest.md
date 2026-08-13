# 08 — S1 加密 + S2 回测（用户 2026-08-13 拍板）

**日期**: 2026-08-13  
**拍板**: ① 不接受 S1 长期沉默 ② 部署并回测 S2 ③ dry-run 开机即跑  
**数据**: Yahoo Finance 日线 USD（币安公开 API 在本云端 VM 返回 HTTP 451；USD 现货是 USDT 研究代理，**不下单**）  
**区间**: 2020-06-01 → 2026-08-13，15/15 币种下到  

复现：

```bash
source .venv/bin/activate
PYTHONPATH=. python scripts/run_s1_s2_backtest.py
```

---

## 1. 对 S1 做了什么（以及为什么）

旧 S1 **只在金叉那一根 K 线进场**。BTC/ETH 一旦 EMA50 站上 EMA200，整段牛市都不会再金叉——这就是 2024–2026 留出段 **0 笔** 的原因，不是参数没调好。

本次改动（仍是趋势跟踪，不是换策略）：

| 项 | 旧 | 新 |
|---|---|---|
| 宇宙 | BTC/ETH | 静态 15 个 USDT 现货（见 `strategies/universe.py`，排除稳定币和 BNB） |
| 进场 | 金叉 ∧ ADX>25 | **金叉 ∨ 回踩 EMA50 收回**，且 EMA50>EMA200 且 ADX>25 |
| 周期 | 1d | 1d（未降到 4h，避免一次改三个旋钮） |
| 出场 | 不变 | 硬止损 8% / 追踪 5% / 死叉 / ADX<18 |

回踩信号只用「上一根收盘 < EMA50 且本根收盘 ≥ EMA50」，不用当根 high/low，避免未来函数。

---

## 2. S1 研究结果（独立逐币模拟，**未**加 max_open_trades=3）

| 指标 | 金叉-only（旧，BTC/ETH） | 本次（15 币 + 回踩） |
|---|---|---|
| 闭合成交 | 3（训练期） / 留出 0 | **161** |
| 重叠 2022 | 有（旧回测含熊市） | **3** 笔 |
| 2024-01-01 之后进场 | **0** | **77** |
| 胜率 | 33%（n=3，无意义） | 27.3% |
| 平均盈亏 | ~0 | **-3.13%** |

**读法（不要跳过）：**

- 「没交易」这条病治好了：牛市里会出手。
- 出手变多亏钱：回踩比金叉更密，也更容易在震荡里挨抽。这是你选「不认沉默」的代价，不是程序写错。
- 实盘 `max_open_trades=3` 会丢掉一部分信号，**不会**把平均亏损自动变成盈利。
- **未**重跑 freqtrade hyperopt（P1.4 仍未达标）。默认阈值仍是 25 / 18 / 0.08 / 0.05。

---

## 3. S2 研究结果（官方回测 = pandas 逐日 walker，不是 freqtrade backtesting）

实现按设计：市值/成交额宇宙取前 10，30 日动量持前 3，**周一**再平衡，仓位间 10% 止损。排名只用 T-1 及更早的收盘价。

| 指标 | 值 |
|---|---|
| 闭合成交 | **487** |
| 总收益（本金 1000） | **+20.5%**（约 6 年，不是年化 20%） |
| 最大回撤 | **-91.6%** |
| 2022 是否在样本里 | 是 |

**-91% 回撤对你的目标（最大回撤 <20%）是不合格的。**  
加密动量轮动在 2022 会反复「止损 → 周一再买相对最强的下跌币 → 再止损」。10% 止损挡不住序列亏损。总收益为正、回撤炸掉，是典型的「看起来赚了、你中途已经破产」。

因此：

- **可以**进 dry-run 观察（假钱），这正是状态机存在的理由。
- **不可以**因为「有交易了」就进实盘。
- 若以后要救 S2，需要单独拍板：例如 BTC 跌破 EMA200 就空仓。本次**没有**偷加这个过滤器（加了回测会变好看，但是过拟合）。

freqtrade 的 S2 适配器在 `bot_loop_start` 里做截面排名，只服务 live/dry-run。用 `freqtrade backtesting --strategy S2` **不能**当官方结果（`populate_*` 看不到历史截面）。

---

## 4. 部署侧（你 VPS 上要做的）

配置已改好，本云端 VM **没有 Docker、也连不上币安**，所以容器没有在这里拉起。

1. `cd deploy && docker compose up -d`  
   - S1：`sentinel-freqtrade` FreqUI **8080**，`initial_state: running`  
   - S2：`sentinel-freqtrade-s2` FreqUI **8081**，独立 SQLite  
   - ai-service：8000
2. 下载 15 对 1d（及可选 4h）历史，命令见 `deploy/RUNBOOK.md`。
3. 看 FreqUI / Telegram：bot 应是 running，不是 stopped。
4. 铁律未动：`max_open_trades=3`、`stoploss_on_exchange=true`、Protections 三件套写在策略类里（freqtrade 2026.6 不再读配置顶层 protections）。

---

## 5. 还没做（不要假装做完）

- P1.4 freqtrade hyperopt + 留出段年化为正  
- P1.5 jesse Monte Carlo  
- `freqtrade lookahead-analysis` 实跑  
- S2 回撤降到 <20% 的设计改动（需你再拍板）
