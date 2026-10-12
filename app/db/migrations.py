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
    # 6 — когда пользователю сказали, что мобильный токен скоро истечёт.
    # NULL — не предупреждали (или токен с тех пор продлён/выдан заново).
    """
    ALTER TABLE users ADD COLUMN token_warned_at TEXT;
    """,
    # 7 — настройки мини-приложения (тема, цвет шапки, веса калькулятора),
    # общие для Telegram и сайта. JSON: набор ключей меняется чаще схемы.
    """
    CREATE TABLE web_prefs (
        telegram_id INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
        payload     TEXT    NOT NULL DEFAULT '{}',
        updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    );
    """,
    # 8 — надёжная доставка и история.
    #
    # outbox: уведомления, записанные той же транзакцией, что и состояние
    # слежения. Раньше состояние сохранялось до отправки, и сбой Telegram
    # или тихие часы съедали сообщение навсегда.
    # mark_history: всё, что случалось с оценками, — даже если уведомление
    # выключено или не дошло.
    # watch_status: когда проверка последний раз прошла и чем закончилась.
    """
    CREATE TABLE outbox (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        kind        TEXT    NOT NULL,
        payload     TEXT    NOT NULL,
        created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
        attempts    INTEGER NOT NULL DEFAULT 0,
        last_error  TEXT
    );
    CREATE INDEX idx_outbox_user ON outbox(telegram_id, id);

    CREATE TABLE mark_history (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        kind        TEXT    NOT NULL,
        payload     TEXT    NOT NULL,
        old_mark    TEXT    NOT NULL DEFAULT '',
        new_mark    TEXT    NOT NULL DEFAULT '',
        created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX idx_mark_history_user ON mark_history(telegram_id, id);

    CREATE TABLE watch_status (
        telegram_id   INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
        last_ok_at    TEXT,
        last_error_at TEXT,
        last_error    TEXT,
        next_check_at TEXT
    );
    """,
    # 9 — часовой пояс, вечерняя сводка и отметки о разосланных сводках
    # (`period` — неделя или день, за который сводка уже ушла).
    """
    ALTER TABLE users ADD COLUMN timezone TEXT NOT NULL DEFAULT 'Europe/Moscow';
    ALTER TABLE users ADD COLUMN evening_summary INTEGER NOT NULL DEFAULT 0;

    CREATE TABLE digests_sent (
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        kind        TEXT    NOT NULL,
        period      TEXT    NOT NULL,
        PRIMARY KEY (telegram_id, kind)
    );
    """,
    # 10 — отметки «задание выполнено», общие для бота и приложения.
    # `homework_key` — короткий отпечаток HomeworkRecord.identity.
    """
    CREATE TABLE homework_done (
        telegram_id  INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        homework_key TEXT    NOT NULL,
        done_at      TEXT    NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (telegram_id, homework_key)
    );
    """,
    # 11 — уведомления об изменениях расписания (по умолчанию выключены).
    # tracked_schedule — последний виденный снимок уроков каждого дня.
    """
    ALTER TABLE users ADD COLUMN notify_schedule INTEGER NOT NULL DEFAULT 0;

    CREATE TABLE tracked_schedule (
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        day         TEXT    NOT NULL,
        payload     TEXT    NOT NULL,
        PRIMARY KEY (telegram_id, day)
    );
    """,
    # 12 — отдельные отзываемые пароли CalDAV, только хэш случайного секрета.
    """
    CREATE TABLE caldav_accounts (
        telegram_id INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        last_sync_at TEXT,
        last_error TEXT
    );
    """,
    # 13 — явный выбор сохранения логина и пароля. Старые записи сохраняем.
    """
    ALTER TABLE users ADD COLUMN remember_credentials INTEGER NOT NULL DEFAULT 1;
    CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    INSERT INTO app_metadata (key, value)
        SELECT 'legacy_import_completed', '1' WHERE EXISTS (SELECT 1 FROM users);
    """,
    # 14 — уведомления можно получать только push'ем, без Telegram.
    """
    ALTER TABLE users ADD COLUMN notify_telegram INTEGER NOT NULL DEFAULT 1;
    """,
    # 15 — независимая базовая сверка почты и блокировка веб-приложения.
    """
    CREATE TABLE mail_baselines (
        telegram_id INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE
    );
    CREATE TABLE app_locks (
        telegram_id INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
        salt BLOB NOT NULL, password_hash BLOB NOT NULL,
        failures INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0
    );
    CREATE TABLE app_unlocks (
        token_hash TEXT PRIMARY KEY,
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        expires_at REAL NOT NULL
    );
    CREATE TABLE app_biometrics (
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        device_id TEXT NOT NULL, token_hash TEXT NOT NULL,
        PRIMARY KEY (telegram_id, device_id)
    );
    CREATE TABLE app_passkeys (
        credential_id BLOB PRIMARY KEY,
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        public_key BLOB NOT NULL, sign_count INTEGER NOT NULL, rp_id TEXT NOT NULL
    );
    CREATE TABLE app_lock_challenges (
        id TEXT PRIMARY KEY,
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        kind TEXT NOT NULL, challenge BLOB NOT NULL, origin TEXT NOT NULL,
        rp_id TEXT NOT NULL, expires_at REAL NOT NULL
    );
    """,
    # 16 — новые блокировки используют четырёхзначный PIN; старые пароли
    # сохраняются до подтверждённой пользователем замены, без потери доступа.
    """
    ALTER TABLE app_locks ADD COLUMN is_pin INTEGER NOT NULL DEFAULT 0;
    """,
    # 17 — дополнительная блокировка Telegram настраивается отдельно от веб-входа.
    """
    ALTER TABLE app_locks ADD COLUMN require_telegram INTEGER NOT NULL DEFAULT 1;
    """,
    # 18 — школьные чаты. Сообщения не удаляются вместе с пользователем
    # (нет внешнего ключа): переписку нужно хранить для разбора жалоб.
    """
    CREATE TABLE chat_profiles (
        telegram_id INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
        netschool_user_id INTEGER,
        school_key TEXT NOT NULL,
        school_name TEXT NOT NULL DEFAULT '',
        class_name TEXT NOT NULL,
        grade INTEGER NOT NULL,
        first_name TEXT NOT NULL,
        consented_at REAL NOT NULL,
        verified_at REAL NOT NULL,
        as_admin INTEGER NOT NULL DEFAULT 1,
        muted_until REAL NOT NULL DEFAULT 0,
        banned INTEGER NOT NULL DEFAULT 0,
        last_sent_at REAL NOT NULL DEFAULT 0
    );
    CREATE INDEX chat_profiles_school ON chat_profiles(school_key);
    CREATE TABLE chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        room TEXT NOT NULL,
        author_id INTEGER NOT NULL,
        author_name TEXT NOT NULL,
        author_grade INTEGER,
        is_admin INTEGER NOT NULL DEFAULT 0,
        text TEXT NOT NULL,
        reply_to INTEGER,
        kind TEXT NOT NULL DEFAULT 'text',
        payload TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        deleted_at REAL,
        deleted_by INTEGER,
        hidden INTEGER NOT NULL DEFAULT 0,
        pinned INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX chat_messages_room ON chat_messages(room, id);
    CREATE INDEX chat_messages_updated ON chat_messages(room, updated_at);
    CREATE TABLE chat_reactions (
        message_id INTEGER NOT NULL,
        telegram_id INTEGER NOT NULL,
        emoji TEXT NOT NULL,
        PRIMARY KEY (message_id, telegram_id, emoji)
    );
    CREATE TABLE chat_reports (
        message_id INTEGER NOT NULL,
        reporter_id INTEGER NOT NULL,
        created_at REAL NOT NULL,
        resolved INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (message_id, reporter_id)
    );
    CREATE TABLE chat_reads (
        telegram_id INTEGER NOT NULL,
        room TEXT NOT NULL,
        last_read INTEGER NOT NULL DEFAULT 0,
        muted INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (telegram_id, room)
    );
    CREATE TABLE chat_moderators (
        telegram_id INTEGER NOT NULL,
        school_key TEXT NOT NULL,
        PRIMARY KEY (telegram_id, school_key)
    );
    CREATE TABLE chat_poll_votes (
        message_id INTEGER NOT NULL,
        telegram_id INTEGER NOT NULL,
        option INTEGER NOT NULL,
        PRIMARY KEY (message_id, telegram_id)
    );
    CREATE TABLE chat_mod_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        target TEXT NOT NULL,
        details TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL
    );
    """,
    # 19 — новый дизайн выходит из беты и включается всем. Сохранённый выбор
    # «classic» сбрасываем один раз; вернуть старый можно в настройках, а
    # приложение один раз покажет об этом окошко (designNotice).
    """
    UPDATE web_prefs SET payload = json_set(payload, '$.design', 'new')
    WHERE json_valid(payload);
    """,
    # 20 — ники вместо настоящих имён, режим уведомлений чата (все / упоминания
    # / выкл). Имена из уже отправленных сообщений стираем: подпись теперь
    # берётся из профиля по нику.
    """
    ALTER TABLE chat_profiles ADD COLUMN nick TEXT;
    ALTER TABLE chat_profiles ADD COLUMN nick_key TEXT;
    ALTER TABLE chat_profiles ADD COLUMN nick_changed_at REAL NOT NULL DEFAULT 0;
    CREATE UNIQUE INDEX chat_profiles_nick ON chat_profiles(nick_key);
    ALTER TABLE chat_reads ADD COLUMN notify TEXT;
    UPDATE chat_reads SET notify = 'off' WHERE muted = 1;
    UPDATE chat_messages SET author_name = '' WHERE is_admin = 0;
    CREATE INDEX chat_reads_room ON chat_reads(room, last_read);
    """,
    # 21 — выбранный ник сохраняется после выхода; менять его может администратор.
    """
    CREATE TABLE chat_nicks (
        telegram_id INTEGER PRIMARY KEY,
        nick TEXT NOT NULL,
        nick_key TEXT NOT NULL UNIQUE
    );
    INSERT INTO chat_nicks SELECT telegram_id, nick, nick_key FROM chat_profiles WHERE nick IS NOT NULL;
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
