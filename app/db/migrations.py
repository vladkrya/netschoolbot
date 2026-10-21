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
    # 22 — ошибки фоновой проверки не рассылаются заново после перезапуска.
    """
    CREATE TABLE watch_error_notices (
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        reason TEXT NOT NULL,
        PRIMARY KEY (telegram_id, reason)
    );
    INSERT INTO watch_error_notices (telegram_id, reason)
    SELECT telegram_id, 'auth' FROM watch_status
    WHERE last_error LIKE '%/login%' AND
        (last_ok_at IS NULL OR last_error_at > last_ok_at);
    """,
    # 23 — одноразовое объявление о чатах только уже существующим аккаунтам.
    """
    CREATE TABLE chat_launch_notices (
        telegram_id INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE
    );
    INSERT INTO chat_launch_notices SELECT telegram_id FROM users;
    """,
    # 24 — кто и как открывает приложение: дизайн и где открыто (Telegram,
    # установленное приложение, браузер). Для /adminstats.
    """
    CREATE TABLE app_visits (
        telegram_id INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
        design TEXT NOT NULL,
        client TEXT NOT NULL,
        first_seen_at REAL NOT NULL,
        seen_at REAL NOT NULL
    );
    """,
    # 25 — правка своих сообщений в чатах. Прежний текст хранится для разбора
    # жалоб: иначе оскорбление можно было бы «исправить» после жалобы.
    """
    ALTER TABLE chat_messages ADD COLUMN edited_at REAL;
    CREATE TABLE chat_edits (
        message_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        edited_at REAL NOT NULL
    );
    CREATE INDEX chat_edits_message ON chat_edits(message_id);
    """,
    # 26 — вход без Telegram: почта и пароль. Коды (подтверждение почты,
    # сброс пароля) хранятся хэшем; код привязки Telegram — на 15 минут.
    """
    CREATE TABLE accounts (
        telegram_id INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        email_verified_at REAL,
        created_at REAL NOT NULL,
        password_changed_at REAL NOT NULL
    );
    CREATE TABLE account_codes (
        email TEXT NOT NULL,
        purpose TEXT NOT NULL,
        code_hash TEXT NOT NULL,
        expires_at REAL NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (email, purpose)
    );
    CREATE TABLE telegram_link_codes (
        code TEXT PRIMARY KEY,
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        expires_at REAL NOT NULL
    );
    """,
    # 27 — версия «Сетевого города» по серверу: после простоя видно, обновили
    # ли его, а смену версии можно сопоставить с новыми ошибками в логах.
    """
    CREATE TABLE sgo_versions (
        host TEXT PRIMARY KEY,
        version TEXT NOT NULL,
        previous TEXT NOT NULL DEFAULT '',
        changed_at REAL NOT NULL,
        seen_at REAL NOT NULL
    );
    """,
    # 28 — напоминания о заданиях и контрольных, риск по оценке, месячная
    # сводка, фильтр значений оценок, слежение за всеми детьми, второй
    # родитель и свои заметки.
    """
    ALTER TABLE users ADD COLUMN homework_reminder INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE users ADD COLUMN reminder_hour INTEGER NOT NULL DEFAULT 18;
    ALTER TABLE users ADD COLUMN test_reminder INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE users ADD COLUMN grade_risk INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE users ADD COLUMN monthly_summary INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE users ADD COLUMN all_children INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE users ADD COLUMN marks_filter TEXT NOT NULL DEFAULT '';

    -- Чей это ребёнок в уведомлении: при слежении за всеми детьми.
    ALTER TABLE outbox ADD COLUMN student TEXT NOT NULL DEFAULT '';

    -- Состояние слежения за остальными детьми (не выбранным): разделы
    -- (оценки, задания, пропуски) целиком JSON — их немного.
    CREATE TABLE child_state (
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        student_id  INTEGER NOT NULL,
        section     TEXT    NOT NULL,
        payload     TEXT    NOT NULL,
        PRIMARY KEY (telegram_id, student_id, section)
    );

    -- Второй родитель получает копии уведомлений без пароля от школы.
    CREATE TABLE followers (
        owner_id    INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        follower_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        name        TEXT    NOT NULL DEFAULT '',
        created_at  REAL    NOT NULL,
        PRIMARY KEY (owner_id, follower_id)
    );
    CREATE INDEX followers_follower ON followers(follower_id);
    CREATE TABLE follow_invites (
        code       TEXT PRIMARY KEY,
        owner_id   INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        expires_at REAL NOT NULL
    );

    -- Свои заметки и задачи ученика: к дате и, если нужно, к предмету.
    CREATE TABLE user_notes (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        day         TEXT    NOT NULL,
        subject     TEXT    NOT NULL DEFAULT '',
        text        TEXT    NOT NULL,
        done        INTEGER NOT NULL DEFAULT 0,
        created_at  REAL    NOT NULL
    );
    CREATE INDEX user_notes_day ON user_notes(telegram_id, day);
    """,
    # Родные: что им доступно — копии уведомлений, просмотр дневника или
    # оба (выбирает ученик в приглашении). follow_views — чей дневник
    # приглашённый сейчас смотрит, если у него есть и свой.
    """
    ALTER TABLE followers ADD COLUMN can_notify INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE followers ADD COLUMN can_view INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE follow_invites ADD COLUMN can_notify INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE follow_invites ADD COLUMN can_view INTEGER NOT NULL DEFAULT 0;
    CREATE TABLE follow_views (
        viewer_id INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
        owner_id  INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE
    );
    """,
    # Ключ виджета на экране iPhone (Scriptable): открывает только расписание
    # на ближайшие дни, отзывается отдельно. Хранится хэш случайного секрета.
    """
    CREATE TABLE widget_keys (
        telegram_id INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
        key_hash    TEXT NOT NULL UNIQUE,
        created_at  TEXT NOT NULL DEFAULT (datetime('now')),
        used_at     TEXT
    );
    """,
    # Виджет на iPhone убран: ключи больше не нужны.
    """
    DROP TABLE IF EXISTS widget_keys;
    """,
    # Свои задачи — ещё и ко времени, с напоминанием за N минут. remind_at —
    # момент напоминания (UTC, секунды); после отправки обнуляется.
    """
    ALTER TABLE user_notes ADD COLUMN at TEXT NOT NULL DEFAULT '';
    ALTER TABLE user_notes ADD COLUMN remind INTEGER;
    ALTER TABLE user_notes ADD COLUMN remind_at REAL;
    CREATE INDEX user_notes_remind ON user_notes(remind_at) WHERE remind_at IS NOT NULL;
    """,
    # Одноразовый опрос «нравится ли приложение» (1–10) — только аккаунтам,
    # которые уже есть на момент обновления. Опрос висит, пока человек не
    # ответит (score) или не откажется (declined_at); «не сейчас» его не
    # снимает. shown_at — когда окно показали впервые (для статистики).
    """
    CREATE TABLE app_survey (
        telegram_id INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
        shown_at    REAL,
        score       INTEGER CHECK (score BETWEEN 1 AND 10),
        answered_at REAL,
        declined_at REAL
    );
    INSERT INTO app_survey (telegram_id) SELECT telegram_id FROM users;
    """,
    # Чей дневник был до /logout: школа и ученик. Выход больше не стирает
    # отметки «выполнено» и историю оценок — при входе в тот же дневник они
    # остаются, а при входе в другой стираются.
    """
    ALTER TABLE users ADD COLUMN last_school_url TEXT NOT NULL DEFAULT '';
    ALTER TABLE users ADD COLUMN last_student_id INTEGER;
    """,
    # Токены входа на конкретном устройстве (по почте или через бота): выход
    # отзывает только такой, а общий токен ссылок из бота и подписки на
    # календарь остаётся.
    """
    ALTER TABLE miniapp_tokens ADD COLUMN device INTEGER NOT NULL DEFAULT 0;
    """,
    # Токен подписки на календарь: открывает только calendar.ics. Адрес
    # подписки хранят календарные приложения и их облака — утечка не должна
    # давать весь дневник. Прежние подписки (scope = '') работают как раньше.
    """
    ALTER TABLE miniapp_tokens ADD COLUMN scope TEXT NOT NULL DEFAULT '';
    """,
    # Установка на экран «Домой» без токена в адресе. kind='enter' — ссылка
    # из /app_install (сутки, можно открыть и во встроенном браузере, и в
    # настоящем); kind='icon' — код в start_url манифеста: первый запуск
    # иконки меняет его на свой токен устройства, дальше код ничего не открывает.
    """
    CREATE TABLE install_codes (
        code        TEXT PRIMARY KEY,
        telegram_id INTEGER NOT NULL,
        kind        TEXT NOT NULL,
        source      TEXT NOT NULL DEFAULT '',
        expires_at  REAL NOT NULL,
        used_at     REAL
    );
    CREATE INDEX idx_install_codes_user ON install_codes(telegram_id);
    """,
    # Экран «Устройства»: чем открывали и когда последний раз.
    """
    ALTER TABLE miniapp_tokens ADD COLUMN label TEXT NOT NULL DEFAULT '';
    ALTER TABLE miniapp_tokens ADD COLUMN last_used REAL;
    """,
    # Кто ходит по общему токену («Ссылки из бота»): токен один, а устройств
    # может быть несколько — экран «Устройства» показывает их списком.
    """
    CREATE TABLE token_clients (
        telegram_id INTEGER NOT NULL,
        token_hash  TEXT NOT NULL,
        client      TEXT NOT NULL,
        model       TEXT NOT NULL DEFAULT '',
        ip          TEXT NOT NULL DEFAULT '',
        first_seen  REAL NOT NULL,
        last_seen   REAL NOT NULL,
        PRIMARY KEY (token_hash, client, model)
    );
    CREATE INDEX idx_token_clients_user ON token_clients(telegram_id);
    """,
    # Все места, где пользователь открывал приложение (app_visits помнит
    # только последнее): кто бывал в Telegram, а кто только в браузере.
    # Для /adminstats. Прошлое — из app_visits, последнее известное место.
    """
    CREATE TABLE app_clients (
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        client TEXT NOT NULL,
        first_seen_at REAL NOT NULL,
        seen_at REAL NOT NULL,
        PRIMARY KEY (telegram_id, client)
    );
    INSERT INTO app_clients (telegram_id, client, first_seen_at, seen_at)
    SELECT telegram_id, client, first_seen_at, seen_at FROM app_visits;
    """,    # 41 — страница статуса «Сетевого города»: проверки всех серверов,
    # история версий (sgo_versions помнит только последнюю) и жалобы на сбои.
    """
    CREATE TABLE sgo_probes (
        host TEXT NOT NULL,
        at REAL NOT NULL,
        ok INTEGER NOT NULL,
        latency_ms INTEGER NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX idx_sgo_probes_at ON sgo_probes(at);
    CREATE INDEX idx_sgo_probes_host_at ON sgo_probes(host, at);
    CREATE TABLE sgo_version_history (
        host TEXT NOT NULL,
        version TEXT NOT NULL,
        previous TEXT NOT NULL DEFAULT '',
        changed_at REAL NOT NULL
    );
    CREATE INDEX idx_sgo_version_history_at ON sgo_version_history(changed_at);
    INSERT INTO sgo_version_history (host, version, previous, changed_at)
    SELECT host, version, previous, changed_at FROM sgo_versions;
    CREATE TABLE sgo_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        host TEXT NOT NULL,
        kind TEXT NOT NULL,
        comment TEXT NOT NULL DEFAULT '',
        client TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL
    );
    CREATE INDEX idx_sgo_reports_at ON sgo_reports(created_at);
    """,
    # 42 — страница статуса отдельно от проверок оценок: мобильное API в
    # проверках и своя история версий. Общую sgo_version_history писал
    # VersionTracker проверок оценок — данные переносятся, таблица больше не нужна.
    """
    ALTER TABLE sgo_probes ADD COLUMN mobile INTEGER;
    CREATE TABLE sgo_status_versions (
        host TEXT NOT NULL,
        version TEXT NOT NULL,
        previous TEXT NOT NULL DEFAULT '',
        changed_at REAL NOT NULL
    );
    CREATE INDEX idx_sgo_status_versions_host ON sgo_status_versions(host, changed_at);
    INSERT INTO sgo_status_versions (host, version, previous, changed_at)
    SELECT host, version, previous, changed_at FROM sgo_version_history;
    DROP TABLE sgo_version_history;
    """,
    # 43 — подписки из приложения: «сообщить, когда сервер «Сетевого города»
    # заработает» (kind='up', разовая) и «об обновлениях сервера»
    # (kind='updates', version — последняя, о которой уже знает человек).
    """
    CREATE TABLE sgo_alerts (
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        host TEXT NOT NULL,
        url TEXT NOT NULL,
        kind TEXT NOT NULL,
        version TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        PRIMARY KEY (telegram_id, host, kind)
    );
    """,
    # 44 — вложения заданий, скачанные заранее (attachments/keeper.py): файл
    # один на сервер школы, режим API и номер вложения — у всего класса он
    # общий, — а отдаётся только тем, у кого это вложение есть в заданиях.
    """
    CREATE TABLE kept_attachments (
        host TEXT NOT NULL,
        mode TEXT NOT NULL,
        attachment_id INTEGER NOT NULL,
        size INTEGER NOT NULL,
        saved_at REAL NOT NULL,
        PRIMARY KEY (host, mode, attachment_id)
    );
    CREATE INDEX idx_kept_attachments_saved ON kept_attachments(saved_at);
    CREATE TABLE kept_attachment_access (
        telegram_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        host TEXT NOT NULL,
        mode TEXT NOT NULL,
        attachment_id INTEGER NOT NULL,
        PRIMARY KEY (telegram_id, host, mode, attachment_id)
    );
    -- Переключатели на весь бот, которые админ меняет из приложения
    -- (например, «сохранение файлов — всем»).
    CREATE TABLE app_flags (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    # Кто открывает приложение с хеллоуинским оформлением — для /adminstats.
    # Пусто — открывали до версии, которая это сообщает.
    """
    ALTER TABLE app_visits ADD COLUMN halloween INTEGER;
    """,
    # 46 — группы и каналы Telegram, куда бот сам пишет о «Сетевом городе»
    # (status/groups.py): простои и обновления, по выбранным регионам.
    # regions — JSON-список хостов; пустой — все регионы.
    """
    CREATE TABLE broadcast_groups (
        chat_id INTEGER PRIMARY KEY,
        title TEXT NOT NULL DEFAULT '',
        outages INTEGER NOT NULL DEFAULT 1,
        updates INTEGER NOT NULL DEFAULT 1,
        regions TEXT NOT NULL DEFAULT '[]',
        added_at REAL NOT NULL
    );
    """,
    # 47 — обезличенные итоги фоновых проверок: только сервер и счётчики за 5 минут.
    """
    CREATE TABLE sgo_account_checks (
        host TEXT NOT NULL,
        bucket REAL NOT NULL,
        good INTEGER NOT NULL DEFAULT 0,
        total INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (host, bucket)
    );
    CREATE INDEX idx_sgo_account_checks_bucket ON sgo_account_checks(bucket);
    """,
    # «Добавить устройство»: одноразовая ссылка на час (kind='device') помнит
    # выданный по ней токен — экран ждёт открытия и показывает, кто вошёл.
    """
    ALTER TABLE install_codes ADD COLUMN token TEXT;
    """,
    # Своя подпись сеанса на экране «Устройства»: «Планшет на кухне».
    """
    ALTER TABLE miniapp_tokens ADD COLUMN name TEXT NOT NULL DEFAULT '';
    """,
    # Решения домашних заданий (solver/): одно на класс — ключ из сервера
    # школы, предмета, даты и текста задания.
    """
    CREATE TABLE homework_solutions (
        key TEXT PRIMARY KEY,
        subject TEXT NOT NULL DEFAULT '',
        due TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        text TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE INDEX idx_homework_solutions_created ON homework_solutions(created_at);
    """,
    # «Устройства»: каким способом вошли (почта, бот, Telegram, ссылка…),
    # и модель с примерным адресом не только у общих ссылок, но и у устройств.
    """
    ALTER TABLE miniapp_tokens ADD COLUMN method TEXT NOT NULL DEFAULT '';
    ALTER TABLE miniapp_tokens ADD COLUMN model TEXT NOT NULL DEFAULT '';
    ALTER TABLE miniapp_tokens ADD COLUMN ip TEXT NOT NULL DEFAULT '';
    """,
    # Условия упражнений со сканов, однажды прочитанные моделью с картинки:
    # дальше номер уходит в модель текстом — картинка стоит в 30 раз дороже.
    """
    CREATE TABLE exercise_conditions (
        book TEXT NOT NULL,
        number INTEGER NOT NULL,
        text TEXT NOT NULL,
        created_at REAL NOT NULL,
        PRIMARY KEY (book, number)
    );
    """,
    # Чей аккаунт «Сетевого города»: один аккаунт приложения — один аккаунт
    # школы (netschool/ownership.py). Ключ — сервер и id пользователя в СГ,
    # одинаковый при входе и по паролю, и через Госуслуги.
    """
    CREATE TABLE netschool_accounts (
        telegram_id INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
        school_url  TEXT    NOT NULL,
        ns_user_id  INTEGER NOT NULL,
        created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX idx_netschool_accounts_key ON netschool_accounts(school_url, ns_user_id);
    """,
    # Разбор недели от ИИ-помощника и тихий режим на каникулы (оба выключены).
    """
    ALTER TABLE users ADD COLUMN weekly_review INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE users ADD COLUMN vacation_quiet INTEGER NOT NULL DEFAULT 0;
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
