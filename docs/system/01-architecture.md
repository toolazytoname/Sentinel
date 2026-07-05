# 01 — 架构决策记录（ADR）

> 每条 ADR 记录一个已定的架构决策。接棒 AI **不得推翻这些决策**；若发现决策与现实冲突，停下来向用户报告，不要自行改道。

## ADR-001: 以 freqtrade 为执行底座，不自研交易引擎

**状态**: 已定
**决策**: 所有实盘/模拟下单、订单管理、交易所对接、基础风控由 freqtrade 承担。我们不写任何直接调用交易所下单 API 的代码。
**理由**: 交易引擎是量化系统中 bug 代价最高的部分（断线重连、部分成交、精度舍入、API 限频）。freqtrade 有 52k star、十年迭代、数千人实盘资金验证。自研引擎对两人以下团队是纯粹的负期望。
**后果**: 策略必须用 freqtrade 的 Strategy 接口写；受限于其单实例单策略模型（多策略 = 多容器实例）；GPL-3.0（个人自用无影响，不分发则无开源义务）。

## ADR-002: LLM 只有三种角色 — 研究、复盘、否决

**状态**: 已定（安全红线，最高优先级）
**决策**: LLM 组件的输出只允许三种用途：
1. 研究报告（新闻/市场摘要，供人阅读）
2. 复盘报告（决策 vs 实际收益的反思，供人阅读 + 注入下轮分析上下文）
3. 风控否决（对即将执行的信号投 VETO 票——可以阻止开仓，**永远不能发起开仓**）

**禁止**: LLM 输出直接或间接成为开仓信号；LLM 修改策略参数；LLM 调整仓位大小。
**理由**: LLM 决策不可复现（TradingAgents 官方承认），无法通过回测验证，违反"回测→模拟→实盘"根本原则。
**实现约束**: AI 服务与 freqtrade 之间的接口只允许两个方向：AI 读取 freqtrade 的 REST API（只读）；AI 向"否决表"写入 VETO 记录，策略在 `confirm_trade_entry` 回调里查表。物理上不存在"AI 下单"的代码路径。

## ADR-003: 部署形态 — Docker Compose 单机

**状态**: 已定
**决策**: 全系统用 docker-compose 编排，跑在一台 VPS（或家用小主机）上。组件：freqtrade（每策略一容器）、FreqUI、AI 服务（FastAPI）、看板（后期）、PostgreSQL（AI 服务用）、定时任务用 cron 容器或 APScheduler。
**理由**: KISS。个人系统不需要 k8s/消息队列/微服务。freqtrade 官方 Docker 支持成熟。
**后果**: 单点故障靠"交易所侧止损单"兜底（进程死了止损单还在交易所挂着）——这是选 freqtrade 的关键理由之一，必须始终开启 `stoploss_on_exchange`。

## ADR-004: 策略风格 — 中低频规则策略，禁止高频/做市

**状态**: 已定
**决策**: 策略限定在 1h/4h/1d K 线级别的规则策略（趋势跟踪、动量轮动、网格限定在震荡确认区间且带下沿止损）。不做 tick 级、不做做市、不做跨所套利。
**理由**: 用户是稳健型；中低频策略回测可信度高（滑点影响小）、可用 freqtrade 完整验证、对服务器和网络要求低。做市/高频对个人小资金不稳健。
**目标校准**: 年化 8-15%、最大回撤 <20%。回测里年化 >30% 的结果默认过拟合，需过 jesse 的 Monte Carlo 检验。

## ADR-005: 资金升级流程硬编码进系统

**状态**: 已定（安全红线）
**决策**: 每个策略必须走完状态机：`backtest → dry-run(≥28天) → live-small(≥56天, ≤总资金5%) → live-scaled(分3档爬坡)`。升级条件（在配置中定义，AI 服务定期核查并出报告）：
- dry-run→live-small：dry-run 盈亏与回测偏差在可解释范围，无重大执行错误
- live-small→scaled：实际最大回撤 < 回测最大回撤 × 1.5，且样本 ≥ 30 笔交易
- 任何阶段回撤超过回测最大回撤 × 1.5 → 自动降级回上一阶段（Protections + 人工确认）

**理由**: 用户明确要求的纪律流程。系统的核心价值就是把这个纪律自动化，防止人性把它跳过。

## ADR-006: 期权线不做自动化系统

**状态**: 已定（2026-07 时点决策，未来可复议）
**决策**: 美股期权（Wheel/Covered Call）不纳入本自动化系统，只提供：① 人工执行的 checklist 文档；② 后期可选的辅助计算工具（选 strike/delta 的计算器）。
**理由**: 2026-05 八部门方案后，富途/老虎进入清退期，唯一通道 IBKR 有政策不确定性。在通道本身可能消失的前提下投入自动化开发是负期望。参考 thetagang（AGPL-3.0）的设计思想即可，不集成。

## ADR-007: 技术栈

**状态**: 已定
| 层 | 选型 | 理由 |
|---|---|---|
| 执行引擎 | freqtrade (Python) | ADR-001 |
| AI 服务 | Python 3.11 + FastAPI + Pydantic | 与 freqtrade 同语言栈，接棒 AI 熟悉度最高 |
| LLM 接入 | OpenAI-compatible 抽象层（支持切换 provider/本地模型） | 控成本，参考 TradingAgents 的 deep/quick 双档设计 |
| 数据库 | freqtrade 自带 SQLite（交易）+ PostgreSQL（AI 服务：研报/复盘/否决表） | 各管各的，不共享库 |
| UI 阶段1 | FreqUI（现成） + Telegram | 零开发成本先跑起来 |
| UI 阶段2 | 自建看板：Next.js + Tailwind，只读聚合 freqtrade REST API + AI 服务 API | 薄壳，纯展示层 |
| 部署 | Docker Compose + VPS | ADR-003 |
| 回测交叉验证 | jesse（仅研究环境，不实盘） | Monte Carlo/显著性检验 |

## ADR-008: 许可证纪律

**状态**: 已定
- 可依赖/可抄：MIT（qlib, Vibe-Trading, vnpy 思想）、Apache-2.0（TradingAgents, hummingbot）、GPL-3.0（freqtrade——以独立进程使用，通过 REST API 交互，不链接其代码）
- 只看不抄不集成：AGPL-3.0（thetagang, optopsy）——看设计思想可以，不 vendor 代码
- 完全回避：FinceptTerminal（商业双许可条款有索赔风险）
