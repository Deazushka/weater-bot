"""
bot.py — Точка входа HeadCare Bot (Telegram)

Обрабатывает все команды, inline-кнопки и сообщения пользователей.
Запускает фоновый планировщик уведомлений.
"""

import logging
import threading
import asyncio
import aiohttp
from aiohttp import web

import telebot
from telebot import types
from datetime import datetime
from typing import Optional
import pytz

import config
import db
import weather as wx
from weather import _aqi_label
import space_weather as sw
import analytics
import scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

config.validate_config()
db.init_db()

bot = telebot.TeleBot(config.BOT_TOKEN, parse_mode="HTML")

# ─── Вспомогательные функции ──────────────────────────────────────────────────

def _main_menu() -> types.ReplyKeyboardMarkup:
    """Возвращает главную клавиатуру бота."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("🌤 Погода"),
        types.KeyboardButton("🌌 Космос"),
        types.KeyboardButton("🤕 Болит голова"),
        types.KeyboardButton("📊 Отчёт"),
        types.KeyboardButton("⚙️ Настройки"),
        types.KeyboardButton("📍 Моя геолокация", request_location=True),
    )
    return kb


def _get_user_city(chat_id: int) -> Optional[str]:
    """Возвращает город пользователя или None."""
    user = db.get_user(chat_id)
    return user["city"] if user else None


def _forecast_line(label: str, fc: dict) -> str:
    """Компактная строка прогноза для одной временной точки."""
    return (
        f"{label}\n"
        f"  🌡 {fc['temp']}°C (ощущ. {fc['feels_like']}°C) · "
        f"💧{fc['humidity']}% · 🌬 {fc['wind_speed']} м/с · "
        f"⏱ {fc['pressure_mmhg']} мм"
    )


def _weather_summary(city: str, chat_id: Optional[int] = None) -> str:
    """Формирует HTML-сводку погоды + AQI + Kp + прогноз +3ч/+6ч."""
    data = wx.get_full_weather(city)
    if not data:
        return f"❌ Не удалось получить погоду для <b>{city}</b>. Проверьте название города."

    kp_data = sw.get_kp_full()
    
    # Персональный совет
    bp_type = "norm"
    if chat_id:
        user = db.get_user(chat_id)
        if user:
            bp_type = user.get("blood_pressure_type", "norm")
    advice = sw.get_storm_advice(kp_data["kp"] if kp_data else 0, bp_type)

    aqi_info = data.get("aqi_info")
    aqi_str  = aqi_info["aqi_label"] if aqi_info else "—"

    kp_str = (
        f'{kp_data["emoji"]} {kp_data["label"]} (Kp = {kp_data["kp"]})'
        if kp_data else "—"
    )

    lines = [
        f"📍 <b>Погода: {data['city']}</b>",
        f"☁️ {str(data.get('description', '')).capitalize()}",
        "",
        f"🌡 Температура: <b>{data['temp']}°C</b> (ощущается как {data['feels_like']}°C)",
        f"💧 Влажность: <b>{data['humidity']}%</b>",
        f"🌬 Ветер: <b>{data['wind_speed']} м/с</b>",
        f"⏱ Давление: <b>{data['pressure_mmhg']} мм рт.ст.</b>",
        "",
        f"🍃 Качество воздуха: <b>{aqi_str}</b>",
        f"🌌 Магнитный фон: <b>{kp_str}</b>",
    ]

    # Прогноз +3ч / +6ч
    forecast = wx.get_forecast_by_city(city)
    if forecast:
        lines.append("")
        if len(forecast) >= 1:
            lines.append(_forecast_line("🕐 <b>Через 3 часа:</b>", forecast[0]))
        if len(forecast) >= 2:
            lines.append(_forecast_line("🕕 <b>Через 6 часов:</b>", forecast[1]))

    lines += ["", f"💬 {advice}"]
    return "\n".join(lines)


# ─── /start ────────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def cmd_start(msg: types.Message) -> None:
    chat_id = msg.chat.id
    name    = msg.from_user.first_name or "друг"
    db.upsert_user(chat_id)
    db.save_alert_config(chat_id)
    bot.send_message(
        chat_id,
        f"👋 Привет, <b>{name}</b>!\n\n"
        "Я <b>HeadCare Bot</b> — твой личный метео-ассистент.\n"
        "Слежу за атмосферным давлением, качеством воздуха и магнитными бурями, "
        "чтобы предупреждать о рисках головной боли.\n\n"
        "Выбери действие ниже 👇",
        reply_markup=_main_menu(),
    )


# ─── /weather ─────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["weather"])
def cmd_weather(msg: types.Message) -> None:
    city = _get_user_city(msg.chat.id)
    if city:
        _send_weather(msg.chat.id, city)
    else:
        bot.send_message(msg.chat.id, "🌍 Введи название города:")
        bot.register_next_step_handler(msg, _step_set_city_and_weather)


def _step_set_city_and_weather(msg: types.Message) -> None:
    city = msg.text.strip()
    db.upsert_user(msg.chat.id, city=city)
    db.save_alert_config(msg.chat.id)
    _send_weather(msg.chat.id, city)


def _send_weather(chat_id: int, city: str) -> None:
    """Отправляет полную сводку (без ASCII-арта)."""
    summary = _weather_summary(city, chat_id)
    if summary.startswith("❌ Не удалось получить погоду"):
        bot.send_message(chat_id, summary, reply_markup=_main_menu())
        db.upsert_user(chat_id, city=None) # Сбрасываем неверный город
        bot.send_message(chat_id, "🌍 Пожалуйста, введи корректное название города:")
        bot.register_next_step_handler_by_chat_id(chat_id, _step_set_city_and_weather)
    else:
        bot.send_message(chat_id, summary, reply_markup=_main_menu())


# ─── /status ──────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["status"])
def cmd_status(msg: types.Message) -> None:
    city = _get_user_city(msg.chat.id)
    if not city:
        bot.send_message(
            msg.chat.id,
            "Сначала задай город через /profile или кнопку «🌤 Погода».",
            reply_markup=_main_menu(),
        )
        return
    summary = _weather_summary(city, msg.chat.id)
    bot.send_message(msg.chat.id, summary, reply_markup=_main_menu())


# ─── /profile ─────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["profile"])
def cmd_profile(msg: types.Message) -> None:
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("❤️ Гипертония",  callback_data="bp_hyper"),
        types.InlineKeyboardButton("💙 Гипотония",   callback_data="bp_hypo"),
        types.InlineKeyboardButton("💚 Нормотония",  callback_data="bp_norm"),
    )
    user = db.get_user(msg.chat.id)
    current = config.BLOOD_PRESSURE_TYPES.get(
        user.get("blood_pressure_type", "norm") if user else "norm", "нормотония"
    )
    bot.send_message(
        msg.chat.id,
        f"Текущий профиль: <b>{current}</b>\n\nВыбери тип артериального давления:",
        reply_markup=kb,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("bp_"))
def cb_blood_pressure(call: types.CallbackQuery) -> None:
    bp_map = {"bp_hyper": "hyper", "bp_hypo": "hypo", "bp_norm": "norm"}
    bp_type = bp_map.get(call.data, "norm")
    db.upsert_user(call.message.chat.id, blood_pressure_type=bp_type)
    label   = config.BLOOD_PRESSURE_TYPES[bp_type]
    bot.answer_callback_query(call.id, f"Профиль обновлён: {label}")
    bot.edit_message_text(
        f"✅ Профиль сохранён: <b>{label}</b>",
        call.message.chat.id,
        call.message.message_id,
    )


# ─── /alerts ──────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["alerts"])
def cmd_alerts(msg: types.Message) -> None:
    cfg = db.get_alert_config(msg.chat.id) or {}
    bot.send_message(
        msg.chat.id,
        f"⚙️ <b>Настройки уведомлений</b>\n\n"
        f"Kp-порог:     <b>{cfg.get('kp_threshold', 5.0)}</b>\n"
        f"Время сводки: <b>{cfg.get('alert_time', '08:00')}</b>\n"
        f"Тихий час:    <b>{cfg.get('quiet_hour_start', 23)}:00 – {cfg.get('quiet_hour_end', 7)}:00</b>\n\n"
        "Введи новый Kp-порог (число от 1 до 9, например: <code>5</code>)\n"
        "или отправь <code>skip</code> для пропуска:",
    )
    bot.register_next_step_handler(msg, _step_alert_kp)


def _step_alert_kp(msg: types.Message) -> None:
    kp = None
    if msg.text.strip().lower() != "skip":
        try:
            kp = float(msg.text.strip())
            if not (1 <= kp <= 9):
                raise ValueError
        except ValueError:
            bot.send_message(msg.chat.id, "❌ Неверное значение. Введи число от 1 до 9.")
            return

    db.save_alert_config(msg.chat.id, kp_threshold=kp)
    bot.send_message(
        msg.chat.id,
        "Введи желаемое время ежедневной сводки (HH:MM, например: <code>08:00</code>)\n"
        "или <code>skip</code>:",
    )
    bot.register_next_step_handler(msg, _step_alert_time)


def _step_alert_time(msg: types.Message) -> None:
    alert_time = None
    if msg.text.strip().lower() != "skip":
        try:
            datetime.strptime(msg.text.strip(), "%H:%M")
            alert_time = msg.text.strip()
        except ValueError:
            bot.send_message(msg.chat.id, "❌ Неверный формат. Используй HH:MM (например, 08:00).")
            return

    db.save_alert_config(msg.chat.id, alert_time=alert_time)
    bot.send_message(
        msg.chat.id,
        "✅ Настройки уведомлений сохранены!\n\n"
        "Хочешь также указать часовой пояс? Введи его (например: <code>Europe/Minsk</code>) "
        "или <code>skip</code>:",
    )
    bot.register_next_step_handler(msg, _step_timezone)


def _step_timezone(msg: types.Message) -> None:
    tz_str = None
    if msg.text.strip().lower() != "skip":
        tz_str = msg.text.strip()
        try:
            pytz.timezone(tz_str)
        except pytz.exceptions.UnknownTimeZoneError:
            bot.send_message(
                msg.chat.id,
                f"❌ Часовой пояс «{tz_str}» не найден.\n"
                "Примеры: <code>Europe/Minsk</code>, <code>Asia/Yekaterinburg</code>",
            )
            return

    if tz_str:
        db.upsert_user(msg.chat.id, timezone=tz_str)

    bot.send_message(
        msg.chat.id,
        "✅ Все настройки сохранены! Буду присылать сводку вовремя.",
        reply_markup=_main_menu(),
    )


# ─── /report ──────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["report"])
def cmd_report(msg: types.Message) -> None:
    report = analytics.generate_monthly_report(msg.chat.id)
    bot.send_message(msg.chat.id, report, reply_markup=_main_menu())


# ─── Геолокация ───────────────────────────────────────────────────────────────

@bot.message_handler(content_types=["location"])
def handle_location(msg: types.Message) -> None:
    lat = msg.location.latitude
    lon = msg.location.longitude

    # Определяем город по координатам
    city_name = wx.reverse_geocode(lat, lon) or f"{lat:.4f},{lon:.4f}"
    
    # Сбрасываем старый город и сохраняем новый или координаты
    db.upsert_user(msg.chat.id, city=city_name)

    data = wx.get_weather_by_coords(lat, lon)
    if not data:
        bot.send_message(msg.chat.id, "❌ Не удалось получить погоду для этой локации.")
        return

    # Отправляем сводку именно для этой локации
    data['city'] = city_name # Переопределяем имя для корректного отображения
    
    # Получаем остальные данные (AQI, KP)
    aqi_info = wx.get_aqi(lat, lon)
    aqi_str  = aqi_info["aqi_label"] if aqi_info else "—"

    kp_data = sw.get_kp_full()
    
    bp_type = "norm"
    user = db.get_user(msg.chat.id)
    if user:
        bp_type = user.get("blood_pressure_type", "norm")
    
    advice = sw.get_storm_advice(kp_data["kp"] if kp_data else 0, bp_type)
    kp_str = (
        f'{kp_data["emoji"]} {kp_data["label"]} (Kp = {kp_data["kp"]})'
        if kp_data else "—"
    )

    lines = [
        f"📍 <b>Погода: {data['city']}</b>",
        f"☁️ {str(data.get('description', '')).capitalize()}",
        "",
        f"🌡 Температура: <b>{data['temp']}°C</b> (ощущается как {data['feels_like']}°C)",
        f"💧 Влажность: <b>{data['humidity']}%</b>",
        f"🌬 Ветер: <b>{data['wind_speed']} м/с</b>",
        f"⏱ Давление: <b>{data['pressure_mmhg']} мм рт.ст.</b>",
        "",
        f"🍃 Качество воздуха: <b>{aqi_str}</b>",
        f"🌌 Магнитный фон: <b>{kp_str}</b>",
    ]

    # Прогноз +3ч / +6ч
    forecast = wx.get_forecast_by_coords(lat, lon)
    if forecast:
        lines.append("")
        if len(forecast) >= 1:
            lines.append(_forecast_line("🕐 <b>Через 3 часа:</b>", forecast[0]))
        if len(forecast) >= 2:
            lines.append(_forecast_line("🕕 <b>Через 6 часов:</b>", forecast[1]))

    lines += ["", f"💬 {advice}"]
    summary = "\n".join(lines)
    
    bot.send_message(msg.chat.id, summary, reply_markup=_main_menu())


# ─── «🤕 Болит голова» ────────────────────────────────────────────────────────

def _handle_headache(msg: types.Message) -> None:
    chat_id = msg.chat.id
    city    = _get_user_city(chat_id)

    pressure_mmhg: Optional[float] = None
    k_index:       Optional[float] = None
    aqi:           Optional[int]   = None
    temperature:   Optional[float] = None
    humidity:      Optional[int]   = None

    if city:
        data = wx.get_full_weather(city)
        if data:
            pressure_mmhg = data.get("pressure_mmhg")
            temperature   = data.get("temp")
            humidity      = data.get("humidity")
            if data.get("aqi_info"):
                aqi = data["aqi_info"].get("aqi")

        kp_data = sw.get_kp_full()
        if kp_data:
            k_index = kp_data["kp"]

        # Проверяем барическую пилу
        if pressure_mmhg:
            saw = analytics.check_barometric_saw(chat_id, pressure_mmhg)
            if saw and saw.get("alert"):
                bot.send_message(
                    chat_id,
                    f"⚠️ <b>Барическая пила!</b>\n"
                    f"За последние {saw['hours']} ч давление изменилось на "
                    f"<b>{saw['abs_delta']} мм</b> ({saw['direction']}).\n"
                    "Резкий перепад — частый триггер головных болей.",
                )

    db.log_headache(chat_id, pressure_mmhg, k_index, aqi, temperature, humidity)

    entry_count = db.get_diary_entry_count(chat_id)

    lines = ["🌿 <b>Запись сохранена. Берегите себя!</b>\n", "Условия в момент боли:"]
    if pressure_mmhg:
        lines.append(f"  ⏱ Давление:   <b>{pressure_mmhg} мм рт.ст.</b>")
    if temperature is not None:
        lines.append(f"  🌡 Температура: <b>{temperature}°C</b>")
    if humidity is not None:
        lines.append(f"  💧 Влажность:   <b>{humidity}%</b>")
    if k_index is not None:
        storm = sw.classify_storm(k_index)
        lines.append(f"  🌌 Kp-индекс:  <b>{k_index} ({storm['label']})</b>")
    if aqi is not None:
        lines.append(f"  🍃 Воздух:     <b>{_aqi_label(aqi)}</b>")

    lines.append(f"\n📖 Всего записей в дневнике: <b>{entry_count}</b>")
    if entry_count >= 5:
        lines.append("Нажми «📊 Отчёт», чтобы увидеть личные триггеры.")

    bot.send_message(chat_id, "\n".join(lines), reply_markup=_main_menu())


# ─── Текстовые кнопки главного меню ──────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text in [
    "🌤 Погода", "🌌 Космос", "🤕 Болит голова", "📊 Отчёт", "⚙️ Настройки"
])
def handle_menu_buttons(msg: types.Message) -> None:
    text    = msg.text
    chat_id = msg.chat.id

    if text == "🌤 Погода":
        city = _get_user_city(chat_id)
        if city:
            _send_weather(chat_id, city)
        else:
            bot.send_message(chat_id, "🌍 Введи название города:")
            bot.register_next_step_handler(msg, _step_set_city_and_weather)

    elif text == "🌌 Космос":
        kp_data = sw.get_kp_full()
        user    = db.get_user(chat_id)
        bp_type = user.get("blood_pressure_type", "norm") if user else "norm"

        if not kp_data:
            bot.send_message(chat_id, "❌ Не удалось получить данные NOAA. Попробуй позже.")
            return

        forecast_max = sw.get_max_kp_forecast_24h()
        advice       = sw.get_storm_advice(kp_data["kp"], bp_type)

        lines = [
            "🌌 <b>Космическая погода</b>",
            f"Текущий Kp-индекс: {kp_data['emoji']} <b>{kp_data['kp']} — {kp_data['label']}</b>",
        ]
        if forecast_max is not None:
            fc_storm = sw.classify_storm(forecast_max)
            lines.append(
                f"Прогноз max Kp (24 ч): {fc_storm['emoji']} <b>{forecast_max} — {fc_storm['label']}</b>"
            )
        lines += ["", f"💬 {advice}"]
        bot.send_message(chat_id, "\n".join(lines), reply_markup=_main_menu())

    elif text == "🤕 Болит голова":
        _handle_headache(msg)

    elif text == "📊 Отчёт":
        report = analytics.generate_monthly_report(chat_id)
        bot.send_message(chat_id, report, reply_markup=_main_menu())

    elif text == "⚙️ Настройки":
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("👤 Профиль",      callback_data="settings_profile"),
            types.InlineKeyboardButton("🔔 Уведомления",  callback_data="settings_alerts"),
        )
        kb.row(
            types.InlineKeyboardButton("🔄 Сменить город", callback_data="settings_city"),
        )
        bot.send_message(chat_id, "⚙️ <b>Настройки</b>\nВыбери раздел:", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("settings_"))
def cb_settings(call: types.CallbackQuery) -> None:
    if call.data == "settings_profile":
        bot.answer_callback_query(call.id)
        cmd_profile(call.message)
    elif call.data == "settings_alerts":
        bot.answer_callback_query(call.id)
        cmd_alerts(call.message)
    elif call.data == "settings_city":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "🌍 Введи новое название города:")
        bot.register_next_step_handler(call.message, _step_set_city_and_weather)


# ─── Healthcheck & Self-ping (aiohttp) ────────────────────────────────────────

async def health_handler(request):
    """Хэндлер для проверки живости сервиса."""
    return web.Response(text="OK")


async def run_http_server(port: int):
    """Минимальный HTTP-сервер для Koyeb / Render."""
    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Health-check HTTP сервер запущен на порту {port}")


async def ping_itself():
    """Фоновая задача для предотвращения засыпания (self-ping)."""
    url = config.APP_URL
    if not url:
        logger.info("APP_URL не задан. Self-ping отключен.")
        return

    # Добавляем протокол, если он отсутствует
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    logger.info(f"Self-ping включен для URL: {url}")
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as response:
                    logger.info(f"Self-ping {url}: {response.status}")
        except Exception as e:
            logger.error(f"Ошибка при self-ping: {e}")
        await asyncio.sleep(5 * 60)  # Каждые 5 минут


def _start_async_background_tasks(port: int) -> None:
    """Запускает HTTP-сервер и self-ping в отдельном потоке (asyncio)."""
    def run_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(asyncio.gather(
            run_http_server(port),
            ping_itself()
        ))
        loop.run_forever()

    t = threading.Thread(target=run_loop, daemon=True)
    t.start()


# ─── Запуск ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8080))
    _start_async_background_tasks(port)
    logger.info("✅ HeadCare Bot запущен...")
    scheduler.start(bot)
    bot.infinity_polling(timeout=30, long_polling_timeout=30)
