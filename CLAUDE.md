# CLAUDE.md — Sentinel 项目会话指引

> 本系统管理用户真金白银。本文 + 下列文件的规则，优先级高于你的任何默认行为和"更好的想法"。

## 每次会话开场必读（按顺序）

1. `docs/system/04-handoff-guide.md` — **铁律**（违反任一条 = 停止并报告用户）
2. `docs/system/03-tasks.md` — 任务队列 + 进度（唯一任务来源，也是恢复点）
3. `docs/system/01-architecture.md` — ADR 架构契约（不得推翻）
4. `docs/system/02-design.md` — 模块/接口/数据流契约（schema、接口签名以此为准）

> **记忆活在文件里，不活在上下文里。** 你做到哪、下一步做什么、不许碰什么，全部由上面四个文件 + git 历史决定，**不靠"记住"**。因此 `/clear`、换 subagent、换会话、换模型、进程崩溃——都不影响连续性：任何空白上下文读完这四个文件就能精确接续。长程连续任务见下方「自治连续执行」。

## ⭐ 当前接棒队列（2026-07-15 更新，便宜模型看这里）

> **便宜模型的机械任务已全部清零**——Phase R + R2 的 RA/RB/RS/RT/RC/RB2/RC2 全部落地，基线 **274 passed, 2 skipped，覆盖率 91.7%**。
> 上一版队列列的 7 个便宜模型任务（RB2.3/RC.5/RC.4/RC2.2/RC2.1/RB2.2/RC.2）**都已完成**，不要重做。
>
> **⚠️ 现在没有「便宜模型可盲取」的任务了。** 剩下的全部需要用户投资判断 + 强模型，详见 `03-tasks.md` 顶部「现状对账」。核心卡点是一条**策略问题、不是工程问题**：
>
> **🔴 头号根因：策略层空转。** S1 趋势跟踪 3.5 年只出 3 笔交易、牛市留出段 0 笔；dry-run 容器 up 9 天却 STOPPED 态 + 0 交易。ADR-005 升档要 ≥30 笔 → 当前 S1 单策略下状态机几十年走不完。**你建的整套安全网下面没有交易可监管。**
>
> **下一步真正的工作（按优先级，均需强模型 / 用户拍板）：**
> 1. **用户先拍板**：S1 的「长期沉默」是认可的 feature（趋势跟踪本该在牛市外沉默），还是要改（加 pairs/降周期/加次级信号）？
> 2. 🧠 **部署 + 回测 S2**（P1.3/P1.6）——让 dry-run「有交易可观察」最快的路。
> 3. 🧠 若要救 S1：先做 P1.1（下市值前 15 币种），再重跑 P1.4 hyperopt。
> 4. 把 dry-run bot 从 STOPPED 切 RUNNING（`dry-run.json` 设 `initial_state: running`）。
> 5. 🧠 P1.5 jesse 交叉验证（上实盘前的闸）。
>
> **绝对不要碰**（需强模型/外部集成，碰了会出错）：
> - **RB.2 剩余（规则2 敞口）**、**RB2.1 剩余（迁 Postgres）**、**RB.4 的全量 Alembic**：需接 freqtrade REST / 换 DB，属集成决策。
> - 任何标 🧠 的项、`RD` 组（用户用 open design 自理）。
> - 已打 `[x]` 或标 🔶「完成/部分完成」的任务：**不要重做**。
>
> ⚠️ **不要按「第一个 `[ ]`」盲取**——P1 里的 `[ ]` 是需强模型 + 投资判断的策略活，不是便宜模型任务。



## 工作约定（强制）

1. **任务来源唯一 = `03-tasks.md`**，按 Phase 与编号顺序做。完成后 `[ ]`→`[x]`，并在该任务下追加一行 `✅ 完成于 <日期>，commit <sha>，备注 <一句话>`。
2. **决策查询顺序**：`03-tasks.md`（做什么）→ `02-design.md`（怎么做）→ `01-architecture.md`（为什么）→ `00-research-summary.md`（依据）。四层都没答案 → 问用户，不要猜。
3. **契约不可变**：ADR 决策、`GET /veto` 接口契约、`ai-service/app/db/models.py` 的字段、ADR-005 状态机门槛、freqtrade 强制风控项——都是契约。实现中发现契约有问题 → 停下在 `03-tasks.md` 记录，**不擅改**。
4. **TDD + 全局规则**：先写测试（RED→GREEN→重构）、覆盖率 ≥80%、不可变数据、文件 <800 行、函数 <50 行、无魔法数字。
5. **🧠 任务两次不过 → 建议切回强模型**，不硬凑"看起来能跑"的版本。
6. **不确定就问，不要猜**。涉及钱的歧义，猜错代价不对称。

## 自治连续执行（长程模式）

> 用户说「连续做完 XX」「长程跑」「一直做」时启用。核心：**每一轮都是无状态的（上下文里什么都不留），有状态的部分全落盘（`03-tasks.md` + git）。** 任何一步崩溃，下一个空白上下文读文件即可精确接续——`/clear` 无害。

**单个任务执行回路（每个 `[ ]` 任务走一遍）：**

1. **崩溃检测**：`git status`。若工作区脏 **且** 当前任务未打勾 → 上次做到一半死了 → `git reset --hard && git clean -fd` 清干净，从头做该任务。（"绝不留悬空状态"靠协议而非记忆。）
2. **认领**：按上方「⭐ 当前接棒队列」取下一个未完成任务（**不要盲取 `03-tasks.md` 里第一个 `[ ]`**——有的 `[ ]` 是需强模型的半成品）；读它引用的 ADR / design 章节。
3. **实现**：派**实现 subagent**（全新上下文，只喂「本任务规格 + 契约红线」），照 TDD 改代码。主会话只当协调器，自己不写实现、保持精简。
4. **校验**：派**校验 subagent**（全新上下文，只喂「本任务验收标准 + `git diff`」），先过客观闸再上评审：
   - **客观闸**（任一不过即失败，不需 LLM 判断）：
     - `source .venv/bin/activate && python -m pytest -q` **全绿且总数 ≥ 268**（基线：268 passed, 2 skipped）
     - `git diff --name-only` ⊆ 本任务声明改动的文件集（防「一口气改太多」）
     - `git diff` 未触碰下列**契约红线**，除非本任务明确要求（防契约漂移）：
       - `strategies/veto_gate.py` 的 fail-open 语义（网络/超时/坏响应一律放行）**只能保持，不能改成 fail-closed**（ADR-002）
       - `GET /veto` 的返回契约 `{decision: PASS|VETO, reason}`（策略侧 `check_veto` 依赖它）
       - `ai-service/app/db/models.py` 的表名/字段名（对照 design §2.4）
       - `ai-service/app/modules/stages.py` 的 `CRITERIA` 状态机门槛（ADR-005，改门槛需用户复述风险）
       - `deploy/user_data/config/config.template.json` 的 `stoploss_on_exchange` / 三件套 Protections / `max_open_trades≤3`（铁律 4）
     - 若任务动了 `strategies/base.py` **或** `deploy/user_data/strategies/base.py` → 另一份副本必须同步改（双副本纪律，见记忆 `strategy-base-duplication`）
   - **LLM 评审**（客观闸过了才做）：逐条核对本任务「DoD」是否**真达成**（测试绿 ≠ 完成，DoD 满足才算）；有没有 mock 掉不该 mock 的东西（状态机、fail-open 路径）。返回 `{pass: bool, blocking_issues: [...]}`。
5. **结算**：
   - 通过 → `[x]` + 追加 `✅` 行 + `git commit`（`feat:`/`fix:` + 任务编号，如 `fix(RA.1): ...`）。
   - 不通过 → 回步骤 3，把 `blocking_issues` 一并喂给实现 subagent 重做；连续 2 次不过 → 该任务下写 `⚠️ BLOCKED: <原因>`，跳下一个不依赖它的任务。
6. **落盘即安全**：commit 后本轮状态已全部持久化，可 `/clear` 或换上下文继续下一轮。

**高危任务例外（不进自治流，停下让用户人工确认）：**
- 改 ADR-005 状态机门槛、关闭任何 Protection、改 `stoploss_on_exchange`、把 `max_open_trades` 调 >3（铁律 3/4）
- 任何触碰真实下单 / dry-run→live 切换 / 提现权限的改动（铁律 1/6）
- 标 🧠 的任务，若你不是强模型：先做，两次不过就标注跳过

**隔离建议**：长程自治优先在 git worktree 里跑（不碰用户当前工作区，做完再合），除非用户另有指示。

## 常用命令

```bash
source .venv/bin/activate            # 进虚拟环境
python -m pytest -q                  # 全量测试（基线 199 passed）
python -m pytest ai-service/tests/test_scheduler.py -q   # 单文件
cd deploy && docker compose up -d    # 起全栈（freqtrade + ai-service）
```

## 项目背景速览

用户：稳健型个人投资者，目标年化 8-15%、最大回撤 <20%，看重**纪律自动化**甚于策略聪明度。曾主观炒股亏钱，本系统的存在意义就是**用流程防止人性犯错**——你写的每行风控代码都在保护用户不被自己伤害。认真对待。
