"""
analytics.py — Аналитический движок HeadCare Bot

Функции:
  - Анализ личных триггеров головных болей из дневника
  - Определение «барической пилы» (резкий перепад давления)
  - Генерация месячного текстового отчёта
"""

from datetime import datetime
from typing import Optional

import db
import config


# ─── Барическая пила ──────────────────────────────────────────────────────────

def check_barometric_saw(chat_id: int, current_pressure: float) -> Optional[dict]:
    """
    Проверяет, произошёл ли резкий перепад атмосферного давления.

    Сравнивает текущее давление с последним сохранённым в БД.
    Если прошло менее 26 ч и разница ≥ PRESSURE_DELTA_ALERT_MMHG → алерт.

    Args:
        chat_id:          ID пользователя
        current_pressure: текущее давление (мм рт. ст.)

    Returns:
        dict с ключами {delta, direction, alert} или None если данных нет.
    """
    alert_cfg = db.get_alert_config(chat_id)
    if not alert_cfg or not alert_cfg.get("last_pressure"):
        # Сохраняем первое значение и выходим
        db.update_last_pressure(chat_id, current_pressure)
        return None

    last_p  = float(alert_cfg["last_pressure"])
    last_ts = alert_cfg.get("last_pressure_ts")
    delta   = current_pressure - last_p

    # Проверяем, что запись свежая (≤ 26 ч)
    hours_passed = _hours_since(last_ts)
    if hours_passed is None or hours_passed > 26:
        db.update_last_pressure(chat_id, current_pressure)
        return None

    db.update_last_pressure(chat_id, current_pressure)

    abs_delta = abs(delta)
    direction = "рост" if delta > 0 else "падение"

    return {
        "delta":     round(delta, 1),
        "abs_delta": round(abs_delta, 1),
        "direction": direction,
        "hours":     round(hours_passed, 1),
        "alert":     abs_delta >= config.PRESSURE_DELTA_ALERT_MMHG,
    }


def _hours_since(iso_ts: str) -> Optional[float]:
    """Возвращает количество часов с момента iso_ts до сейчас."""
    try:
        dt = datetime.fromisoformat(iso_ts)
        delta = datetime.utcnow() - dt
        return delta.total_seconds() / 3600
    except Exception:
        return None


# ─── Анализ триггеров ─────────────────────────────────────────────────────────

def analyze_triggers(chat_id: int, month: Optional[str] = None) -> dict:
    """
    Анализирует дневник головных болей и выявляет личные триггеры.

    Args:
        chat_id: ID пользователя
        month:   'YYYY-MM' или None (все записи)

    Returns:
        dict с результатами анализа:
            count         — кол-во записей
            avg_pressure  — среднее давление в дни боли
            avg_kp        — средний Kp в дни боли
            avg_aqi       — средний AQI в дни боли
            low_pressure_share — доля дней с давлением < 748 мм
            high_kp_share      — доля дней с Kp ≥ 3
            triggers      — список строк-выводов
    """
    entries = db.get_diary_entries(chat_id, month)
    count   = len(entries)

    if count == 0:
        return {"count": 0, "triggers": ["Записей в дневнике пока нет."]}

    pressures = [e["pressure_mmhg"] for e in entries if e["pressure_mmhg"]]
    kp_vals   = [e["k_index"]       for e in entries if e["k_index"] is not None]
    aqi_vals  = [e["aqi"]           for e in entries if e["aqi"] is not None]

    avg_pressure = round(sum(pressures) / len(pressures), 1) if pressures else None
    avg_kp       = round(sum(kp_vals)  / len(kp_vals),   2) if kp_vals   else None
    avg_aqi      = round(sum(aqi_vals) / len(aqi_vals),  1) if aqi_vals  else None

    low_p_count = sum(1 for p in pressures if p < 748)
    high_kp_cnt = sum(1 for k in kp_vals   if k >= 3)

    low_pressure_share = round(low_p_count / len(pressures) * 100) if pressures else 0
    high_kp_share      = round(high_kp_cnt / len(kp_vals)   * 100) if kp_vals   else 0

    triggers = []

    if avg_pressure and avg_pressure < 750:
        triggers.append(
            f"📉 Боль часто совпадает с пониженным давлением (среднее {avg_pressure} мм рт. ст.)."
        )
    elif avg_pressure and avg_pressure > 760:
        triggers.append(
            f"📈 Боль часто совпадает с повышенным давлением (среднее {avg_pressure} мм рт. ст.)."
        )

    if low_pressure_share >= 60:
        triggers.append(
            f"⚠️ В {low_pressure_share}% случаев давление было ниже 748 мм — вероятный триггер."
        )

    if avg_kp is not None and avg_kp >= 3:
        triggers.append(
            f"🌌 Средний Kp в дни боли: {avg_kp} — магнитные бури вероятно влияют на вас."
        )

    if high_kp_share >= 50:
        triggers.append(
            f"🔴 В {high_kp_share}% случаев Kp ≥ 3 — геомагнитная активность значима для вас."
        )

    if avg_aqi and avg_aqi >= 3:
        triggers.append(
            f"💨 Среднее качество воздуха в дни боли: {avg_aqi}/5 — возможен аллергический фактор."
        )

    if not triggers:
        triggers.append("Паттерны пока не выявлены. Продолжайте вести дневник 🙂")

    return {
        "count":              count,
        "avg_pressure":       avg_pressure,
        "avg_kp":             avg_kp,
        "avg_aqi":            avg_aqi,
        "low_pressure_share": low_pressure_share,
        "high_kp_share":      high_kp_share,
        "triggers":           triggers,
    }


# ─── Месячный отчёт ───────────────────────────────────────────────────────────

def generate_monthly_report(chat_id: int) -> str:
    """
    Формирует текстовый месячный отчёт для отправки в Telegram.

    Returns:
        Готовая строка с отчётом (HTML-разметка).
    """
    current_month = datetime.utcnow().strftime("%Y-%m")
    month_display = datetime.utcnow().strftime("%B %Y")

    analysis = analyze_triggers(chat_id, month=current_month)
    count    = analysis["count"]

    if count == 0:
        return (
            f"📊 <b>Отчёт за {month_display}</b>\n\n"
            "В этом месяце записей о головной боли нет.\n"
            "Нажмите «🤕 Болит голова», когда потребуется."
        )

    lines = [
        f"📊 <b>Отчёт за {month_display}</b>",
        f"Всего эпизодов: <b>{count}</b>",
        "",
    ]

    if analysis.get("avg_pressure"):
        lines.append(f"🌡 Среднее давление в дни боли: <b>{analysis['avg_pressure']} мм</b>")
    if analysis.get("avg_kp") is not None:
        lines.append(f"🌌 Средний Kp-индекс:           <b>{analysis['avg_kp']}</b>")
    if analysis.get("avg_aqi"):
        lines.append(f"💨 Средний AQI:                 <b>{analysis['avg_aqi']}/5</b>")

    lines += ["", "🔍 <b>Выявленные триггеры:</b>"]
    for trigger in analysis["triggers"]:
        lines.append(f"  • {trigger}")

    lines += ["", "💡 Продолжайте вести дневник — точность анализа растёт с каждой записью."]

    return "\n".join(lines)
