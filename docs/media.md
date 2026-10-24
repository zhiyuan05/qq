# 媒体与拟人回复

## 链路

```
QQ 段解析 (qq_segments) → 私聊下载 (media/net + media/store)
  → 图片归一化 (media/images) → 多模态提示词 → LLM
  → Humanizer → ReplyPlan → QQ 段数组投递
```

群聊只解析出占位符，不下载媒体、不启用拟人分段/表情包/语音。

## 限额（`media` 配置）

| 项 | 默认 | 说明 |
|---|---|---|
| `max_fetch_bytes` | 8 MB | 单次下载上限 |
| `max_image_pixels` | 64 MP | 解码像素上限 |
| `max_images_per_message` | 5 | 单条消息最多处理图片数 |
| `vision_max_side` | 1280 px | 送模型前缩放 |
| `vision_max_bytes` | 1 MB | 送模型单图上限 |
| `retention_days` | 7 | 媒体保留期，每日清理 |

## 安全

- 每次请求及每跳重定向都重新解析 DNS；任一地址不是公网地址即拒绝。
- 仅 http/https，拒绝 URL 凭据、localhost、`.local`。
- 8 秒单次 / 20 秒总超时，流式读取并在超限时中止。
- 文件按内容哈希落盘；扩展名由魔数决定，不信任 URL。
- 表情包只从索引库选择真实文件，发送前再次校验 `realpath`。

## 表情包

- 库目录：`characters/stickers/`；索引缓存：`data/stickers.db`（可重建）。
- 自动收集仅私聊：GIF/WebP 直接收；PNG/JPG 需 ≤512 px 且 ≤2 MB。
- `review_required=true` 时先入 `candidate`，用 `/sticker approve <hash前缀>` 或 API 审核。
- LLM 输出 `[sticker:标签]`，`Humanizer` 解析并选图；会话 30 秒、同图 10 分钟冷却。

## 拟人回复

- 长回复按句末标点/换行拆成最多 3 条，每条 ≤500 字；放不下则回退旧的长消息分割。
- 引用只挂第一条；图片/语音消息必引用，文本按 `quote_probability`。
- 出站拦截本机路径、凭据赋值和已配置密钥；命中即丢弃该条并记录哈希前缀。公网 URL 会先被剥离，避免正常链接中的 `/data/` 等路径误判。
- 图片历史窗口只重新附带最近 N 张；视觉块按路径缓存，避免重复读盘和缩放。
- 私聊出站配额：默认 8 条/分钟、60 条/小时（语音独立配额）。

## 故障降级

下载失败、视觉调用失败、表情包索引失败、TTS 失败都只降级，不影响文字回复。
