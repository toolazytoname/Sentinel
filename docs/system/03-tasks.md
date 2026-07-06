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

---

# Phase R — 架构审查改进项（2026-07-06 强模型 review 追加）

> **给接棒的弱模型看：本节每个任务都写清了「文件:行号」「哪里错了」「怎么改」「怎么验证」。**
> **三条铁律，违反就停下来别猜：**
> 1. 每改完一个任务，必须运行 `source .venv/bin/activate && python -m pytest -q`，**199 个测试必须全绿**才算完成。改动导致测试变红且你不确定为什么 → 停下，回滚，标 🧠 升级。
> 2. 行号可能因为前面的任务改动而漂移。**优先用「可搜索字符串」定位**，行号只是参考。
> 3. 不许改测试文件去将就实现（除非任务明确要求加测试）。修实现，不修测试。
>
> 执行顺序：先做 RA（正确性 Bug，会导致线上错误）→ 再 RB（架构缺陷）→ 再 RC（工程质量）→ 最后 RD（UI/UE）。

## RA — 正确性 Bug（会导致线上静默失效，最高优先级）

- [x] **RA.1** 修 `run_daily_research` 的 `datetime` NameError（每日高危事件告警从不发出）
  ✅ 完成于 2026-07-07，备注：一行 import 修复 + 新增告警路径测试，全量 200 passed（校验 subagent 独立复核通过）
  - **文件**: `ai-service/app/scheduler.py`
  - **定位**: 搜索字符串 `from datetime import timedelta, timezone`（约第 61 行，在 `run_daily_research` 函数体内）
  - **问题**: 该行只 import 了 `timedelta, timezone`，但下面第 75 行用了 `datetime.now(timezone.utc)`。`datetime` 这个名字在函数作用域和模块顶部都没 import → 运行时抛 `NameError`。因为整个 job 被 `safe_run()` 包着，异常被吞进日志，**结果是：每日研究 job 里「severity≥4 推 Telegram」这条告警路径永远崩溃，用户永远收不到高危告警，且表面上一切正常**。
  - **修复**: 把那一行改成同时 import `datetime`：
    ```python
    from datetime import datetime, timedelta, timezone
    ```
    （参考同文件 `run_weekly_rollup` 第 126 行，它就是这么写的、是对的。）
  - **加测试**（DoD 必须）: 在 `ai-service/tests/test_scheduler.py` 新增一个测试，构造一条 severity≥4 的 `ResearchNoteRow`，调用 `run_daily_research(fake_ingester, fake_notifier)`，断言 `fake_notifier.send_research_alerts` 被调用且入参含这条 note。这个测试在修复前必须失败（红），修复后变绿。
  - **验证**: `python -m pytest ai-service/tests/test_scheduler.py -q` 全绿。
  - **DoD**: 新测试存在且覆盖告警路径；199+1 个测试全绿。

- [ ] **RA.2** 修 `POST /reflection` 返回的 `id` 永远是 0
  - **文件**: `ai-service/app/main.py`
  - **定位**: 搜索字符串 `id=0,  # writer inserted but didn't return id`（约第 276 行，`submit_reflection` 函数内）
  - **问题**: 复盘写库成功后，接口把 `ReflectionResponse.id` 硬编码成 `0` 返回给调用方，调用方拿不到真实主键，无法后续引用这条复盘。
  - **修复**（分两步，和 RB.3 的 session 修复配合，先做 RB.3 再做本条更稳；若单独做本条按下面来）:
    1. 确认文件顶部已有 `from app.db.repository import get_reflection_by_trade_id`（`/trade-close` 里已在用，若只是函数内 import，就在函数内也 import 一次）。
    2. 在 `writer.record(ctx)` 成功之后、构造 `ReflectionResponse` 之前，加一行重新查库拿真实 id：
       ```python
       row = get_reflection_by_trade_id(db, ctx.trade_id, body.strategy)
       ```
    3. 把 `id=0,` 改成 `id=row.id if row else 0,`。
  - **验证**: `python -m pytest ai-service/tests/test_api.py -q`。若已有 `/reflection` 的测试，补断言 `resp.json()["id"] > 0`。
  - **DoD**: 接口返回真实 id；全测试绿。

## RB — 架构缺陷（设计意图 vs 实现不符，会让「安全网」形同虚设）

- [ ] **RB.1** 🧠 否决链路的 LLM 层在实盘里**永远不会生效**（超时不匹配 + 同步阻塞）
  - **涉及文件**: `strategies/veto_gate.py`、`ai-service/app/main.py` 的 `GET /veto`、`ai-service/app/llm/openai_compat.py`
  - **问题（读懂再动手）**:
    - 策略侧 `strategies/veto_gate.py` 第 17 行 `DEFAULT_TIMEOUT_S = 3`：`confirm_trade_entry` 调 `GET /veto` 只等 **3 秒**，超时就 fail-open 放行（这是对的，ADR-002）。
    - 但 `GET /veto`（main.py）是**同步**在请求里跑完整 `audit()`：规则层 + 调 **deep 档 LLM**（`openai_compat.py` 第 40 行 LLM 自己的 timeout 是 **30 秒**）。
    - 结果：deep LLM 几乎不可能在 3 秒内返回 → 策略侧每次都超时 → **每次都 fail-open PASS**。也就是说设计文档 §2.2 里「LLM 反方陈述」这层否决，**在实盘里从来没真正投过票**。只有规则 1（高危事件）能在 3 秒内否决。
    - 更深的问题：设计文档 §2.2 和 ADR-002 的原意是「策略查**否决表**（SELECT only）」——即 AI **异步**把 VETO 写进表，策略只做一次极快的读表。当前实现把 LLM 塞进了请求热路径，既慢又违背设计。
  - **修复方向（按设计文档回正，不是打补丁）**:
    - 把 `GET /veto` 改成**只读否决表 + 只跑规则层**，不在请求里调 LLM。目标响应时间 < 200ms。
    - LLM 反方陈述改为**异步预计算**：由 scheduler 或一个后台任务，对「当前有持仓意图的 pair」定期跑 `llm_veto`，把结果写进 `veto_records`；`GET /veto` 读最近一条有效记录（比如 15 分钟内）来决定 PASS/VETO。
    - 若短期内做不到异步改造，**至少**先把 `GET /veto` 改成只跑规则层（`check_rules`）、不调 LLM，避免热路径挂 LLM。LLM 否决作为独立后台能力后续补。
  - **这是架构级改动，标 🧠**：动手前先在本任务下写 3-5 行「我要怎么改」的方案，不确定就停下来问用户。**不要一边改一边猜**。
  - **验证**: 新增集成测试覆盖「否决表命中 → VETO」「表里无记录 → PASS」「LLM 后台写入 → 下次读表生效」；`GET /veto` 的测试断言响应里不再触发 LLM mock 被调用。
  - **DoD**: `GET /veto` 不在请求内调 LLM；全测试绿；本任务下留有方案说明。

- [ ] **RB.2** `GET /veto` 把规则 2、规则 3 写死成失效
  - **文件**: `ai-service/app/main.py`
  - **定位**: 搜索 `current_total_exposure_pct=0.0,  # strategy doesn't know the book`（约第 180-183 行）
  - **问题**: `GET /veto` 里把 `current_total_exposure_pct=0.0`、`max_exposure_pct=1.0`、`upcoming_event_window_minutes=0` 写死，等于**规则 2（敞口超限）和规则 3（重大事件窗口）永久失效**——设计文档 §2.2 的 4 项检查，实盘只剩 1 项（规则 1）活着。
  - **修复方向**:
    - 敞口（规则 2）: AI 服务应通过 freqtrade 只读 REST API 查当前总持仓（ADR-002 允许 AI→freqtrade 只读），算出真实 `current_total_exposure_pct` 再传入。若本期不接 freqtrade REST，就**在事件日历/配置里维护一个敞口上限**并说明规则 2 暂缺的原因（写进本任务注释），不要假装它生效。
    - 事件窗口（规则 3）: 需要一个「重大事件日历」（FOMC/CPI 等），可先做一个静态 JSON 配置文件 `ai-service/app/data/event_calendar.json`，`GET /veto` 读它算 `upcoming_event_window_minutes`。
  - **依赖**: 建议在 RB.1 做完（否决表就绪）后一起做，因为异步预计算天然能拿到更全的 context。
  - **DoD**: 规则 2、规则 3 要么真实生效、要么在代码注释和本文件里明确标注「暂缺 + 原因」，不留「写死成 0 假装启用」的状态；全测试绿。

- [ ] **RB.3** DB session 所有权混乱：借来的请求 session 被中途关闭
  ⚠️ BLOCKED（2026-07-07，标 🧠 待强模型）：机械替换 `lambda: db`→`get_session` 会让 `_persist` 用**模块级 engine**，与测试套件经 `get_db` 依赖覆盖注入的 engine 不是同一个 → 写入库与读取库分离，`test_trade_close_webhook.py` 4 个测试变红（`reflection_id=None`、`recorded` 变 `skipped`）。已回退，无悬空。**正确修法**：统一 writer 的 session engine 与测试覆盖 engine（如让 `_persist` 复用请求 session 但不用 `with` 关闭它，或测试同时覆盖 `get_session`）——需重构 session 接线，非机械改动。RA.2 改为独立做（不依赖本任务，见 RA.2）。
  - **文件**: `ai-service/app/main.py`（2 处）+ `ai-service/app/modules/reflection.py`
  - **定位**: main.py 搜索 `ReflectionWriter(extractor, lambda: db)`（约第 266 行和第 421 行，两处都要改）
  - **问题**: 这两处把请求级 session `db` 用 `lambda: db` 传进 `ReflectionWriter`。而 `reflection.py` 的 `_persist` 里写的是 `with self._session_factory() as session:` —— 对 SQLAlchemy 的 `Session` 用 `with` 会在退出时 **`close()` 掉这个 session**。于是 `writer.record()` 一跑完，请求的 `db` 就被关了；`/trade-close` 第 431 行还拿这个已关闭的 `db` 去 `get_reflection_by_trade_id` 再查一次（靠 SQLAlchemy「关闭后自动开新事务」的隐式行为侥幸能跑，但语义是错的、脆弱）。同时 `get_db()` 的 `finally` 又会 close 一次（双重关闭）。
  - **修复**（一处一行，简单且能顺带修好 RA.2 的 session 问题）:
    - 确认 main.py 顶部已 import：`from app.db import get_session`（第 33 行已有，确认即可）。
    - 把两处 `ReflectionWriter(extractor, lambda: db)` 都改成 `ReflectionWriter(extractor, get_session)`。这样 `_persist` 开/关**自己的新 session**，请求的 `db` 全程有效，后面的重新查库也安全。
  - **为什么安全**: `insert_reflection` 内部 `commit()` 了，新 session 在同一个 engine 上能读到已提交的行。
  - **验证**: `python -m pytest ai-service/tests/test_api.py ai-service/tests/test_trade_close_webhook.py ai-service/tests/test_reflection.py -q` 全绿。若变红，说明测试是用 `lambda: db` 注入的特殊 session，**停下来标 🧠**，别硬改。
  - **DoD**: 两处改完；相关测试全绿。

- [ ] **RB.4** SQLite 无 migration，加了列但老库不会自动升级（重部署会炸）
  - **文件**: `ai-service/app/db/models.py`
  - **问题**: P2.1 的 DoD 写的是「用 Alembic 管 migration」，但实现用的是 `Base.metadata.create_all()`（`models.py` 第 114 行）。`create_all` **只建不改**——`StrategyStageRow` 已经比设计文档 §2.4 多了两列（`trade_count`、`max_observed_drawdown_pct`，第 94-95 行）。**已经有数据的旧 sentinel.db 在新代码下不会自动加这两列，查询会报 `no such column`**。docker volume `ai_data` 是持久化的，重部署时这个坑必踩。
  - **修复方向**（二选一，KISS 优先选 1）:
    1. **接入 Alembic**（符合原 DoD）: `pip install alembic`，`alembic init`，生成初始 migration，容器启动脚本里跑 `alembic upgrade head` 替代 `create_all`。改 `ai-service/scripts/start.sh` 和 `requirements.txt`。
    2. **最小兜底**（若本期不想上 Alembic）: 写一个 `ensure_schema()` 幂等函数，启动时检查缺列并 `ALTER TABLE ... ADD COLUMN`，并在本任务注释里写明「临时方案，策略数增长前必须换 Alembic」。
  - **标 🧠**（涉及部署链路）。动手前写方案。
  - **DoD**: 从一个「缺新列的旧库」启动服务不再报错；方案写清；全测试绿。

## RC — 工程质量（不致命但影响可维护性 / 违反项目规约）

- [ ] **RC.1** 消除 `StrategyBase` / `veto_gate` 双副本漂移风险
  - **背景**: `strategies/base.py` 与 `deploy/user_data/strategies/base.py` 是**手工维护的两份副本**（freqtrade 容器只挂载 `deploy/user_data/strategies/`，读不到仓库根的 `strategies/` 包），现已确认内容漂移。`check_veto` 逻辑也被内联复制进了 deploy 版。手工同步迟早出事。
  - **修复方向**（KISS，二选一）:
    1. **加一个漂移守卫测试**（最省事，推荐先做）: 在 `strategies/tests/` 新增 `test_deploy_sync.py`，读取两份文件的**核心逻辑部分**（如 `confirm_trade_entry`、`check_veto` 的函数体），断言它们等价；不一致就测试红。这样至少 CI/pytest 会拦住漂移。
    2. **构建期生成**: 写个脚本 `deploy/sync_strategies.sh`，从 `strategies/` 生成 deploy 副本，禁止手改 deploy 版（文件头加「AUTO-GENERATED, DO NOT EDIT」）。
  - **验证**: 故意改动一份、跑测试应变红；两份一致时绿。
  - **DoD**: 存在自动机制（测试或生成脚本）能拦截漂移；`python -m pytest strategies -q` 绿。
  - 关联记忆: `strategy-base-duplication`、`veto-endpoint-contract`。

- [ ] **RC.2** 接入测试覆盖率门槛（testing.md 要求 ≥80%，当前没测量）
  - **文件**: `pytest.ini`、`ai-service/requirements.txt`
  - **问题**: `pytest.ini` 的 `addopts` 只有 `-v --tb=short`，没有 `--cov`，覆盖率从未被测量或强制。
  - **修复**:
    1. `requirements.txt` 加一行 `pytest-cov`。
    2. `pytest.ini` 的 `addopts` 改成：
       ```
       addopts = -v --tb=short --cov=ai-service/app --cov=strategies --cov-report=term-missing --cov-fail-under=80
       ```
    3. 跑一次看真实覆盖率。**若不足 80%，不要把阈值调低**——补测试（RA.1、RB 系列已经会加一些）。补不上来的模块标 🧠。
  - **验证**: `python -m pytest -q` 末尾打印覆盖率且 ≥80% 才通过。
  - **DoD**: 覆盖率门槛生效且达标。

- [ ] **RC.3** 消除策略里的逐行 Python 循环（反模式 + 潜在 lookahead 风险）
  - **文件**: `strategies/s1_trend_follow/strategy.py`（`populate_entry_trend` 约第 158 行、`populate_exit_trend` 约第 170 行）；`s2_momentum_rotation` 同类问题一并查。
  - **问题**: 用 `for i in range(len(dataframe))` + `dataframe.iat[...]` 逐行写信号列，是 pandas 反模式：慢，且逐行改易引入未来函数（lookahead）。设计文档要求策略过 `lookahead-analysis`。
  - **修复方向**: 用**向量化**布尔运算生成整列信号。例如入场：
    ```python
    params = StrategyParams(adx_entry_threshold=float(self.adx_entry.value))
    cond = df["golden_cross"] & (df["ema_fast"] > df["ema_slow"]) & (df["adx"] > params.adx_entry_threshold)
    df["enter_long"] = cond.fillna(False).astype(int)
    ```
    出场同理，用 `|` 组合 death_cross 与 adx 崩塌两个条件。
  - **注意**: 纯函数 `entry_signal`/`exit_signal`（单元测试在用）**保留不动**，只改 freqtrade adapter 里的批量填列部分，保证向量化结果和逐行结果一致。
  - **验证**: `python -m pytest strategies -q` 全绿（现有策略测试就是校验信号正确性的）。
  - **DoD**: 两个策略的 populate 无 Python 逐行循环；测试绿。

- [ ] **RC.4** 更新过期的 README（当前谎称「设计阶段，无代码」）
  - **文件**: `README.md`
  - **问题**: 第 8 行 badge `Code: Coming Soon`、第 30 行「本仓库目前**只包含设计文档**，代码尚未开始编写」——但 Phase 0/1/2 已大量落地（freqtrade 骨架、S1/S2 策略、AI 服务全套、199 个测试）。误导任何新接棒的人/AI。
  - **修复**:
    - badge 改成 `status-phase--2--in--progress` 之类真实状态。
    - 「当前状态」段落改成：Phase 0/1/2 已实现（列已完成项），Phase 3（看板）/Phase 4（实盘）未开始。
    - 补一段「如何本地跑起来」：`cd deploy && cp .env.example .env`（填密钥）→ `docker compose up -d` → FreqUI `http://localhost:8080` / AI 服务 `http://localhost:8000/healthz`；本地测试 `source .venv/bin/activate && python -m pytest -q`。
  - **DoD**: README 状态与实际一致，含可照做的启动步骤。

- [ ] **RC.5** 清理死 import 与小噪音
  - **文件**: `ai-service/app/main.py`
  - **问题**: 顶部 import 了 `get_research_extractor`、`get_strategy_stage`、`Optional`、`ResearchExtractor` 等但函数体未用（`submit_research_note` 根本不调 LLM）。属噪音，易误导。
  - **修复**: 逐个确认未被引用后删除。**删一个跑一次 `python -m pytest ai-service/tests/test_api.py -q`**，防止删错。
  - **DoD**: 无未使用 import；全测试绿。

## RD — UI / UE：给用户一个「不用敲终端」的操作面（用户明确需求）

> ⏭️ **SKIPPED（2026-07-07，用户决定）**：UI 界面改由用户用 **open design** 自行设计，本组 RD.1/RD.2/RD.3 暂不由自治流实现。
> 若 open design 产出的前端需要后端支撑（如只读聚合接口、`POST /strategy/{name}/approve` 升级批准端点），届时再单独立任务。**注意红线仍生效**：批准端点只改 strategy_stages 阶段标记，绝不自动动钱/改交易配置（ADR-002/005）。


- [ ] **RD.1** 在 ai-service 内置一个只读运维面板（单页，零构建）
  - **做什么**: 给 FastAPI 加一个 `GET /`（或 `/dashboard`）返回一个**单文件 HTML 页**（用 `fastapi.responses.HTMLResponse` + 内联 JS/CSS，或 Jinja2 模板；不引 React、不加构建步骤）。页面用浏览器 `fetch` 调现有接口，展示 4 块：
    1. **策略阶段**: 调 `GET /strategy/{name}/stage`（先做一个 `GET /strategies` 列表接口返回所有策略名，若没有就先加，逻辑复用 `all_strategy_stages`）。展示 stage、天数、交易数、回撤、promote/hold/demote 建议。
    2. **最近否决**: 复用 `recent_vetoes`（repository 已有），加一个 `GET /vetoes?hours=24` 接口，页面列表展示。
    3. **最近复盘**: 加 `GET /reflections?strategy=&limit=20`（复用 `reflections_for_strategy`），列表展示 what_worked / lesson。
    4. **研究笔记**: 加 `GET /research?limit=20`（复用查询），时间线展示。
  - **约束**: **只读**。所有写操作（升级批准）放 RD.2，且要二次确认。
  - **文件**: 新增 `ai-service/app/web.py`（放 HTML 与只读聚合路由），在 `main.py` 里 `app.include_router(...)` 挂上。HTML 可先内联成字符串常量，够用就行。
  - **验证**: 起服务 `uvicorn app.main:app`，浏览器开 `http://localhost:8000/` 能看到四块数据；新增接口各配一个 `test_web.py` 的最小测试（TestClient 断言 200 + 关键字段）。
  - **DoD**: 一个 URL 就能在浏览器看到全系统状态，无需 curl；新接口有测试；全测试绿。

- [ ] **RD.2** 面板上的「一键批准升级/降级」（唯一的写操作，带确认）
  - **背景**: 现在升级要人工 curl `POST /strategy/check` 看建议，再手工改配置/调 `apply_recommendation`。给用户一个按钮。
  - **做什么**:
    - 加接口 `POST /strategy/{name}/approve`，body `{recommendation, approved_by}`；内部先 `check_stage_upgrade` 复算一遍（**不能信前端传来的建议，必须服务端重新核对**，防止误点/过期建议），一致才调 `apply_recommendation(session, report, approved_by=...)`。
    - **红线**: 严格遵守 ADR-002/ADR-005——系统**只改 strategy_stages 表的阶段标记**，**绝不自动动钱、不自动改 freqtrade 配置**。批准后页面提示用户「请手动把 live 配置的资金上调到对应档」。这一点要在按钮旁的文案里写死。
    - 前端按钮点击弹 `confirm()` 二次确认，附带展示服务端复算出的 rationale。
  - **验证**: `test_web.py` 加：建议是 promote 时 approve 成功、阶段前进；建议是 hold 时 approve 被拒（返回 4xx，不动阶段）；前端传的建议与服务端复算不符时被拒。
  - **DoD**: 面板能一键批准且只在服务端复核通过时生效；不触碰资金/交易配置；测试覆盖三条路径；全测试绿。

- [ ] **RD.3** 一条命令拉起 + 一页「怎么用」文档（降低操作门槛）
  - **做什么**:
    - 在 `deploy/` 补/完善 `start.sh` 或加 `Makefile`，让「起全栈 / 停 / 看日志 / 跑测试」各是一条命令（如 `make up` / `make down` / `make logs` / `make test`）。
    - 写 `docs/human/04-daily-operation.md`：用**非技术语言 + 截图位**说明日常三件事——① 打开哪个网址看什么（FreqUI 8080 看交易、AI 面板 8000 看纪律状态）；② 收到 Telegram 告警怎么办；③ 收到「可升级」建议后点哪个按钮 + 之后手动做什么。
  - **DoD**: 用户照 `04-daily-operation.md` 能在不碰代码/不敲复杂命令的前提下完成日常监控与升级批准。

---

## 附：本次 review 未发现的问题（给弱模型定心，别去「修」这些）

- **密钥卫生 OK**: `deploy/.env` **未**进 git（`.gitignore` 生效），只有 `.env.example` 在版本库里。**不要**去把 `.env` 加进 git。
- **freqtrade 强制风控项 OK**: `config.template.json` 里 `stoploss_on_exchange`、`MaxDrawdown`/`StoplossGuard`/`CooldownPeriod` 三件套、`max_open_trades:3` 均在（文件是 JSONC 带 `//` 注释，freqtrade 支持，别当成坏 JSON 去「修」）。
- **fail-open 语义 OK**: `strategies/veto_gate.py` 的「网络/超时/坏响应一律放行」是 ADR-002 要求的正确行为，**不要**改成 fail-closed。
- **199 个测试当前全绿**: 任何改动的基线就是它。改完必须仍全绿。
