# Sentinel FreqUI 策略挂载点

本目录是 freqtrade 容器的 strategy-path。每个策略一个 `.py` 文件（Docker 镜像不挂载仓库根上的 `strategies/` 包）。

当前：
- `S1TrendFollow.py` — 趋势跟踪：金叉 **或** 上升趋势中回踩 EMA50 收回，ADX 过滤，硬止损 / 追踪止损
- `S2MomentumRotation.py` — 动量轮动：市值/成交额宇宙内 30 日动量前 3，周一再平衡
- `base.py` — AI 否决（fail-open）

改 `base.py` 必须同步 `strategies/base.py`。改 S1 信号必须同步 `strategies/s1_trend_follow/strategy.py`。
