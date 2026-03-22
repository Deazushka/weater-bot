"""
weather.py — Интеграция с OpenWeatherMap API

Функции:
  - Получение погоды по городу или координатам
  - Индекс качества воздуха (AQI)
  - Геокодирование города → (lat, lon)
"""

import requests
from typing import Optional

import config


# ─── Вспомогательные функции ──────────────────────────────────────────────────

def _hpa_to_mmhg(hpa: float) -> float:
    """Конвертирует давление из гПа в мм рт. ст."""
    return round(hpa * config.HPA_TO_MMHG, 1)


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

def geocode_city(city: str) -> Optional[tuple[float, float]]:
    """
    Возвращает (lat, lon) для названия города или None при ошибке.

    Args:
        city: название города (может быть на русском или английском)
    """
    try:
        resp = requests.get(
            f"{config.OWM_GEO_URL}/direct",
            params={"q": city, "limit": 1, "appid": config.OWM_API_KEY},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return None
        return data[0]["lat"], data[0]["lon"]
    except Exception:
        return None


def reverse_geocode(lat: float, lon: float) -> Optional[str]:
    """Возвращает название города по координатам или None."""
    try:
        resp = requests.get(
            f"{config.OWM_GEO_URL}/reverse",
            params={"lat": lat, "lon": lon, "limit": 1, "appid": config.OWM_API_KEY},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
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


def get_weather_by_city(city: str) -> Optional[dict]:
    """
    Возвращает словарь с метеоданными для города.

    Returns:
        dict с ключами: city, description, temp, feels_like, humidity,
                        pressure_hpa, pressure_mmhg, wind_speed
        None при ошибке
    """
    try:
        resp = requests.get(
            f"{config.OWM_BASE_URL}/weather",
            params={
                "q":     city,
                "appid": config.OWM_API_KEY,
                "units": "metric",
                "lang":  "ru",
            },
            timeout=10,
        )
        resp.raise_for_status()
        return _parse_weather(resp.json())
    except Exception:
        return None


def get_weather_by_coords(lat: float, lon: float) -> Optional[dict]:
    """Возвращает метеоданные по координатам."""
    try:
        resp = requests.get(
            f"{config.OWM_BASE_URL}/weather",
            params={
                "lat":   lat,
                "lon":   lon,
                "appid": config.OWM_API_KEY,
                "units": "metric",
                "lang":  "ru",
            },
            timeout=10,
        )
        resp.raise_for_status()
        return _parse_weather(resp.json())
    except Exception:
        return None


# ─── Качество воздуха ─────────────────────────────────────────────────────────

def get_aqi(lat: float, lon: float) -> Optional[dict]:
    """
    Возвращает данные о качестве воздуха.

    Returns:
        dict: {aqi: int (1-5), aqi_label: str, co, no2, pm2_5, pm10}
        None при ошибке
    """
    try:
        resp = requests.get(
            config.OWM_AQI_URL,
            params={"lat": lat, "lon": lon, "appid": config.OWM_API_KEY},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
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


def get_aqi_by_city(city: str) -> Optional[dict]:
    """Вспомогательная функция: AQI по названию города."""
    coords = geocode_city(city)
    if not coords:
        return None
    return get_aqi(*coords)


# ─── Полная сводка ─────────────────────────────────────────────────────────────

def get_full_weather(city: str) -> Optional[dict]:
    """
    Возвращает объединённые данные: погода + AQI для города.

    Returns:
        dict с полями weather и aqi_info (может быть None при недоступности AQI)
    """
    weather = get_weather_by_city(city)
    if not weather:
        return None

    coords  = geocode_city(city)
    aqi_info = get_aqi(*coords) if coords else None

    return {**weather, "aqi_info": aqi_info}
