"""实时天气查询（Open-Meteo，无需 API Key）。"""
from __future__ import annotations

import time
from typing import Any

import httpx

from utils.logger import setup_logger

logger = setup_logger("utils.weather")

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_CACHE_TTL = 6 * 3600
_CACHE_SIZE = 200
_geo_cache: dict[str, tuple[float, dict[str, Any]]] = {}

_WEATHER_CODES = {
    0: "晴",
    1: "大部晴朗",
    2: "局部多云",
    3: "阴",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "大毛毛雨",
    56: "冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "米雪",
    80: "阵雨",
    81: "强阵雨",
    82: "暴雨",
    85: "阵雪",
    86: "强阵雪",
    95: "雷阵雨",
    96: "雷暴伴小冰雹",
    99: "雷暴伴大冰雹",
}


async def _geocode(city: str) -> dict[str, Any] | None:
    key = city.strip()
    cached = _geo_cache.get(key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _CACHE_TTL:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(
                _GEOCODE_URL,
                params={"name": key, "count": 1, "language": "zh", "format": "json"},
            )
        data = response.json() if response.status_code == 200 else {}
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("天气城市查询失败 (%s)", type(exc).__name__)
        return None
    results = data.get("results") if isinstance(data, dict) else None
    if not results:
        return None
    location = results[0]
    if len(_geo_cache) >= _CACHE_SIZE:
        _geo_cache.clear()
    _geo_cache[key] = (now, location)
    return location


async def weather_report(city: str, days: int = 3) -> str | None:
    """Return a compact Chinese forecast for ``city`` or None when unknown."""
    location = await _geocode(city)
    if not location:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                _FORECAST_URL,
                params={
                    "latitude": location["latitude"],
                    "longitude": location["longitude"],
                    "current": "temperature_2m,apparent_temperature,weather_code",
                    "daily": ",".join(
                        [
                            "weather_code",
                            "temperature_2m_max",
                            "temperature_2m_min",
                            "precipitation_probability_max",
                        ]
                    ),
                    "timezone": "Asia/Shanghai",
                    "forecast_days": max(1, min(7, days)),
                },
            )
        payload = response.json() if response.status_code == 200 else {}
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("天气查询失败 (%s)", type(exc).__name__)
        return None
    current = payload.get("current") or {}
    daily = payload.get("daily") or {}
    dates = daily.get("time") or []
    if not dates:
        return None

    place = str(location.get("name") or city)
    admin = str(location.get("admin1") or "")
    country = str(location.get("country") or "")
    label = " ".join(part for part in (place, admin, country) if part and part != place)
    lines = []
    if current.get("temperature_2m") is not None:
        code = _WEATHER_CODES.get(int(current.get("weather_code") or -1), "")
        feels = current.get("apparent_temperature")
        detail = f"现在 {current['temperature_2m']:.0f}°C"
        if feels is not None:
            detail += f"（体感 {feels:.0f}°C）"
        if code:
            detail += f"，{code}"
        lines.append(detail)

    names = daily.get("weather_code") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    rains = daily.get("precipitation_probability_max") or []
    for index, date in enumerate(dates):
        try:
            code = _WEATHER_CODES.get(int(names[index]), "未知")
        except (IndexError, TypeError, ValueError):
            code = "未知"
        high = highs[index] if index < len(highs) else None
        low = lows[index] if index < len(lows) else None
        rain = rains[index] if index < len(rains) else None
        day = date[5:].replace("-", "月") + "日"
        line = f"{day} {code}"
        if high is not None and low is not None:
            line += f" {low:.0f}~{high:.0f}°C"
        if rain is not None:
            line += f" 降水概率 {rain:.0f}%"
        lines.append(line)
    header = f"{place}" + (f"（{label}）" if label else "")
    return f"{header}\n" + "\n".join(lines)
