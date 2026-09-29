"""Версионированная схема базы.

Миграции — список SQL-скриптов; применённая версия хранится в `PRAGMA
user_version`. Никаких сторонних миграторов: схема маленькая, а лишняя
зависимость в проекте, который разворачивается на одном VDS, дороже.
"""

from __future__ import annotations

import aiosqlite

# ВАЖНО: существующие миграции править нельзя — только добавлять новые в конец.
MIGRATIONS: list[str] = [
    # 1 — исходная схема.
    """
    CREATE TABLE users (
        telegram_id        INTEGER PRIMARY KEY,
        enabled            INTEGER NOT NULL DEFAULT 0,
        display_name       TEXT    NOT NULL DEFAULT '',
        school_url         TEXT    NOT NULL DEFAULT '',
        school_name        TEXT    NOT NULL DEFAULT '',
        login_type         TEXT    NOT NULL DEFAULT 'password',
        login              TEXT    NOT NULL DEFAULT '',
        password           TEXT    NOT NULL DEFAULT '',
        student_name       TEXT    NOT NULL DEFAULT '',
        selected_student_id INTEGER,
        check_interval     INTEGER NOT NULL DEFAULT 300,
        notify_grades      INTEGER NOT NULL DEFAULT 1,
        notify_changes     INTEGER NOT NULL DEFAULT 1,
        notify_deletes     INTEGER NOT NULL DEFAULT 1,
        notify_homework    INTEGER NOT NULL DEFAULT 1,
        notify_mail        INTEGER NOT NULL DEFAULT 0,
        weekly_summary     INTEGER NOT NULL DEFAULT 0,
        quiet_start        TEXT,
        quiet_end          TEXT,
        created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at         TEXT    NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE students (
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        student_id  INTEGER NOT NULL,
        name        TEXT    NOT NULL,
        position    INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (telegram_id, student_id)
    );

    -- Фильтры вынесены в отдельную таблицу: в JSON это был список внутри
    -- словаря внутри словаря, который каждый обработчик читал по-своему.
    CREATE TABLE filters (
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        kind        TEXT    NOT NULL CHECK (kind IN ('exclude_title', 'include_subject')),
        value       TEXT    NOT NULL,
        PRIMARY KEY (telegram_id, kind, value)
    );

    -- Состояние слежения за оценками. Раньше это был sent_grades.json на всех
    -- пользователей разом плюс два файла на каждого.
    CREATE TABLE tracked_marks (
        telegram_id     INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        identity        TEXT    NOT NULL,
        payload         TEXT    NOT NULL,
        missing_streak  INTEGER NOT NULL DEFAULT 0,
        updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (telegram_id, identity)
    );

    CREATE TABLE tracked_homework (
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        identity    TEXT    NOT NULL,
        seen_at     TEXT    NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (telegram_id, identity)
    );

    CREATE TABLE seen_mail (
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        message_id  INTEGER NOT NULL,
        seen_at     TEXT    NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (telegram_id, message_id)
    );

    -- Сессия «Сетевого города» одна на пользователя, в базе, а не в файле
    -- session_<id>.json, который писали и бот, и веб одновременно.
    CREATE TABLE netschool_sessions (
        telegram_id INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
        payload     TEXT NOT NULL,
        updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE miniapp_tokens (
        token       TEXT    PRIMARY KEY,
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
        revoked_at  TEXT
    );
    CREATE INDEX idx_miniapp_tokens_user ON miniapp_tokens(telegram_id);

    -- Одноразовые коды подтверждения входа в мини-приложение.
    CREATE TABLE login_codes (
        code        TEXT    PRIMARY KEY,
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        expires_at  TEXT    NOT NULL,
        used_at     TEXT
    );

    CREATE TABLE push_subscriptions (
        endpoint    TEXT    PRIMARY KEY,
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        p256dh      TEXT    NOT NULL,
        auth        TEXT    NOT NULL,
        created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
        failures    INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_push_user ON push_subscriptions(telegram_id);

    -- Кэш ответов «Сетевого города» для мини-приложения, чтобы оно
    -- открывалось мгновенно и работало офлайн.
    CREATE TABLE cache_entries (
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        section     TEXT    NOT NULL,
        payload     TEXT    NOT NULL,
        updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (telegram_id, section)
    );
    """,
    # 2 — тихая пересборка состояния после переноса из старых JSON-файлов.
    #
    # Старый проект хранил ключ оценки как поля, склеенные через «_», и без
    # номера урока. Новый ключ считается иначе, поэтому перенесённые записи
    # не совпадут с тем, что вернёт сервер, и первая же проверка объявила бы
    # новым весь журнал за год. Флаг заставляет первую проверку пройти молча
    # и пересобрать состояние по текущим правилам.
    """
    ALTER TABLE users ADD COLUMN baseline_pending INTEGER NOT NULL DEFAULT 0;
    """,
    # 3 — время первого обнаружения новых домашних заданий. Старым заданиям
    # оставляем NULL: их фактическое появление восстановить нельзя.
    """
    ALTER TABLE tracked_homework ADD COLUMN first_seen_at TEXT;
    """,
    # 4 — выбор API: веб (по умолчанию) или мобильное, команда /changeapi.
    """
    ALTER TABLE users ADD COLUMN api_mode TEXT NOT NULL DEFAULT 'web';
    """,
    # 5 — уведомления о пропусках (мобильный режим). По умолчанию выключены;
    # tracked_absences хранит уже виденные отметки, чтобы не слать повторно.
    """
    ALTER TABLE users ADD COLUMN notify_absences INTEGER NOT NULL DEFAULT 0;

    CREATE TABLE tracked_absences (
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        identity    TEXT    NOT NULL,
        seen_at     TEXT    NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (telegram_id, identity)
    );
    """,
]


async def migrate(db: aiosqlite.Connection) -> int:
    """Применить недостающие миграции. Возвращает итоговую версию схемы."""
    cursor = await db.execute("PRAGMA user_version")
    row = await cursor.fetchone()
    current = int(row[0]) if row else 0

    if current > len(MIGRATIONS):
        raise RuntimeError(
            f"База версии {current} новее, чем знает код ({len(MIGRATIONS)}). "
            "Обновите приложение — откат схемы не поддерживается."
        )

    for version in range(current, len(MIGRATIONS)):
        await db.executescript(MIGRATIONS[version])
        # PRAGMA не принимает параметры, но значение здесь — индекс списка.
        await db.execute(f"PRAGMA user_version = {version + 1}")
        await db.commit()

    return len(MIGRATIONS)
