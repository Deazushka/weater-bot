"""
space_weather.py — Интеграция с NOAA SWPC API

Функции:
  - Получение текущего Kp-индекса
  - Прогноз Kp на 24 ч
  - Классификация магнитной бури
"""

import aiohttp
from typing import Optional

import config


# ─── Классификация ────────────────────────────────────────────────────────────

def classify_storm(kp: float) -> dict:
    """
    Возвращает метку и описание магнитной активности по Kp-индексу.

    Шкала NOAA G (Geomagnetic Storm):
        Kp < 5  → спокойно
        Kp 5    → G1 (слабая буря)
        Kp 6    → G2 (умеренная)
        Kp 7    → G3 (сильная)
        Kp 8    → G4 (очень сильная)
        Kp 9    → G5 (экстремальная)
    """
    if kp < 4:
        return {"level": "calm",   "emoji": "🟢", "label": "Спокойно",           "storm_class": None}
    elif kp < 5:
        return {"level": "unsettled", "emoji": "🟡", "label": "Неспокойно",      "storm_class": None}
    elif kp < 6:
        return {"level": "G1",    "emoji": "🟠", "label": "Слабая буря (G1)",    "storm_class": "G1"}
    elif kp < 7:
        return {"level": "G2",    "emoji": "🔴", "label": "Умеренная буря (G2)", "storm_class": "G2"}
    elif kp < 8:
        return {"level": "G3",    "emoji": "🔴", "label": "Сильная буря (G3)",   "storm_class": "G3"}
    elif kp < 9:
        return {"level": "G4",    "emoji": "🆘", "label": "Очень сильная (G4)", "storm_class": "G4"}
    else:
        return {"level": "G5",    "emoji": "☢️",  "label": "Экстремальная (G5)", "storm_class": "G5"}


# ─── Медицинский совет по Kp ──────────────────────────────────────────────────

def get_storm_advice(kp: float, bp_type: str = "norm") -> str:
    """
    Возвращает персональный совет в зависимости от Kp и типа давления.

    Args:
        kp:      текущий Kp-индекс
        bp_type: 'hyper' | 'hypo' | 'norm'
    """
    if kp < 4:
        return "Магнитная обстановка спокойная. Самочувствие должно быть стабильным."

    storm = classify_storm(kp)
    base  = f"⚠️ {storm['label']}. Возможно ухудшение самочувствия у метеозависимых."

    extras = {
        "hyper": " Гипертоникам: следите за давлением, избегайте стрессов.",
        "hypo":  " Гипотоникам: пейте больше воды, возможна усталость и слабость.",
        "norm":  " Рекомендуем избегать перегрузок и больше отдыхать.",
    }
    return base + extras.get(bp_type, "")


# ─── Получение данных NOAA ────────────────────────────────────────────────────

async def get_kp_index() -> Optional[float]:
    """
    Возвращает последнее измеренное значение Kp-индекса.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(config.NOAA_KP_URL, timeout=10) as resp:
                resp.raise_for_status()
                data = await resp.json()
                # Данные — список записей [timestamp, Kp]. Берём последнюю непустую.
                for entry in reversed(data):
                    kp_val = entry.get("kp_index") if isinstance(entry, dict) else (
                        entry[1] if isinstance(entry, list) and len(entry) > 1 else None
                    )
                    if kp_val is not None:
                        return float(kp_val)
                return None
    except Exception:
        return None


async def get_kp_full() -> Optional[dict]:
    """
    Возвращает текущий Kp с классификацией.
    """
    kp = await get_kp_index()
    if kp is None:
        return None
    storm = classify_storm(kp)
    return {"kp": kp, **storm}


async def get_kp_forecast() -> Optional[list[dict]]:
    """
    Возвращает прогноз Kp на ближайшие 24 ч.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(config.NOAA_KP_FORECAST, timeout=10) as resp:
                resp.raise_for_status()
                data = await resp.json()
                result = []
                for entry in data[:24]:  # первые 24 записи (по 1 ч)
                    if isinstance(entry, dict):
                        kp_val = entry.get("kp")
                        ts     = entry.get("time_tag", "")
                    elif isinstance(entry, list) and len(entry) >= 2:
                        ts, kp_val = entry[0], entry[1]
                    else:
                        continue
                    if kp_val is None:
                        continue
                    kp_f = float(kp_val)
                    result.append({"time_tag": ts, "kp": kp_f, **classify_storm(kp_f)})
                return result if result else None
    except Exception:
        return None


async def get_max_kp_forecast_24h() -> Optional[float]:
    """Возвращает максимальный ожидаемый Kp за следующие 24 ч."""
    forecast = await get_kp_forecast()
    if not forecast:
        return None
    return max(e["kp"] for e in forecast)
