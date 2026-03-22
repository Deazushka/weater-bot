"""
db.py — Слой базы данных для HeadCare Bot (SQLite)

Таблицы:
  - users        : профиль пользователя
  - alerts       : настройки уведомлений
  - health_diary : журнал головных болей с метеоданными
"""

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

import config

# Блокировка для потокобезопасности (scheduler + bot работают в разных потоках)
_lock = threading.Lock()


# ─── Инициализация ─────────────────────────────────────────────────────────────

def init_db() -> None:
    """Создаёт таблицы, если их ещё нет."""
    with _connect() as conn:
        c = conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                chat_id             INTEGER PRIMARY KEY,
                city                TEXT,
                timezone            TEXT DEFAULT 'UTC',
                blood_pressure_type TEXT DEFAULT 'norm'
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                chat_id           INTEGER PRIMARY KEY,
                kp_threshold      REAL    DEFAULT 5.0,
                alert_time        TEXT    DEFAULT '08:00',
                quiet_hour_start  INTEGER DEFAULT 23,
                quiet_hour_end    INTEGER DEFAULT 7,
                last_pressure     REAL,
                last_pressure_ts  TEXT,
                FOREIGN KEY (chat_id) REFERENCES users(chat_id)
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS health_diary (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id      INTEGER NOT NULL,
                timestamp    TEXT    NOT NULL,
                pressure_mmhg REAL,
                k_index      REAL,
                aqi          INTEGER,
                temperature  REAL,
                humidity     INTEGER,
                FOREIGN KEY (chat_id) REFERENCES users(chat_id)
            )
        """)

        conn.commit()


# ─── Контекстный менеджер соединения ──────────────────────────────────────────

@contextmanager
def _connect():
    with _lock:
        conn = sqlite3.connect(config.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()


# ─── CRUD: users ───────────────────────────────────────────────────────────────

def upsert_user(
    chat_id: int,
    city: Optional[str] = None,
    timezone: Optional[str] = None,
    blood_pressure_type: Optional[str] = None,
) -> None:
    """Создаёт или обновляет профиль пользователя."""
    with _connect() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO users (chat_id, city, timezone, blood_pressure_type)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                city                = COALESCE(excluded.city, users.city),
                timezone            = COALESCE(excluded.timezone, users.timezone),
                blood_pressure_type = COALESCE(excluded.blood_pressure_type, users.blood_pressure_type)
        """, (chat_id, city, timezone, blood_pressure_type))
        conn.commit()


def get_user(chat_id: int) -> Optional[dict]:
    """Возвращает профиль пользователя или None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return dict(row) if row else None


def get_all_users() -> list[dict]:
    """Возвращает всех зарегистрированных пользователей."""
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM users").fetchall()
        return [dict(r) for r in rows]


# ─── CRUD: alerts ──────────────────────────────────────────────────────────────

def save_alert_config(
    chat_id: int,
    kp_threshold: Optional[float] = None,
    alert_time: Optional[str] = None,
    quiet_hour_start: Optional[int] = None,
    quiet_hour_end: Optional[int] = None,
) -> None:
    """Создаёт или обновляет настройки уведомлений пользователя."""
    with _connect() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO alerts (chat_id, kp_threshold, alert_time, quiet_hour_start, quiet_hour_end)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                kp_threshold      = COALESCE(excluded.kp_threshold, alerts.kp_threshold),
                alert_time        = COALESCE(excluded.alert_time, alerts.alert_time),
                quiet_hour_start  = COALESCE(excluded.quiet_hour_start, alerts.quiet_hour_start),
                quiet_hour_end    = COALESCE(excluded.quiet_hour_end, alerts.quiet_hour_end)
        """, (chat_id, kp_threshold, alert_time, quiet_hour_start, quiet_hour_end))
        conn.commit()


def get_alert_config(chat_id: int) -> Optional[dict]:
    """Возвращает настройки уведомлений или None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM alerts WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return dict(row) if row else None


def get_all_alert_configs() -> list[dict]:
    """Возвращает все настройки уведомлений (для планировщика)."""
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM alerts").fetchall()
        return [dict(r) for r in rows]


def update_last_pressure(chat_id: int, pressure_mmhg: float) -> None:
    """Сохраняет последнее значение давления для анализа «барической пилы»."""
    ts = datetime.utcnow().isoformat()
    with _connect() as conn:
        conn.execute("""
            UPDATE alerts
            SET last_pressure = ?, last_pressure_ts = ?
            WHERE chat_id = ?
        """, (pressure_mmhg, ts, chat_id))
        conn.commit()


# ─── CRUD: health_diary ────────────────────────────────────────────────────────

def log_headache(
    chat_id: int,
    pressure_mmhg: Optional[float] = None,
    k_index: Optional[float] = None,
    aqi: Optional[int] = None,
    temperature: Optional[float] = None,
    humidity: Optional[int] = None,
) -> None:
    """Записывает эпизод головной боли с текущими метеоусловиями."""
    ts = datetime.utcnow().isoformat()
    with _connect() as conn:
        conn.execute("""
            INSERT INTO health_diary
                (chat_id, timestamp, pressure_mmhg, k_index, aqi, temperature, humidity)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (chat_id, ts, pressure_mmhg, k_index, aqi, temperature, humidity))
        conn.commit()


def get_diary_entries(chat_id: int, month: Optional[str] = None) -> list[dict]:
    """
    Возвращает записи дневника.

    Args:
        chat_id: ID чата пользователя
        month:   строка вида 'YYYY-MM', если None — все записи
    """
    with _connect() as conn:
        if month:
            rows = conn.execute("""
                SELECT * FROM health_diary
                WHERE chat_id = ? AND timestamp LIKE ?
                ORDER BY timestamp DESC
            """, (chat_id, f"{month}%")).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM health_diary
                WHERE chat_id = ?
                ORDER BY timestamp DESC
            """, (chat_id,)).fetchall()
        return [dict(r) for r in rows]


def get_diary_entry_count(chat_id: int) -> int:
    """Возвращает общее количество записей в дневнике пользователя."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM health_diary WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return row["cnt"] if row else 0
