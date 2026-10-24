# 聊天机器人平台 — 架构状态与路线

更新日期：2026-09-03

## 当前基线

- 安全：多层限流、熔断、内容过滤、Web 令牌认证、WebSocket 来源校验、请求 ID。
- 可靠性：LLM 故障转移、优雅关闭、revision 乐观并发、SQLite/PostgreSQL 会话快照与重启恢复。
- 可观测性：liveness/readiness、HTTP 与 WebSocket 指标、Engine/存储/限流/熔断状态。
- 架构：四阶段 Pipeline、依赖注入、插件钩子、可替换 `SessionStore`、应用状态容器、统一平台生命周期状态机。
- 工程：Python 3.11+、Docker healthcheck、Ruff、pytest、数据库增量迁移。

## 已完成的本轮 P0/P1

| 项目 | 状态 |
|---|---|
| WebSocket 流式对话完整经过 Pipeline | 完成 |
| 管理面板认证与 WebSocket 来源校验 | 完成 |
| 持久化会话元数据和消息快照 | 完成 |
| 启动恢复与 `/new` 删除同步 | 完成 |
| 同会话并发竞态保护 | 完成 |
| liveness/readiness 分离 | 完成 |
| HTTP/WebSocket/Engine/存储运行指标 | 完成 |
| 多数据库路径连接池隔离 | 完成 |
| 纯 ASGI 认证/安全头/追踪/指标中间件 | 完成 |
| SQLite WAL 与迁移 SHA-256 完整性校验 | 完成 |
| PostgreSQL SessionStore 与实体记忆统一存储 | 完成 |
| revision 乐观并发与跨实例 PostgreSQL advisory lock | 完成 |
| PostgreSQL 16 CI 集成契约 | 完成 |
| Web request_id 幂等、重连和结果回放 | 完成 |
| 完整 `/api/v1` 与类型化 OpenAPI 契约 | 完成 |
| 登录/聊天模板和静态资源分离 | 完成 |
| 管理页面统一外部资源、严格 CSP 与 WebSocket 心跳退避 | 完成 |
| 非 root、只读根文件系统的容器加固 | 完成 |
| 统一 LLM Client/Provider、流式故障转移、冷却与连接池生命周期 | 完成 |
| 主配置与备用配置 schema 对齐、通用 LLM 环境变量 | 完成 |
| 幂等 owner token、payload fingerprint、租约与取消隔离 | 完成 |
| 分布式锁池与业务连接池隔离 | 完成 |
| 旧会话按需 hydration 与单查询一致快照 | 完成 |
| 跨平台主体记忆隔离、实体开关真实生效 | 完成 |
| Web fail-closed、回环端口与私密响应禁止缓存 | 完成 |
| 同步向量 I/O 脱离事件循环、WebSocket 慢消费者隔离 | 完成 |
| 严格配置 schema 与 wheel 迁移资源验收 | 完成 |

## 后续优先级

### P1：生产化

1. 统一 `Principal / Workspace / Membership / RBAC / AuditEvent`，替换裸平台用户 ID 与全局共享令牌。
2. 追加式 `Conversation / Message / Run / InboundEvent / OutboxDelivery`，实现跨副本和重启幂等。
3. 将聊天垂直链路抽成 `ChatOrchestrator` 应用用例，Engine 退化为兼容 façade 与生命周期容器。
4. 独立 Alembic migration job、备份恢复演练和数据库 DDL/DML 角色分离。

### P2：可维护性

1. 将 Plugin 升级为带 schema、权限、deadline、凭据隔离和调用审计的 Tool 系统。
2. 建立 Run trace、供应商 usage/成本、用户反馈、golden eval 与 Prompt 版本发布闭环。
3. 为管理端补持续运行的浏览器端到端测试、无障碍扫描和双副本故障注入。
4. 引入依赖锁、类型检查、架构依赖门禁与覆盖率阈值。

从 0 到 1 的分阶段目标和验收定义见 [平台化路线图](platform-roadmap.md)，设计来源与取舍见
[GitHub 成熟项目参考架构](reference-architecture.md)。

## 2026-10-23 QQ 拟人化落地

- 入站 OneBot 段解析（数组 + CQ）、引用解析、私聊媒体下载与多模态识图。
- `ReplyPlan` 投递接缝：分段、延迟、首条引用、表情包、语音；群聊与旧入口保持字符串路径。
- 上下文预算、每会话恢复窗口、追加型 `conversation_archive` 与增量摘要。
- 版本化人格（候选制、回滚、衰减）与 `/persona`、`/sticker` 命令及 v1 API。
- 可选语音（Whisper/Edge TTS，`--voice` 构建）、出站审计与发送配额。
- Token 账本（cache miss/hit/write/output、峰谷计价）与 `/api/v1/usage`。
