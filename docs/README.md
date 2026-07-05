# Sentinel — 稳健型个人量化系统 · 文档中心

> 生成日期：2026-07-05 | 生成者：Fable 5（架构攻坚阶段）
> 项目名：**Sentinel**（哨兵 —— 守纪律的长期守护者）
> 项目定位：以 freqtrade 为执行底座的稳健型数字货币量化系统，带统一 UI 与 AI 分析层。

## 目录结构

```
docs/
├── README.md                 ← 你在这里（导航）
├── system/                   ← 给接棒 AI 看的（系统线）
│   ├── 00-research-summary.md    调研结论汇总（选型依据，含数据）
│   ├── 01-architecture.md        架构决策记录（ADR，为什么这么设计）
│   ├── 02-design.md              Sentinel 设计（模块、接口、数据流）
│   ├── 03-tasks.md               分阶段任务清单（接棒 AI 的工作队列）
│   └── 04-handoff-guide.md       接棒指南（弱模型开发守则，必读）
└── human/                    ← 给用户本人看的（人的线）
    ├── 01-build-vs-buy.md        要不要自己造？（决策论证）
    ├── 02-financial-growth-path.md  财商提升路径（学习地图）
    └── 03-action-roadmap.md      实操路线图（12 个月，含期权线）
```

## 阅读顺序

**如果你是接棒的 AI**：按顺序读 `system/04-handoff-guide.md` → `00` → `01` → `02` → `03`，然后从 `03-tasks.md` 里按 Phase 顺序领任务。**不要跳过 04**，里面有铁律。

**如果你是用户本人**：读 `human/` 三篇即可，`system/00-research-summary.md` 可以当参考资料。

## 一句话总纲

> Sentinel = freqtrade 地基 + 三层自建薄壳（策略、统一看板、AI 分析层）。
> LLM 永远只做研究、复盘和否决，绝不直接下单。
> 资金流程铁律：回测 → dry-run 模拟 ≥ 4 周 → 小额实盘 ≥ 8 周 → 逐步加仓。

## 命名约定（给接棒 AI）

- 代码/容器/服务前缀：`sentinel-`（例：`sentinel-strategy`、`sentinel-ai`、`sentinel-dashboard`）
- 数据库表名沿用 `02-design.md` §2.4 不变；如需新增表建议 `sentinel_` 前缀（可选）
- 日志/告警中的产品自指用 **Sentinel**；口语化表述保留中文"系统"
