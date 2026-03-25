"""
scheduler.py — Фоновый планировщик HeadCare Bot

Запускает фоновые задачи в отдельном потоке:
  - Ежедневная метео-сводка в заданное пользователем время
  - Экстренный алерт при превышении Kp-порога
  - Проверка «барической пилы» каждые 6 ч
"""

import threading
import time
import logging
from datetime import datetime
from typing import Optional

import schedule
import pytz
import requests

import db
import weather as wx
import space_weather as sw
import analytics
import config

logger = logging.getLogger(__name__)

# Ссылка на объект бота (задается при вызове start())
_bot = None


# ─── Запуск ────────────────────────────────────────────────────────────────────

def start(bot) -> None:
    """
    Запускает фоновый поток с планировщиком задач.

    Args:
        bot: экземпляр telebot.TeleBot
    """
    global _bot
    _bot = bot

    # Экстренный Kp-алерт — каждые N часов
    schedule.every(config.KP_CHECK_INTERVAL_HOURS).hours.do(_job_kp_alerts)

    # Барическая пила — каждые N часов
    schedule.every(config.PRESSURE_CHECK_INTERVAL_HOURS).hours.do(_job_barometric_check)

    # Ежедневные сводки — каждую минуту сравниваем HH:MM с настройками пользователей
    schedule.every(1).minutes.do(_job_daily_digests)

    # Self-ping (каждые 4 минуты), чтобы Koyeb не усыплял Web-сервис
    if config.APP_URL:
        schedule.every(4).minutes.do(_job_self_ping)

    t = threading.Thread(target=_run_loop, daemon=True)
    t.start()
    logger.info("Планировщик запущен.")


def _run_loop() -> None:
    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            logger.error(f"Ошибка в планировщике: {e}")
        time.sleep(30)


# ─── Вспомогательные функции ──────────────────────────────────────────────────

def _is_quiet_hour(chat_id: int) -> bool:
    """Возвращает True, если сейчас «тихий час» пользователя."""
    cfg  = db.get_alert_config(chat_id)
    user = db.get_user(chat_id)
    if not cfg or not user:
        return False

    tz_str = user.get("timezone", "UTC")
    try:
        tz = pytz.timezone(tz_str)
    except Exception:
        tz = pytz.UTC

    local_hour = datetime.now(tz).hour
    start_h = cfg.get("quiet_hour_start")
    if start_h is None: start_h = 23
    end_h = cfg.get("quiet_hour_end")
    if end_h is None: end_h = 7

    if start_h > end_h:
        return local_hour >= start_h or local_hour < end_h
    return start_h <= local_hour < end_h


def _send_safe(chat_id: int, text: str, **kwargs) -> None:
    """Отправляет сообщение, подавляя исключения."""
    try:
        _bot.send_message(chat_id, text, parse_mode="HTML", **kwargs)
    except Exception as e:
        logger.warning(f"Не удалось отправить сообщение {chat_id}: {e}")


def _user_local_time_str(chat_id: int) -> Optional[str]:
    """Возвращает текущее локальное время пользователя в формате HH:MM."""
    user = db.get_user(chat_id)
    if not user:
        return None
    tz_str = user.get("timezone", "UTC")
    try:
        tz = pytz.timezone(tz_str)
        return datetime.now(tz).strftime("%H:%M")
    except Exception:
        return datetime.utcnow().strftime("%H:%M")


# ─── Задачи ────────────────────────────────────────────────────────────────────

def _job_kp_alerts() -> None:
    """Рассылает экстренные алерты при превышении Kp-порога."""
    kp_data = sw.get_kp_full()
    if not kp_data:
        return

    kp = kp_data["kp"]
    configs = db.get_all_alert_configs()

    for cfg in configs:
        chat_id   = cfg["chat_id"]
        threshold = cfg.get("kp_threshold") or config.KP_DEFAULT_THRESHOLD

        if kp < threshold:
            continue
        if _is_quiet_hour(chat_id):
            continue

        user    = db.get_user(chat_id)
        bp_type = user.get("blood_pressure_type", "norm") if user else "norm"
        advice  = sw.get_storm_advice(kp, bp_type)

        text = (
            f"🚨 <b>Магнитная буря!</b>\n"
            f"Kp = {kp} — {kp_data['emoji']} {kp_data['label']}\n\n"
            f"💬 {advice}"
        )
        _send_safe(chat_id, text)


def _job_barometric_check() -> None:
    """Проверяет резкий перепад давления для всех пользователей с городом."""
    users = db.get_all_users()

    for user in users:
        chat_id = user["chat_id"]
        city    = user.get("city")
        if not city:
            continue
        if _is_quiet_hour(chat_id):
            continue

        data = wx.get_weather_by_city(city)
        if not data or not data.get("pressure_mmhg"):
            continue

        saw = analytics.check_barometric_saw(chat_id, data["pressure_mmhg"])
        if saw and saw.get("alert"):
            text = (
                f"⚠️ <b>Барическая пила в {city}!</b>\n"
                f"За {saw['hours']} ч давление изменилось на <b>{saw['abs_delta']} мм</b> "
                f"({saw['direction']}).\n"
                f"Текущее давление: <b>{data['pressure_mmhg']} мм рт.ст.</b>\n\n"
                "Резкий перепад — частый триггер головных болей. Берегите себя 🌿"
            )
            _send_safe(chat_id, text)


def _job_daily_digests() -> None:
    """Рассылает ежедневные сводки пользователям в их заданное время."""
    configs  = db.get_all_alert_configs()

    for cfg in configs:
        chat_id    = cfg["chat_id"]
        alert_time = cfg.get("alert_time", "08:00")

        local_time = _user_local_time_str(chat_id)
        if local_time != alert_time:
            continue
        if _is_quiet_hour(chat_id):
            continue

        _send_daily_digest(chat_id)


def _send_daily_digest(chat_id: int) -> None:
    """Формирует и отправляет ежедневную сводку одному пользователю."""
    user = db.get_user(chat_id)
    if not user:
        return
    city    = user.get("city")
    bp_type = user.get("blood_pressure_type", "norm")

    kp_data = sw.get_kp_full()
    kp_str  = (
        f'{kp_data["emoji"]} {kp_data["label"]} (Kp = {kp_data["kp"]})'
        if kp_data else "—"
    )

    lines = ["🌅 <b>Ваша ежедневная метео-сводка</b>", ""]

    if city:
        data = wx.get_full_weather(city)
        if data:
            lines += [
                f"📍 <b>{city}</b>",
                f"🌡 Температура: <b>{data['temp']}°C</b>",
                f"⏱ Давление:   <b>{data['pressure_mmhg']} мм рт.ст.</b>",
                f"💧 Влажность:  <b>{data['humidity']}%</b>",
            ]
            if data.get("aqi_info"):
                lines.append(f"🍃 Воздух:     <b>{data['aqi_info']['aqi_label']}</b>")

    lines += ["", f"🌌 Геомагнетизм: <b>{kp_str}</b>"]

    if kp_data:
        advice = sw.get_storm_advice(kp_data["kp"], bp_type)
        lines += ["", f"💬 {advice}"]

    # Прогноз Kp на 24 ч
    max_kp = sw.get_max_kp_forecast_24h()
    if max_kp is not None and max_kp >= 4:
        fc_storm = sw.classify_storm(max_kp)
        lines += [
            "",
            f"⚡️ <b>Прогноз:</b> завтра ожидается {fc_storm['emoji']} {fc_storm['label']} "
            f"(max Kp = {max_kp}). Будьте осторожны!",
        ]

    _send_safe(chat_id, "\n".join(lines))


def _job_self_ping() -> None:
    """Пингует собственный веб-сервер, чтобы PaaS (Koyeb) не усыплял его из-за бездействия."""
    url = config.APP_URL
    if not url:
        return

    # Добавляем протокол, если он отсутствует
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    try:
        r = requests.get(url, timeout=5)
        logger.debug(f"Self-ping {url}: {r.status_code}")
    except Exception as e:
        logger.warning(f"Ошибка при self-ping: {e}")

