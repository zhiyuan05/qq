# GitHub 成熟项目参考架构

调研日期：2026-09-03。这里只迁移可验证的架构模式，没有复制第三方实现代码。

| 参考项目 | 成熟模式 | 本项目采用情况 |
|---|---|---|
| [Open WebUI](https://github.com/open-webui/open-webui) | 持久化 Chat 与规范化消息分离；高频字段建索引；数据库边界清洗；运行依赖放入应用状态；流式接口使用纯 ASGI 中间件 | 新增 `chat_sessions` 元数据表、复用 `conversations` 消息表、索引和 NUL 清洗；Web 状态移入 `app.state`；认证、安全头和指标改为纯 ASGI |
| [Chainlit](https://github.com/Chainlit/chainlit) | 用可选 Data Layer 隔离存储实现；连接丢失后短期保留会话以支持恢复；构建后的前端静态资源由 FastAPI 服务 | 新增小型 `SessionStore` Protocol；Engine 不绑定 SQLite；Web 重连复用稳定 client/request ID；管理页面与 Python 路由彻底分离 |
| [Rasa Tracker Store](https://github.com/RasaHQ/rasa/blob/main/rasa/core/tracker_store.py) | Tracker Store 通过 retrieve/save/get-or-create 恢复对话状态，并允许 SQL 等不同后端实现 | `SessionStore` 隔离存储契约；启动恢复、单会话读取、事务保存和删除走同一接口 |
| [LangGraph PostgreSQL Checkpointer](https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-postgres) | PostgreSQL checkpoint 独立封装连接、事务和并发访问，供多个运行实例共享状态 | PostgreSQL 成为正式后端；每次进入跨实例临界区后重新读取会话快照，避免长期驻留内存状态覆盖新提交 |
| [SQLAlchemy version counters](https://docs.sqlalchemy.org/en/20/orm/versioning.html) | `version_id_col` 在 UPDATE 时校验旧版本，可检测过期 ORM 状态 | `chat_sessions.revision` 非空且单调递增；元数据 CAS 成功后才在同一事务替换消息快照 |
| [FastAPI Full Stack Template](https://github.com/fastapi/full-stack-fastapi-template) | 聚合子 Router、稳定 API 前缀、类型化 OpenAPI、前后端资源分离 | 同一组 Router 挂载兼容 `/api` 与正式 `/api/v1`；OpenAPI 只发布 v1；全部 Web 页面资源移出 Python |
| [Dify](https://github.com/langgenius/dify) | readiness 与可观测性独立配置，生产环境可接 OpenTelemetry | 当前先提供无额外依赖的有界运行指标；OpenTelemetry 作为下一阶段可选增强 |
| [LiteLLM](https://github.com/BerriAI/litellm) | Router 集中处理 fallback、retry、cooldown 与部署级故障状态 | `ProviderRegistry` 统一普通/流式路由；连续失败 Provider 临时冷却，统计与路由状态位于同一边界 |
| [Semantic Kernel](https://github.com/microsoft/semantic-kernel) | Chat Completion Service 可替换，并为流式内容提供专用异步契约 | `LLMProvider.chat_stream()` 成为正式契约；Provider 自己处理协议事件，上层不访问 SDK 私有字段 |
| [OneBot v11](https://github.com/botuniverse/onebot-11/blob/master/communication/ws.md) / [NapCatQQ](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-onebot/config/config.ts) | WebSocket 同时承载事件推送和带 `echo` 的动作调用；令牌放在握手头；实现提供心跳和重连配置 | 删除非标准 `/get_new_msg` 轮询；QQ 改为单接收协程、独立事件队列、`echo` Future 路由和指数退避重连 |
| [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) / [Telegram Bot API](https://core.telegram.org/bots/api#getupdates) | 明确 initialize/start/stop/shutdown 边界；`offset` 只确认已处理更新，并在每轮响应后推进 | 平台基类统一幂等状态机并持有后台任务；Telegram 顺序确认更新，回复失败时保留 offset，缓存计算结果后只重试发送 |
| [websockets](https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html) | 新 asyncio 客户端支持上下文管理、保活、额外握手头和断线重连 | QQ 连接由适配器完整拥有，停止时关闭 socket、失败所有待定动作并回收 worker，不遗留孤儿任务 |

## 本轮关键决策

1. SQLite 继续作为单机默认后端，降低部署门槛；PostgreSQL 是共享状态和多实例运行的生产后端。
2. 会话快照在单个事务内替换，避免只写入一半；不同数据库 URL 使用独立连接池，测试和部署配置互不污染。
3. 同一个 `session_key` 串行处理，优先保证上下文顺序和快照一致性；不同会话仍可并发。单机锁会回收空闲 key，多实例使用事务级 PostgreSQL advisory lock。
4. liveness 只说明进程可响应，readiness 还检查 Engine 启动状态和持久化连接，可直接用于 Docker healthcheck。
5. 指标只暴露计数和状态，不包含消息正文、令牌或用户标识。
6. Web 请求以 `client_id + request_id` 为幂等键：首个请求拥有执行权，并发副本等待同一结果，已完成副本直接回放。
7. 数据库迁移一旦应用便不可原地修改；SHA-256 不一致会阻止启动，避免代码与实际 schema 静默漂移。
8. OpenAPI 是正式契约而不是路由副产品：只暴露 v1、operation ID 不随路径变化、主要响应由 Pydantic schema 描述。
9. 生产容器默认非 root，应用代码位于只读根文件系统；写操作限制在明确挂载的数据与角色卡目录。
10. 管理端采用同源静态壳和稳定页面 URL，不把用户数据插入 HTML 字符串；CSP 不再允许内联脚本或样式。
11. Engine 只依赖统一 `ChatClient` 契约；兼容入口只负责构造，不再维护另一套调用逻辑。
12. 流式调用只在首块输出前允许故障转移；已经输出后中断时明确结束，避免不同模型内容拼接或重复。
13. 同一 API 端点的主/备用模型共享连接池；资源所有权明确，Engine 只关闭自己创建的客户端。
14. 平台适配器通过 `stopped → starting → running → stopping` 状态机管理资源；失败启动完成回滚后仍保留 `failed` 诊断状态。
15. 平台发送不再静默吞错，而是返回 `DeliveryReceipt` 或抛出类型化异常；管理 API 只公开异常类型，避免令牌随 URL 泄露。
16. Telegram 更新采用“回调结果缓存 + 发送成功后推进 offset”；可避免发送失败时重复调用 LLM，同时保留至少一次回复交付语义。
17. OneBot 接收与事件处理分离：接收协程优先路由动作响应，事件进入有界队列，从结构上消除回调内发送消息造成的 WebSocket 自锁。
18. `revision` 同时是 ORM version counter 和存储契约：过期快照写入失败且整笔事务回滚，消息子表不会被部分替换。
19. 分布式锁使用 transaction-scoped 而非 session-scoped advisory lock，连接归还池时自动释放，也兼容 transaction pooling；代价是处理期间独占一个连接。
20. 会话与实体记忆共享同一 SQLAlchemy 数据连接池；分布式锁使用独立命名池，避免锁持有者在小池配置下等待自己的连接形成自锁。
21. SQLite 历史迁移仍使用不可变 SQL + SHA-256；PostgreSQL 初始 schema 创建通过固定 advisory lock 串行化，后续复杂演进再切换 Alembic。
22. 非回环 Web 监听没有管理令牌时直接拒绝启动；Compose 端口只绑定宿主机回环地址，公网入口交给 TLS 反向代理。
23. 幂等 owner 由不可复用 token 标识，waiter 取消不能删除共享执行；相同 request ID 的 payload 哈希不一致会明确冲突。
24. 在完整 Account/Workspace 模型落地前，所有记忆先统一使用 `platform:user_id` 主体键；无法判断来源的旧裸 ID 数据不做猜测性跨平台绑定。
25. `restore_limit` 只控制启动预热量，不再决定数据可见性；任何持久化模式遇到本地 cache miss 都会在会话锁内按 key 懒加载。
26. 会话元数据与消息通过单条 JOIN 查询组装，避免 PostgreSQL `READ COMMITTED` 下两次 SELECT 分别看到不同 revision。
27. Chroma 客户端仍是同步接口，但搜索、写入和清理均进入受管理线程，不再阻塞 WebSocket、平台轮询和其他会话的事件循环。
28. WebSocket 广播对连接快照并发发送并设置逐连接 deadline；慢消费者会被淘汰，不能阻塞 HTTP chat 返回或其他客户端。
29. 所有运行配置模型统一 `extra=forbid`；未知顶层或嵌套键会在启动时失败，避免拼写错误被静默当成默认值。
30. 创建共享角色、触发/重置学习和修改群配置进入显式权限边界；管理员可使用平台作用域 ID，群管理员只管理所属群。

## 下一阶段路线

- 多实例部署：将 Web 幂等缓存和平台事件队列移入共享基础设施，并增加入口主节点选举。
- 数据演进：当迁移数量增长后引入 Alembic，并为回滚与备份建立发布流程。
- 可观测性：按需接入 OpenTelemetry/Prometheus exporter、分布式 trace 与告警阈值。
- Web 边界：补持续运行的 Playwright 端到端测试、无障碍扫描、压力测试和断连恢复测试。
