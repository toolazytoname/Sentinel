# P2.5 — Trade-Close Webhook

**日期**: 2026-07-06
**目标**: 在 freqtrade 平仓事件触发时，自动让 ai-service 生成 reflection，
把人工 POST `/reflection` 替换为事件驱动。

---

## 1. 数据流

```
freqtrade trade process
   │
   │  EXIT_FILL (RPCMessageType.exit_fill)
   │  ├─ is_final_exit=true  → 走 LLM reflection 路径
   │  └─ is_final_exit=false → 立即返回 200 + skipped
   ▼
freqtrade.rpc.webhook.Webhook
   │  payload = recursive_format(config.webhook.exit_fill, msg)
   ▼  HTTP POST {url}/trade-close   (format=json)
ai-service POST /trade-close
   │
   ├─ 幂等检查：(trade_id, strategy) 已存在 → 200 + skipped="already_reflected"
   ├─ is_final_exit=false          → 200 + skipped="partial_fill"
   └─ 走到 ReflectionWriter:
        ├─ LLM 503 → 端点返 503（freqtrade 日志可见，不阻塞 freqtrade）
        └─ 成功 → 200 + reflection_id
   ▼
DB: reflections 行 + （未来）weekly rollup 自动汇总
```

---

## 2. 接口契约

### 2.1 freqtrade 端（`config.webhook.exit_fill`）

字段集源自 `freqtradebot._notify_exit` 的 `RPCExitMsg`（freqtrade 2026.6）。
freqtrade 用 `recursive_format` 把 dict 里所有字符串值做 `str.format(**msg)` 替换；
非字符串值（dict/list/int）原样保留。

```json
{
  "webhook": {
    "enabled": true,
    "url": "${AI_SERVICE_WEBHOOK_URL}",
    "format": "json",
    "retries": 2,
    "retry_delay": 5,
    "timeout": 10,
    "exit_fill": {
      "trade_id": "{trade_id}",
      "strategy": "S1TrendFollow",
      "pair": "{pair}",
      "direction": "{direction}",
      "open_rate": "{open_rate}",
      "close_rate": "{close_rate}",
      "profit_ratio": "{profit_ratio}",
      "profit_amount": "{profit_amount}",
      "open_date": "{open_date}",
      "close_date": "{close_date}",
      "exit_reason": "{exit_reason}",
      "enter_tag": "{enter_tag}",
      "stake_amount": "{stake_amount}",
      "stake_currency": "{stake_currency}",
      "is_final_exit": "{is_final_exit}",
      "sub_trade": "{sub_trade}",
      "side": "long"
    }
  }
}
```

**为什么把 `strategy` 硬编码进 dict**：freqtrade 不知道自己的策略名（一个
freqtrade 实例只跑一个策略，但 RPC 层不传这个上下文）。dict 里放非字符串值
（`"strategy": "S1TrendFollow"` 是字符串，但 `recursive_format` 只对**已经是
字符串的**值做 format——这里 `"strategy"` 的值是字符串但**没有 `{...}` 占位符**，
format 后不变）。每换一个策略要复制一份配置；可接受，因为 ADR-001 已是
"一容器一策略"。

**`${AI_SERVICE_WEBHOOK_URL}` 替换**：在 `deploy/start.sh` 的 Python heredoc
里用 `str.replace` 做占位符替换。`.env` 里默认 `http://127.0.0.1:8000/trade-close`，
假设 ai-service 与 freqtrade 同在 host 网络模式（docker-compose.yml 已配）。

### 2.2 ai-service 端（`POST /trade-close`）

请求体（与 freqtrade 字段一一对应 + ai-service 加固）：

| 字段 | 类型 | 约束 | 备注 |
|---|---|---|---|
| `trade_id` | int | required | freqtrade Trade.id（DB PK） |
| `strategy` | str | 1-64 字符 | 来自 freqtrade 配置硬编码 |
| `pair` | str | 1-32 字符 | 例 "BTC/USDT" |
| `side` | "long"/"short" | default "long" | 当前 S1/S2 都 long-only |
| `direction` | str | informational | "Long"/"Short"，日志用 |
| `open_rate` | float | gt=0 | |
| `close_rate` | float | gt=0 | |
| `profit_ratio` | float | required | 0.05 = +5% |
| `profit_amount` | float | required | 计价币绝对盈亏 |
| `open_date` | datetime | ISO 8601 | 含 tz |
| `close_date` | datetime | ISO 8601 | 含 tz |
| `exit_reason` | str | default "unknown" | freqtrade ExitType 枚举 |
| `enter_tag` | str? | optional | freqtrade buy_tag |
| `stake_amount` | float | gt=0 | |
| `stake_currency` | str | default "USDT" | |
| `is_final_exit` | bool | default true | **唯一决定是否触发 reflection** |
| `sub_trade` | bool | default false | DCA 部分成交标记 |
| `extra` | dict | default {} | freqtrade 不认识的元数据 |

响应（`TradeCloseResponse`）：

| 字段 | 含义 |
|---|---|
| `status="recorded"` | 成功：LLM 调过、reflection 已写库 |
| `status="skipped"`, reason="partial_fill" | 非最终 fill，跳过 |
| `status="skipped"`, reason="already_reflected" | 重复 webhook，幂等 |
| `reflection_id` | DB 主键，仅 recorded 时有 |

错误：
- `422` — Pydantic 校验失败（freqtrade 字段错）
- `503` — LLM 不可用。freqtrade 收到 503 后会按 `retries: 2, retry_delay: 5`
  重试 2 次仍失败后放弃；其自身交易流不受影响（webhook 是 best-effort）。

---

## 3. 关键设计决策

### 3.1 只在 `is_final_exit=true` 触发

freqtrade 一次出场可能分多笔 fill（DCA 分批、限价单部分成交等）。`_notify_exit`
每次都发 `EXIT_FILL`，最后一条 `is_final_exit=true`。我们必须**只对最终 fill**
调 LLM，否则同笔交易会跑多次 reflection（且 LLM 看到的"profit_ratio"是部分
平仓的，不真实）。

freqtrade 设置里 `"sub_trade": true` 同样标记为 partial，统一跳过。

### 3.2 幂等键 = (trade_id, strategy)

freqtrade SQLite 与 ai-service SQLite 是两个 DB，但 trade_id 在 freqtrade 内
是全局自增主键，理论上不会跨策略撞。多策略隔离保留 `strategy` 字段作为幂等
键的一部分，防止**多 freqtrade 实例**意外复用 trade_id（每个策略一个容器）。
详见 `ai-service/tests/test_trade_close_webhook.py::test_trade_close_idempotency_keyed_on_strategy`。

### 3.3 LLM 503 → 端点 503（不静默吞）

webhook 失败是事件丢失的最大风险点。让 freqtrade 日志里看到明确 503 比重试
5 次还连不上更可诊断。freqtrade 自己的 trading loop 不受 webhook 影响。

### 3.4 hold_duration_hours 由 (open_date, close_date) 算

不信任 freqtrade 在 webhook 里单独传 duration（它实际不传这个字段），而是从
两个时间戳算。如果数据错乱（close < open）——已经在 main.py 里 clamp 到
`max(0.001, ...)` 防止 reflection schema 的 gt=0 校验失败。

### 3.5 signal_snapshot 把 freqtrade 字段压平

LLM 看到的 signal_snapshot 形如：
```python
{
  "enter_tag": "golden_cross",
  "exit_reason": "exit_signal",
  "stake_amount": 300.0,
  "stake_currency": "USDT",
  "profit_amount": 15.0,
  "direction": "Long",
  ...extra,  # freqtrade 不知道的策略侧额外数据
}
```
这样 reflection 的 prompt 里有 freqtrade 没传的字段（如 stake_amount）但保留
freqtrade 传的字段，是 LLM 复盘最需要的信号。

---

## 4. 配置文件清单

| 文件 | 变更 |
|---|---|
| `deploy/user_data/config/config.template.json` | 新增 `webhook` block（含 exit_fill 模板） |
| `deploy/user_data/config/dry-run.json` | 同上（与 template 同步，ADR-005 纪律） |
| `deploy/start.sh` | 加 `${AI_SERVICE_WEBHOOK_URL}` 替换 |
| `deploy/.env` | 加 `AI_SERVICE_WEBHOOK_URL=http://127.0.0.1:8000/trade-close` |
| `deploy/.env.example` | 同步 |
| `ai-service/app/api_schemas.py` | 新增 `TradeCloseRequest`/`TradeCloseResponse` |
| `ai-service/app/main.py` | 新增 `POST /trade-close` 端点 |
| `ai-service/app/db/repository.py` | 新增 `get_reflection_by_trade_id` |
| `ai-service/tests/test_trade_close_webhook.py` | 14 个测试 |

---

## 5. 当前状态 / 已知缺口

### 5.1 ✅ 已就绪
- ai-service 端点 + 幂等 + partial fill 过滤 + LLM 503 透传
- freqtrade 配置 + start.sh 替换 + .env 默认
- 14 个测试覆盖（happy path / partial / 幂等 / 503 / 字段校验 / 反向时间）
- 全套 199 测试通过，无回归

### 5.2 ⚠️ 等 Phase 2 ai-service 容器起来

freqtrade 容器已挂新配置（含 `webhook` block），但**容器内运行时配置
`/tmp/dry-run-runtime.json` 是启动时生成的快照**。下次重启容器时新配置生效。

ai-service 容器**尚未启动**（docker-compose.yml 里 ai-service 段还在注释里）。
在那之前，freqtrade 真发生 EXIT_FILL 时会 POST 到 `127.0.0.1:8000/trade-close`
→ 连接拒绝 → webhook 警告 log 2 条（retries=2）。不影响 freqtrade 自身。

### 5.3 ⚠️ 没做端点鉴权

当前 `/trade-close` 与 `/telegram/webhook` 一样没有 secret token。考虑：
- docker-compose 用 host 网络模式，端口 8000 只在宿主机监听
- ai-service 容器上线时建议监听 `127.0.0.1:8000`（不是 `0.0.0.0`），关闭外网
- 长期方案：HMAC 签名（freqtrade webhook 的 `webhook.headers` 字段支持自定义头）

短期可接受（host 网络 + 单机部署），但 Phase 2 上线前应至少做 IP allowlist 或
监听 `127.0.0.1`。

### 5.4 ⚠️ S1 hyperopt 结果不变

P1.4 报告已记录 S1 在 1d 上 2024-2026 holdout 零交易。webhook 接入后可能要
等数周才有第一笔真 EXIT_FILL 触发 reflection；weekly rollup 暂只能统计到 0。
这是策略事实，不是 webhook bug。

---

## 6. 验证命令

### 6.1 单元测试

```bash
DATABASE_URL=sqlite:///:memory: OPENAI_API_KEY=sk-test \
  pytest ai-service/tests/test_trade_close_webhook.py -v
```

### 6.2 端到端（等 ai-service 容器起来后）

```bash
# 1. 起 ai-service（Phase 2 启用 docker-compose.yml 里 ai-service 段）
# 2. 模拟 freqtrade 发 webhook（freqtrade 容器内）
docker exec sentinel-freqtrade python3 - <<'PY'
import json, requests
from datetime import datetime, timezone, timedelta
payload = {
    "trade_id": 99999,
    "strategy": "S1TrendFollow",
    "pair": "BTC/USDT",
    "direction": "Long",
    "open_rate": 60000.0,
    "close_rate": 63000.0,
    "profit_ratio": 0.05,
    "profit_amount": 15.0,
    "open_date": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
    "close_date": datetime.now(timezone.utc).isoformat(),
    "exit_reason": "exit_signal",
    "enter_tag": "golden_cross",
    "stake_amount": 300.0,
    "stake_currency": "USDT",
    "is_final_exit": True,
    "sub_trade": False,
    "side": "long",
}
r = requests.post("http://127.0.0.1:8000/trade-close", json=payload, timeout=15)
print(r.status_code, r.json())
PY
```

期望：`200 OK`，`status=recorded`，`reflection_id` 非空。
随后 `sqlite3 ai-service/sentinel.db 'SELECT * FROM reflections ORDER BY id DESC LIMIT 1;'`
应能看到刚写的行。

### 6.3 验证幂等

```bash
# 把上面命令再跑一次
# 期望：200 OK, status=skipped, reason=already_reflected, reflection_id 相同
```