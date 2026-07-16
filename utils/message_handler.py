"""消息类型处理 - 图片/语音/文件"""
from typing import Optional

from platforms.models import UnifiedMessage, MessageType
from utils.logger import setup_logger

logger = setup_logger("msg_handler")


class MessageHandler:
    """处理不同类型的消息"""

    @staticmethod
    def extract_text(msg: UnifiedMessage) -> str:
        """提取消息文本内容"""
        if msg.message_type == MessageType.TEXT:
            return msg.content
        elif msg.message_type == MessageType.IMAGE:
            return f"[图片] {msg.content}" if msg.content else "[图片]"
        elif msg.message_type == MessageType.VOICE:
            return f"[语音] {msg.content}" if msg.content else "[语音]"
        elif msg.message_type == MessageType.FILE:
            return f"[文件] {msg.content}" if msg.content else "[文件]"
        return msg.content

    @staticmethod
    def is_processable(msg: UnifiedMessage) -> bool:
        """判断消息是否可处理"""
        return msg.message_type in (MessageType.TEXT, MessageType.IMAGE, MessageType.VOICE)

    @staticmethod
    def get_image_url(msg: UnifiedMessage) -> Optional[str]:
        """提取图片URL（如果有）"""
        if msg.message_type == MessageType.IMAGE:
            return msg.raw_data.get("url") or msg.raw_data.get("image_url")
        return None
