# Sentinel

> **A disciplined cryptocurrency quantitative trading system for the long-term investor.**
> 稳健型个人数字货币量化系统 —— 哨兵守纪律，复利自生长。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status: Design Phase](https://img.shields.io/badge/status-design--phase-blue.svg)]()
[![Code: Coming Soon](https://img.shields.io/badge/code-coming%20soon-lightgrey.svg)]()
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)]()

## What is Sentinel?

Sentinel 是一个面向**稳健型个人投资者**的加密货币量化交易系统。它的核心价值不是"赚得多"，而是**"用机器化的纪律防止你自己犯曾经犯过的错"** ——

- 回测完不够？**强制走完 dry-run 模拟 ≥28 天**
- dry-run 表现可以？**强制走完小额实盘 ≥56 天 + ≥30 笔交易**
- 想半夜手动干预？**系统在状态机里给你留了位置：冲动先记进日记，复盘时再决定**

7×24 无人值守的"躺着"交易，外加 AI 复盘、风控审计、研究摘要三件套。

## 设计原则

1. **纪律 > 策略聪明度**：策略年化 8-15%、最大回撤 <20% 的合理预期，远比"回测年化 100% 实盘归零"靠谱。
2. **LLM 不下单**：LLM 只能做研究助理、复盘反思、风控否决；**永远没有开仓权**。
3. **用现成的地基**：以 [freqtrade](https://github.com/freqtrade/freqtrade) 为执行引擎（52k★，十年迭代，实盘验证），自建薄壳只占 20%。
4. **流程硬编码**：资金升级状态机写入系统，机器替你守住你最容易被自己突破的纪律关卡。

## 当前状态

🚧 **设计阶段（Design Phase）** —— 本仓库目前**只包含设计文档**，代码尚未开始编写。

按 `docs/system/03-tasks.md` 的 Phase 0-4 推进：

- Phase 0：环境与骨架
- Phase 1：策略与验证流水线
- Phase 2：AI 服务（研究 / 复盘 / 否决）
- Phase 3：统一看板
- Phase 4：实盘升级（按状态机，不可压缩）

## 文档索引

📖 完整文档在 [`docs/`](docs/)，**强烈建议先读**：

- [`docs/README.md`](docs/README.md) — 文档导航
- [`docs/system/00-research-summary.md`](docs/system/00-research-summary.md) — 选型依据（为什么用 freqtrade，为什么 LLM 不能下单）
- [`docs/system/01-architecture.md`](docs/system/01-architecture.md) — 架构决策记录（ADR）
- [`docs/system/02-design.md`](docs/system/02-design.md) — 系统设计（模块、接口、数据流）
- [`docs/system/03-tasks.md`](docs/system/03-tasks.md) — 分阶段任务清单
- [`docs/system/04-handoff-guide.md`](docs/system/04-handoff-guide.md) — 接棒 AI 开发守则（铁律）
- [`docs/human/01-build-vs-buy.md`](docs/human/01-build-vs-buy.md) — 要不要自己造？
- [`docs/human/02-financial-growth-path.md`](docs/human/02-financial-growth-path.md) — 财商提升路径
- [`docs/human/03-action-roadmap.md`](docs/human/03-action-roadmap.md) — 12 个月实操路线

## 技术栈

| 层 | 选型 |
|---|---|
| 执行引擎 | freqtrade（GPL-3.0，外部进程集成） |
| 回测交叉验证 | jesse（仅研究环境） |
| AI 服务 | Python + FastAPI + PostgreSQL |
| LLM 接入 | OpenAI-compatible 抽象层（支持切换 provider/本地模型） |
| 看板（Phase 3） | Next.js + Tailwind |
| 部署 | Docker Compose + VPS |

## ⚠️ 免责声明

**Trading involves risk of substantial loss.** 本项目尚处于早期设计阶段，**未经充分测试前不要用于真实资金**。开源作者不对使用本项目产生的任何盈亏负责。

加密货币市场 7×24 高波动，请仅使用你能承受完全损失的资产参与。强烈建议从小额实盘开始，走完 dry-run → 小额实盘的完整流程后再考虑加仓。

## 许可证

本项目以 [MIT License](LICENSE) 开源。

> 注意：本项目调用 [freqtrade](https://github.com/freqtrade/freqtrade)（GPL-3.0）作为外部进程，符合 MIT 兼容性。如未来直接链接 freqtrade 源码，整仓许可证须升级为 GPL-3.0，详见 [`docs/system/01-architecture.md`](docs/system/01-architecture.md) ADR-008。

## 致谢

设计过程中参考了以下优秀开源项目（按其许可证条款使用）：

- [freqtrade](https://github.com/freqtrade/freqtrade) — GPL-3.0 — 执行引擎
- [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) — Apache-2.0 — 多角色 AI 决策编排思想
- [microsoft/qlib](https://github.com/microsoft/qlib) — MIT — 数据/因子研究基础设施
- [jesse-ai/jesse](https://github.com/jesse-ai/jesse) — MIT — 回测研究台
- [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) — MIT — 影子账户/授权门控设计