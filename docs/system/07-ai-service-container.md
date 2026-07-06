# P2.1 — AI Service Container

**日期**: 2026-07-06
**目标**: 把 ai-service 装进 docker，跟 freqtrade 一起在 host 网络模式运行，
让 P2.5 的 webhook 真正打通闭环（之前 ai-service 容器还没起，freqtrade
每发一次 EXIT_FILL 都 connection-refused）。

---

## 1. 拓扑

```
┌─────────────────────────────────────────────────────────┐
│ Host network namespace (127.0.0.1)                      │
│                                                         │
│  freqtrade container ──┐                                │
│  (sentinel-freqtrade)  │                                │
│   • 0.0.0.0:8080 FreqUI │                                │
│   • webhook payload     │ POST /trade-close             │
│                         ▼                               │
│  ai-service container ────────► 127.0.0.1:8000          │
│  (sentinel-ai)          │       /healthz, /veto, ...    │
│   • /healthz ◄──────────┘                               │
│   • SQLite at /app/data/sentinel.db                     │
│     (named volume deploy_ai_data)                       │
│                                                         │
│  Both share host loopback → host.docker.internal 不可达 │
│  问题自然消失                                            │
└─────────────────────────────────────────────────────────┘
```

两个容器都用 `network_mode: host`，因此它们**直接共享宿主回环**——
freqtrade 用 `http://127.0.0.1:8000/trade-close` 打 ai-service，不需要
任何反向代理或端口映射。

---

## 2. 关键决策

### 2.1 host 网络模式（不是 bridge）

延续 deploy/docker-compose.yml 里 freqtrade 的选择。理由：国内环境下
host.docker.internal 在 OrbStack / 部分 Linux 配置下不可达，host 网络是最
可靠的路径。两个容器都用 host 网络意味着它们能直接互相访问 127.0.0.1，
这是 P2.5 webhook 唯一需要的能力。

### 2.2 ai-service 只绑 127.0.0.1:8000（不绑 0.0.0.0）

`scripts/start.sh` 默认 `AI_SERVICE_HOST=127.0.0.1`。即使在 host 网络
模式下显式绑 loopback，端口也不会被局域网其他主机访问到——这是
`docs/system/06-trade-close-webhook.md §5.3` 提到的"短期可接受"的
"端点鉴权缺失"缓解措施的第一层。

### 2.3 SQLite + named volume

DB schema 简单（4 张表，行数几千量级/年），SQLite + 命名 volume 足够。
named volume (`deploy_ai_data`) 让容器重建时数据不丢。后续如果想上
Postgres，只需改 `DATABASE_URL` 环境变量 + 加 `postgres` service（已
在 compose 文件末尾留好注释）。

### 2.4 LLM 访问走代理

跟 freqtrade 一样注入 `HTTPS_PROXY=http://127.0.0.1:7890`，因为 ai-service
调用的 agnes-ai / openai 也需要本地代理（国内环境）。

### 2.5 非 root 用户 + 写入目录

镜像里 `useradd sentinel`、`mkdir /app/data`、`chown sentinel:sentinel /app`。
容器内 uvicorn 进程跑在非 root，SQLite 文件落在 `/app/data/sentinel.db`。
Dockerfile 的 `HEALTHCHECK` 用 `curl` 直打 `/healthz`——这要求镜像带 curl，
所以在 `apt-get install` 里加了。

---

## 3. 配置文件清单

| 文件 | 状态 |
|---|---|
| `ai-service/Dockerfile` | 新增 — python:3.12-slim + requirements + curl |
| `ai-service/requirements.txt` | 新增 — 锁定 6 个核心依赖 |
| `ai-service/scripts/start.sh` | 新增 — uvicorn 启动脚本（绑定 127.0.0.1） |
| `deploy/docker-compose.yml` | 修改 — 启用 ai-service service + named volume |

环境变量（ai-service 容器）：
- `DATABASE_URL=sqlite:////app/data/sentinel.db`（持久化到 named volume）
- `HTTPS_PROXY=http://127.0.0.1:7890`（来自 docker-compose environment）
- `SCHEDULER_ENABLED=true`（可在 .env 里设 false 停掉后台任务）
- `AI_SERVICE_HOST=127.0.0.1` / `AI_SERVICE_PORT=8000`（绑 loopback）
- `AGNES_API_KEY` 或 `OPENAI_API_KEY`（**.env 当前为空**——见 §5）

---

## 4. 启动 / 验证

```bash
cd deploy

# 构建并启动（首次约 1 分钟）
docker compose build ai-service
docker compose up -d ai-service

# 状态
docker compose ps ai-service   # 期望 STATUS: Up (healthy)
docker compose logs --tail 25 ai-service
#   INFO:     Uvicorn running on http://127.0.0.1:8000
#   INFO:     127.0.0.1:40456 - "GET /healthz HTTP/1.1" 200 OK

# 从宿主机探活
curl -fsS http://127.0.0.1:8000/healthz
#   {"status":"ok","version":"0.1.0"}

# 端到端：模拟 freqtrade 发 webhook（partial_fill 路径，不调 LLM）
docker exec sentinel-freqtrade python3 - <<'PY'
import json, urllib.request
from datetime import datetime, timezone, timedelta
payload = {
    "trade_id": 99999, "strategy": "S1TrendFollow", "pair": "BTC/USDT",
    "direction": "Long", "open_rate": 60000.0, "close_rate": 63000.0,
    "profit_ratio": 0.05, "profit_amount": 15.0,
    "open_date": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
    "close_date": datetime.now(timezone.utc).isoformat(),
    "exit_reason": "exit_signal", "enter_tag": "golden_cross",
    "stake_amount": 300.0, "stake_currency": "USDT",
    "is_final_exit": False, "sub_trade": True, "side": "long",
}
req = urllib.request.Request("http://127.0.0.1:8000/trade-close",
    data=json.dumps(payload).encode(),
    headers={"Content-Type":"application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=10) as r:
    print("STATUS", r.status)
    print("BODY", r.read().decode())
PY
# 期望：STATUS 200, BODY {"status":"skipped","trade_id":99999,"reason":"partial_fill","reflection_id":null}
```

---

## 5. 当前状态 / 已知缺口

### 5.1 ✅ 已就绪
- Dockerfile + requirements.txt + entrypoint 三件套齐全
- docker-compose 启用 ai-service 服务，named volume 持久化 DB
- 容器构建成功、启动 healthy、`/healthz` 通
- 端到端联通验证：freqtrade 容器 → host loopback → ai-service 容器 →
  DB 写入 / partial-fill skip 路径 OK

### 5.2 ⚠️ `.env` 缺 LLM key

`deploy/.env` 当前 `AGNES_API_KEY` 和 `OPENAI_API_KEY` 都是空字符串。
容器启动时 `_settings()` 兜底成 `sk-fake-for-dev`，所以 reflection 路径
**会被 503**（LLM 返回 401 → 端点返 503）。

**修复方法**（任选）：
```bash
# 方案 A（推荐，访问 agnes-ai）
echo "AGNES_API_KEY=你的agnes_key" >> deploy/.env
docker compose restart ai-service

# 方案 B（用 OpenAI）
echo "OPENAI_API_KEY=sk-..." >> deploy/.env
docker compose restart ai-service
```

之后 full reflection e2e 可验证（`is_final_exit=true` 应得
`status=recorded`）。

### 5.3 ⚠️ 没有外网暴露（按设计）

ai-service 绑 127.0.0.1，局域网/公网都不可达。这是 §2.2 的安全缓解。
如果将来需要从另一台机器访问（dashboard 远程等），需要：
- 改 AI_SERVICE_HOST=0.0.0.0 + 加防火墙白名单，或
- 走反向代理 + 鉴权（nginx + basic auth / mTLS）

### 5.4 ⚠️ scheduler 自动启动

`scripts/start.sh` 默认 `SCHEDULER_ENABLED=true`，意味着容器一启动
就会开始跑 daily research / weekly rollup（cron 触发，时间通常 UTC）。
想让容器纯做 webhook 服务时设 `SCHEDULER_ENABLED=false`。

---

## 6. 验证命令

```bash
# 单元测试（依赖已通过 ai-service 镜像验证过能装上）
cd /Users/lazy/Code/crack/finance
DATABASE_URL=sqlite:///:memory: OPENAI_API_KEY=sk-test \
  pytest ai-service/tests/ -q

# 当前应在 199/199 通过（185 prior + 14 P2.5 webhook 测试），无回归
```