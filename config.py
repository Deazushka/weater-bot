"""
config.py — Конфигурация HeadCare Bot

Загружает переменные окружения и определяет константы приложения.
"""

import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv не установлен — переменные должны быть в окружении

# ─── Telegram ──────────────────────────────────────────────────────────────────
BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")

# ─── OpenWeatherMap API ────────────────────────────────────────────────────────
OWM_API_KEY: str = os.environ.get("OWM_API_KEY", "")
OWM_BASE_URL = "https://api.openweathermap.org/data/2.5"
OWM_GEO_URL  = "http://api.openweathermap.org/geo/1.0"
OWM_AQI_URL  = "https://api.openweathermap.org/data/2.5/air_pollution"

# ─── NOAA SWPC API ─────────────────────────────────────────────────────────────
NOAA_KP_URL      = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
NOAA_KP_FORECAST = "https://services.swpc.noaa.gov/json/planetary_k_index_forecast.json"


# ─── Медицинские пороги ────────────────────────────────────────────────────────
# Kp-индекс: порог по умолчанию для алертов магнитных бурь
KP_DEFAULT_THRESHOLD: int = 5

# Перепад атмосферного давления (мм рт. ст.) за сутки → алерт «барическая пила»
PRESSURE_DELTA_ALERT_MMHG: int = 7

# Перевод давления: 1 гПа = 0.750062 мм рт. ст.
HPA_TO_MMHG: float = 0.750062

# ─── Типы артериального давления ──────────────────────────────────────────────
BLOOD_PRESSURE_TYPES = {
    "hyper": "гипертония",
    "hypo":  "гипотония",
    "norm":  "нормотония",
}

# ─── База данных ───────────────────────────────────────────────────────────────
DB_PATH: str = os.environ.get("DB_PATH", "alerts.db")

# ─── Расписание фоновых задач ─────────────────────────────────────────────────
# Интервал (часы) проверки Kp-индекса для экстренных алертов
KP_CHECK_INTERVAL_HOURS: int = 3

# Интервал (часы) проверки «барической пилы»
PRESSURE_CHECK_INTERVAL_HOURS: int = 6

# ─── Валидация конфигурации ────────────────────────────────────────────────────
def validate_config() -> None:
    """Проверяет наличие обязательных переменных окружения."""
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not OWM_API_KEY:
        missing.append("OWM_API_KEY")
    if missing:
        raise EnvironmentError(
            f"Отсутствуют обязательные переменные окружения: {', '.join(missing)}\n"
            "Задайте их перед запуском бота."
        )
