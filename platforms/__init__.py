"""Platform adapter contracts and implementations."""

from platforms.base import (
    BasePlatform,
    PlatformConfigurationError,
    PlatformDeliveryError,
    PlatformError,
    PlatformNotReadyError,
    PlatformRateLimitedError,
    PlatformState,
)
from platforms.manager import PlatformManager
from platforms.models import DeliveryReceipt, MessageType, Platform, UnifiedMessage

__all__ = [
    "BasePlatform",
    "DeliveryReceipt",
    "MessageType",
    "Platform",
    "PlatformConfigurationError",
    "PlatformDeliveryError",
    "PlatformError",
    "PlatformManager",
    "PlatformNotReadyError",
    "PlatformRateLimitedError",
    "PlatformState",
    "UnifiedMessage",
]
