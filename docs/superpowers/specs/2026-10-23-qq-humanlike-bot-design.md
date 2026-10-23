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
3. `VoiceTranscriber`：`faster-whisper`，默认 `base`、`int8`、`language=zh`、懒加载；信号量限制并发 1；超过 `max_audio_seconds` 截断或拒绝。
4. 转写文本作为 `content`，前缀 `[语音] `；原始语音作为 `Attachment(kind=VOICE, transcript=...)`。
5. 转写失败：`[语音消息，未能识别，请发文字]`，继续对话。

### 8.2 出站

1. `SpeechSynthesizer`：`edge-tts` 输出 mp3 到 `data/media/tts/`，默认音色 `zh-CN-XiaoxiaoNeural`。
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
  asr_model: "base"
  max_audio_seconds: 60
  tts_voice: "zh-CN-XiaoxiaoNeural"
  voice_probability: 0.15
  voice_max_chars: 80
  tts_timeout_seconds: 15
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

## 12. 测试与验收

### 单元测试

- `tests/test_qq_media.py`：image/record 段解析、URL 校验、哈希去重。
- `tests/test_media_store.py`：大小限制、原子写入、清理。
- `tests/test_vision_payload.py`：多模态消息构造、模型切换、历史图片窗口。
- `tests/test_stickers.py`：索引、标签匹配、自动收集启发式、冷却与去重。
- `tests/test_humanizer.py`：标记解析、分段上限、延迟范围、引用决策。
- `tests/test_voice.py`：SILK 解码失败降级、ASR 超时、TTS 失败退回文本。
- `tests/test_reply_plan.py`：`process_message` 兼容性、plan 投递顺序。

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

1. **媒体与视觉**：`Attachment`、`MediaStore`、QQ 图片解析、DeepSeek 多模态、降级。
2. **表情包**：索引、标签生成、自动收集、发送。
3. **拟人编排**：`Humanizer`、`ReplyPlan`、引用、分段延迟、`deliver_reply`。
4. **语音**：SILK 解码、ASR、TTS、概率与降级。
5. **收尾**：配置校验、文档、指标、全量测试。

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
