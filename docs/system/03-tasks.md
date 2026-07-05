# 03 — 分阶段任务清单

> 接棒 AI 的工作队列。按 Phase 顺序执行，Phase 内按编号执行。
> 每个任务标注了验收标准（DoD）。完成一个勾一个，不要跳跃。
> 标 🧠 的任务遇到困难时建议升级回强模型处理。

## Phase 0 — 环境与骨架（预计 1-2 天）

- [ ] **P0.1** 初始化 git 仓库，建目录结构：
  ```
  finance/
  ├── docs/            (已有)
  ├── deploy/          docker-compose.yml + 配置模板
  ├── strategies/      freqtrade 策略
  ├── ai-service/      FastAPI 服务
  ├── dashboard/       (Phase 3 再建)
  └── research/        jesse 回测环境 + notebook
  ```
  DoD: git init 完成，.gitignore 覆盖密钥/数据文件（`*.env`, `user_data/`, `*.sqlite`）
- [ ] **P0.2** docker-compose 起 freqtrade dry-run + FreqUI，用官方示例策略跑通
  DoD: 浏览器能打开 FreqUI 看到 dry-run 交易；参考 https://www.freqtrade.io/en/stable/docker_quickstart/
- [ ] **P0.3** 配置 Telegram bot，能收到 freqtrade 通知、能用 /status 查询
  DoD: 手机收到 dry-run 开仓通知
- [ ] **P0.4** 编写 `deploy/config.template.json`，包含 02-design.md §2.1 的所有强制项
  DoD: Protections 三件套 + stoploss_on_exchange 均在模板中，有注释说明每项为什么存在

## Phase 1 — 策略与验证流水线（预计 1-2 周）

- [ ] **P1.1** 下载历史数据：BTC/ETH/市值前15现货，1d+4h，2020 至今
  DoD: `freqtrade download-data` 完成，数据完整性抽查无缺口
- [ ] **P1.2** 🧠 实现 S1 趋势跟踪策略（见 02-design.md §3）
  DoD: 回测 2021-2025 跑通；**必须包含 2022 熊市**；lookahead-analysis 通过；指标计算函数有单测
- [ ] **P1.3** 🧠 实现 S2 动量轮动策略
  DoD: 同上
- [ ] **P1.4** 超参优化 + walk-forward：hyperopt 只在 2020-2023 训练段跑，2024-2026 作为留出段验证
  DoD: 留出段年化为正且回撤 <25%；训练段和留出段表现差距有书面解释
- [ ] **P1.5** 🧠 jesse 交叉验证：把 S1/S2 逻辑在 jesse 复现，跑 Monte Carlo
  DoD: research/ 下有 notebook 记录结果；若两框架回测结论矛盾，停下来报告用户
- [ ] **P1.6** S1、S2 进入 dry-run 阶段，登记 strategy_stages（此时可先用一个 markdown 文件人工记录，AI 服务还没建）
  DoD: 两策略 dry-run 容器 7x24 运行，Telegram 心跳正常
- [ ] **P1.7** 编写 `StrategyBase` 基类（confirm_trade_entry 查否决表的骨架，AI 服务未上线前默认 PASS）
  DoD: 单测覆盖超时降级路径（AI 服务不可达 → 放行 + 告警日志）

## Phase 2 — AI 服务（预计 2-3 周，与 dry-run 观察期并行）

- [ ] **P2.1** FastAPI 骨架 + PostgreSQL + 02-design.md §2.4 的四张表（用 Alembic 管 migration）
  DoD: docker-compose 集成，健康检查端点，pytest 骨架，覆盖率报告接入
- [ ] **P2.2** LLM 抽象层：OpenAI-compatible 客户端，deep/quick 双档配置，带重试/超时/成本记录
  DoD: 单测（mock LLM）；每次调用的 token 消耗落库可查
- [ ] **P2.3** 研究模块：接 2-3 个免费源（CoinGecko API、交易所公告 RSS），每日定时摘要入库
  DoD: research_notes 每天有数据；severity≥4 推 Telegram；LLM 输出用 Pydantic schema 校验，校验失败重试
- [ ] **P2.4** 🧠 风控审计模块：规则检查 4 项 + LLM 反方陈述，暴露 `GET /veto?strategy=&pair=` 接口
  DoD: 集成测试覆盖：规则触发 VETO / LLM 触发 VETO / 服务超时策略端放行 三条路径；StrategyBase 接入真实接口
- [ ] **P2.5** 复盘模块：平仓 webhook 触发单笔复盘，周日定时生成周报
  DoD: dry-run 的真实平仓能触发复盘入库 + Telegram 周报收到
- [ ] **P2.6** 升级核查模块：每日核查 ADR-005 状态机条件，出报告
  DoD: strategy_stages 表接管 P1.6 的人工记录；升级/降级建议推 Telegram

## Phase 3 — 看板（预计 1-2 周）

- [ ] **P3.1** Next.js 骨架 + 只读 API 聚合层（freqtrade REST + AI 服务）
  DoD: 本地跑通，无任何写操作端点
- [ ] **P3.2** 总览页（净值曲线、回撤、阶段状态机可视化）
  DoD: 数据与 FreqUI 一致
- [ ] **P3.3** 交易页（交易列表联复盘）、研究页（notes 时间线）、风控页（veto + protections 历史）
  DoD: 四页齐全，移动端可看
- [ ] **P3.4** 部署收尾：VPS 部署文档、备份脚本、密钥轮换手册
  DoD: `deploy/RUNBOOK.md` 完成，按文档从零部署一遍成功

## Phase 4 — 实盘升级（按 ADR-005 状态机，时间由数据决定，不可压缩）

- [ ] **P4.1** dry-run ≥28 天后：对照回测偏差报告，用户确认 → S1 小额实盘（≤总资金 5%）
- [ ] **P4.2** live-small ≥56 天且 ≥30 笔交易后：升级核查报告 → 用户确认 → 第一档加仓
- [ ] **P4.3** 每季度：全系统复盘（AI 周报汇总 + 参数是否漂移 + 是否需要下架策略）

## 长期 Backlog（YAGNI，有真实需求再做）

- qlib PIT 数据库 + 因子研究流水线（当策略数 >5 时）
- FinGPT 本地情绪因子（当研究模块证明新闻信号有价值时）
- nautilus_trader 迁移评估（当需要 tick 级时）
- Deribit BTC 备兑模块（当合规通道明确时）
