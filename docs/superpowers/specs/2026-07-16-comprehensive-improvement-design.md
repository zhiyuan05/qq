# 聊天机器人平台全面完善设计文档

日期: 2026-07-16
状态: 设计稿

## 概述

本项目是一个功能相对完整的聊天机器人平台，支持 QQ/Telegram/Console 三平台接入，
使用 DeepSeek API 作为 LLM 后端，已具备角色卡、风格引擎、三层记忆、
Pipeline 中间件、安全层、自学习系统和 Web 管理面板。131 测试全部通过。

本设计针对代码质量、功能完整性、测试覆盖、架构整洁度和用户体验五个维度
提出系统性改进方案，分为三个阶段实施。

---

## 第一阶段：代码质量与架构清理（基础）

### 1.1 消除重复限流器

**问题**: `utils/rate_limiter.py`（Router 使用）和 `security/rate_limiter.py`（Pipeline 使用）
是两个独立的限流实现，功能重叠但 API 不同。

**方案**: 
- 废弃 `utils/rate_limiter.py`，Router 改用 `security/rate_limiter.RateLimiter`
- Router.route() 不再自己做限流检查，交由 Pipeline 的 RateLimitMiddleware 统一处理
- Router 只保留去重和命令解析职责

**文件**: `utils/rate_limiter.py`（删除）、`core/router.py`（修改）、`core/engine.py`（修改）

### 1.2 消除老旧 Config 类

**问题**: `utils/config.py` 中 `Config` 类（dot-notation 旧系统）及其 `get_config()` / `init_config()` 
函数不再被任何代码调用，但保留在文件中造成混淆。

**方案**: 
- 移除 `Config` 类、`get_config()`、`init_config()` 函数
- `_config` 全局变量一并移除

**文件**: `utils/config.py`（删除 ~60 行死代码）

### 1.3 消息拆分去重

**问题**: SplitMiddleware 和 `BasePlatform.send_long_message` 都做消息拆分，
`Engine._handle_chat` 也调用 `split_message`（已移除但需确认）。

**方案**: 
- 确认 Engine._handle_chat 不再调用 split_message
- SplitMiddleware 统一负责 LLM 回复的拆分
- `send_long_message` 保留作为平台层的最终兜底拆分

**文件**: 无需改动，已在之前完成

### 1.4 代码类型标注补齐

**问题**: 大部分文件缺少类型导入和函数返回值类型标注。

**方案**: 在以下核心文件添加类型标注：
- `core/engine.py` — 全部公共方法加返回类型
- `core/session.py` — 类属性和方法
- `core/pipeline.py` — Middleware 接口
- `characters/manager.py` — CharacterCard 方法
- `learning/analyzer.py` — 异步方法
- `security/rate_limiter.py` — 公共方法

### 1.5 消除引擎臃肿

**问题**: `core/engine.py` 628 行，Engine 类有 20+ 方法，职责过多。

**方案**: 
- 将命令处理器拆分为 `core/commands.py`（CommandHandler 类）
- 将学习相关方法移到 `learning/engine_adapter.py`
- Engine 保持协调者角色，具体逻辑委托给子模块

### 1.6 清理 Web 路由冗余

**问题**: `web/routes/api.py` 每个路由函数内部重复 import JSONResponse。

**方案**: 将 JSONResponse import 移到文件顶部。

---

## 第二阶段：功能增强（核心价值）

### 2.1 Web 面板升级

#### 2.1.1 添加 WebSocket 实时更新
- 使用 FastAPI WebSocket 端点 `/ws`
- 新消息到达时推送通知到前端
- 替代当前 30 秒轮询

#### 2.1.2 添加认证系统
- 简单的 token 认证（读取 config.yaml 中的 web.auth_token）
- 未认证时显示登录页面
- 会话使用 cookie/session

#### 2.1.3 角色卡管理页面
- 角色卡创建表单（名称、描述、系统提示词、规则）
- 角色卡编辑（修改现有角色卡）
- 角色卡预览（完整查看角色卡详情）

#### 2.1.4 Web 控制台聊天
- 在 Web 面板中直接与机器人对话
- 支持切换角色卡和风格
- WebSocket 流式显示 LLM 回复
- 对话历史显示

#### 2.1.5 配置管理页面
- 机器人基础设置（名称、默认角色、默认风格）
- LLM 设置（模型、温度、最大 Token）
- 安全设置（管理员列表、限流参数）
- 设置保存到 config.yaml

#### 2.1.6 界面改进
- 暗色模式支持（CSS变量 + localStorage 记忆偏好）
- 加载骨架屏（Skeleton loading）
- API 错误自动重试（最多 3 次）
- 会话列表分页（超过 20 条时分页）
- 消息搜索（按内容过滤会话）

### 2.2 对话管理增强

#### 2.2.1 对话导出 UI
- `/api/sessions/{id}/export` 已有，添加页面上的导出按钮
- 支持格式: JSON、TXT、Markdown

#### 2.2.2 对话搜索
- 全文搜索对话内容（基于 SQLite LIKE 或 ChromaDB）
- Web 面板添加搜索入口
- 支持按平台、日期、角色卡过滤

### 2.3 系统监控

#### 2.3.1 详细统计
- 消息量趋势（按天/按小时）
- LLM Token 消耗统计
- 各平台活跃度对比
- 角色卡使用频率排名

#### 2.3.2 日志查看
- Web 面板日志查看器（基于已有 `/admin logs` ）
- 日志级别过滤
- 日志搜索

---

## 第三阶段：测试与质量保障

### 3.1 补充测试覆盖

#### 3.1.1 学习系统测试
- `tests/test_learning.py`: LearningAnalyzer、EvolutionManager、LearningScheduler
- mock LLM 返回模拟分析结果
- 测试备份/恢复/重置流程

#### 3.1.2 插件系统测试
- `tests/test_plugins.py`: PluginManager 注册/注销/消息处理
- `tests/test_web_search.py`: 搜索插件命令解析、错误处理
- mock httpx 避免真实网络请求

#### 3.1.3 记忆系统测试
- `tests/test_memory_long_term.py`: ChromaDB 存储/搜索/清理
- `tests/test_memory_summarizer.py`: 摘要生成（mock LLM）
- `tests/test_memory_forgetting.py`: 遗忘策略的边界条件

#### 3.1.4 Engine 与 Pipeline 集成测试
- `tests/test_engine_pipeline.py`: Engine 使用 Pipeline 处理消息
- 测试 RateLimitMiddleware 在 Engine 中的实际阻断效果
- 测试 SplitMiddleware 在 Engine 中的拆分行为

#### 3.1.5 Utils 工具测试
- `tests/test_message_splitter.py`: 按句子/标点/长度的拆分逻辑
- `tests/test_delay.py`: HumanDelay 延迟计算
- `tests/test_logger.py`: 日志配置与轮转

### 3.2 测试基础设施改进

#### 3.2.1 Fixtures 完善
- `conftest.py` 添加 database fixture 测试用内存 SQLite
- character_manager fixture 加载测试用角色卡
- style_engine fixture 加载测试用风格

#### 3.2.2 CI 测试配置
- `pytest.ini` 添加默认配置
- 测试覆盖率配置（`.coveragerc`）
- 添加 `Makefile` 常用命令

---

## 实施优先级

```
第一阶段（代码质量）→ 第二阶段（功能增强）→ 第三阶段（测试）
```

### Phase 1 — 代码清理（收益/成本比最高）
1. 消除冗余限流器
2. 消除死代码（旧 Config 类）
3. 添加测试基础设施
4. 学习系统测试
5. 插件系统测试
6. 补齐类型标注

### Phase 2 — 功能增强
1. Web 面板认证系统
2. Web 控制台聊天（WebSocket）
3. 角色卡管理页面
4. 暗色模式 + 骨架屏
5. 系统监控统计

### Phase 3 — 测试完成
1. Engine-Pipeline 集成测试
2. 记忆系统测试
3. Utils 工具测试
4. CI 配置

---

## 架构影响

### 新增/修改的接口

```
core/commands.py          # [新增] 命令处理器，从 Engine 拆分
core/engine.py            # [修改] 移除命令处理逻辑，改为委托
utils/rate_limiter.py     # [删除] 由 security/rate_limiter.py 替代
web/routes/auth.py        # [新增] 认证路由
web/routes/ws.py          # [新增] WebSocket 路由
web/static/               # [新增] CSS/JS 静态文件
```

### 无破坏性变更
- 所有现有 API 路径保持不变
- 所有命令接口保持不变
- 所有中间件接口保持不变
- 数据库 schema 保持不变

---

## 风险与缓解

| 风险 | 概率 | 缓解措施 |
|------|------|----------|
| 移除 utils/rate_limiter 时遗漏引用 | 低 | grep 确认所有 import |
| WebSocket 增加 Web 面板复杂度 | 中 | 使用 FastAPI 原生 WebSocket，fallback 到轮询 |
| 测试 mock 与实际 LLM 行为不符 | 中 | 保持少量集成测试+大量单元测试 |
| 角色卡编辑可能导致数据丢失 | 低 | 保存前备份，版本历史 |
| 认证系统可能锁住管理员 | 低 | 初始 token 从 config.yaml 读取，支持 CLI 重置 |
