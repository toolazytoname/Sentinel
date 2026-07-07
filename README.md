# Sentinel

> **A disciplined cryptocurrency quantitative trading system for the long-term investor.**
> 稳健型个人数字货币量化系统 —— 哨兵守纪律，复利自生长。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status: Phase 2 in progress](https://img.shields.io/badge/status-phase--2--in--progress-blue.svg)](docs/system/03-tasks.md)
[![Code: Phase 0/1/2 implemented](https://img.shields.io/badge/code-phase%200%2F1%2F2%20implemented-brightgreen.svg)]()
[![Tests: 268 passed](https://img.shields.io/badge/tests-268%20passed-success.svg)]()
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

🚧 **Phase 0 / 1 / 2 已落地（268 passed, 2 skipped），Phase 3 看板 / Phase 4 实盘未开始。**

按 `docs/system/03-tasks.md` 的 Phase 0-4 推进：

- ✅ **Phase 0**：环境与骨架 — docker-compose 起 freqtrade dry-run + FreqUI，Telegram 通知，`config.template.json` 含 Protections 三件套 + `stoploss_on_exchange`
- ✅ **Phase 1**：策略与验证流水线 — S1 趋势跟踪 + S2 动量轮动，`StrategyBase` 接入否决表，hyperopt + walk-forward 划分就绪
- ✅ **Phase 2**：AI 服务 — FastAPI + SQLite + 四张表（含 LLM token 落库）+ LLM 抽象层（OpenAI-compatible，含 dev 假 key / prod fail-fast）+ 研究模块（CoinGecko 事件源）+ 异步 LLM 否决预计算 + 复盘模块 + 升级核查模块
- 🚧 **Phase R / R2**：架构审查改进项（RB / RC / RS）— 当前进度见 `03-tasks.md` Phase R 节
- ⏭️ **Phase 3**：统一看板（用户用 open design 自理）
- ⏸️ **Phase 4**：实盘升级（按 ADR-005 状态机，时间由数据决定，不可压缩）

> 接棒开发请先读 [`docs/system/04-handoff-guide.md`](docs/system/04-handoff-guide.md) 铁律与 [`docs/system/03-tasks.md`](docs/system/03-tasks.md) 任务队列。

## 快速开始（本地开发）

```bash
# 1. 准备环境变量（本地真密钥不要 commit）
cd deploy
cp .env.example .env
$EDITOR .env   # 填 AGNES_API_KEY / OPENAI_API_KEY / TELEGRAM_BOT_TOKEN 等

# 2. 起全栈（freqtrade dry-run + ai-service，host 网络模式）
docker compose up -d

# 3. 验证两个服务在线
curl -fsS http://localhost:8080/api/v1/ping        # freqtrade FreqUI 后端
curl -fsS http://127.0.0.1:8000/healthz             # AI 服务健康检查
# 浏览器看 FreqUI：http://localhost:8080

# 4. 本地跑测试（不进 docker）
cd ..
source .venv/bin/activate
python -m pytest -q          # 应输出 268 passed, 2 skipped
```

> ⚠️ AI 服务默认绑 `127.0.0.1:8000`（仅本机），靠 host 网络隔离；如需对外暴露请配 `SENTINEL_API_TOKEN` + VPS 防火墙（见 `deploy/RUNBOOK.md`）。

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

**Trading involves risk of substantial loss.** 本项目已完成 Phase 0/1/2（freqtrade dry-run + AI 服务骨架），但**实盘前必须走完 ADR-005 状态机（dry-run ≥28 天 → 小额实盘 ≥56 天 + ≥30 笔交易）**——不要直接进入真金白银阶段。开源作者不对使用本项目产生的任何盈亏负责。

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