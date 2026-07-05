# Sentinel FreqUI 入口占位策略（Phase 0）

> ⚠️ **本策略仅用于 Phase 0 跑通流程，不可用于实盘。**
> Phase 1 将替换为 `S1TrendFollow` 和 `S2MomentumRotation`。

本目录是 freqtrade 策略挂载点。Phase 0 启动时会自动从 freqtrade 官方策略路径加载 `SampleStrategy`（来自 freqtrade 容器内置）；当 `deploy/user_data/config/*.json` 中的 `"strategy"` 字段被改为自定义策略名时，freqtrade 会从本目录加载。

Phase 1 待创建：
- `S1TrendFollow.py` — 趋势跟踪（双均线 + ADX 过滤 + 追踪止损）
- `S2MomentumRotation.py` — 动量轮动（市值前十剔除稳定币 + 周度再平衡）

详见 `docs/system/02-design.md` §3。