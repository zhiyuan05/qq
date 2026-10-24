# 持久化部署指南

## 后端选择

| 场景 | 后端 | `multi_instance` |
|---|---|---|
| 本地开发、单进程机器人 | SQLite（默认） | `false` |
| 单实例生产、外部托管数据库 | PostgreSQL | `false` |
| 多个应用副本共享会话 | PostgreSQL | `true` |

`database_url` 优先于兼容字段 `db_path`。支持普通 `postgresql://` URL，启动时会规范化为
`postgresql+asyncpg://`；SQLite 也可继续传文件路径。

## 独立数据库迁移任务

生产发布应在新版本应用接收流量前执行一次迁移任务。源码目录和容器内均可使用模块入口；安装 wheel
后还可使用等价的 `chatbot-migrate` 命令：

```bash
# 默认依次读取 .env、config.yaml（不存在时读取 config/config.yaml）
python -m database.migrate

# SQLite：命令行目标优先级最高
python -m database.migrate --database data/chatbot.db

# 安装 wheel 后的等价入口
chatbot-migrate --config /etc/chatbot/config.yaml --env-file /etc/chatbot/chatbot.env
```

目标数据库的优先级为 `--database`、进程环境中的 `CHATBOT_DATABASE_URL`、YAML 的
`persistence.database_url`、`persistence.db_path`。生产 PostgreSQL 建议通过受保护的环境变量或
secret manager 注入 URL，不要把含密码的 URL 直接写在命令行中，因为系统进程列表可能记录参数。
迁移日志会隐藏 URL 密码并省略查询参数，也不会输出异常回溯中的凭据。
单次迁移默认最多运行 300 秒，超时会取消事务并返回失败；大型变更可显式设置
`--migration-timeout SECONDS`。

命令具有稳定退出码，可直接作为发布闸门：

| 退出码 | 含义 |
|---:|---|
| `0` | 迁移完成或当前 schema 已是最新 |
| `2` | 命令行参数错误 |
| `3` | 配置、dotenv 或数据库 URL 无效 |
| `4` | 连接、迁移或连接清理失败 |
| `130` | 操作者中断 |

当前应用启动仍会执行幂等 schema 检查作为安全网；独立命令用于在滚动发布前显式发现失败，避免不同
版本副本同时接收流量后才暴露 DDL 问题。

## 一键启动内置 PostgreSQL

在 `.env` 中设置一个只含字母和数字的高强度密码：

```env
CHATBOT_POSTGRES_PASSWORD=replaceWithALongRandomPassword
```

然后启动基础 Compose 与 PostgreSQL 覆盖文件：

```bash
docker compose -f docker-compose.yml -f docker-compose.postgres.yml build chatbot
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d postgres
docker compose -f docker-compose.yml -f docker-compose.postgres.yml run --rm --no-deps chatbot python -m database.migrate
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d chatbot
docker compose -f docker-compose.yml -f docker-compose.postgres.yml ps
```

覆盖文件会设置 asyncpg URL、开启多实例协调，并等待 PostgreSQL 通过 `pg_isready` 后再启动应用。
数据库数据保存在命名卷 `chatbot-postgres` 中；`docker compose down` 不会删除该卷，只有显式使用
`down -v` 才会删除。

## 连接外部 PostgreSQL

```env
CHATBOT_DATABASE_URL=postgresql+asyncpg://chatbot:encoded-password@db.example:5432/chatbot
CHATBOT_MULTI_INSTANCE=true
```

密码里的 `@`、`:`、`/`、`%` 等字符需要按 URL 规则编码。TLS、证书和连接参数可继续放在 URL
查询字符串中。应用启动会串行化 schema 初始化；所有副本必须运行同一代码版本后再接收流量。

## 一致性模型

- `chat_sessions.revision` 是乐观锁。保存时必须匹配已提交版本，冲突会回滚完整快照并重新加载最新状态。
- 单实例按 session key 使用进程内锁；空闲锁会移除，不随历史用户数无限增长。
- 多实例在 PostgreSQL 中使用 transaction-scoped advisory lock。进入锁后重新加载会话，因此同一会话跨副本有序；不同会话可并发。
- advisory lock 会在生成整段 LLM 回复期间占用协调器专用池中的一个数据库连接；业务读写使用隔离的
  数据池，不会等待锁持有者释放同一条连接。`pool_size + max_overflow` 仍应结合每个副本的并发活跃
  会话数设置，并为健康检查和后台任务留出余量。
- Web `request_id` 结果缓存目前仍是进程内状态；跨副本重试可由 revision 防止旧快照覆盖，但尚不保证跨副本只调用一次 LLM。
- `conversations` 是**可替换快照**：每次保存先删除再重插，因此其自增 id 不稳定。摘要和人格演化使用
  追加型 `conversation_archive`（唯一键 `session_id + request_id + turn_index`），游标只认归档 id。
- 启动恢复与多实例每轮刷新只读取每会话最近 `persistence.messages_per_session` 条（窗口函数），
  不把全部历史载入内存；旧消息仍保留在库中供增量摘要/人格提取。
- 迁移 005/006/007 新增 `attachments_json`、`summary_until_id`、`conversation_archive`、
  `persona_*` 与 `llm_usage_events`。PostgreSQL 不执行 `.sql` 文件，新列同时由
  `database/models.py` 的 `create_all` 与 `database/db.py` 的 `ADD COLUMN IF NOT EXISTS` 保证。

## 从 SQLite 切换

当前版本不会自动把 SQLite 历史数据复制到 PostgreSQL。安全发布顺序是：

1. 停止入口流量并备份 `data/chatbot.db`。
2. 配置 PostgreSQL，在单个应用副本上启动并确认 `/api/v1/health/ready`。
3. 如需历史会话，使用经过验证的数据迁移脚本导入；不要直接复制 SQLite 文件。
4. 启动其余同版本副本，再恢复入口流量。

如不迁移历史数据，新 PostgreSQL 会从空状态开始，但 SQLite 备份保持可回退。回退前同样应停止写入，
否则两个后端会形成不可自动合并的分叉。

## 备份与发布检查

- SQLite：应用停写后复制数据库文件，或使用 SQLite 在线备份 API；同时保留迁移表。
- PostgreSQL：使用托管服务快照或 `pg_dump`，并定期执行恢复演练。
- 发布前运行 `python -m pytest tests -q`。CI 的 `postgres` job 会在 PostgreSQL 16 上验证 schema、
  revision 冲突、实体 upsert 和跨连接 advisory lock。
