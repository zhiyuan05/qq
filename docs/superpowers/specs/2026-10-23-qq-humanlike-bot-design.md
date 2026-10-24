# QQ 拟人化机器人设计（识图 / 表情包 / 语音 / 分段回复）

- 日期：2026-10-23
- 状态：已确认
- 方案：A（扩展现有管线）
- 范围：QQ 私聊；现有 QQ 群、Web 面板、Pipeline、限流、熔断、记忆保持不变

## 1. 目标与非目标

### 目标

1. 接收并理解 QQ 私聊图片，能接梗、读截图文字、描述图片内容。
2. 接收 QQ 私聊语音，本地转写后进入正常对话；可按概率用语音回复。
3. 维护本地表情包库，支持自动收集、标签检索和情绪化发送。
4. 回复拟人化：按长度模拟打字延迟，拆成 1–3 条短消息，必要时引用原消息。
5. 所有新增能力失败时退回纯文本，不阻塞正常对话。
6. 人格演化：在只读的基础人设之上，增量学习并版本化地完善全局人格和每用户关系。
7. 上下文有界：任何时刻只使用摘要、最近窗口和按需检索的特质/记忆，不重放全量历史。

### 非目标

- 微信群、个人微信 Hook。
- QQ 群主动插话、随机参与群聊。
- 主动发起消息（早安/晚安/隔天追问）。
- 声音克隆、定制音色。
- Web 端图片/语音输入。
- 历史图片全量重发。

## 2. 架构总览

```text
QQ (NapCat OneBot v11)
  → QQPlatform._process_raw_message
      ├─ text/at  → 现有路径
      ├─ image    → MediaStore 下载 → Attachment
      └─ record   → VoiceCodec 解码 → ASR → 文本 + Attachment
  → UnifiedMessage(attachments=[...])
  → Engine.process_message_plan()
      ├─ ContextAssembler：基础人设 + 全局/用户特质 + 摘要 + 最近 12 条 + top-k 记忆
      ├─ _prepare_chat：当前图片 → 多模态内容块；走 llm.vision_model
      ├─ LLM 生成文本，可含 [sticker:标签] / [voice] 标记
      └─ Humanizer.plan() → ReplyPlan
  → QQPlatform.deliver_reply(plan)
      ├─ reply 段（引用）
      ├─ 逐条文本 + 打字延迟
      ├─ image 段（表情包）
      └─ record 段（TTS 语音）
```

新增模块：

| 模块 | 职责 |
|------|------|
| `media/store.py` | 媒体下载、校验、哈希落盘、去重、保留期清理 |
| `media/stickers.py` | 表情包扫描、标签索引、自动收集、标签选图 |
| `media/voice.py` | SILK 解码、本地 ASR、Edge TTS 合成 |
| `core/humanizer.py` | 标记解析、分段、延迟、引用与语音/表情决策 |
| `core/context_assembler.py` | 按 token 预算组装提示词，禁止全量历史 |
| `memory/persona.py` | 全局/用户人格档案、特质合并、衰减、检索与版本回滚 |
| `learning/persona_evolution.py` | 基于游标的增量人格提取，不再改写角色卡 YAML |

## 3. 数据模型

### 3.1 `platforms/models.py`

```python
@dataclass(frozen=True, slots=True)
class Attachment:
    kind: MessageType          # IMAGE / VOICE / FILE
    path: str                  # 本地存储路径
    mime: str
    size: int
    sha256: str
    duration: float | None = None
    transcript: str = ""       # 仅语音

@dataclass(slots=True)
class ReplyPlan:
    bubbles: list[str] = field(default_factory=list)
    reply_to: str = ""              # 被引用消息的 message_id
    sticker_path: str = ""
    voice_path: str = ""
    voice_text: str = ""            # 语音对应的原文，便于日志与降级
```

`UnifiedMessage` 新增：

```python
attachments: list[Attachment] = field(default_factory=list)
```

`MessageType` 已有 `IMAGE / VOICE / FILE`，不新增枚举。

### 3.2 会话历史

`ChatMessage` 新增 `attachments: list[Attachment]`，默认空列表；持久化需要新增迁移 `005_conversation_attachments.sql`：

```sql
ALTER TABLE conversations ADD COLUMN attachments_json TEXT NOT NULL DEFAULT '[]';
```

- `SQLAlchemySessionStore` 保存/恢复时对该列做 JSON 序列化，旧数据按 `[]` 处理。
- **恢复上限**：`load_sessions()` 与 `load_session()` 都只读取每个会话最近的 `persistence.messages_per_session` 条（默认 20）加摘要，不能把全部 `conversations` 行载入内存。旧消息仍保留在数据库中，需要时按游标增量分析（见第 16、17 节）。
- 文本消息原样保存。
- 图片保存为 `[图片]` 占位，附件元数据写入 `attachments_json`；最近的 `history_image_window` 张图片在构建提示词时重新附带，文件已被清理时退回占位。
- 语音保存为 `[语音] <transcript>`，附件记录时长与转写，不重新附带音频。
- 表情包作为 assistant 的图片消息记录为 `[表情包:标签]`。

## 4. 媒体存储（`media/store.py`）

- 目录：`media.storage_dir`，默认 `data/media/YYYY-MM/`。
- 文件名：`<sha256[:32]>.<ext>`；扩展名按内容嗅探，不信任 URL。
- 下载限制：`max_image_bytes`、连接/读取超时、最多 2 次重定向；拒绝非 `http/https`。
- 写入采用临时文件 + 原子替换，避免半文件。
- 相同哈希直接复用，不重复下载/落盘。
- 清理：启动后每日一次，删除超过 `retention_days` 且未被表情包库引用的文件。
- 所有方法返回 `Attachment | None`，失败只记录日志。

## 5. 识图链路

1. `QQPlatform` 解析 `image` 段，取 `url` 或 `file`，交给 `MediaStore`。
2. 无文本但有图片时，`content` 置为空字符串，但消息不再被 `filter` 中间件丢弃；过滤条件改为“无文本且无附件”。
3. `Engine._prepare_chat`：
   - 当前轮图片转成 OpenAI 内容块：
     ```python
     {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,...", "detail": "auto"}}
     ```
   - 超过 32 MiB 的图片先做一次缩放（Pillow，最长边 2048），控制请求体大小。
4. 模型路由：
   - 有图片 → `llm.vision_model`（默认 `deepseek-flash`）。
   - 纯文本 → `llm.model`（默认 `deepseek-chat`）。
   - 使用 provider 的 `with_model()`，共享同一 `AsyncOpenAI` 连接池。
5. 历史图片窗口：默认最近 2 张，超过的只保留 `[图片]` 占位。
6. 失败降级：下载失败、超限、视觉接口失败 → 在用户消息后追加 `[图片未能识别]`，继续文本对话。

## 6. 表情包系统

### 6.1 索引

- 目录：`characters/stickers/**`，支持 PNG/JPG/GIF/WebP。
- 侧车数据库：`data/stickers.db`（stdlib `sqlite3`，WAL）。它是可重建缓存，不纳入主数据库迁移。
- 表结构：
  ```sql
  CREATE TABLE stickers (
    hash TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    tags TEXT NOT NULL,          -- 逗号分隔
    source TEXT NOT NULL,        -- library / collected
    enabled INTEGER NOT NULL DEFAULT 1,
    used_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
  );
  CREATE INDEX idx_stickers_tags ON stickers(tags);
  ```
- 首次扫描：按内容哈希入库；对未打标签的图片调用视觉模型生成 3–5 个中文标签（开心、无语、晚安、震惊等）。失败时退回文件名分词 + `默认`。
- 标签生成会调用 DeepSeek 视觉接口；文档和配置中明确说明。

### 6.2 自动收集

- 仅 QQ 私聊。
- 判定启发式：GIF/WebP 直接收集；PNG/JPG 需最长边 ≤ 512 且 ≤ `max_bytes`。
- 限制：单日 `max_per_day`、单文件 `max_bytes`、总容量上限（默认 500 MB）；超限只记日志。
- 去重：内容哈希已存在则跳过。
- 可关闭：`stickers.auto_collect: false`。
- 审核队列：`stickers.review_required` 为 true 时，自动收集的图先以 `candidate` 状态入库，不参与发送；通过 `/sticker approve` 或 Web 面板确认后才转为 `active`。默认 false。
- 情绪标签：标签生成同时产出 `emotion`（开心/无语/难过/生气/惊讶/晚安等），用于情绪匹配，不参与文件路径选择。
- 本地保存，不上传；标签生成除外。

### 6.3 发送

- LLM 可在回复中输出 `[sticker:标签]`；Humanizer 解析并剥离。
- 选图顺序：
  1. 精确标签匹配；
  2. 标签包含/被包含匹配；
  3. 未命中且 `send_probability` 命中时随机选一张；
  4. 都不满足则不发表情包。
- 同一会话 30 秒内不重复发表情包；同一张图 10 分钟内不重复。
- 通过 QQ `image` 消息段发送，单独成条。

## 7. 拟人回复编排（`core/humanizer.py`）

### 7.1 输入输出

```python
def plan(
    text: str,
    message: UnifiedMessage,
    *,
    config: HumanizeConfig,
    stickers: StickerLibrary | None,
    rng: random.Random | None = None,
) -> ReplyPlan
```

### 7.2 规则

1. **标记解析**：移除 `[sticker:...]`、`[voice]`；记录表情包标签和语音意图。
2. **分段**：
   - 按空行、换行、句末标点切分；
   - 合并过短片段，最多 `max_bubbles` 条；
   - 总长度低于 `split_threshold`（默认 60 字）不拆。
3. **延迟**：
   - 每条之间 `delay = clamp(len(chunk) / typing_chars_per_second, min_delay, max_delay)`；
   - 叠加 ±30% 随机抖动；首条延迟为 0，由平台发送前统一等待。
4. **引用**：
   - 图片、语音、表情包消息必须引用；
   - 文本消息按 `quote_probability` 决定；
   - 引用 ID 取触发消息的 `message_id`。
5. **语音**：
   - 用户发语音且 `voice.enabled` → 按 `voice_probability` 用语音回复；
   - 文本回复且长度 ≤ `voice_max_chars`（默认 80）才考虑语音；
   - 语音内容取第一条 bubble 的纯文本。
6. **顺序**：引用 + 第一条文本 → 后续文本 → 表情包 → 语音。表情包和语音都不与文本合并。

### 7.3 投递

- `BasePlatform.deliver_reply(plan)` 默认实现：逐条调用 `send_message` 并按计划延迟。
- `QQPlatform` 覆写：
  - 引用：`{"type": "reply", "data": {"id": plan.reply_to}}`；
  - 文本：`{"type": "text", "data": {"text": bubble}}`；
  - 表情包：`{"type": "image", "data": {"file": "file://..."}}`；
  - 语音：`{"type": "record", "data": {"file": "file://..."}}`。
- 消息段数组交给 OneBot `send_private_msg`；文本需转义 CQ 码，文件路径只使用本地已校验路径。
- 拟人分段、表情包、语音仅对 `group_id is None` 的 QQ 私聊生效；群聊继续走现有 `send_long_message`。
- 引擎兼容：
  - 新增 `Engine.process_message_plan(msg) -> ReplyPlan | None`；
  - 原 `process_message(msg) -> str | None` 保留，内部调用 plan 并拼接文本；
  - WebSocket 流式路径不启用拟人分段、表情包和语音，保持现状。

## 8. 语音链路

### 8.1 入站

1. `QQPlatform` 解析 `record` 段，取 `file`/`url`，交给 `MediaStore`。
2. `VoiceCodec.decode_silk()` → 16 kHz 单声道 WAV：
   - 优先 `pilk`（自带 SILK 编解码）；
   - 失败时尝试系统 `ffmpeg`；
   - 再失败则返回占位 `[语音消息，无法解码]`。
3. `VoiceTranscriber` 通过 `ASRProvider` 协议调用，默认实现 `FasterWhisperProvider`（`base`、`int8`、`language=zh`、懒加载）；预留 `FunASRProvider` 等实现，由 `voice.asr_provider` 选择。
4. 并发限制 1；超过 `max_audio_seconds` 截断或拒绝。
5. 转写文本作为 `content`，前缀 `[语音] `；原始语音作为 `Attachment(kind=VOICE, transcript=...)`。
6. 转写失败：`[语音消息，未能识别，请发文字]`，继续对话。

### 8.2 出站

1. `SpeechSynthesizer` 通过 `TTSProvider` 协议调用，默认实现 `EdgeTTSProvider`（输出 mp3 到 `data/media/tts/`，音色 `zh-CN-XiaoxiaoNeural`）；预留 `PiperProvider`、`GPTSoVITSProvider` 等实现，由 `voice.tts_provider` 选择。
2. 通过 OneBot `record` 段发送；NapCat 内置 ffmpeg/silk 转码。
3. 若 NapCat 发送失败：记录错误并退回文本，不重试语音。
4. 资源限制：单条 ≤ `voice_max_chars`，合成超时 15 秒。

### 8.3 资源约束（2 核 4G）

- Whisper 懒加载，空闲 10 分钟后释放模型。
- 同一时间只处理一条语音。
- `base` 为默认；`small` 可通过配置启用，但内存占用更高。
- Edge TTS 为网络调用，CPU 占用可忽略。

## 9. 配置

```yaml
llm:
  model: "deepseek-chat"
  vision_model: "deepseek-flash"

media:
  storage_dir: "data/media"
  max_image_bytes: 10485760
  retention_days: 7
  history_image_window: 2

stickers:
  enabled: true
  library_dir: "characters/stickers"
  auto_collect: true
  max_bytes: 2097152
  max_per_day: 50
  max_total_bytes: 524288000
  review_required: false
  send_probability: 0.2
  cooldown_seconds: 30
  repeat_window_seconds: 600

humanize:
  enabled: true
  max_bubbles: 3
  split_threshold: 60
  typing_chars_per_second: 6
  min_delay: 0.8
  max_delay: 4.0
  quote_probability: 0.5

voice:
  enabled: true
  asr_provider: "faster_whisper"
  tts_provider: "edge_tts"
  asr_model: "base"
  max_audio_seconds: 60
  tts_voice: "zh-CN-XiaoxiaoNeural"
  voice_probability: 0.15
  voice_max_chars: 80
  tts_timeout_seconds: 15

persistence:
  messages_per_session: 20

context:
  max_prompt_tokens: 4000
  recent_messages: 12
  summary_tokens: 300
  user_trait_tokens: 150
  global_trait_tokens: 150
  memory_top_k: 3

persona:
  enabled: true
  evolve_after_messages: 30
  evolve_schedule: "04:00"
  max_traits_per_update: 3
  min_global_confidence: 0.75
  min_user_confidence: 0.6
  max_global_active: 12
  max_user_active: 8
  max_global_core: 3
  max_user_core: 2
  core_confidence: 0.85
  decay_per_week: 0.05
  profile_max_chars: 2000
```

`utils/config.py` 新增对应 Pydantic 模型，全部带默认值和范围校验；非法值启动时报错。

## 10. 错误处理与降级

| 故障 | 行为 |
|------|------|
| 图片下载失败/超限 | `[图片未能识别]`，继续文本回复 |
| 视觉模型失败 | 同上，并记录一次失败指标 |
| 表情包索引失败 | 禁用表情包，文本正常 |
| 标签生成失败 | 用文件名/默认标签 |
| SILK 解码失败 | `[语音消息，无法解码]` |
| ASR 失败/超时 | `[语音消息，未能识别，请发文字]` |
| TTS 失败/超时 | 改发文本 |
| 引用消息已被撤回 | 去掉 reply 段，正常发送文本 |
| 媒体落盘失败 | 本条消息按纯文本处理 |

所有失败均不向用户抛异常；日志记录路径、大小、哈希和错误类型，不记录图片/语音内容。

## 11. 安全与隐私

- 媒体下载限制协议、重定向、大小和超时，防止 SSRF 与磁盘打满。
- 自动收集仅本地存储；除标签生成外不上传。
- 表情包标签匹配只从索引库选路径，不接受 LLM 输出的任意路径。
- 引用 ID 只接受十六进制/数字，避免消息段注入。
- 图片、语音文件不进入 Web 静态目录，不通过 API 暴露。
- 日志不输出转写文本和图片内容。
- 人格档案按用户隔离；用户可查看和删除自己的关系档案，全局档案仅管理员可改。
- 人格提取拒绝手机号、地址、证件号、银行卡、密码等敏感信息，证据只保留表达风格相关的短句。

## 12. 测试与验收

### 单元测试

- `tests/test_qq_media.py`：image/record 段解析、URL 校验、哈希去重。
- `tests/test_media_store.py`：大小限制、原子写入、清理。
- `tests/test_vision_payload.py`：多模态消息构造、模型切换、历史图片窗口。
- `tests/test_stickers.py`：索引、标签匹配、自动收集启发式、冷却与去重。
- `tests/test_humanizer.py`：标记解析、分段上限、延迟范围、引用决策。
- `tests/test_voice.py`：SILK 解码失败降级、ASR 超时、TTS 失败退回文本、ASR/TTS provider 切换。
- `tests/test_reply_plan.py`：`process_message` 兼容性、plan 投递顺序。
- `tests/test_context_assembler.py`：token 预算、裁剪优先级、当前消息永不被裁。
- `tests/test_persona.py`：增量游标、特质合并/冲突/衰减、core 晋升、全局跨会话门槛、版本回滚。
- `tests/test_persona_safety.py`：敏感信息过滤、指令注入拒绝、跨用户隔离。
- `tests/test_session_restore_window.py`：1000 条历史只恢复最近 N 条。

### 集成测试

- mock OneBot WebSocket：发送图片帧 → 断言视觉内容块与回复。
- 发送语音帧 → 断言转写文本进入会话。
- 断言出站顺序为引用 → 文本分段 → 表情包/语音。
- 断言所有失败场景仍有文本回复。

### 验收场景

1. 用户发一张梗图，机器人能描述并接话。
2. 用户发 10 秒语音，机器人转写后正常回复。
3. 长回复拆成 2–3 条，条间有可见延迟。
4. 图片/语音消息的回复带引用。
5. 对话情绪合适时发送一次表情包，且不连续刷屏。
6. 断网、接口失败、媒体损坏时仍能文字回复。
7. 连续聊 1000 条后重启，只恢复摘要和最近 20 条，启动时间不随历史线性增长。
8. 经过多轮对话，机器人能逐渐形成稳定的口头禅、偏好和称呼，且可查看、回滚和重置。

## 13. 依赖与部署

新增 Python 依赖：

- `faster-whisper`
- `edge-tts`
- `pilk`（SILK 解码）
- `Pillow`（图片缩放）

系统依赖：

- NapCat 侧需要可用的 ffmpeg（发送语音转码）。
- 机器人容器/主机不需要 GPU；2 核 4G 可运行 `base` 模型。
- 首次启动前预下载 faster-whisper 模型并挂载缓存目录（`WHISPER_CACHE_DIR` 或 HuggingFace 缓存），避免运行时拉取失败。
- Edge TTS 需要访问微软语音服务；网络不可用时自动禁用语音回复并退回文本。

## 14. 实施顺序

1. **持久化与上下文边界**：每会话恢复窗口、`ContextAssembler`、摘要游标、启动/每轮恢复测试。
2. **人格演化**：迁移 006、`PersonaStore`、core/retrieved 分层、增量提取、合并/衰减/版本回滚、`/persona` 命令与 API。
3. **媒体与视觉**：`Attachment`、迁移 005、`MediaStore`、QQ 图片解析、DeepSeek 多模态、降级。
4. **表情包**：索引、标签生成、自动收集、发送。
5. **拟人编排**：`Humanizer`、`ReplyPlan`、引用、分段延迟、`deliver_reply`。
6. **语音**：SILK 解码、ASR、TTS、概率与降级。
7. **收尾**：配置校验、文档、指标、全量测试。

每个阶段独立可测试，前一阶段不依赖后一阶段。

## 15. 风险与对策

| 风险 | 对策 |
|------|------|
| DeepSeek 视觉模型名称/能力变化 | 模型名可配置；失败自动降级文本 |
| NapCat 发送语音失败 | 退回文本；文档注明 ffmpeg 要求 |
| 2C4G 内存不足 | 默认 `base`、懒加载、空闲释放、并发 1 |
| 自动收集版权/隐私 | 仅本地、可关闭、容量上限、不对外上传 |
| 拟人分段造成刷屏 | 最多 3 条、冷却时间、仅私聊生效 |
| 多模态请求体过大 | 图片缩放、32 MiB 上限、历史窗口 2 |
| 人格漂移或被人为带偏 | 基础人设只读、置信度门槛、全局特质需跨会话证据、版本回滚 |
| 提示注入污染人格 | 结构化提取、类别白名单、拒绝指令型内容、敏感信息过滤 |

## 16. 人格演化层

### 16.1 分层

| 层 | 是否可变 | 说明 |
|----|----------|------|
| 基础人设 | 只读 | 角色卡 YAML：身份、底线、基础语气、示例对话 |
| 全局人格 | 可变、版本化 | 所有用户共享的语言习惯、偏好、观点、情绪模式 |
| 用户关系 | 可变、版本化 | 仅对某个 `platform:user_id` 生效的称呼、共同话题、雷区、互动习惯 |
| 会话摘要 | 可变 | 当前会话的滚动摘要，带覆盖游标 |
| 长期记忆 | 可变 | 现有 ChromaDB 向量记忆，按需 top-k 检索 |

`learning/evolution.py` 直接改写角色卡的旧路径停用；角色卡只作为基础人设读取。

演化特质分两层：

- **core（核心特质）**：长期稳定、置信度最高的少量特质，每轮固定注入，例如固定称呼、稳定口头禅、明确喜恶。全局最多 3 条，每用户最多 2 条。
- **retrieved（检索特质）**：其余 active 特质，按当前消息相关度取 top-k 注入，不常驻。

这样既保证人设稳定，又不会把整份档案塞进每轮提示词。

### 16.2 存储（迁移 `006_persona.sql`）

```sql
CREATE TABLE persona_profiles (
  scope TEXT NOT NULL,              -- global / user
  scope_key TEXT NOT NULL,          -- global / qq:<user_id>
  revision INTEGER NOT NULL DEFAULT 0,
  profile_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL,
  PRIMARY KEY (scope, scope_key)
);

CREATE TABLE persona_traits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope TEXT NOT NULL,
  scope_key TEXT NOT NULL,
  category TEXT NOT NULL,           -- speech_style / preference / opinion / relationship / emotional_pattern / boundary
  trait_key TEXT NOT NULL,
  value TEXT NOT NULL,
  confidence REAL NOT NULL,
  evidence TEXT NOT NULL,
  source_conversation_id INTEGER,
  source_sessions TEXT NOT NULL DEFAULT '[]',  -- 去重后的 session_id，最多 10 个
  tier TEXT NOT NULL DEFAULT 'retrieved',      -- core / retrieved
  status TEXT NOT NULL DEFAULT 'candidate',  -- candidate / active / faded / archived
  created_at TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  UNIQUE(scope, scope_key, category, trait_key, value)
);
CREATE INDEX idx_persona_traits_active ON persona_traits(scope, scope_key, status, confidence);

CREATE TABLE persona_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope TEXT NOT NULL,
  scope_key TEXT NOT NULL,
  revision INTEGER NOT NULL,
  diff_json TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE persona_cursors (
  session_id TEXT PRIMARY KEY,
  last_conversation_id INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);

ALTER TABLE chat_sessions ADD COLUMN summary_until_id INTEGER NOT NULL DEFAULT 0;
```

迁移必须可重复执行校验，遵循现有 SHA-256 迁移规则。

### 16.3 增量提取

- 触发：每个会话累计 `persona.evolve_after_messages`（默认 30）条新消息，或每日 `persona.evolve_schedule`（默认 04:00）。
- 输入：`conversations` 中 `id > persona_cursors.last_conversation_id` 的消息，单次最多 100 条；加上现有 active 特质（最多 20 条）和基础人设摘要。
- 绝不重新读取游标之前的消息。
- LLM 返回结构化 JSON：

```json
{
  "traits": [
    {
      "category": "speech_style",
      "trait_key": "句末语气词",
      "value": "偶尔用“嘛”",
      "confidence": 0.72,
      "evidence": "用户多次回应后继续使用",
      "scope": "global"
    }
  ]
}
```

- 校验：类别白名单、单条 ≤100 字、置信度 0–1、证据 ≤200 字；拒绝手机号、地址、证件号、银行卡、密码等敏感内容；拒绝包含“忽略规则/修改系统提示”等指令型文本。
- 更新游标：无论是否产生特质，都推进到本批最后一条消息 ID，避免重复分析。

### 16.4 合并、门槛与衰减

- **完全重复**：`confidence = 1 - (1 - old)(1 - new)`，上限 0.99，更新 `last_seen`。
- **同 key 不同 value**：
  - 新值置信度 ≥ 旧值 + 0.15 → 旧值 `faded`，新值 `active`；
  - 否则新值保存为 `candidate`，旧值保留。
- **全局特质**：置信度 ≥ 0.75 且至少在 2 个不同会话中出现，才允许 `active`；单次对话不能改变全局人格。
- **用户特质**：置信度 ≥ 0.6 即可 `active`，但只作用于该用户。
- **核心晋升**：全局特质置信度 ≥ 0.85 且在 ≥5 个不同会话中出现，可晋升 `core`；用户特质 ≥ 0.8 且在 ≥3 个会话中出现，可晋升 `core`。超出 core 名额时保留置信度最高者，其余退回 `retrieved`。
- **数量上限**：全局 active ≤ 12 条，每用户 active ≤ 8 条；全局 core ≤ 3，用户 core ≤ 2；单次演化最多应用 3 条变更。
- **衰减**：每日任务对 `last_seen` 超过 7 天的特质降低 0.05 置信度；低于 0.35 转 `faded`，低于 0.2 转 `archived`，不再注入提示词。
- **渐进性**：单次演化不允许替换基础人设或安全规则；人格变化只表现为特质增减。

### 16.5 注入与检索

- 每轮只注入：
  - 基础人设（固定）；
  - 全局 core 特质（最多 3 条，固定注入）；
  - 用户 core 特质（最多 2 条，固定注入）；
  - 全局 retrieved 特质，按“与当前消息关键词相关度 + 置信度”取 top 8；
  - 当前用户 retrieved 特质，同样规则取 top 6；
  - 会话摘要；
  - 最近消息。
- 相关度使用简单关键词/标签匹配，不新增 embedding 依赖。
- 特质注入格式为简短条目，不把原始证据拼进提示词。
- 用户关系档案不会跨用户共享。

### 16.6 版本、命令与可观测

- 每次应用变更写入 `persona_versions`（diff、原因、revision），`persona_profiles.revision` 递增。
- 命令：
  - `/persona show`：查看当前全局与个人特质；
  - `/persona forget <关键词>`：删除匹配特质；
  - `/persona rollback [n]`：回滚最近 n 次演化；
  - `/persona reset`：重置个人关系档案（管理员可重置全局）。
- Web API：`GET /api/v1/persona`、`POST /api/v1/persona/rollback`、`DELETE /api/v1/persona/traits`，受现有令牌保护。
- 指标：`persona_updates_total`、`persona_trait_count`、`persona_rejected_candidates`、`persona_prompt_tokens`。

### 16.7 安全边界

- 演化结果不能覆盖系统提示、安全规则、角色卡基础身份和用户明确设置的边界。
- 不记录用户隐私和敏感信息；证据只保留与表达风格相关的短句，并可一键删除。
- 提取结果视为数据，不作为指令执行；包含指令型内容直接丢弃。
- 用户可查看、删除自己的关系档案；全局档案仅管理员可改。

## 17. 上下文预算与启动恢复

### 17.1 `ContextAssembler`

每轮提示词按固定优先级组装，超预算时从低优先级开始裁剪：

1. 系统安全规则 + 基础人设 + core 特质（固定，不裁剪）；
2. 当前用户消息与当前图片（固定，不裁剪）；
3. 最近消息，默认 12 条；
4. 会话摘要，默认 ≤300 tokens；
5. 当前用户 retrieved 特质，默认 ≤150 tokens / top 6；
6. 全局 retrieved 特质，默认 ≤150 tokens / top 8；
7. 长期记忆 top-k，默认 3 条 / ≤300 tokens。

裁剪顺序：长期记忆 → 全局 retrieved 特质 → 用户 retrieved 特质 → 最旧的最近消息 → 截断摘要。core 特质和当前消息永不裁剪。

- 总预算 `context.max_prompt_tokens`，默认 4000。
- token 计数复用现有 `llm/token_counter.py`；失败时按字符数估算。
- 组装结果只包含“基础人设 + 特质 + 摘要 + 最近消息 + 检索记忆”，**不包含全量历史**。

### 17.2 启动与每轮恢复

- `load_sessions(limit, messages_per_session)`：先用窗口函数筛选每个会话最近的 N 条，再加载；摘要单独读取。
- `load_session(session_id, messages_per_session)`：多实例模式每轮刷新也只取最近 N 条。
- 默认 `persistence.messages_per_session = 20`；可在配置中调整。
- `Session.messages` 在内存中始终不超过 `memory.short_term.max_messages`。
- ChromaDB、embedding 模型保持懒加载，启动时不加载。
- 摘要器只处理 `id > summary_until_id` 的新消息，更新摘要后推进游标；旧消息不再重复摘要。

### 17.3 指标与测试

- 指标：`context_prompt_tokens`、`context_trims_total`、`session_restore_messages`。
- 测试：
  - 插入 1000 条会话消息，断言 `load_session` 只返回最近 N 条；
  - 断言提示词不包含游标之前的消息；
  - 断言超预算时按优先级裁剪且当前消息保留；
  - 断言摘要器只处理增量消息。

## 18. 参考项目与取舍

| 项目 | 采用方式 | 具体借鉴点 | 不采用的部分 |
|------|----------|------------|--------------|
| [AstrBot](https://github.com/AstrBotDevs/AstrBot) | 架构参考 | 多平台适配层、插件体系、WebUI、人格设定、上下文压缩、DeepSeek 接入 | 不复制代码（AGPL-3.0）；不引入其 Agent 沙箱和完整插件生态 |
| [MaiBot](https://github.com/MaiM-with-u/MaiBot) | 行为参考 | 表情包情绪匹配、自动收集与审核、表达学习、动态人格 | 不做群聊主动插话；不引入其独立情绪引擎 |
| [CharMemory](https://github.com/bal-spec/sillytavern-character-memory) | 机制参考 | 每 N 条消息增量提取结构化记忆、可编辑记忆、按相关性注入 | 不使用 SillyTavern 的数据格式和扩展运行时 |
| [Letta](https://github.com/letta-ai/letta) | 概念参考 | core / archival 分层、上下文预算、人格稳定性 | 不部署 Letta 服务，不引入其存储和 API 依赖 |
| [Open-LLM-VTuber](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber) | 语音参考 | ASR/TTS provider 工厂、失败降级、VAD 思路 | 不做 Live2D、实时打断、桌面宠物 |
| [NapCatQQ](https://github.com/NapNeko/NapCatQQ) | 现有接入 | OneBot 图片/语音/引用消息段、SILK 转码 | 不改协议端本身 |

### 许可证与依赖原则

- AstrBot 为 AGPL-3.0：只借鉴架构，不复制代码，避免许可证传染。
- 不把 mem0、Letta、SillyTavern 等运行时作为依赖引入；2 核 4G 单机优先使用 SQLite + 现有 LLM 客户端。
- 新增 Python 依赖只保留语音和图片处理必需的库，见第 13 节。
- 所有参考项目的行为只作为设计输入，最终实现以本 spec 的接口和数据模型为准。

## 19. 实现增补（2026-10-23，合并 qq-bridge 机制）

实现时发现以下假设需要修正，代码以本节为准：

1. **入站段解析**：`platforms/qq.py` 原先直接返回 `raw_message`，图片/语音/引用都是 CQ 码文本。现由 `platforms/qq_segments.py` 同时解析 OneBot 数组和 CQ 字符串；`content` 是逻辑文本，`model_content` 是模型视角（含 `[引用 人：原文]`、媒体占位符）。
2. **追加型归档**：`conversations` 每次保存都会删除重插，id 不稳定。新增 `conversation_archive`（迁移 005），摘要与人格游标只认它的自增 id。
3. **PostgreSQL**：不执行 `.sql` 迁移文件；所有新表/列必须同时写入 `database/models.py`，新列还需在 `database/db.py` 的 `_run_postgresql_migrations` 中补 `ADD COLUMN IF NOT EXISTS`。
4. **语音可选**：镜像固定 Python 3.12，`faster-whisper`/`edge-tts` 通过 `INSTALL_VOICE=true` 构建；`voice.enabled` 默认 false。SILK 解码优先 `pilk`，不可用时回退 ffmpeg，再失败则文本占位。
5. **NapCat 媒体可见性**：`./data/media` 以相同绝对路径 `/app/data/media` 挂入 NapCat，否则 `file://` 图片/语音发送失败。
6. **图片限额**：下载 8 MB / 64 MP / 单条 5 张；送模型 ≤1 MB、最长边 1280 px；GIF 取首帧。
7. **全局人格候选制**：全局特质即使达到置信度与跨会话门槛，也必须管理员 `/persona approve` 后才生效；用户特质按阈值自动生效。
8. **投递接缝**：`Engine.process_message()` 与流式路径保持返回字符串；平台入口改用 `process_message_plan()`，返回 `ReplyPlan`。群聊与非拟人化计划回退到 `send_long_message`。
9. **出站安全**：新增 `security/outbound_guard.py`（本机路径/凭据/已知密钥拦截）与 `platforms/delivery.py`（8/分钟、60/小时配额，发送成功才计语音配额）。
10. **Token 账本**：迁移 007；`llm/usage.py` 按 cache miss/hit/write/output 记录，事件键幂等，`/api/v1/usage` 聚合。
