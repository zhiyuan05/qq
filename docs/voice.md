# 语音（可选）

语音默认关闭。基础镜像已安装 ffmpeg/ffprobe（视频关键帧需要），但不安装 `faster-whisper`、`edge-tts`，因此 2C4G 服务器可以只跑文字聊天。

## 部署

```bash
./deploy.sh --voice              # 可选再叠加 --postgres
```

脚本会：

1. 以 `CHATBOT_INSTALL_VOICE=true` 构建镜像（安装 `requirements-voice.txt`；ffmpeg 已在基础镜像中）；
2. 把 Whisper `base` 模型预热到 `./data/whisper`；
3. 设置 `CHATBOT_VOICE_ENABLED=true` 和默认 2G 容器内存上限。

模型预热失败不会阻塞部署；语音会在首次使用时重试，仍失败则自动退回文字。

## 资源

- ASR 并发固定为 1，`base` + `int8`，空闲 10 分钟后卸载。
- 单条语音最长 60 秒（`voice.max_audio_seconds`），超长音频在 ASR 前直接拒绝；TTS 超时 15 秒。
- QQ 语音优先让 NapCat 用 `get_record(out_format=wav)` 转码，避免依赖 pilk；解码后的临时 WAV 在转写后删除。
- 语音配额默认 2 条/分钟、10 条/小时，与文本配额分开。
- 2C4G 实测 `base` 模型使用 `CHATBOT_MEMORY_LIMIT=1536M` 可稳定运行；换 `small` 时建议 2G，并用 `docker stats` 观察峰值。

## 入站

`record` 段优先使用 `url`。解码顺序：WAV 直通 → `pilk`（若已安装）→ ffmpeg → 占位
`[语音消息，未能识别，请发文字]`。转写文本进入会话，原始音频保存在 `data/media/voice/`。

## 出站

Edge TTS 只写入 `data/media/tts/`，`file://` 路径必须落在媒体目录内，拒绝 base64/data/http 输入。
发送失败时回退为文字；只有 `record` 动作成功后才计入语音配额。

## 排障

| 现象 | 检查 |
|---|---|
| 启动日志“语音依赖未安装” | 镜像是否用 `--voice` 构建 |
| 语音发出后对方无声 | NapCat 是否挂载了 `./data/media` 到相同路径 |
| 首次识别很慢 | 模型未预热或正在下载；检查 `data/whisper` 与 `WHISPER_CACHE_DIR` |
| TTS 超时 | 服务器是否能访问微软语音服务 |
