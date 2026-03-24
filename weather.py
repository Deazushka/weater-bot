"""
weather.py — Интеграция с OpenWeatherMap API

Функции:
  - Получение погоды по городу или координатам
  - Индекс качества воздуха (AQI)
  - Геокодирование города → (lat, lon)
"""

import aiohttp
from typing import Optional

import config


# ─── Вспомогательные функции ──────────────────────────────────────────────────

def _hpa_to_mmhg(hpa: float) -> float:
    """Конвертирует давление из гПа в мм рт. ст."""
    return round(float(hpa * config.HPA_TO_MMHG), 1)


def _aqi_label(aqi: int) -> str:
    """Текстовая метка для AQI (1–5 по шкале OWM)."""
    labels = {
        1: "🟢 Хороший",
        2: "🟡 Удовлетворительный",
        3: "🟠 Умеренный",
        4: "🔴 Плохой",
        5: "🟣 Очень плохой",
    }
    return labels.get(aqi, "❓ Неизвестно")


# ─── Геокодирование ────────────────────────────────────────────────────────────

async def geocode_city(city: str) -> Optional[tuple[float, float]]:
    """
    Возвращает (lat, lon) для названия города или None при ошибке.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{config.OWM_GEO_URL}/direct",
                params={"q": city, "limit": 1, "appid": config.OWM_API_KEY},
                timeout=10,
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                if not data:
                    return None
                return data[0]["lat"], data[0]["lon"]
    except Exception:
        return None


async def reverse_geocode(lat: float, lon: float) -> Optional[str]:
    """Возвращает название города по координатам или None."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{config.OWM_GEO_URL}/reverse",
                params={"lat": lat, "lon": lon, "limit": 1, "appid": config.OWM_API_KEY},
                timeout=10,
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                if not data:
                    return None
                return data[0].get("local_names", {}).get("ru") or data[0].get("name")
    except Exception:
        return None


# ─── Погода ────────────────────────────────────────────────────────────────────

def _parse_weather(data: dict) -> dict:
    """Разбирает ответ OWM /weather в унифицированный словарь."""
    main = data.get("main", {})
    return {
        "city":         data.get("name", ""),
        "description":  data.get("weather", [{}])[0].get("description", ""),
        "temp":         round(main.get("temp", 0)),
        "feels_like":   round(main.get("feels_like", 0)),
        "humidity":     main.get("humidity", 0),
        "pressure_hpa": main.get("pressure", 0),
        "pressure_mmhg": _hpa_to_mmhg(main.get("pressure", 0)),
        "wind_speed":   data.get("wind", {}).get("speed", 0),
    }


async def get_weather_by_city(city: str) -> Optional[dict]:
    """
    Возвращает словарь с метеоданными для города.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{config.OWM_BASE_URL}/weather",
                params={
                    "q":     city,
                    "appid": config.OWM_API_KEY,
                    "units": "metric",
                    "lang":  "ru",
                },
                timeout=10,
            ) as resp:
                resp.raise_for_status()
                return _parse_weather(await resp.json())
    except Exception:
        return None


async def get_weather_by_coords(lat: float, lon: float) -> Optional[dict]:
    """Возвращает метеоданные по координатам."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{config.OWM_BASE_URL}/weather",
                params={
                    "lat":   lat,
                    "lon":   lon,
                    "appid": config.OWM_API_KEY,
                    "units": "metric",
                    "lang":  "ru",
                },
                timeout=10,
            ) as resp:
                resp.raise_for_status()
                return _parse_weather(await resp.json())
    except Exception:
        return None


# ─── Качество воздуха ─────────────────────────────────────────────────────────

async def get_aqi(lat: float, lon: float) -> Optional[dict]:
    """
    Возвращает данные о качестве воздуха.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                config.OWM_AQI_URL,
                params={"lat": lat, "lon": lon, "appid": config.OWM_API_KEY},
                timeout=10,
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                item = data.get("list", [{}])[0]
                aqi  = item.get("main", {}).get("aqi", 0)
                comp = item.get("components", {})
                return {
                    "aqi":       aqi,
                    "aqi_label": _aqi_label(aqi),
                    "co":        comp.get("co", 0),
                    "no2":       comp.get("no2", 0),
                    "pm2_5":     comp.get("pm2_5", 0),
                    "pm10":      comp.get("pm10", 0),
                }
    except Exception:
        return None


async def get_aqi_by_city(city: str) -> Optional[dict]:
    """Вспомогательная функция: AQI по названию города."""
    coords = await geocode_city(city)
    if not coords:
        return None
    return await get_aqi(*coords)


# ─── Полная сводка ─────────────────────────────────────────────────────────────

async def get_full_weather(city: str) -> Optional[dict]:
    """
    Возвращает объединённые данные: погода + AQI для города.
    """
    weather = await get_weather_by_city(city)
    if not weather:
        return None

    coords  = await geocode_city(city)
    aqi_info = await get_aqi(*coords) if coords else None

    return {**weather, "aqi_info": aqi_info}
