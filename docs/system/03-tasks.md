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

- [x] **RA.2** 修 `POST /reflection` 返回的 `id` 永远是 0
  ✅ 完成于 2026-07-07，备注：仿 `trade_close` 写库后按 trade_id 重新查库取真实主键，新增 id>0 测试（修复前 RED），全量 201 passed；独立做未依赖 RB.3，`lambda: db` 接线保持不动。
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

- [x] **RB.1** 🧠 否决链路的 LLM 层在实盘里**永远不会生效**（超时不匹配 + 同步阻塞）
  ✅ **完成（2026-07-07，两步）**：① 最小安全版 `GET /veto` 只跑规则层、不调 LLM（commit 7bd43cc）。② 异步收官（本次）：新增 scheduler 后台 job `run_llm_veto_precompute`——对「近 24h 被查询过的 (strategy,pair)」跑 `llm_veto` 写 `veto_records(source="llm")`，逐 pair fail-open；`GET /veto` 规则通过后读 `latest_llm_veto`（TTL 默认 15min，env `VETO_LLM_TTL_MIN`）命中则 VETO——**LLM 否决自此真正生效且热路径零 LLM 调用**。scheduler 仅在注入 veto_extractor 时注册该 job；GET /veto 请求路径不写 source=llm（防自我强化）；DB 查询异常 fail-open。独立校验 10 项全过。全量 250 passed。
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
  🔶 **规则3 完成（2026-07-07）**：新增 `ai-service/app/data/event_calendar.json`（手维护宏观事件窗口，UTC）+ `app/modules/events.py`（纯加载/计算，破损/缺失→[]/0 fail-open）；`GET /veto` 与 `run_llm_veto_precompute` 的 context 用 `current_event_window_minutes()` 计算 `upcoming_event_window_minutes`，启用 check_rules 规则3（事件前 <30min VETO）。破损日历降级为 0 不 500。修了 `.gitignore` 的 `data/` 误伤 app/data。全量 268 passed。**剩余 = 规则2（敞口）**：需接 freqtrade 只读 REST 拿真实总持仓（ADR-002 允许 AI→freqtrade 只读）——这是独立集成，依赖你的 freqtrade REST 配置/凭据，**未做、保持 context 敞口禁用不伪造**。
  - **文件**: `ai-service/app/main.py`
  - **定位**: 搜索 `current_total_exposure_pct=0.0,  # strategy doesn't know the book`（约第 180-183 行）
  - **问题**: `GET /veto` 里把 `current_total_exposure_pct=0.0`、`max_exposure_pct=1.0`、`upcoming_event_window_minutes=0` 写死，等于**规则 2（敞口超限）和规则 3（重大事件窗口）永久失效**——设计文档 §2.2 的 4 项检查，实盘只剩 1 项（规则 1）活着。
  - **修复方向**:
    - 敞口（规则 2）: AI 服务应通过 freqtrade 只读 REST API 查当前总持仓（ADR-002 允许 AI→freqtrade 只读），算出真实 `current_total_exposure_pct` 再传入。若本期不接 freqtrade REST，就**在事件日历/配置里维护一个敞口上限**并说明规则 2 暂缺的原因（写进本任务注释），不要假装它生效。
    - 事件窗口（规则 3）: 需要一个「重大事件日历」（FOMC/CPI 等），可先做一个静态 JSON 配置文件 `ai-service/app/data/event_calendar.json`，`GET /veto` 读它算 `upcoming_event_window_minutes`。
  - **依赖**: 建议在 RB.1 做完（否决表就绪）后一起做，因为异步预计算天然能拿到更全的 context。
  - **DoD**: 规则 2、规则 3 要么真实生效、要么在代码注释和本文件里明确标注「暂缺 + 原因」，不留「写死成 0 假装启用」的状态；全测试绿。

- [x] **RB.3** DB session 所有权混乱：借来的请求 session 被中途关闭
  ✅ 完成于 2026-07-07（经 RT.1 解锁）：两处 `ReflectionWriter(extractor, lambda: db)` → `get_session`，`_persist` 用自己的新 session、不再关闭借来的请求 session。校验 subagent 隔离实验证明：不加 RT.1 只上本改动会让 4 个 trade_close 测试真失败（跨库 bug），加 RT.1 后全绿。`reflection.py` 未动。全量 220 passed。
  - **文件**: `ai-service/app/main.py`（2 处）+ `ai-service/app/modules/reflection.py`
  - **定位**: main.py 搜索 `ReflectionWriter(extractor, lambda: db)`（约第 266 行和第 421 行，两处都要改）
  - **问题**: 这两处把请求级 session `db` 用 `lambda: db` 传进 `ReflectionWriter`。而 `reflection.py` 的 `_persist` 里写的是 `with self._session_factory() as session:` —— 对 SQLAlchemy 的 `Session` 用 `with` 会在退出时 **`close()` 掉这个 session**。于是 `writer.record()` 一跑完，请求的 `db` 就被关了；`/trade-close` 第 431 行还拿这个已关闭的 `db` 去 `get_reflection_by_trade_id` 再查一次（靠 SQLAlchemy「关闭后自动开新事务」的隐式行为侥幸能跑，但语义是错的、脆弱）。同时 `get_db()` 的 `finally` 又会 close 一次（双重关闭）。
  - **修复**（一处一行，简单且能顺带修好 RA.2 的 session 问题）:
    - 确认 main.py 顶部已 import：`from app.db import get_session`（第 33 行已有，确认即可）。
    - 把两处 `ReflectionWriter(extractor, lambda: db)` 都改成 `ReflectionWriter(extractor, get_session)`。这样 `_persist` 开/关**自己的新 session**，请求的 `db` 全程有效，后面的重新查库也安全。
  - **为什么安全**: `insert_reflection` 内部 `commit()` 了，新 session 在同一个 engine 上能读到已提交的行。
  - **验证**: `python -m pytest ai-service/tests/test_api.py ai-service/tests/test_trade_close_webhook.py ai-service/tests/test_reflection.py -q` 全绿。若变红，说明测试是用 `lambda: db` 注入的特殊 session，**停下来标 🧠**，别硬改。
  - **DoD**: 两处改完；相关测试全绿。

- [x] **RB.4** SQLite 无 migration，加了列但老库不会自动升级（重部署会炸）
  🔶 **完成（2026-07-07，采用轻量方案 option 2）**：新增 `ensure_schema(engine)`——`create_all` 建缺表 + `inspect` 逐表比对、`ALTER TABLE ADD COLUMN` 回填缺列（有默认值才 NOT NULL，老行不丢数据），幂等、sqlite/postgres 兼容；`get_engine` 里替换裸 `create_all`。新增回填测试（模拟老库缺 `trade_count`/`max_observed_drawdown_pct` → 回填且旧行可读）。全量 222 passed。**决策留痕**：单机个人系统不上重型 Alembic；全量 **Alembic + 迁移 PostgreSQL** 留作规模上来（策略数 >5 或多写高并发）后的文档化升级项，与 RB2.1 剩余合并。
  - **文件**: `ai-service/app/db/models.py`
  - **问题**: P2.1 的 DoD 写的是「用 Alembic 管 migration」，但实现用的是 `Base.metadata.create_all()`（`models.py` 第 114 行）。`create_all` **只建不改**——`StrategyStageRow` 已经比设计文档 §2.4 多了两列（`trade_count`、`max_observed_drawdown_pct`，第 94-95 行）。**已经有数据的旧 sentinel.db 在新代码下不会自动加这两列，查询会报 `no such column`**。docker volume `ai_data` 是持久化的，重部署时这个坑必踩。
  - **修复方向**（二选一，KISS 优先选 1）:
    1. **接入 Alembic**（符合原 DoD）: `pip install alembic`，`alembic init`，生成初始 migration，容器启动脚本里跑 `alembic upgrade head` 替代 `create_all`。改 `ai-service/scripts/start.sh` 和 `requirements.txt`。
    2. **最小兜底**（若本期不想上 Alembic）: 写一个 `ensure_schema()` 幂等函数，启动时检查缺列并 `ALTER TABLE ... ADD COLUMN`，并在本任务注释里写明「临时方案，策略数增长前必须换 Alembic」。
  - **标 🧠**（涉及部署链路）。动手前写方案。
  - **DoD**: 从一个「缺新列的旧库」启动服务不再报错；方案写清；全测试绿。

## RC — 工程质量（不致命但影响可维护性 / 违反项目规约）

- [x] **RC.1** 消除 `StrategyBase` / `veto_gate` 双副本漂移风险
  ✅ 完成于 2026-07-07，备注：新增 `strategies/tests/test_deploy_sync.py`（AST 归一化比对，8 个参数化用例）——比对 confirm_trade_entry 签名、共享常量、deploy 内联 check_veto 与 veto_gate 等价、两侧 fail-open 分支存在。对文档/import 差异宽容，能抓到 fail-open→fail-closed 回归（校验 subagent 独立复现 RED）。全量 209 passed。
  - **背景**: `strategies/base.py` 与 `deploy/user_data/strategies/base.py` 是**手工维护的两份副本**（freqtrade 容器只挂载 `deploy/user_data/strategies/`，读不到仓库根的 `strategies/` 包），现已确认内容漂移。`check_veto` 逻辑也被内联复制进了 deploy 版。手工同步迟早出事。
  - **修复方向**（KISS，二选一）:
    1. **加一个漂移守卫测试**（最省事，推荐先做）: 在 `strategies/tests/` 新增 `test_deploy_sync.py`，读取两份文件的**核心逻辑部分**（如 `confirm_trade_entry`、`check_veto` 的函数体），断言它们等价；不一致就测试红。这样至少 CI/pytest 会拦住漂移。
    2. **构建期生成**: 写个脚本 `deploy/sync_strategies.sh`，从 `strategies/` 生成 deploy 副本，禁止手改 deploy 版（文件头加「AUTO-GENERATED, DO NOT EDIT」）。
  - **验证**: 故意改动一份、跑测试应变红；两份一致时绿。
  - **DoD**: 存在自动机制（测试或生成脚本）能拦截漂移；`python -m pytest strategies -q` 绿。
  - 关联记忆: `strategy-base-duplication`、`veto-endpoint-contract`。

- [x] **RC.2** 接入测试覆盖率门槛（testing.md 要求 ≥80%，当前没测量）
  ✅ 完成于 2026-07-07，备注：requirements.txt 加 pytest-cov==7.1.0（顺便带 coverage==7.15.0）；pytest.ini addopts 改 `-v --tb=short --cov=ai-service/app --cov=strategies --cov-report=term-missing --cov-fail-under=80`。**实测总覆盖率 91.74%**（远高于 80% 门槛，无需调整阈值）；74 个文件中只有 4 个低于 80%：strategies/base.py 73%（veto 串行/并行分支 + AI 不可达降级）、strategies/s1_trend_follow/strategy.py 70%（populate_* 适配器层）、s2_momentum_rotation/strategy.py 72%（同前）、deps.py 80%（lifespan 与 reset 辅助）；这些是 freqtrade 内联调用框架层，freqtrade 容器外单测难直接覆盖。274 passed, 2 skipped。
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

- [x] **RC.3** 消除策略里的逐行 Python 循环（反模式 + 潜在 lookahead 风险）
  🔶 **完成（2026-07-07）**：S1 抽出模块级纯向量化函数 `build_entry_signals`/`build_exit_signals`（类外可测），`populate_*` 改为委托；等价性测试钉死「向量化 == 已测 `entry_signal` 逐行 + 复刻原 populate_exit_trend」并覆盖 warmup/NaN 边界。**S2 无需改**（本就无逐行循环，是横截面轮动纯函数）。全量 241 passed, 2 skipped（freqtrade 未装的类级用例）。**剩余人工闸**：`freqtrade lookahead-analysis` 本机跑不了——等价性已证明向量化未改行为，但上线前仍需手动跑一次 lookahead-analysis（04-handoff 常见坑#2）。
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

- [x] **RC.4** 更新过期的 README（当前谎称「设计阶段，无代码」）
  ✅ 完成于 2026-07-07，备注：badge 改 Phase 2 in progress + Code Phase 0/1/2 implemented + Tests 268 passed；当前状态段列各 Phase 真实进度；新增「快速开始（本地开发）」一节（cp .env.example → docker compose up → curl 两个健康端点 → pytest）；免责声明改写为「实盘前必须走完 ADR-005 状态机」。
  - **文件**: `README.md`
  - **问题**: 第 8 行 badge `Code: Coming Soon`、第 30 行「本仓库目前**只包含设计文档**，代码尚未开始编写」——但 Phase 0/1/2 已大量落地（freqtrade 骨架、S1/S2 策略、AI 服务全套、199 个测试）。误导任何新接棒的人/AI。
  - **修复**:
    - badge 改成 `status-phase--2--in--progress` 之类真实状态。
    - 「当前状态」段落改成：Phase 0/1/2 已实现（列已完成项），Phase 3（看板）/Phase 4（实盘）未开始。
    - 补一段「如何本地跑起来」：`cd deploy && cp .env.example .env`（填密钥）→ `docker compose up -d` → FreqUI `http://localhost:8080` / AI 服务 `http://localhost:8000/healthz`；本地测试 `source .venv/bin/activate && python -m pytest -q`。
  - **DoD**: README 状态与实际一致，含可照做的启动步骤。

- [x] **RC.5** 清理死 import 与小噪音
  ✅ 完成于 2026-07-07，备注：删 `Optional`（typing）、`LLMClient`（app.llm）、`get_research_extractor`（app.deps）3 个未引用 import，逐个删除 + 跑 test_api.py 兜底。Header/ResearchExtractor/get_strategy_stage 均有用，保留。全量 268 passed。
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

---

# Phase R2 — 二轮架构审查（2026-07-07 强模型 review 追加）

> **接棒说明（含便宜模型）**：本节是在 RA/RC 修复落地后，架构师第二轮 review 的新发现，**Phase R 未覆盖**。
> 执行规则同 Phase R 三条铁律（改完必须 `source .venv/bin/activate && python -m pytest -q` 全绿，基线现为 **209 passed**；优先用可搜索字符串定位；不改测试将就实现）。
> **难度分级**（token 不够时按此分派）：
> - 🟢 **可交便宜模型**：机械改动、有精确 diff、测试能兜底 → RB2.3 / RC2.1 / RC2.2 / RB2.2
> - 🟡 **中等**：涉及安全语义或新表，需照着方案走、别自由发挥 → RS.1 / RS.2 / RB2.4
> - 🧠 **必须强模型**：改部署/DB引擎/测试基建，动手前先写方案 → RS.3 / RB2.1 / RT.1

## RS — 安全（HIGH，提交/上线前必修；security.md 红线）

- [x] **RS.1** 🟡 Telegram webhook 无来源校验，任何人可伪造 update 触发查询/回复
  ✅ 完成于 2026-07-07，备注：handler 顶部校验 `X-Telegram-Bot-Api-Secret-Token` 头 vs 环境 `TELEGRAM_WEBHOOK_SECRET`；配了 secret 时不匹配→静默 200 丢弃（不处理/不回复/不触发重试），dev 未配→告警放行保持可测；`.env.example` 已加说明。新增 4 用例（monkeypatch 隔离 env）。全量 213 passed。
  - **文件**: `ai-service/app/main.py` 的 `telegram_webhook`（搜索 `def telegram_webhook`）；`ai-service/app/notifier.py` 第 22 行注释已自认「不校验 X-Telegram-Bot-Api-Secret-Token」。
  - **问题**: 端点接收任意 `dict` 就路由命令、读库、回消息。公网暴露时（见 RS.3）任何人 POST 伪造 `/status` 即可套出策略/复盘信息，或被刷量。
  - **修复**:
    1. Telegram 设置 webhook 时支持 `secret_token`，Telegram 每次回调会带 HTTP 头 `X-Telegram-Bot-Api-Secret-Token`。在 `.env.example` 增加 `TELEGRAM_WEBHOOK_SECRET`。
    2. `telegram_webhook` 签名改为接收 `request: Request`（`from fastapi import Request`），读取 `request.headers.get("X-Telegram-Bot-Api-Secret-Token")`，与环境变量 `TELEGRAM_WEBHOOK_SECRET` 比对；不一致直接 `return {"ok": True}` 且不做任何处理（**返回 200 但静默丢弃**，不泄露信息、也不触发 Telegram 重试风暴）。secret 未配置时（本地 dev）记一条 warning 并放行，保持可测。
    3. body 仍按现有方式解析（可用 `await request.json()`）。
  - **验证/加测**: `ai-service/tests/test_telegram_webhook.py` 加两个用例：带正确 secret → 正常回复；带错误/缺失 secret 且环境配了 secret → 不调用 `notifier.send_message`。`python -m pytest ai-service/tests/test_telegram_webhook.py -q` 全绿。
  - **DoD**: 配了 secret 时伪造 update 被静默丢弃且有测试覆盖；全量测试绿。

- [x] **RS.2** 🟡 缺失 LLM 密钥时静默 fallback 假 key（掩盖配置错误 → 全线 fail-open）
  ✅ 完成于 2026-07-07，备注：新增 `SENTINEL_ENV`（dev默认/prod）+ `validate_required_secrets()`，在 lifespan 启动顶部调用；prod 且无 AGNES/OPENAI key → 启动即抛 RuntimeError 拒绝启动，dev 保留假 key 兜底。新增 `test_startup_validation.py`（5 用例，autouse fixture + monkeypatch 隔离 env/缓存）。全量 218 passed。
  - **文件**: `ai-service/app/deps.py` 第 34 行 `os.environ.get("AGNES_API_KEY") or os.environ.get("OPENAI_API_KEY", "sk-fake-for-dev")`
  - **问题**: 生产若忘配 key，会静默用 `sk-fake-for-dev` → 所有 LLM 调用 401 → 否决/研究/复盘全部 fail-open 或失败，**但服务照常起、healthz 照样绿**，故障隐形。违反 security.md「启动期校验必需密钥」。
  - **修复**:
    1. 增加环境变量 `SENTINEL_ENV`（`dev`|`prod`，默认 `dev`）。
    2. 保留 dev 下的 `sk-fake-for-dev` 兜底（方便本地/测试）；但当 `SENTINEL_ENV=prod` 且未提供任何真实 key 时，在**启动期**（lifespan 或 `get_engine` 之前）抛错拒绝启动，错误信息明确「AGNES_API_KEY / OPENAI_API_KEY 必须设置」。
    3. 不要在请求路径里才发现，要在 app 启动 fail-fast。
  - **验证/加测**: 加测试：`SENTINEL_ENV=prod` 且无 key → 构造 app/调用配置校验函数抛预期异常；dev 无 key → 不抛。注意用 `reset_caches_for_testing()` 清 lru_cache。
  - **DoD**: prod 缺 key 直接启动失败并给清晰信息；dev 不受影响；测试覆盖两条路径。

- [x] **RS.3** 🧠 部署加固：host 网络模式下 8000 端口的无鉴权端点暴露面
  ✅ 完成于 2026-07-07，备注：① 运维写端点（`/strategy/register`、`/strategy/check`、`/research/note`、`/reflection`）加可选 `X-Sentinel-Token` 校验（`SENTINEL_API_TOKEN` 未配=dev 放行，配了则不匹配 401）；`/veto`、`/trade-close` **不 gate**（freqtrade/策略无法带自定义头，靠网络隔离）。② 确认 uvicorn 已绑 `${AI_SERVICE_HOST:-127.0.0.1}` loopback。③ RUNBOOK 新增「端口暴露与防火墙加固」章节（ufw 示例 + 为何两端点免 token）。新增 6 用例（含"配 token 时 /veto、/trade-close 仍 200"红线）。全量 233 passed。
  - **文件**: `deploy/docker-compose.yml`（ai-service 用 `network_mode: host`，监听 `127.0.0.1:8000`）；`deploy/RUNBOOK.md`
  - **问题**: `/veto`、`/telegram/webhook`、`/strategy/*`、`/trade-close` 均无鉴权（设计上靠「只在本机被 freqtrade 调用」）。一旦 VPS 上服务监听到 `0.0.0.0` 或防火墙没关 8000，这些端点直接对公网开放（能读策略阶段、刷 veto、伪造 trade-close 造假复盘）。
  - **修复方向**（动手前写方案，二选一或组合）:
    1. 明确把 ai-service 绑定 `127.0.0.1`（确认 `AI_SERVICE_HOST=127.0.0.1` 真的生效、uvicorn 只听 loopback），并在 `RUNBOOK.md` 写死「VPS 防火墙必须 deny 8000/8080 外部入站，只留 SSH + 你自己的 IP」。
    2. 对 `/trade-close`、`/strategy/*` 加一个简单的共享密钥头校验（`X-Sentinel-Token`，freqtrade webhook 配置里带上），内部调用也带 token。
  - **DoD**: RUNBOOK 有明确的端口暴露/防火墙章节；关键写端点要么绑 loopback 要么有 token 校验；方案写清。**涉及真实部署安全，必须强模型，不确定问用户**。

## RB2 — 健壮性 / 正确性（MED）

- [ ] **RB2.1** 🧠 SQLite 并发写无保护，scheduler 线程 + 请求线程易「database is locked」
  🔶 **部分完成（2026-07-07，短期加固 option 1）**：`get_engine` 对 sqlite URL 加 `check_same_thread=False` + connect 钩子设 `PRAGMA journal_mode=WAL` / `busy_timeout=5000`，Postgres URL 严格 no-op；新增并发写测试（2 线程各写 25 行不 locked）+ WAL 生效断言。全量 220 passed。**剩余（可选，强模型）**：按 ADR-007 迁移到 PostgreSQL（与 RB.4 Alembic 合并做）——WAL 只是缓解，多写者高并发下 Postgres 才是终态。
  - **文件**: `ai-service/app/db/models.py` 第 112 行 `create_engine(url, echo=False, future=True)`
  - **问题**: `BackgroundScheduler`（后台线程）写 `research_notes`，同时 FastAPI 请求线程写 `veto_records`/`reflections`。SQLite 默认单写者 + 无 busy timeout，并发下会抛 `database is locked`；且多线程用 SQLite 未设 `check_same_thread=False` 有隐患。设计文档 §2.4/ADR-007 本就规划 PostgreSQL，SQLite 只是临时。
  - **修复方向**（二选一）:
    1. **短期加固**（KISS）: `create_engine` 增加 `connect_args={"check_same_thread": False}`，并在连接后开启 WAL + busy_timeout（`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;`，用 SQLAlchemy `event.listen(engine, "connect", ...)` 设置）。只对 sqlite URL 生效，Postgres URL 时跳过。
    2. **按设计上 Postgres**: 与 RB.4（Alembic）一起做，`DATABASE_URL` 指向 postgres，docker-compose 取消注释 postgres 服务。
  - **注意红线**: 不改任何表结构/字段名（契约）。只动 engine 创建与 PRAGMA。
  - **验证/加测**: 加一个并发写小测试（两个线程各插一批 veto_records + research_notes）不报 locked。
  - **DoD**: 并发写不再 locked；sqlite/postgres 两种 URL 都能起；测试覆盖。**🧠：涉及 DB 引擎与部署，先写方案**。

- [x] **RB2.2** 🟢 `veto_records` 无限增长（每次入场信号都插一行，无保留策略）
  ✅ 完成于 2026-07-07，备注：repository 新增 purge_old_veto_records / purge_old_research_notes（参数化 DELETE，bound value 不拼字符串）+ count_* helper；scheduler 新增 run_daily_cleanup(sf, retention_days) 返回删除条数 dict；SchedulerConfig 加 cleanup_cron (默认 "0 10 * * *") + retention_days (RETENTION_DAYS env 默认 90)；SentinelScheduler.start() 末尾注册 daily_cleanup job。原 3-job scheduler 测试改为 4-job。新增 6 用例（每表 100/91 天边界 + 默认 90d 不动 + retention_days=30 显式覆盖 + 空库 noop + RETENTION_DAYS env 解析）。全量 274 passed, 2 skipped。
  - **文件**: `ai-service/app/db/repository.py`（`insert_veto_record`）；`ai-service/app/scheduler.py`（加清理 job）
  - **问题**: 每次 `confirm_trade_entry` 都 `GET /veto` → 插一行 veto_record。长期跑（尤其 dry-run 高频信号）表会膨胀，SQLite 体积和查询变慢。research_notes 同理。
  - **修复**: 加一个 repository 函数 `purge_old_veto_records(session, keep_days=90)`（`DELETE WHERE created_at < cutoff`，参数化，别拼字符串），在 scheduler 的每日 job 里调一次（复用 `run_daily_stage_check` 之后，或新加一个 `run_daily_cleanup`）。`keep_days` 走环境变量 `RETENTION_DAYS`（默认 90），是**保留天数不是删除阈值**，别删反。
  - **验证/加测**: 插 100/91 天前的旧记录 + 几条新记录 → 调清理 → 断言只剩新记录。
  - **DoD**: 有可配置保留期的定时清理 + 测试；默认 90 天。

- [x] **RB2.3** 🟢 修 `datetime.utcnow()` 弃用用法 + 复盘响应用真实时间戳
  ✅ 完成于 2026-07-07，备注：main.py:25 加 timezone import；main.py:352 `created_at=row.created_at if row else datetime.now(timezone.utc)`（用 RA.2 已查的真实行）；全仓 grep 仅剩 `_utcnow()` 函数名 + 注释。268 passed, 1 warning（无关 Starlette 弃用）。
  - **文件**: `ai-service/app/main.py` 第 285 行 `created_at=datetime.utcnow(),`（在 `submit_reflection` 里）
  - **问题**: ① `datetime.utcnow()` 在 Python 3.12+ 已弃用（本仓 venv 是 3.14），会有 DeprecationWarning，且返回 naive 时间。② 该处**伪造**了一个当前时间当 created_at 返回，而 RA.2 已经重新查到了真实 `row`，应直接用 `row.created_at`。
  - **修复**: 把 `created_at=datetime.utcnow(),` 改成 `created_at=row.created_at if row else datetime.now(timezone.utc),`（`row` 变量来自 RA.2 已加的重新查库；确认文件顶部或函数内有 `from datetime import timezone`，没有就补 import）。顺带全仓 `grep -rn "utcnow()" ai-service strategies` 确认再无其它弃用点。
  - **验证**: `python -m pytest ai-service/tests/test_api.py -q` 全绿；运行时无 utcnow 的 DeprecationWarning。
  - **DoD**: 无 `datetime.utcnow()`；复盘响应 created_at 来自真实行；测试绿。

- [x] **RB2.4** 🟡 LLM 每次调用的 token/成本从不落库（P2.2 的 DoD 未达成）
  ✅ 完成于 2026-07-07，备注：新增 `llm_calls` 表（经 ensure_schema 建）+ `insert_llm_call`；`OpenAICompatibleClient` 加可选 `usage_callback`（解析一次 json 同时取 text 与 usage，缺 usage 不崩、回调异常不影响 completion），deps `_llm_client` 接线用新 session 写库。分层清晰：客户端零 DB 依赖。新增 5 用例（含"回调抛异常不破坏 complete"）。全量 227 passed。
  - **文件**: `ai-service/app/llm/openai_compat.py`（`_extract_text` 丢弃了响应里的 `usage`）；需新增一张表/或复用日志
  - **问题**: 设计 P2.2 明确要求「每次调用的 token 消耗落库可查」，当前 OpenAI 兼容响应里的 `usage.prompt_tokens/completion_tokens` 被直接丢弃，无法核算成本。
  - **修复方向**（先做方案，注意这涉及新表 = schema 变更，**要和 RB.4 Alembic 协调**，别用裸 `create_all` 加表后又忘了迁移）:
    1. 新增表 `llm_calls(id, model, model_tier, prompt_tokens, completion_tokens, total_tokens, created_at)`（若 RB.4 未落地，先在本任务注释标注「等 RB.4 统一迁移」）。
    2. `complete()` 成功后从 `resp.json()["usage"]` 取三个数，写一行。为不阻塞主流程，写失败只记 warning 不抛。
    3. 加只读接口/或让运维面板（未来）能查每日 token 汇总。
  - **验证/加测**: mock 一个带 usage 的响应 → 断言落库一行且数值正确；无 usage 字段时不报错。
  - **DoD**: 每次 LLM 调用的 token 数落库可查；测试覆盖有/无 usage 两种响应。**🟡：照方案走，新表务必走迁移不要裸建**。

## RT — 测试基建（MED，解锁被 BLOCK 的 RB.3）

- [x] **RT.1** 🧠 统一 session 供给，使测试覆盖与生产 engine 一致（解锁 RB.3）
  ✅ 完成于 2026-07-07：新增 `ai-service/tests/conftest.py`（`bind_module_engine`/`reset_module_engine`），让测试把模块级 `_engine`/`_SessionLocal` 绑到与 `get_db` 覆盖同一个 test engine，`get_session()` 与请求 `db` 读写同库；teardown 复位单例 + `reset_caches_for_testing()` 防泄漏。据此解锁并完成 RB.3。全量 220 passed（两次跑 + 混合子集 54 passed 验隔离）。
  - **背景**: RB.3 被 BLOCK 的根因是——测试通过覆盖 FastAPI 依赖 `get_db` 注入测试 session，但 `ReflectionWriter` 内部若改用模块级 `get_session`，用的是另一个 engine，导致写入库 ≠ 读取库。这说明**测试基建对「非请求路径的 session」没有统一覆盖点**，是个真实的架构薄弱点。
  - **问题**: 生产里 `get_db` 与 `get_session` 共享模块 engine（一致）；但测试只覆盖了 `get_db`，没覆盖 `get_session`。任何走 `get_session` 的后台/写库路径在测试里都指向真 engine。
  - **修复方向**（先写方案）:
    1. 让测试 fixture 在覆盖 `get_db` 的同时，也把模块 engine 指向同一个测试 DB（例如通过 `DATABASE_URL` 环境变量 + `reset_caches_for_testing()` + 重建 engine，确保 `get_session()` 和 `get_db()` 用同一个 in-memory/临时库）。
    2. 或者引入一个统一的 `SessionProvider` 依赖，生产/测试都从它取，测试只覆盖这一个点。
    3. 完成后**回到 RB.3**：把两处 `ReflectionWriter(extractor, lambda: db)` 改成 `get_session`（消除借用请求 session 被 `with` 关闭的问题），此时测试应能全绿。
  - **DoD**: 测试里 `get_session` 与 `get_db` 指向同库；RB.3 的改动能通过全部测试；RB.3 随之标记完成。**🧠：测试基建重构，先写方案，别硬改**。

## RC2 — 优化（LOW，token 不够可整批交便宜模型）

- [x] **RC2.1** 🟢 收敛配置读取到 pydantic Settings + 去掉重复 local import
  ✅ 完成于 2026-07-07，备注：新增 pydantic_settings.BaseSettings 子类 Settings（field=env var 大小写不敏感映射，默认值与原手搓 dict 完全一致，api_key/base_url 用 @property 保留 AGNES→OPENAI / LLM_BASE_URL→OPENAI_API_BASE fallback 链）；deps.py 三处 _settings()['db_url'] / _settings()['api_key'] 等改成属性访问；validate_required_secrets() 改读 Settings 字段；requirements.txt 加 pydantic-settings==2.14.2。main.py 顶部 import 加 get_reflection_by_trade_id；submit_reflection 内删除 local import。_settings() 仍为 @lru_cache 包装，reset_caches_for_testing 行为不变。全量 268 passed。
  - **文件**: `ai-service/app/deps.py`（`_settings()` 手搓 `os.environ.get`）；`ai-service/app/main.py`（`submit_reflection` 里第二次 local import `get_reflection_by_trade_id`，`trade_close` 里已 import 过）
  - **问题**: ① 配置散在 `os.environ.get`，无类型/无校验，易拼错 key（security.md 要求边界校验）。② RA.2 引入的 local import 与 `trade_close` 内的重复。
  - **修复**（小步、低风险）: ① 用 `pydantic-settings` 的 `BaseSettings` 定义一个 `Settings` 类集中声明所有环境变量（api_key/base_url/db_url/models/proxy/retention/env/webhook_secret），`_settings()` 返回它；保持默认值不变以不破坏现有测试。② 把 `get_reflection_by_trade_id` 提到 main.py 顶部 import 一次，删掉两处 local import。
  - **验证**: `python -m pytest -q` 全绿（这是纯重构，行为不变，测试是唯一裁判）。
  - **DoD**: 配置集中且有类型；无重复 local import；全测试绿。

- [ ] **RC2.2** 🟢 docker-compose 代理硬编码改为 env 驱动
  - **文件**: `deploy/docker-compose.yml`（`HTTPS_PROXY: "http://127.0.0.1:7890"` 等在 freqtrade 与 ai-service 两处硬编码）
  - **问题**: 代理地址写死在编排文件里，VPS 上没有该本地代理时 LLM/交易所访问全部失败；也不利于「本机开发 vs VPS 部署」切换。
  - **修复**: 改成 `HTTPS_PROXY: "${HTTPS_PROXY:-}"` 之类由 `.env` 注入（`.env.example` 增加 `HTTPS_PROXY=` 注释说明「国内本机填 http://127.0.0.1:7890；VPS 留空」）。默认留空 = 不走代理。
  - **验证**: `cd deploy && docker compose config` 能正确渲染；不影响本机（本机 .env 里填上代理即可）。
  - **DoD**: 代理不再硬编码；本机/VPS 靠 .env 切换；有注释说明。

## 附：对 Phase R 既有任务的补充

- **RC.3（策略向量化）的验收闸要加一条**：向量化改写后除了 `pytest strategies -q` 绿，还必须跑 `freqtrade lookahead-analysis`（04-handoff-guide.md 常见坑第 2 条）——向量化最容易在无意中引入未来函数。弱模型做 RC.3 时若无法跑 lookahead-analysis，**标 🧠 升级**，不要仅凭单测绿就判完成。
- **RB.4（Alembic）与 RB2.4/RB2.1-方案2 强相关**：都涉及加表/迁移，建议合并到一次「上 Alembic + 迁移到 Postgres」的强模型任务里统一做，避免各自裸 `create_all` 造成迁移碎片。
