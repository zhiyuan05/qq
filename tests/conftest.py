"""测试公共配置"""
import sys
from pathlib import Path

import pytest

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.config import AppConfig


@pytest.fixture
def default_config() -> AppConfig:
    return AppConfig()


@pytest.fixture
def config_with_key() -> AppConfig:
    cfg = AppConfig()
    cfg.llm.api_key = "test-key"
    return cfg
