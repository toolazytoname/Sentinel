# Sentinel — 部署与运行手册（RUNBOOK）

> 本手册覆盖 Phase 0 的本地启动、监控、停止、常见问题处理。
> 阅读对象：项目维护者（含明早起来要激活 Telegram 的你本人）。

## 1. 环境前置

| 依赖 | 最低版本 | 检查命令 |
|---|---|---|
| Docker | 29.x | `docker --version` |
| Docker Compose | v2.x | `docker compose version` |
| OrbStack（macOS 推荐） | latest | OrbStack 已自带 docker socket |
| 本地代理 | listening on 127.0.0.1:7890 | `lsof -iTCP:7890 -sTCP:LISTEN` |

## 2. 启动

```bash
cd /Users/lazy/Code/crack/finance/deploy

# 1. 首次启动：拷贝环境模板
[ -f .env ] || cp .env.example .env
chmod 600 .env

# 2. 编辑 .env，填入密钥（明早起床后做）
#    - FREQTELEGRAM__TELEGRAM_TOKEN（@BotFather 创建机器人获取）
#    - FREQTELEGRAM__CHAT_ID（@userinfobot 获取你的数字 user_id）
#    - API_PASSWORD（FreqUI 登录密码，建议至少 16 位）
#    - API_JWT_SECRET（JWT 签名密钥，任意长随机字符串）
#    - EXCHANGE_KEY/SECRET（Phase 0 dry-run 留空；切 live 前必填）

# 3. 启动
docker compose up -d

# 4. 查看状态
docker compose ps
docker compose logs -f freqtrade
```

## 3. 访问

- **FreqUI（Web 界面）**：http://localhost:8080
  - 用户名：`sentinel`
  - 密码：`API_PASSWORD` 的值
- **REST API**：http://localhost:8080/api/v1/
- **日志**：`/freqtrade/user_data/logs/freqtrade.log`（容器内路径），宿主对应 `deploy/user_data/logs/`

## 4. 常用命令

```bash
# 实时日志
docker compose logs -f freqtrade

# 进入容器（调试用）
docker compose exec freqtrade bash

# 停止（保留 SQLite 和日志）
docker compose down

# 重启
docker compose restart freqtrade

# 完全清空（包括 SQLite / 策略 / 日志）—— ⚠️ 删数据，慎用
docker compose down -v
rm -rf user_data/*

# 重新下载历史数据（Phase 1 需要）
docker compose run --rm freqtrade download-data \
    --exchange okx --pairs BTC/USDT ETH/USDT \
    --timeframes 1d 4h --days 1500
```

## 5. 明早起床后：激活 Telegram

```bash
# Step 1：创建 Telegram bot
#   手机 Telegram → 搜索 @BotFather → 发送 /new
#   按提示设置 name 和 username → 拿到 token（形如 123456789:ABC...）
#   把它填进 deploy/.env 的 FREQTELEGRAM__TELEGRAM_TOKEN

# Step 2：获取你的 user_id（数字）
#   搜索 @userinfobot → 发任意消息 → 它会回你 user_id
#   填进 .env 的 FREQTELEGRAM__CHAT_ID

# Step 3：允许命令控制（可选，但推荐）
#   把同一个 user_id 也填进 FREQTELEGRAM__ALLOWED_CHAT_IDS

# Step 4：启用 freqtrade 的 telegram 模块
#   编辑 deploy/user_data/config/dry-run.json：
#     "telegram": { "enabled": true, ... }
#   （token 和 chat_id 留空字符串即可，freqtrade 会从环境变量读）

# Step 5：重启
cd /Users/lazy/Code/crack/finance/deploy
docker compose restart freqtrade

# Step 6：测试
#   给你的 bot 发 /status，应该能收到 freqtrade 当前状态
#   还能用 /profit /balance /forceexit <trade_id> 等命令
```

完整 Telegram 命令列表：https://www.freqtrade.io/en/stable/telegram-usage/

## 6. 网络代理（必读）

国内环境下，所有访问 Binance/OKX 等交易所的请求必须经代理。本项目在 `docker-compose.yml` 中已配置：

```yaml
environment:
  - HTTP_PROXY=http://host.docker.internal:7890
  - HTTPS_PROXY=http://host.docker.internal:7890
  ...
```

`host.docker.internal` 是 Docker 访问宿主机回环地址的官方别名。如果你在 Linux 上跑（用 Docker Desktop 或原生 Docker），这个别名也可用；如果是 Linux 服务器上的原生 docker，请改成宿主机的内网 IP。

**如果你换了代理端口**（比如 Clash 用了 7891），需要：
1. 改 `docker-compose.yml` 的端口
2. 改 `.gitignore` 不变的 `.env` 中保留的代理配置（如有）
3. 重建容器：`docker compose up -d --force-recreate freqtrade`

## 7. 安全清单（运维前必看）

- [ ] **API key 只开交易权限，禁止提现** —— 切 live 前在交易所后台确认
- [ ] **.env 权限永远是 600** —— `chmod 600 deploy/.env`
- [ ] **FreqUI 密码和 JWT secret 不要用默认值** —— Phase 0 的占位仅用于本地测试
- [ ] **不要把 SQLite 数据库 git commit** —— `.gitignore` 已覆盖，但要自觉
- [ ] **不要在容器内手动改配置** —— 容器重启后改动丢失；改宿主机上的 `user_data/config/*.json`

## 8. 故障排查

### 8.1 容器反复重启
```bash
docker logs sentinel-freqtrade --tail 50
```
常见原因：
- `Could not load markets` → 网络/代理问题，检查 6 节
- `Configuration error: ... deprecated` → freqtrade 版本升级导致配置语法变化，去 [官方迁移文档](https://www.freqtrade.io/en/stable/) 查
- `Permission denied` → 检查 `user_data/` 目录权限

### 8.1.1 Phase 0 启动记录（2026-07-05/06）
- ✅ 容器正常启动、FreqUI 端口 8080 监听
- ✅ freqtrade 进程存活、读到了 dry-run.json
- ✅ Docker 代理环境变量注入成功（容器内访问 Binance/OKX API 不再是 "not available"）
- ⚠️ **freqtrade 内部访问交易所仍然 RequestTimeout** —— 怀疑本地代理 (127.0.0.1:7890) 未把 `api.binance.com` / `www.okx.com` 列入代理规则；明早起来后**第一步先验证代理本身能否访问交易所**
  - 验证命令（在主机终端）：`curl -x socks5h://127.0.0.1:7890 https://api.binance.com/api/v3/ping`（应返回 `{}`）
  - 如不通，检查代理客户端（Clash / Surge / v2rayN）的规则列表，必要时切换节点或换代理端口
- 📝 配置曾因 freqtrade 2026.6 弃用顶层 `protections` 而临时移除；Phase 1 实现自定义策略时需要在策略类内通过 `protections` 参数重新引入（这是 P1.x 的工作）

### 8.2 FreqUI 能打开但登录失败
- 确认密码是 `API_PASSWORD` 的值（不是字面量 `***REMOVED***`）
- 如果改过 .env 没重启容器 → `docker compose restart freqtrade`

### 8.3 Telegram 收不到消息
- 先用 `/start @你的bot` 给 bot 发个消息激活会话
- 确认 `enabled: true` 已配置
- 看 `docker logs | grep telegram` 的报错（最常见是 chat_id 填错成 username）

### 8.4 端口 8080 被占用
```bash
lsof -iTCP:8080 -sTCP:LISTEN
# 杀掉占用进程，或改 docker-compose.yml 的 ports 映射（左边的 8080）
```

## 9. 端口暴露与防火墙加固（RS.3）

**核心风险**：VPS 上防火墙配错，ai-service (8000) 或 FreqUI (8080) 被公网直接访问。

- **ai-service (8000)** 与 **FreqUI (8080)** 绝不能对公网可达，只允许本机（loopback）与你的管理 IP 访问。
- 本项目用 `network_mode: host`，此时 compose 里的 `ports:` 映射**无效**——容器直接共享宿主网络栈，真正决定可达性的是**进程的绑定地址**：
  - ai-service 通过 `scripts/start.sh` 绑定 `${AI_SERVICE_HOST:-127.0.0.1}`（默认 loopback），已避免 `0.0.0.0`。
  - FreqUI 监听 8080，同样应确认其 `api_server.listen_ip_address` 为 `127.0.0.1`（或用下面的防火墙兜底）。
- **端点保护分层**：
  - `/veto` 与 `/trade-close` **故意不加令牌**——由本机 freqtrade/策略调用，无法发自定义头，仅靠此处的网络隔离（loopback 绑定 + 防火墙）保护。
  - OPS 写端点 `/strategy/register`、`/strategy/check`、`/research/note`、`/reflection` 在设置了 `SENTINEL_API_TOKEN` 后**额外**要求请求头 `X-Sentinel-Token`，不匹配返回 401（见 `.env.example`）。

### 9.1 ufw 兜底示例

即使进程已绑定 loopback，也建议加一层防火墙纵深防御。允许 SSH，拒绝公网访问 8000/8080，只放行本机与管理 IP：

```bash
# 默认策略：拒绝入站、允许出站
sudo ufw default deny incoming
sudo ufw default allow outgoing

# 放行 SSH（否则会把自己锁在门外！先确认端口，默认 22）
sudo ufw allow 22/tcp

# 8000 / 8080 只允许本机回环（host 网络下本机组件间通信）
sudo ufw allow from 127.0.0.1 to any port 8000 proto tcp
sudo ufw allow from 127.0.0.1 to any port 8080 proto tcp

# 如需从管理机远程访问 FreqUI/ai-service，只放行你的固定 IP（替换 A.B.C.D）
sudo ufw allow from A.B.C.D to any port 8080 proto tcp

# 其余入站到 8000/8080 一律拒绝（默认 deny incoming 已覆盖，此处显式声明更直观）
sudo ufw deny 8000/tcp
sudo ufw deny 8080/tcp

sudo ufw enable
sudo ufw status verbose
```

> 提示：`ufw allow from 127.0.0.1` 对某些内核/回环流量并非必需（本机 loopback 通常不过 INPUT 链的公网规则），但显式声明可读性更好，也避免个别配置下误伤。真正兜底的是「默认 deny incoming + 只放行 SSH 与管理 IP」。

## 10. 下一步（明早任务清单）

激活 Telegram 后，对照 `docs/system/03-tasks.md` 进入 P1：
- P1.1 下载历史数据（命令见 4 节）
- P1.2 实现 S1TrendFollow 策略（详见 `docs/system/02-design.md` §3）
- P1.6 dry-run 持续运行 ≥28 天

在此期间，本地保持 freqtrade 容器 7×24 运行（用 `restart: unless-stopped` 已经实现了，开机自启 + 崩溃自愈）。