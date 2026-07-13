# QQ AI Bot

基于 **FastAPI + QQ 官方机器人 WebSocket** 的 AI 对接服务。群聊 @机器人 或私聊直接调用 AI，支持 OpenAI 兼容协议（DeepSeek / Qwen / GPT 等）、Google Gemini、Anthropic Claude 三类模型，并可通过 MCP（Model Context Protocol）扩展工具调用能力。

---

## 功能特性

- **双通道接入**
  - 群聊：`GROUP_AT_MESSAGE_CREATE` 事件 @机器人 自动回复
  - 私聊：`C2C_MSG_RECEIVE` 事件 DM 自动回复
- **多模型路由**：根据模型名前缀自动分发（`gpt-*` / `deepseek-*` → OpenAI 兼容；`gemini-*` → Google Gemini；`claude-*` → Anthropic）
- **多 Key 轮询**：每个 provider 支持配置多个 API Key，请求随机分发
- **多轮会话**：基于文件持久化的 `SessionManager`，重启不丢上下文；支持 `/reset`、`/clear`、`/new` 命令重置
- **MCP 工具扩展**：启动时连接配置的 MCP server，把工具以 OpenAI function-calling 格式注入对话；最大 2 轮 tool-call 防失控
- **HTTP 测试接口**：`/api/chat` 直接调用 AI（绕开 QQ），方便联调与压测
- **健康检查**：`/api/health`、`/api/models`、`/api/sessions`
- **自动重连**：WebSocket 断开后 5 秒重连；Hello / Ready / Resume / Reconnect 状态机完整
- **接收与处理解耦**：receive loop 只负责读帧并入队，独立的 dispatch worker 调用 handler；即使 AI 回复耗时数十秒，WebSocket 连接也不会因心跳 ACK 堆积或服务器重连请求被忽略而断开
- **TCP keepalive**：兼容 NAT / 防火墙 5 分钟超时

---

## 快速开始

### 1. 注册 QQ 机器人

1. 访问 [QQ 开放平台](https://q.qq.com) 注册开发者账号
2. 创建机器人应用，获取 `AppID` 和 `AppSecret`
3. 在管理端配置 **IP 白名单**（你的服务器公网 IP）
4. 开启 **群聊** 与 **单聊** 权限
5. 沙箱环境建议先在 [沙箱平台](https://sandbox.q.qq.com) 测试

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 填入你的配置
```

最小可用配置（仅 OpenAI 兼容协议）：

```bash
QQ_APP_ID=your_app_id
QQ_APP_SECRET=your_app_secret
AI_OPENAI_KEYS=sk-key1,sk-key2
AI_OPENAI_BASE_URL=https://api.openai.com/v1
AI_DEFAULT_MODEL=gpt-4o-mini
```

### 3. 安装依赖

要求 Python **≥ 3.10**。

```bash
pip install -e .
```

或使用 uv / poetry：

```bash
uv pip install -e .
```

### 4. 启动

```bash
python main.py
```

或使用 Docker：

```bash
docker compose -f deploy/docker-compose.yml up -d
```

启动后访问 `http://localhost:8000/docs` 查看 OpenAPI 文档。

---

## 配置项

完整配置见 [`.env.example`](.env.example)。

| 分组 | 变量 | 说明 |
|------|------|------|
| QQ | `QQ_APP_ID` / `QQ_APP_SECRET` | QQ 机器人凭证 |
| OpenAI 兼容 | `AI_OPENAI_KEYS`（逗号分隔）<br>`AI_OPENAI_BASE_URL` | 支持 DeepSeek / Qwen / GPT 等任意 OpenAI 兼容端点 |
| Gemini | `AI_GEMINI_KEYS`<br>`AI_GEMINI_BASE_URL`（可选，用于代理） | Google 官方 SDK；多 Key 轮询 |
| Claude | `AI_CLAUDE_KEYS` | Anthropic 官方 SDK |
| 模型默认 | `AI_DEFAULT_MODEL` / `AI_SYSTEM_PROMPT`<br>`AI_MAX_TOKENS` / `AI_TEMPERATURE` / `AI_PRESENCE_PENALTY` | 缺省参数 |
| 会话 | `SESSION_TIMEOUT`（分钟）<br>`SESSION_MAX_HISTORY`（每用户消息数）<br>`SESSIONS_DIR` | 文件持久化路径 |
| MCP | `MCP_SERVERS` | JSON 数组，见下节 |
| 服务 | `HOST` / `PORT` | 默认 `0.0.0.0:8000` |

---

## MCP 工具配置

`MCP_SERVERS` 是一个 JSON 数组，每个元素描述一个 MCP server。三种传输方式：

```bash
# SSE 远程服务
MCP_SERVERS=[{"type":"sse","url":"http://localhost:3000/sse"}]

# Streamable HTTP 远程服务（Firecrawl 等）
MCP_SERVERS=[{"type":"streamable_http","url":"https://mcp.firecrawl.dev/v2/mcp"}]

# Stdio 本地进程
MCP_SERVERS=[{"type":"stdio","command":"python","args":["/path/to/server.py"]}]

# 混合多个
MCP_SERVERS=[{"type":"streamable_http","url":"https://mcp.firecrawl.dev/v2/mcp"},{"type":"stdio","command":"node","args":["server.js"]}]
```

启动时所有 server 一次性连接，工具列表缓存到内存。每次 AI 对话最多 **2 轮 tool-call**（防止超过 QQ 5 分钟被动回复窗口）。

---

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 服务基本信息 |
| GET | `/api/health` | 健康检查（含 QQ 连接状态） |
| GET | `/api/models` | 列出当前可用模型 |
| GET | `/api/sessions` | 活跃会话数 |
| POST | `/api/chat` | 直接调用 AI |

### 调用示例

```bash
# 健康检查
curl http://localhost:8000/api/health

# 列出可用模型
curl http://localhost:8000/api/models

# 调用 AI（默认模型）
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"你好"}]}'

# 指定模型 + 多轮
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-3-5-sonnet-20241022",
    "messages": [
      {"role":"system","content":"你是一个简洁的助手"},
      {"role":"user","content":"用一句话介绍 FastAPI"}
    ]
  }'
```

> **安全提示**：`/api/*` 当前**无任何鉴权**，部署到公网前请在反代层（Nginx / Caddy / Cloudflare）加 IP 白名单或 Basic Auth，避免 AI 配额被滥用。

---

## 项目结构

```
qq-ai-bot/
├── main.py                      # FastAPI 入口 + 生命周期
├── config.py                    # pydantic-settings 配置（支持逗号分隔多 Key）
├── pyproject.toml
├── .env.example
├── README.md
├── api/
│   ├── router.py                # /api/health /api/models /api/sessions /api/chat
│   └── schemas.py               # Pydantic 请求/响应模型
├── qq_bot/
│   ├── client.py                # WebSocket 客户端（接收/处理解耦、OpCode 状态机、自动重连、TCP keepalive）
│   ├── gateway.py               # AccessToken 刷新 + Gateway URL 拉取
│   ├── messages.py              # 群 / 私聊消息发送（Markdown msg_type=2）
│   ├── events.py                # 事件 Pydantic 模型
│   └── intents.py               # Intent 位标志
├── services/
│   ├── ai_router.py             # 多 provider 路由 + MCP tool loop
│   ├── openai_compat.py         # OpenAI 兼容 adapter（DeepSeek / Qwen / GPT）
│   ├── claude_adapter.py        # Anthropic Claude adapter
│   ├── gemini_client.py         # Google Gemini adapter（含 JSON Schema 清洗）
│   └── mcp_tools.py             # MCP 连接、工具注册、调用
├── plugins/
│   ├── group_ai/                # 群聊 @机器人 handler
│   │   ├── __init__.py          # 事件处理 + at-tag 注入 + 命令解析
│   │   └── context.py           # SessionManager（文件持久化）
│   └── c2c_ai/                  # 私聊 handler
│       └── __init__.py
└── deploy/
    ├── Dockerfile
    └── docker-compose.yml
```

---

## 架构：接收与处理解耦

```
                   ┌─────────────────────────────────────────────┐
                   │              QQBotClient.start()             │
                   │                                             │
                   │  ┌──────────────┐    ┌───────────────────┐  │
   WebSocket  ────►│  │ _receive_loop │───►│ asyncio.Queue     │  │
   (读帧)          │  │  只读帧+入队  │    │ (dispatch_queue)  │  │
                   │  └──────────────┘    └────────┬──────────┘  │
                   │                               │             │
                   │  ┌──────────────┐    ┌────────▼──────────┐  │
   心跳 OpCode 1  │  │_heartbeat_loop│    │  _dispatch_loop   │  │
   ──────────────►│  │  定时发送     │    │  取事件→调 handler │  │
                   │  └──────────────┘    │  (AI 调用可阻塞)   │  │
                   │                      └───────────────────┘  │
                   └─────────────────────────────────────────────┘
```

**为什么这样设计**：AI handler（尤其使用 MCP 工具时）可能阻塞数十秒。如果
在 receive loop 内直接 `await handler()`，WebSocket 会在这段时间无人读取，
导致心跳 ACK 堆积、服务器 Reconnect 请求（OpCode 7）被忽略、连接最终被
`websockets` 库因 `max_queue` 溢出而关闭。解耦后，receive loop 永远在读帧，
连接始终保持健康。

dispatch worker 在 `start()` 中创建一次，**跨重连保持存活**——即使 socket
断开，队列中尚未处理的事件也不会丢失。

---

## 部署

### Docker Compose（推荐）

```bash
cd deploy
cp ../.env.example ../.env  # 编辑后填入真实凭证
docker compose up -d
```

`docker-compose.yml` 已配置：
- 日志滚动 `max-size: 10m` × 3 份
- 端口映射 `8000:8000`
- 加载上级目录 `.env`
- `restart: unless-stopped`

### 手动部署

```bash
pip install -e .
python main.py
```

### 反向代理建议

公网部署推荐 Nginx / Caddy 终结 TLS，并加上：

```nginx
# 示例：仅允许特定 IP 访问 /api/*
location /api/ {
    allow 1.2.3.4;        # 你的家庭 / 公司 IP
    deny all;
    proxy_pass http://127.0.0.1:8000;
}
```

---

## 注意事项

- **IP 白名单**：新机器人必须配置，否则无法连接
- **被动回复时效**：收到事件后 **5 分钟** 内必须回复；MCP 工具循环硬限制 2 轮
- **消息去重**：使用 `msg_seq` 防止重复回复
- **沙箱环境**：开发阶段建议先在 [QQ 沙箱平台](https://sandbox.q.qq.com) 测试
- **会话持久化**：默认存储到 `./sessions/<group_openid>.json`，**生产环境建议挂载持久卷**
- **时区**：所有时间戳假设为北京时间（UTC+8），token 过期判断使用 UTC

---

## 安全建议

1. **`/api/chat` 无鉴权** — 部署到公网前务必在反代层加 IP 白名单或 API Key 校验，避免 AI 配额被滥用
2. **用户消息内容写入 DEBUG 日志**（`qq_bot/client.py` `_receive_loop` / `_handle_dispatch`）— 生产环境建议将日志级别设为 `WARNING` 及以上，避免隐私数据落盘

---

## 路线图

- [ ] `/api/chat` 加 API Key 鉴权
- [ ] Dockerfile 增加非 root USER
- [ ] 会话存储迁移至 SQLite（替代 JSON 文件）
- [ ] 增加单元测试（SessionManager、Provider 路由、MCP tool loop）
- [ ] 引入 ruff + mypy + pytest CI

---

## 许可证

MIT
