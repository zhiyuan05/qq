# 从聊天机器人到 Agent 平台：0→1 路线图

## 产品判断

当前 v2.4 已具备一个可靠模块化单体的运行基线，但身份、数据事实来源和执行记录仍以“个人机器人”
为中心。下一阶段不急于拆微服务；先在单体内建立稳定领域边界，等吞吐量、团队边界或独立扩缩容需求
出现后再拆部署单元。

目标依赖方向：

```text
Web / QQ / Telegram / CLI
            ↓
application use cases（Chat / Manage / Query / Publish）
            ↓
domain（Identity / Conversation / Agent / Tool / Policy）
            ↓ ports
SQLAlchemy / Vector Store / LLM / Transport / Filesystem
```

## M1：身份、Workspace、权限与审计

先建立 `Account`、`ExternalIdentity`、`Workspace`、`Membership`、`ApiKey/WebSession` 和
`AuditEvent`。现有数据进入 default workspace；`platform:user_id` 是过渡主体键，跨平台绑定必须显式完成。

验收：

- 相同数字 ID 的不同平台用户无法互读会话、记忆或角色卡。
- HTTP、WebSocket 和平台消息在进入应用层前都解析为同一种 `Principal`。
- 会话导出、角色发布、学习触发等操作有对象级授权和不可变审计记录。
- 退出可立即撤销服务端会话；登录失败有共享限速。

## M2：追加式 Conversation / Message / Run

详细状态机、事务边界、故障语义和渐进迁移见
[持久化执行与投递设计](durable-execution.md)。

把“每轮删除并重写快照”演进为追加式事实模型：

```text
Conversation
  ├─ Message(id, parent_id, role, status, external_event_id)
  └─ Run(request_id, agent_version, model, status, timing, usage)
       ├─ RunEvent(sequence, type, payload)
       ├─ ToolCall(status, arguments, result)
       └─ OutboxDelivery(chunk_index, status, remote_message_id)
```

`InboundEvent` 保存 payload hash、owner lease、fencing token 和最终响应。会话 checkpoint 只用于加速，
不再是唯一事实来源。

验收：

- 1000 轮会话新增一轮只追加相关记录，不重写历史。
- 同一 request/event ID 命中两个副本或跨重启重试，只产生一个 Run 和一次模型调用。
- 流式取消、工具失败和部分投递都有可查询状态；已发送分片不会从第一片重发。
- 管理 API 从 Repository 分页查询完整数据库，不受内存 `restore_limit` 影响。

## M3：应用编排层与受治理 Tool

先迁移一条完整聊天垂直链路到 `ChatOrchestrator`，再迁移命令和管理用例。Engine 保留兼容 façade，
不再直接拥有文件、数据库、Chroma 和平台实现。Web Search 作为第一个 `ToolSpec` 迁移到统一
`ToolRegistry / ToolExecutor / ToolPolicy`。

验收：

- Web、命令和平台入口复用同一应用用例，不直接读 `SessionManager` 或本地文件。
- 工具参数先过 JSON Schema；执行有权限、超时、取消、并发和输出大小限制。
- 工具密钥不会进入 Prompt 或日志；每次调用关联 Run 和审计主体。
- 架构测试保证 domain 不依赖 FastAPI、SQLAlchemy、Chroma 或平台 SDK。

## M4：可观测、反馈、评测和发布闭环

以 Run 为业务 trace 主键，记录 Provider/Model、Prompt/Agent 版本、TTFT、总耗时、usage、成本、
工具与记忆阶段。自动学习只生成 Draft，经离线评测和审批后发布，可灰度和回滚。

验收：

- 任一响应可由 request ID 定位完整 Run；管理端显示 p50/p95、TTFT、失败率和成本。
- 至少 30–50 条核心 golden cases 在 CI 比较质量、延时和成本预算。
- 生产角色卡不被后台任务直接覆盖；历史 Run 能还原当时 Agent/Prompt 版本。

## 横向工程门禁

- Alembic 独立 migration job；Runtime 数据库角色无 DDL 权限；N-1 快照升级和恢复演练进入 CI。
- 依赖锁与 SBOM、类型检查、架构 import 规则、覆盖率阈值、Playwright 关键路径和双副本混沌测试。
- 多实例拆成无状态 API worker、单例/选主平台入口和后台 worker；事件队列、限速、Pub/Sub 和向量库
  使用共享基础设施。
- 每个里程碑都以一条可上线垂直链路交付，避免一次性大爆炸重写。

参考边界来自 [Open WebUI](https://github.com/open-webui/open-webui)、
[Dify](https://github.com/langgenius/dify)、[Rasa](https://github.com/RasaHQ/rasa)、
[LangGraph](https://github.com/langchain-ai/langgraph) 和 [Flowise](https://github.com/FlowiseAI/Flowise)。
