"""
scheduler.py — Фоновый планировщик HeadCare Bot

Запускает фоновые задачи в отдельном потоке:
  - Ежедневная метео-сводка в заданное пользователем время
  - Экстренный алерт при превышении Kp-порога
  - Проверка «барической пилы» каждые 6 ч
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

import pytz

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
    Инициализирует планировщик. В асинхронной версии задачи запускаются в main().

    Args:
        bot: экземпляр telebot.async_telebot.AsyncTeleBot
    """
    global _bot
    _bot = bot
    logger.info("Планировщик инициализирован.")


async def run_scheduler():
    """Запускает бесконечные циклы для фоновых задач."""
    logger.info("Циклы планировщика запущены.")
    await asyncio.gather(
        _kp_alerts_loop(),
        _barometric_check_loop(),
        _daily_digests_loop(),
    )


async def _kp_alerts_loop():
    while True:
        try:
            await _job_kp_alerts()
        except Exception as e:
            logger.error(f"Ошибка в _kp_alerts_loop: {e}")
        await asyncio.sleep(config.KP_CHECK_INTERVAL_HOURS * 3600)


async def _barometric_check_loop():
    while True:
        try:
            await _job_barometric_check()
        except Exception as e:
            logger.error(f"Ошибка в _barometric_check_loop: {e}")
        await asyncio.sleep(config.PRESSURE_CHECK_INTERVAL_HOURS * 3600)


async def _daily_digests_loop():
    while True:
        try:
            await _job_daily_digests()
        except Exception as e:
            logger.error(f"Ошибка в _daily_digests_loop: {e}")
        # Проверяем каждую минуту
        await asyncio.sleep(60)


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
    start_h    = cfg.get("quiet_hour_start", 23)
    end_h      = cfg.get("quiet_hour_end", 7)

    if start_h > end_h:
        return local_hour >= start_h or local_hour < end_h
    return start_h <= local_hour < end_h


async def _send_safe(chat_id: int, text: str, **kwargs) -> None:
    """Отправляет сообщение, подавляя исключения."""
    if _bot is None:
        logger.error("Попытка отправить сообщение до инициализации _bot в планировщике.")
        return
    try:
        await _bot.send_message(chat_id, text, parse_mode="HTML", **kwargs)
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

async def _job_kp_alerts() -> None:
    """Рассылает экстренные алерты при превышении Kp-порога."""
    kp_data = await sw.get_kp_full()
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
        await _send_safe(chat_id, text)


async def _job_barometric_check() -> None:
    """Проверяет резкий перепад давления для всех пользователей с городом."""
    users = db.get_all_users()

    for user in users:
        chat_id = user["chat_id"]
        city    = user.get("city")
        if not city:
            continue
        if _is_quiet_hour(chat_id):
            continue

        data = await wx.get_weather_by_city(city)
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
            await _send_safe(chat_id, text)


async def _job_daily_digests() -> None:
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

        await _send_daily_digest(chat_id)


async def _send_daily_digest(chat_id: int) -> None:
    """Формирует и отправляет ежедневную сводку одному пользователю."""
    user = db.get_user(chat_id)
    if not user:
        return
    city    = user.get("city")
    bp_type = user.get("blood_pressure_type", "norm")

    kp_data = await sw.get_kp_full()
    kp_str  = (
        f'{kp_data["emoji"]} {kp_data["label"]} (Kp = {kp_data["kp"]})'
        if kp_data else "—"
    )

    lines = ["🌅 <b>Ваша ежедневная метео-сводка</b>", ""]

    if city:
        data = await wx.get_full_weather(city)
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
    max_kp = await sw.get_max_kp_forecast_24h()
    if max_kp is not None:
        max_kp_val = float(max_kp)
        if max_kp_val >= 4:
            fc_storm = sw.classify_storm(max_kp_val)
            lines += [
                "",
                f"⚡️ <b>Прогноз:</b> завтра ожидается {fc_storm['emoji']} {fc_storm['label']} "
                f"(max Kp = {max_kp_val}). Будьте осторожны!",
            ]

    await _send_safe(chat_id, "\n".join(lines))


# Удалено: _job_self_ping (перенесено в bot.py)

