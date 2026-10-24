# 持久化执行与投递设计

> 状态：v2.4 后续实现契约。当前版本已经具备会话 revision、同会话协调和进程内请求幂等，
> 但尚未完成本文定义的跨重启 Run/Outbox 闭环。

## 为什么要引入 Run

一次聊天不是单条函数调用，而是一段可能跨越网络、数据库和消息平台的业务执行：接收事件、读取上下文、
调用模型、提交会话、过滤输出、拆分消息和投递。只保存最终会话快照无法回答下面这些问题：

- 重启后的同一平台事件是否已经调用过模型；
- 哪个副本拥有执行权，旧副本是否仍有资格提交；
- 用户实际收到的是哪一版经过过滤的结果；
- 长消息投递到第几个分片，超时究竟是失败还是 ACK 丢失；
- 某次响应使用了什么模型、Prompt 版本、耗时和 token。

因此下一阶段以 `InboundEvent -> Run -> Message / RunEvent -> OutboxDelivery` 作为事实链，
现有 `chat_sessions` 只保留可重建的读取投影。

## 可承诺的语义

数据库边界内可以保证：

- 每个 connector 作用域内的外部事件只有一个逻辑 `InboundEvent`；
- 一个事件只创建一个逻辑 `Run`；
- 同一会话只允许最早的有效 turn 执行；
- 未成功提交的回复绝不进入 outbox；
- 租约接管后，旧 worker 会被 fencing token 拒绝；
- 已提交但未投递的结果可在进程崩溃后恢复。

不能对不受本系统控制的 API 无条件承诺物理 exactly-once。模型或消息平台可能已经接受请求，而本地在收到
ACK 前断线。若下游没有稳定幂等键，该次 attempt 必须进入 `UNKNOWN`，默认等待人工判断，不能盲目重试。
系统对外使用的准确表述是：

> 一个逻辑 Run、一个已提交结果、任一时刻最多一个有效 attempt；只有下游支持幂等键时，才进一步保证
> 一个外部副作用。

## 数据模型

### InboundEvent

核心字段：

- `id`、`source`、`connector_key`、`external_event_id`；
- `dedupe_key` 和 `payload_hash`；
- `principal_key`、`conversation_id`、规范化后的请求；
- `status`、`run_id`、`attempt_count`、`next_attempt_at`；
- `lease_owner`、单调递增的 `lease_token`、`lease_expires_at`；
- 净化后的 `error_code` 和各阶段时间戳。

必须有 `UNIQUE(connector_key, external_event_id)`。同一 ID 携带不同 `payload_hash` 是协议冲突，返回 409，
不得把旧结果用于新 payload。

### Run

核心字段：

- `id`、唯一的 `inbound_event_id`、`conversation_id`、`turn_no`、`request_id`；
- `status`：`QUEUED / RUNNING / RETRY_WAIT / SUCCEEDED / FAILED / CANCELLED / UNKNOWN / SUPERSEDED`；
- 输入/输出 revision、租约和 fencing token；
- Agent、Prompt、Provider、Model 与 provider request ID；
- 最终响应、hash、TTFT、总耗时、token 和成本；
- 有界且脱敏的错误类型/错误码。

`UNIQUE(conversation_id, turn_no)` 保证会话顺序；队列索引覆盖 `status / next_attempt_at / created_at`。

### Message 与 RunEvent

新 `messages` 表追加 user 和最终 assistant 事实，不直接复用会被旧快照代码删除的 `conversations` 表。
每个会话使用单调 sequence，旧 Message ID 永不改变。

`run_events(run_id, sequence)` 记录状态转换、工具调用和分批流式输出。流式内容按时间或大小批次落盘，
不按 token 逐行写入。

### OutboxDelivery

创建 Run 成功事务时就确定目标、正文和分片：

- `UNIQUE(run_id, destination, chunk_index)`；
- 稳定 `idempotency_key`、`payload_hash`；
- `PENDING / SENDING / RETRY_WAIT / DELIVERED / UNKNOWN / DEAD / CANCELLED / EXPIRED`；
- attempt、可用时间、租约、fencing token、远端 message ID 和投递截止时间。

重试沿用已保存的分片，禁止再次按新配置切分。

## 事务与状态转换

### T0：接收

在一个短事务中规范化消息、计算 hash、幂等插入 `InboundEvent` 并创建唯一 Run。入口只等待 T0，
Webhook 不同步等待 LLM。返回值为 `CREATED / INFLIGHT / REPLAY / CONFLICT`。

### T1：领取

PostgreSQL worker 使用 `FOR UPDATE SKIP LOCKED` 找到可执行 Run，并通过条件更新取得租约、递增
`lease_token`。SQLite 只允许单 worker，并用短写事务取得所有权。外部调用期间不持有数据库事务或
advisory-lock 连接。

### T2：执行

先提交 `RUNNING`，再调用 LLM；长调用用短事务 heartbeat 续约。明确的连接前失败可按预算重试；
请求是否已被模型接受无法判断时进入 `UNKNOWN`。

### T3：提交结果

一个事务同时验证 Run fence 和会话 revision，然后：

1. 把 Run 更新为 `SUCCEEDED`；
2. 追加 user 与经过最终输出过滤的 assistant Message；
3. 更新 `chat_sessions` 投影及 revision；
4. 插入确定性 outbox 分片；
5. 完成 InboundEvent 并追加 RunEvent。

任一步失败都回滚。会话 CAS 冲突不得向用户返回本地生成的答案。

### T4：投递

worker 用短事务领取 outbox，在事务外调用平台 API，再用 `delivery_id + lease_token` 条件提交结果。
老 worker 回写零行即视为 fence 拒绝。只有能确认远端未接受的错误可以自动重试；ACK 歧义进入
`UNKNOWN`。

## API 契约

- `POST /api/v1/chat` 接受 `Idempotency-Key`；
- 新建并同步完成可返回 200，仍在执行返回 202，payload 冲突返回 409；
- `GET /api/v1/runs/{id}` 查询状态；
- `GET /api/v1/runs/{id}/events?after_sequence=N` 支持续传；
- HTTP 与 WebSocket 必须共享 idempotency namespace；
- 管理员强制重试 `UNKNOWN` 时必须明确 `force=true` 并写审计事件。

## 渐进迁移

1. 004 迁移只新增 durable 表，不改变旧表；
2. 先迁移 Web 非流式链路，短期双写新事实和旧投影；
3. 分批幂等回填旧 conversation，未回填会话仍回退旧读取器；
4. 再迁移 Telegram（持久化 `update_id`）和 QQ；
5. 微信公众号从一开始只走新 InboundEvent/Outbox；
6. 全部实例升级、回填和校验后停止旧快照重写；
7. 最后归档旧 `conversations` 表。

旧、新 writer 混跑期间不得宣称 durable multi-instance。生产滚动升级前需要独立 migration job、N-1
恢复演练和兼容性门禁。

## 必测故障场景

- 100 个相同事件并发只产生一个 Run；相同 ID 不同 payload 返回冲突；
- 租约接管后 token 递增，旧 owner 无法完成 Run；
- T3 每一步故障注入都不留下半条 Message、Outbox 或 completion；
- CAS 冲突不返回响应且不生成 Outbox；
- LLM 前崩溃可安全领取，LLM 后 ACK 不明进入 `UNKNOWN`；
- T3 后发送前崩溃可恢复，已 `DELIVERED` 分片不会重发；
- 两个 PostgreSQL worker 可领取不同会话，同会话 turn 严格有序；
- HTTP/WS 重连共享结果；数据库重启和应用关闭时 drain 行为可预测。

## 设计依据

- [Temporal Workflow Execution](https://github.com/temporalio/documentation/blob/main/docs/encyclopedia/workflow/workflow-execution/workflow-execution.mdx)：以持久化事件历史恢复执行；
- [Temporal History Service](https://github.com/temporalio/temporal/blob/main/docs/architecture/history-service.md)：使用单调 generation fencing 旧 owner；
- [LangGraph PostgreSQL checkpointer](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/langgraph/checkpoint/postgres/base.py)：checkpoint 与 pending writes 的幂等键；
- [Rasa SQL tracker store](https://github.com/RasaHQ/rasa/blob/main/rasa/core/tracker_store.py)：追加事件并由事件恢复 tracker；
- [PostgreSQL `SELECT`](https://www.postgresql.org/docs/16/sql-select.html)：`SKIP LOCKED` 的 queue-like consumer 语义；
- [AWS Transactional Outbox](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html)：把业务状态和待发布事件放入同一事务。
