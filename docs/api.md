# Web API 契约

## 版本策略

- 正式前缀：`/api/v1`
- 兼容前缀：`/api`
- OpenAPI：`/openapi.json`，只包含正式 v1 路径
- 兼容前缀暂不移除，但新客户端和管理页面不得继续依赖它

当未来出现不兼容的请求或响应变化时新增 `/api/v2`，v1 在明确的弃用周期内继续维护。

## 认证

- 浏览器：调用 `/api/v1/auth/login` 后使用 `HttpOnly`、`SameSite=Strict` Cookie
- API 客户端：`Authorization: Bearer <CHATBOT_WEB_AUTH_TOKEN>`
- 公开接口：登录、认证检查、liveness、readiness 和静态资源
- 其他页面、API、OpenAPI schema 与 WebSocket 均受同一认证策略保护

## 主要接口

| 分组 | 路径 |
|---|---|
| 健康与指标 | `/health`、`/health/ready`、`/metrics`、`/stats`、`/stats/detailed` |
| 会话 | `/sessions`、`/sessions/{id}`、`/sessions/{id}/export` |
| 对话 | `/chat` |
| 角色与风格 | `/cards`、`/cards/{name}`、`/styles` |
| 管理 | `/logs`、`/platforms/status`、`/learning/{card}` |
| 人格 | `/persona`、`/persona/approve/{trait_id}`、`/persona/rollback`、`/persona/traits` |
| 表情包 | `/stickers`、`/stickers/{digest}/approve`、`/stickers/{digest}/reject` |
| 用量 | `/usage` |
| 认证 | `/auth/login`、`/auth/logout`、`/auth/check` |

表内路径均相对于 `/api/v1`。

`GET /platforms/status` 返回每个已注册适配器的 `state`、`running`、`ready`、连接状态及有界计数器。
`last_error` 只暴露异常类型，不返回可能包含平台令牌的请求 URL 或响应正文。

`GET /metrics` 的 Engine 区域包含 `persistence_conflicts` 和 `session_coordinator`。后者公开协调器
后端、当前活跃 key、获取次数、竞争次数和超时次数，不包含 session key 或用户标识。

## 请求关联与幂等

- HTTP 响应始终携带 `X-Request-ID`。
- `/chat` 可接收 `request_id`；相同 `client_id + request_id` 的并发请求共享结果，已完成请求返回缓存结果并附加 `replayed: true`。
- `request_id` 复用但消息、角色卡或风格不同，HTTP 返回 `409`，WebSocket 返回 `idempotency_conflict`；不会回放错误请求的结果。
- 进程内 owner 具有 120 秒租约；waiter 等待有确定上限，租约过期后新请求可凭新 token 接管，旧 owner 不能提交到新 generation。
- `/ws/chat` 使用同一语义，每个 chunk 和 done 事件都会回传 `request_id`。
- 平台消息优先使用 `session_key + message_id` 去重，避免不同会话之间发生编号碰撞。
