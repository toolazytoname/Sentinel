# 02 — 系统设计

> 模块划分、接口契约、数据流。接棒 AI 实现时以本文为准；有歧义先查 `01-architecture.md` 的 ADR，再不明白就问用户。

## 1. 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│                        用户 (浏览器 / Telegram)                │
└──────────┬──────────────────────────────┬───────────────────┘
           │                              │
┌──────────▼──────────┐        ┌──────────▼───────────┐
│  看板 Dashboard      │        │  FreqUI + Telegram    │
│  (Phase 3, Next.js) │        │  (Phase 1, 现成)      │
│  只读聚合展示         │        │  控制 + 监控          │
└──────────┬──────────┘        └──────────┬───────────┘
           │ HTTP (只读)                   │
┌──────────▼──────────────────────────────▼───────────┐
│                    freqtrade 实例群                    │
│  strategy-a 容器 │ strategy-b 容器 │ dry-run 容器 ...  │
│  每容器: 策略 + Protections + 交易所侧止损 + REST API   │
└───────┬──────────────────────────────────▲──────────┘
        │ REST API (只读)                   │ confirm_trade_entry
        │                                  │ 查否决表 (SELECT only)
┌───────▼──────────────────────────────────┴──────────┐
│                AI 服务 (FastAPI, Phase 2)             │
│  ┌────────────┐ ┌────────────┐ ┌─────────────────┐  │
│  │ 研究模块     │ │ 复盘模块    │ │ 风控审计模块      │  │
│  │ 新闻→摘要    │ │ 交易→反思   │ │ 信号→VETO/PASS  │  │
│  └─────┬──────┘ └─────┬──────┘ └────────┬────────┘  │
│        └──────────────┴─────────────────┘            │
│                   PostgreSQL                          │
│     (research_notes / reflections / veto_records)     │
└──────────────────────────────────────────────────────┘
```

数据流方向铁律（对应 ADR-002）：
- AI 服务 → freqtrade：**只读** REST API（查持仓/交易记录/盈亏）
- freqtrade → AI 服务：策略在 `confirm_trade_entry` 回调里 HTTP GET 查否决表；**超时/失败默认放行**（AI 服务挂了不能阻塞交易系统，AI 只是额外保险不是必经关卡）
- 不存在 AI → 下单的任何路径

## 2. 模块规格

### 2.1 freqtrade 层（Phase 1）

- 每个策略一个容器，独立配置、独立 SQLite、独立端口
- 所有配置必须包含（模板见 `deploy/config.template.json`，接棒时创建）：
  - `stoploss_on_exchange: true`（ADR-003 兜底）
  - Protections: `MaxDrawdown`(回撤>10%停24h) + `StoplossGuard`(4h内3次止损停12h) + `CooldownPeriod`(止损后冷却2根K线)
  - `max_open_trades` ≤ 3/策略；`tradable_balance_ratio` ≤ 0.99
  - dry-run 与 live 配置只差 `dry_run` 一个字段，git 里两份配置并排放，diff 必须只有这一行
- 策略基类 `StrategyBase`（薄）：统一实现 confirm_trade_entry 查否决表、统一日志格式、统一 custom_stoploss 骨架

### 2.2 AI 服务（Phase 2, FastAPI）

**研究模块**
- 输入：RSS/交易所公告/CoinGecko 等免费源（Phase 2 只做 2-3 个源，YAGNI）
- 处理：LLM 摘要为结构化 JSON：`{asset, event_type, severity(1-5), summary, source_url, published_at}`
- 输出：存 `research_notes` 表；severity ≥ 4 推 Telegram
- 成本控制：quick 档模型（便宜/本地），批处理，每日定时而非实时

**复盘模块（抄 TradingAgents 的 reflection）**
- 触发：每笔交易平仓后 + 每周日汇总
- 输入：该笔交易的完整记录（入场理由=策略信号快照、出场、盈亏）+ 期间的 research_notes
- 输出：`reflections` 表 `{trade_id, what_worked, what_failed, lesson, confidence}`；周报推 Telegram
- 关键：复盘产出**给人看**，以及作为风控审计模块的上下文；不自动改参数

**风控审计模块（VETO 权）**
- 触发：策略产生入场信号时（confirm_trade_entry 前置查询）
- 检查项（规则优先，LLM 兜底）：
  1. [规则] 该资产 24h 内有 severity≥4 的负面事件？
  2. [规则] 当前总敞口是否超账户上限？
  3. [规则] 是否处于重大事件窗口（美联储议息等，日历维护）？
  4. [LLM] 综合上下文的"反方陈述"——输出 `{veto: bool, reason}`
- 输出：`veto_records` 表；所有 VETO 推 Telegram 并要求人工确认后才能解除
- **失败语义：AI 服务不可达 → 策略端默认 PASS 并告警**（可用性优先，AI 是增强不是依赖）

**升级核查模块（ADR-005 的自动化）**
- 每日核查各策略所处阶段（backtest/dry-run/live-small/live-scaled）与天数、交易数、回撤 vs 回测基准
- 满足升级条件 → 生成升级建议报告推 Telegram，**人工确认后手动改配置**（系统不自动动钱）
- 触发降级条件 → 立即告警 + 建议动作

### 2.3 看板（Phase 3, Next.js）

只读聚合，四个页面，不做任何控制功能（控制走 FreqUI/Telegram）：
1. **总览**：各策略净值曲线、总资产、当前回撤、所处阶段（状态机可视化）
2. **交易**：交易列表 + 每笔的复盘摘要（联表 reflections）
3. **研究**：research_notes 时间线
4. **风控**：veto_records + Protections 触发历史 + 升级核查报告

### 2.4 数据库 Schema（PostgreSQL, AI 服务专用）

```sql
research_notes(id, asset, event_type, severity, summary, source_url, published_at, created_at)
reflections(id, trade_id, strategy, what_worked, what_failed, lesson, confidence, created_at)
veto_records(id, strategy, pair, signal_time, veto BOOLEAN, reason, checked_rules JSONB, resolved_by, created_at)
strategy_stages(id, strategy, stage, entered_at, criteria_snapshot JSONB, approved_by)
```

## 3. 策略路线（Phase 1 先做两个，YAGNI）

| 策略 | 类型 | 周期 | 逻辑概要 | 定位 |
|---|---|---|---|---|
| S1 趋势跟踪 | 动量 | 1d | BTC/ETH 双均线+ADX过滤，趋势确认进，破位/追踪止损出 | 主力，吃大趋势 |
| S2 动量轮动 | 轮动 | 1d | 市值前10（剔除稳定币）按30d动量排名，持前2-3，周度再平衡 | 分散，降低单币风险 |

两个都是社区反复验证过的经典逻辑（freqtrade 社区有大量参考实现可查），不追求新颖，追求可验证。回测要求：≥3 年数据（含 2022 熊市），walk-forward 验证，并用 jesse 跑 Monte Carlo 交叉确认。

## 4. 非功能性要求

- **告警**：进程存活、API 连通、余额异常，全部推 Telegram；心跳每 15min
- **备份**：SQLite/PostgreSQL 每日备份到对象存储或本地第二磁盘
- **密钥**：交易所 API key 只给交易权限**禁止提现权限**；密钥经环境变量注入，永不进 git；LLM key 同理
- **测试**：AI 服务单元测试覆盖 ≥80%；策略逻辑的指标计算部分要有单测；VETO 查询的超时降级路径必须有集成测试
