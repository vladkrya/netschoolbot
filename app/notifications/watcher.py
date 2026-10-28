"""Фоновая проверка оценок.

Замена `GradeNotifier` из старого проекта, где `check_new_grades` была одной
функцией на 568 строк: в ней вперемешку жили HTTP-запросы, сравнение
состояний, отправка сообщений и работа с файлами. Из-за глубины вложенности
там, в частности, обнаружение удалённых оценок оказалось в ветке, куда
исполнение не попадало при успешном повторном запросе.

Здесь всё разнесено: сравнение — в `diff.py` (чистая функция, покрыта
тестами), доставка — в `Notifier` (интерфейс, в тестах подменяется), а этот
модуль только связывает их и отвечает за расписание.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any, Protocol

from ..db.repositories import MarkStateRepository, UserRepository
from ..domain import formatting, insights, review
from ..domain.models import ApiMode, MarkKind, NotificationPrefs, Student, User
from ..domain.periods import Quarter, class_average_period, current_period, periods_from_terms, resolve_periods
from ..domain.vacations import on_vacation, vacations
from ..domain.records import AbsenceRecord, HomeworkRecord, MarkEvent, RecentEvents
from .outage import OutageMonitor
from ..netschool.errors import NetSchoolError, Reason
from ..netschool.mapping import absences_from_days, is_placeholder_name
from ..netschool.service import DiaryService, TokenState
from .diff import confirm_deletions, diff_homework, diff_marks
from .digests import (
    DigestKind,
    ended_period,
    evening_period,
    month_bounds,
    monthly_period,
    next_school_day,
    previous_week,
    reminder_period,
    weekly_period,
)
from .outbox import OutboxKind, decode, encode, mark_to_dict
from .schedule_diff import diff_schedule, watched_days

logger = logging.getLogger("netschoolbot.watcher")

# Пауза между уведомлениями, чтобы Telegram не начал ограничивать бота.
NOTIFICATION_DELAY = 2.0

# Если новых оценок слишком много, лучше прислать одну сводку, чем засыпать
# человека сообщениями.
BULK_THRESHOLD = 5

# После ошибки проверки следующий заход откладывается, но не дольше этого.
MAX_BACKOFF = 1800
# Сервер школы молчит дольше этого — пишем человеку, что знаем и ждём.
# Короткие сбои проходят сами, и сообщать о каждом было бы шумом.
SERVER_DOWN_NOTICE_AFTER = 3600

# В мобильном режиме каждый проход смотрит лёгкую ленту событий, а полный
# дневник сверяет только при новых событиях и не реже этого срока: лента не
# показывает удалённые и исправленные давние оценки.
FULL_SYNC_INTERVAL = 3600.0

# Об отметке посещаемости сообщаем, только если урок был не раньше этого числа
# дней назад: так включение уведомлений или смена API не засыпают человека
# пропусками за прошлые недели.
ABSENCE_NOTIFY_DAYS = 7

# Четвертные и годовые оценки выставляют раз в период — сверяем их реже оценок.
TERM_MARKS_INTERVAL = 1800.0

# Как часто снимать средний балл класса (только мобильное API): это запрос
# на каждый предмет. После новых оценок снимок берётся сразу — так видно,
# каким средний был до работы и каким стал после.
CLASS_AVERAGES_INTERVAL = 3 * 3600.0

# Срок мобильного токена меряется в днях — проверять его каждый проход незачем.
TOKEN_CHECK_INTERVAL = 6 * 3600.0

# Остальных детей (при слежении за всеми) сверяем не чаще этого: у них нет
# дешёвой ленты событий, и каждый проход — полный дневник.
CHILD_CHECK_INTERVAL = 900.0

# Состояние заданий невыбранного ребёнка дальше этого не растёт.
CHILD_HOMEWORK_LIMIT = 3000

# Отметки посещаемости, которые не считаются пропуском урока.
NOT_ABSENT = frozenset({"ОП", "ОСВ"})


# Отложенное уведомление живёт в очереди не дольше этого: утренняя досылка
# после тихих часов укладывается, а недельной давности новость уже не нужна.
OUTBOX_MAX_AGE = dt.timedelta(days=2)
OUTBOX_MAX_ATTEMPTS = 20


def _local_now(user: User) -> dt.datetime:
    """Местное время пользователя. Отдельной функцией — чтобы тесты могли его подменить."""
    return user.now()


def _mark_event_wanted(event: MarkEvent, prefs: NotificationPrefs) -> bool:
    if event.kind is MarkKind.NEW:
        return prefs.grades and prefs.wants_mark(event.record.mark)
    if event.kind is MarkKind.CHANGED:
        return prefs.changes
    return prefs.deletes


def _count_absences(days: list) -> int:
    """Пропущенные уроки (без опозданий и освобождений)."""
    return sum(
        1 for day in days for lesson in day.lessons
        if (code := (lesson.absence or "").strip().upper()) and code not in NOT_ABSENT
    )


class Notifier(Protocol):
    """Куда уходят уведомления. Позволяет тестировать цикл без Telegram."""

    async def send_mark_events(self, user: User, events: list[MarkEvent]) -> None: ...

    async def send_homework(self, user: User, items: list[HomeworkRecord]) -> None: ...

    async def send_absences(self, user: User, items: list[AbsenceRecord]) -> None: ...

    async def send_token_expiring(self, user: User, expires_at: dt.datetime) -> None: ...

    async def send_error(self, user: User, error: NetSchoolError) -> None: ...

    async def send_text(
        self, user: User, text: str, *, push: tuple[str, str] | None = None, kind: str = "",
        family: bool = True,
    ) -> None: ...


class UserWatcher:
    """Проверка оценок одного пользователя."""

    def __init__(
        self,
        user_id: int,
        *,
        users: UserRepository,
        state: MarkStateRepository,
        diary: DiaryService,
        notifier: Notifier,
        clock: Callable[[], float] = time.monotonic,
        outages: OutageMonitor | None = None,
        live: Callable[[int, dict], Awaitable[None]] | None = None,
        notes: Any = None,
        homework_seen: Callable[[User, list[HomeworkRecord]], None] | None = None,
        keep_signed_in: Callable[[User], Awaitable[bool]] | None = None,
        weekly_review: Callable[[str], Awaitable[str | None]] | None = None,
    ) -> None:
        self._user_id = user_id
        # «Не выходить из аккаунта» для Госуслуг (AppContext.keep_signed_in).
        self._keep_signed_in = keep_signed_in
        self._last_keep_attempt: float | None = None
        # Разбор недели от ИИ-помощника: факты → текст (AppContext.weekly_review).
        self._weekly_review = weekly_review
        # Тихий режим на каникулы: (день, каникулы ли) — считаем раз в сутки.
        self._vacation_day: tuple[dt.date, bool] | None = None
        # Задания с вложениями — хранилищу файлов (attachments/keeper.py).
        self._homework_seen = homework_seen
        # Свои задачи ученика — для вечернего напоминания (NotesRepository).
        self._notes = notes
        self._last_children_check: float | None = None
        self._outages = outages
        self._live = live
        self._users = users
        self._state = state
        self._diary = diary
        self._notifier = notifier
        self._backoff = 0.0
        # С какого момента подряд не отвечает сервер школы (часы self._clock).
        self._down_since: float | None = None
        # Что уже видели в ленте событий и когда в последний раз сверяли
        # дневник целиком. В памяти: после перезапуска первая проверка
        # просто пройдёт полным путём.
        self._clock = clock
        self._event_keys: frozenset[str] | None = None
        self._last_full_sync: float | None = None
        self._last_token_check: float | None = None
        self._last_term_marks: float | None = None
        self._last_class_averages: float | None = None
        self._class_averages_due = False

    async def run(self) -> None:
        """Бесконечный цикл проверки. Останавливается отменой задачи."""
        while True:
            interval = await self.run_once()
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                logger.info("Проверка для %s остановлена", self._user_id)
                raise

    async def run_once(self) -> float:
        """Один проход. Возвращает, через сколько секунд идти на следующий."""
        user = await self._users.get(self._user_id)
        if user is None or not user.ready_to_check:
            logger.info("Проверка для %s больше не нужна", self._user_id)
            return float(MAX_BACKOFF)

        # Почта не связана с лентой оценок и проверяется даже при её неизменности.
        # В «Моей школе» почты нет: спрашивать её значит каждый раз получать отказ.
        if user.api_mode is not ApiMode.MYSCHOOL:
            try:
                messages = await self._diary.fetch_mail(user, limit=50)
                await self._state.save_mail_check(user.telegram_id, messages, notify=user.notifications.mail)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Проверка почты %s не завершилась: %s", user.telegram_id, exc)

        try:
            await self._check(user)
            await self._check_children(user)
        except NetSchoolError as exc:
            if self._outages is not None:
                await self._outages.record(user.school.url, ok=False,
                                           server_down=exc.reason is Reason.SERVER_UNAVAILABLE,
                                           diary_failed=exc.reason.breaks_diary)
            delay = await self._handle_error(user, exc)
            await self._record_status(ok=False, error=exc.user_message, delay=delay)
            await self._flush_outbox(user)
            return delay
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Непредвиденная ошибка не должна убивать цикл: пользователь
            # молча перестал бы получать уведомления, как это бывало раньше.
            logger.exception("Сбой проверки для %s", self._user_id)
            self._backoff = min(max(self._backoff * 2, 60.0), MAX_BACKOFF)
            await self._record_status(ok=False, error=f"Внутренняя ошибка: {type(exc).__name__}", delay=self._backoff)
            await self._flush_outbox(user)
            return self._backoff

        await self._check_term_marks(user)
        await self._snapshot_class_averages(user)
        # Очередь разбирается после проверки, а не только при новых событиях:
        # так после тихих часов или сбоя Telegram отложенное уходит само.
        await self._flush_outbox(user)
        await self._check_token(user)
        await self._keep_session(user)
        await self._send_digests(user)

        self._backoff = 0.0
        self._down_since = None
        if self._outages is not None:
            await self._outages.record(user.school.url, ok=True)
        await self._record_status(ok=True, delay=float(user.check_interval))
        return float(user.check_interval)

    async def _check(self, user: User) -> None:
        user = await self._refresh_student_if_needed(user)
        events = await self._diary.fetch_recent_events(user)
        if events is not None and not self._full_sync_due(events):
            return
        # Один запрос дневника на оценки и задания сразу, а не по запросу на каждое.
        days = await self._diary.fetch_diary(user)
        marks = [record for day in days for record in day.marks]
        homework = [record for day in days for record in day.homework]

        known = await self._state.load_marks(user.telegram_id)
        first_run = not await self._state.has_history(user.telegram_id)

        # Сравнение — по всем видам событий: история нужна целиком, а что из
        # неё слать, решают настройки уведомлений ниже.
        result = diff_marks(known, marks, filters=user.filters, first_run=first_run)

        if result.pending_deletes:
            confirmed = await self._confirm_deletes(user, result.pending_deletes)
            result.events.extend(confirm_deletions(result, confirmed))

        known_homework = await self._state.load_homework(user.telegram_id)
        fresh_homework, all_homework = diff_homework(
            known_homework, homework, first_run=first_run
        )
        absences, fresh_absences = await self._diff_absences(user, days, first_run=first_run)
        today = _local_now(user).date()
        schedule_now = watched_days(days, today)
        # Снимок обновляется всегда, даже при выключенных уведомлениях: иначе
        # включение тумблера выдало бы давние перестановки за новые.
        schedule_changes = (
            [] if first_run else diff_schedule(await self._state.load_schedule(user.telegram_id), schedule_now)
        )

        prefs = user.notifications
        mark_events = [e for e in result.events if _mark_event_wanted(e, prefs)]
        outbox: list[tuple[str, str]] = []
        if mark_events:
            outbox.append((OutboxKind.MARKS.value, encode(OutboxKind.MARKS, mark_events)))
        if fresh_homework and prefs.homework:
            outbox.append((OutboxKind.HOMEWORK.value, encode(OutboxKind.HOMEWORK, fresh_homework)))
        if fresh_absences and prefs.absences:
            outbox.append((OutboxKind.ABSENCES.value, encode(OutboxKind.ABSENCES, fresh_absences)))
        if schedule_changes and prefs.schedule:
            outbox.append((OutboxKind.SCHEDULE.value, encode(OutboxKind.SCHEDULE, schedule_changes)))
        if prefs.grade_risk and not first_run and result.events:
            risks = await self._grade_risks(user, result.events, today)
            if risks:
                outbox.append((OutboxKind.RISK.value, encode(OutboxKind.RISK, risks)))
        if outbox and await self._on_vacation(user, today):
            # Тихий режим на каникулы: история и снимки обновляются, а в
            # очередь ничего не встаёт — после каникул не придёт всё разом.
            outbox = []
        history = [
            (
                event.kind.value,
                json.dumps(mark_to_dict(event.record), ensure_ascii=False),
                event.old_mark,
                event.new_mark,
            )
            for event in result.events
        ]

        # Состояние и очередь — одной транзакцией: повторная проверка не
        # пришлёт то же самое ещё раз, а недоставленное не потеряется.
        await self._state.save_check(
            user.telegram_id,
            tracked=result.tracked,
            homework=all_homework,
            observed_new_homework={item.identity for item in fresh_homework},
            absences=absences,
            outbox=outbox,
            history=history,
            # Базовая сверка завершена только после сохранения всех разделов.
            baseline_done=first_run,
            schedule=schedule_now,
            today=today,
        )
        self._note_full_sync(events)
        if result.events and not first_run:
            self._class_averages_due = True
        if self._homework_seen is not None:
            try:
                self._homework_seen(user, homework)
            except Exception as exc:  # noqa: BLE001 — файлы вторичны
                logger.debug("Вложения заданий %s не переданы: %s", user.telegram_id, exc)
        # Открытое приложение обновит экраны сразу, не дожидаясь своего опроса.
        # Сообщаем о любых изменениях, даже если уведомления о них выключены.
        changed = [kind for kind, present in (
            ("marks", result.events), ("homework", fresh_homework),
            ("absences", fresh_absences), ("schedule", schedule_changes)) if present]
        if changed and self._live is not None and not first_run:
            try:
                await self._live(user.telegram_id, {"t": "diary", "kinds": changed})
            except Exception as exc:  # noqa: BLE001 — сигнал приложению не важнее проверки
                logger.debug("Сигнал приложению %s не ушёл: %s", user.telegram_id, exc)

    async def _flush_outbox(self, user: User) -> None:
        """Отправить накопленные уведомления, кроме тихих часов.

        Неудачная отправка остаётся в очереди до следующего прохода; слишком
        старое или безнадёжное удаляется, чтобы не прислать вчерашнюю
        оценку как новость через неделю.
        """
        rows = await self._state.pending_outbox(user.telegram_id)
        if not rows:
            return
        if self._is_quiet(user):
            logger.info("Тихие часы у %s: в очереди %s", user.telegram_id, len(rows))
            return
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        for row in rows:
            created = dt.datetime.fromisoformat(row["created_at"])
            if now - created > OUTBOX_MAX_AGE or row["attempts"] >= OUTBOX_MAX_ATTEMPTS:
                logger.warning(
                    "Уведомление %s для %s не доставлено и снято с очереди", row["id"], user.telegram_id
                )
                await self._state.ack_outbox(row["id"])
                continue
            try:
                kind = OutboxKind(row["kind"])
                items = decode(kind, row["payload"])
            except (ValueError, KeyError, TypeError) as exc:
                logger.warning("Повреждённое уведомление %s: %s", row["id"], exc)
                await self._state.ack_outbox(row["id"])
                continue
            target = user
            student = row["student"] if "student" in row.keys() else ""
            if student:
                target = replace(user, student_name=student)
            try:
                await self._send(target, kind, items)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — любой сбой доставки = попробовать позже
                logger.info("Уведомление для %s отложено: %s", user.telegram_id, exc)
                await self._state.fail_outbox(row["id"], type(exc).__name__)
                # Порядок важен: следующее не отправляем раньше предыдущего.
                return
            await self._state.ack_outbox(row["id"])

    async def _send(self, user: User, kind: OutboxKind, items: list) -> None:
        if kind is OutboxKind.MARKS:
            await self._notifier.send_mark_events(user, items)
        elif kind is OutboxKind.HOMEWORK:
            await self._notifier.send_homework(user, items)
        elif kind is OutboxKind.ABSENCES:
            await self._notifier.send_absences(user, items)
        elif kind is OutboxKind.MAIL:
            import html
            lines = ["📬 Новая школьная почта"]
            for item in items:
                lines.append(f"\n<b>{html.escape(str(item.get('subject') or 'Без темы'))}</b>"
                             f"\n{html.escape(str(item.get('author') or 'Без отправителя'))}")
            preview = str(items[0].get("subject") or "Без темы")
            await self._notifier.send_text(user, "\n".join(lines), push=("Школьная почта", preview), kind="mail")
        elif kind is OutboxKind.SCHEDULE:
            await self._notifier.send_text(
                user,
                formatting.schedule_changes(items),
                push=("Изменения в расписании", formatting.schedule_preview(items)),
                kind="schedule",
            )
        elif kind is OutboxKind.TERMS:
            await self._notifier.send_text(
                user,
                formatting.term_marks(items),
                push=("Новые оценки", formatting.term_marks_push(items)),
                kind="marks",
            )
        elif kind is OutboxKind.RISK:
            await self._notifier.send_text(
                user,
                formatting.risk_alerts(items),
                push=("Итоговая оценка под угрозой", formatting.risk_preview(items)),
                kind="marks",
            )

    async def _send_digests(self, user: User) -> None:
        """Сводки и напоминания: вечером на завтра, по понедельникам, раз в месяц.

        Сбой сводки не трогает проверку оценок: отметка о периоде ставится
        только после отправки, поэтому следующий проход попробует снова.
        """
        if self._is_quiet(user):
            return
        now = _local_now(user)
        if await self._on_vacation(user, now.date()):
            return
        prefs = user.notifications
        jobs: list[tuple[DigestKind, str, Any]] = []
        if prefs.evening_summary and (period := evening_period(now)):
            jobs.append((DigestKind.EVENING, period, None))
        if (prefs.homework_reminder or prefs.test_reminder) and (
            period := reminder_period(now, prefs.reminder_hour)
        ):
            jobs.append((DigestKind.REMINDER, period, None))
        if prefs.weekly_summary and (period := weekly_period(now)):
            jobs.append((DigestKind.WEEKLY, period, None))
        if prefs.monthly_summary and (period := monthly_period(now)):
            jobs.append((DigestKind.MONTHLY, period, None))
        for kind, period, extra in jobs:
            if await self._state.digest_period(user.telegram_id, kind.value) == period:
                continue
            await self._deliver_digest(user, kind, period, extra)
        if prefs.monthly_summary and await self._term_digest_due(user, now):
            # Итоги периода: границы зависят от школы, их узнаём раз в сутки.
            try:
                year, _ = await self._periods(user, now.date())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — сводка вторична
                logger.info("Периоды для %s не получены: %s", user.telegram_id, exc)
                return
            ended = ended_period(now, year)
            if ended is not None and await self._state.digest_period(
                user.telegram_id, DigestKind.TERM.value
            ) != ended.start.isoformat():
                await self._deliver_digest(user, DigestKind.TERM, ended.start.isoformat(), (ended, year))

    async def _term_digest_due(self, user: User, now: dt.datetime) -> bool:
        """Дешёвая проверка перед запросом периодов: раз в сутки, а не каждый проход."""
        if now.time() < dt.time(8, 0):
            return False
        today = now.date().isoformat()
        if await self._state.digest_period(user.telegram_id, "term-check") == today:
            return False
        await self._state.set_digest_period(user.telegram_id, "term-check", today)
        return True

    async def _deliver_digest(self, user: User, kind: DigestKind, period: str, extra: Any) -> None:
        try:
            parts = []
            targets = self._children_of(user)
            for index, child in enumerate(targets):
                # Свои задачи общие на аккаунт — напоминаем о них один раз.
                text = await self._digest_text(child, kind, dt.date.fromisoformat(period), extra,
                                               with_notes=index == 0)
                if text:
                    parts.append(
                        f"{formatting.student_header(child.student_name)}\n{text}" if len(targets) > 1 else text
                    )
            if parts:
                await self._notifier.send_text(user, "\n\n".join(parts))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — сводка вторична
            logger.info("Сводка %s для %s не отправлена: %s", kind.value, user.telegram_id, exc)
            return
        await self._state.set_digest_period(user.telegram_id, kind.value, period)

    def _children_of(self, user: User) -> list[User]:
        """Для кого собирать сводку: выбранный ребёнок или все дети аккаунта."""
        if not (user.notifications.all_children and len(user.available_students) > 1):
            return [user]
        selected = [s for s in user.available_students if s.id == user.selected_student_id]
        others = [s for s in user.available_students if s.id != user.selected_student_id]
        return [replace(user, selected_student_id=s.id, student_name=s.name) for s in selected + others]

    async def _digest_text(
        self, user: User, kind: DigestKind, day: dt.date, extra: Any = None, *, with_notes: bool = True
    ) -> str | None:
        today = _local_now(user).date()
        if kind is DigestKind.EVENING:
            days = await self._diary.fetch_period(user, day, day, today=today)
            found = next((d for d in days if d.day == day), None)
            return formatting.evening_summary(found, day, tz=user.tz)
        if kind is DigestKind.REMINDER:
            return await self._reminder_text(user, day, today, with_notes=with_notes)
        if kind is DigestKind.MONTHLY:
            return await self._monthly_text(user, day, today)
        if kind is DigestKind.TERM:
            ended, year = extra
            return await self._term_text(user, ended, year, today)
        start, end = previous_week(day)
        days = await self._diary.fetch_period(user, start, end, today=day)
        week = [r for d in days for r in d.marks if start <= r.date <= end]
        text = formatting.weekly_summary(week, start, end)
        if user.notifications.grade_risk:
            try:
                year, prefs = await self._periods(user, day)
                period = current_period(year, day)
                period_days = await self._diary.fetch_period(user, period.start, day, today=day)
                marks = [r for d in period_days for r in d.marks if period.covers(r.date)]
                text = "\n".join([text, *formatting.edge_lines(insights.on_the_edge(marks, prefs))])
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — без этой части сводка всё равно полезна
                logger.info("Средние за период для %s не посчитаны: %s", user.telegram_id, exc)
        if user.notifications.weekly_review and self._weekly_review is not None:
            try:
                analysis = await self._review_text(user, day, week)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — сводка уходит и без разбора
                logger.info("Разбор недели для %s не получен: %s", user.telegram_id, exc)
                analysis = None
            if analysis:
                text = f"{text}\n\n🤖 <b>Разбор недели</b>\n{formatting.esc(analysis)}"
        return text

    async def _review_text(self, user: User, day: dt.date, week: list) -> str | None:
        """Факты недели и периода — модели, её ответ — в сводку."""
        year, prefs = await self._periods(user, day)
        period = current_period(year, day)
        period_days = await self._diary.fetch_period(user, period.start, day, today=day)
        marks = [r for d in period_days for r in d.marks
                 if period.covers(r.date) and user.filters.allows_subject(r.subject)]
        ahead = review.week_ahead(day)
        upcoming = await self._diary.fetch_period(user, ahead[0], ahead[-1], today=day)
        tests = insights.upcoming_tests([h for d in upcoming for h in d.homework], ahead)
        facts = review.review_facts(week, marks, prefs, tests, period_label=period.label)
        if facts is None:
            return None
        answer = await self._weekly_review(facts)
        return review.clean_review(answer) if answer else None

    async def _reminder_text(self, user: User, day: dt.date, today: dt.date, *, with_notes: bool) -> str | None:
        """Что на завтра не отмечено сделанным, свои задачи и ближайшие проверочные."""
        prefs = user.notifications
        after = next_school_day(day)
        days = await self._diary.fetch_period(user, day, after, today=today)
        homework = [h for d in days for h in d.homework]
        tests = insights.upcoming_tests(homework, [day, after]) if prefs.test_reminder else []
        undone: list[HomeworkRecord] = []
        notes: list[dict] = []
        if prefs.homework_reminder:
            done = await self._state.done_homework(user.telegram_id)
            reminded_tests = {(t.due_date, t.subject) for t in tests}
            undone = [
                h for h in homework
                if h.due_date == day and not h.nothing_to_do and h.key not in done
                # Проверочная уже в своём разделе — второй раз не повторяем.
                and (h.due_date, h.subject) not in reminded_tests
            ]
            if with_notes and self._notes is not None:
                notes = [n for n in await self._notes.between(user.telegram_id, day, day) if not n["done"]]
        return formatting.reminder(day, undone, notes, tests)

    async def _monthly_text(self, user: User, first: dt.date, today: dt.date) -> str:
        start, end = month_bounds(first)
        previous_start, _ = month_bounds((start - dt.timedelta(days=1)).replace(day=1))
        days = await self._diary.fetch_period(user, previous_start, end, today=today)

        def stats(low: dt.date, high: dt.date) -> insights.PeriodStats:
            chosen = [d for d in days if low <= d.day <= high]
            return insights.period_stats(
                [r for d in chosen for r in d.marks if low <= r.date <= high],
                absences=_count_absences(chosen),
            )

        return formatting.monthly_summary(start, stats(start, end), stats(previous_start, start - dt.timedelta(days=1)))

    async def _term_text(self, user: User, ended: Quarter, year: list[Quarter], today: dt.date) -> str:
        index = next((i for i, item in enumerate(year) if item.key == ended.key), 0)
        previous = year[index - 1] if index > 0 else None
        days = await self._diary.fetch_period(user, (previous or ended).start, ended.end, today=today)
        marks = [r for d in days for r in d.marks if ended.covers(r.date)]
        prefs = await self._state.user_prefs(user.telegram_id)
        stats = insights.period_stats(marks, absences=_count_absences([d for d in days if ended.covers(d.day)]))
        previous_average = None
        if previous is not None:
            previous_average = insights.weighted([r for d in days for r in d.marks if previous.covers(r.date)])
        return formatting.term_summary(ended.label, insights.subject_grades(marks, prefs), stats, previous_average)

    async def _periods(self, user: User, today: dt.date) -> tuple[list[Quarter], dict]:
        """Учебные периоды пользователя и его настройки приложения."""
        prefs = await self._state.user_prefs(user.telegram_id)
        setting = prefs.get("periods") or {}
        terms = None
        if (setting.get("scheme") or "auto") == "auto":
            try:
                terms = await self._diary.fetch_terms(user)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — без периодов школы есть общепринятые
                logger.debug("Периоды школы для %s не получены: %s", user.telegram_id, exc)
        return resolve_periods(setting, terms, today), prefs

    async def _on_vacation(self, user: User, today: dt.date) -> bool:
        """Каникулы ли сегодня — только при включённом тихом режиме.

        Каникулы — промежутки между периодами школы, а без них общепринятые
        даты (domain/vacations.py). Не узнали — не молчим."""
        if not user.notifications.vacation_quiet:
            return False
        if self._vacation_day is not None and self._vacation_day[0] == today:
            return self._vacation_day[1]
        try:
            prefs = await self._state.user_prefs(user.telegram_id)
            school: list[Quarter] = []
            if ((prefs.get("periods") or {}).get("scheme") or "auto") == "auto":
                school = periods_from_terms(await self._diary.fetch_terms(user) or [], today)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — без периодов школы есть общепринятые даты
            logger.debug("Периоды для тихого режима %s не получены: %s", user.telegram_id, exc)
            school = []
        quiet = on_vacation(today, vacations(today, school, "school" if school else "default")) is not None
        self._vacation_day = (today, quiet)
        return quiet

    async def _grade_risks(self, user: User, events: list[MarkEvent], today: dt.date) -> list:
        """Предметы, где итоговая за текущий период из-за новых оценок стала ниже.

        Окно фоновой проверки — несколько недель, а итоговая считается за
        весь период, поэтому период запрашивается отдельно; сбой здесь не
        мешает остальным уведомлениям.
        """
        try:
            year, prefs = await self._periods(user, today)
            period = current_period(year, today)
            relevant = [e for e in events if period.covers(e.record.date)]
            if not relevant:
                return []
            days = await self._diary.fetch_period(user, period.start, today, today=today)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — предупреждение вторично
            logger.info("Риск по оценкам для %s не посчитан: %s", user.telegram_id, exc)
            return []
        after = [
            r for d in days for r in d.marks
            if period.covers(r.date) and r.mark and user.filters.allows_subject(r.subject)
        ]
        new = {e.record.identity for e in relevant if e.kind is MarkKind.NEW}
        changed = {e.record.identity: e.old_mark for e in relevant if e.kind is MarkKind.CHANGED}
        before = []
        for record in after:
            if record.identity in new:
                continue
            if record.identity in changed:
                record = replace(record, mark=changed[record.identity])
            before.append(record)
        before.extend(e.record for e in relevant if e.kind is MarkKind.DELETED)
        return insights.grade_drops(before, after, prefs)

    async def _check_children(self, user: User) -> None:
        """Остальные дети аккаунта, если родитель следит за всеми.

        Состояние каждого — отдельно (child_state), уведомления подписаны
        именем. Сбой у одного ребёнка не мешает ни другим, ни выбранному.
        """
        if not (user.notifications.all_children and len(user.available_students) > 1):
            return
        now = self._clock()
        if self._last_children_check is not None and now - self._last_children_check < CHILD_CHECK_INTERVAL:
            return
        self._last_children_check = now
        for student in user.available_students:
            if student.id == user.selected_student_id:
                continue
            child = replace(user, selected_student_id=student.id, student_name=student.name)
            try:
                await self._check_child(child, student)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — другой ребёнок важнее одного сбоя
                logger.info("Проверка ребёнка %s у %s не прошла: %s", student.id, user.telegram_id, exc)

    async def _check_child(self, child: User, student: Student) -> None:
        days = await self._diary.fetch_diary(child)
        marks = [record for day in days for record in day.marks]
        homework = [record for day in days for record in day.homework]
        saved = await self._state.load_child(child.telegram_id, student.id)
        first_run = saved is None
        result = diff_marks(saved["marks"] if saved else {}, marks, filters=child.filters, first_run=first_run)
        if result.pending_deletes:
            confirmed = await self._confirm_deletes(child, result.pending_deletes)
            result.events.extend(confirm_deletions(result, confirmed))
        known_homework = saved["homework"] if saved else set()
        fresh_homework, current_homework = diff_homework(known_homework, homework, first_run=first_run)
        all_homework = known_homework | current_homework
        if len(all_homework) > CHILD_HOMEWORK_LIMIT:
            all_homework = current_homework
        absences: set[str] | None = None
        fresh_absences: list[AbsenceRecord] = []
        if child.effective_api_mode is ApiMode.MOBILE:
            current = absences_from_days(days)
            known_absences = saved["absences"] if saved else set()
            absences = known_absences | {a.identity for a in current}
            if not first_run:
                earliest = _local_now(child).date() - dt.timedelta(days=ABSENCE_NOTIFY_DAYS)
                fresh_absences = [a for a in current if a.identity not in known_absences and a.date >= earliest]

        prefs = child.notifications
        outbox: list[tuple[str, str]] = []
        mark_events = [e for e in result.events if _mark_event_wanted(e, prefs)]
        if mark_events:
            outbox.append((OutboxKind.MARKS.value, encode(OutboxKind.MARKS, mark_events)))
        if fresh_homework and prefs.homework:
            outbox.append((OutboxKind.HOMEWORK.value, encode(OutboxKind.HOMEWORK, fresh_homework)))
        if fresh_absences and prefs.absences:
            outbox.append((OutboxKind.ABSENCES.value, encode(OutboxKind.ABSENCES, fresh_absences)))
        await self._state.save_child_check(
            child.telegram_id, student.id, student.name,
            tracked=result.tracked, homework=all_homework, absences=absences, outbox=outbox,
        )

    async def _record_status(self, *, ok: bool, delay: float, error: str = "") -> None:
        next_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(seconds=delay)
        try:
            await self._state.set_watch_status(
                self._user_id, ok=ok, error=error, next_check_at=next_at
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — статус вторичен по отношению к проверке
            logger.debug("Статус проверки %s не записан: %s", self._user_id, exc)

    async def _check_term_marks(self, user: User) -> None:
        """Новые четвертные, годовые и итоговые оценки — как пуши «1 четверть Алгебра - 5».

        Только мобильный режим: в веб-дневнике официальных итоговых нет.
        Первый снимок (и снимок другого ученика) запоминается молча.
        """
        if user.effective_api_mode is not ApiMode.MOBILE:
            return
        now = self._clock()
        if self._last_term_marks is not None and now - self._last_term_marks < TERM_MARKS_INTERVAL:
            return
        self._last_term_marks = now
        try:
            totals = await self._diary.fetch_totals(user)
            if not totals:
                return
            current = {
                f"{subject}\x1f{period}": mark
                for subject, item in totals.items()
                for period, mark in [
                    *((key, (term or {}).get("mark") or "") for key, term in (item.get("terms") or {}).items()),
                    ("year", item.get("year") or ""),
                    ("final", item.get("final") or ""),
                ]
                if mark
            }
            student = str(user.selected_student_id or user.student_name or "")
            try:
                saved = json.loads(await self._state.digest_period(user.telegram_id, "term-marks") or "{}")
            except ValueError:
                saved = {}
            known = saved.get("marks") if saved.get("student") == student else None
            fresh = []
            if isinstance(known, dict):
                for key, mark in current.items():
                    if known.get(key) != mark and user.notifications.wants_mark(mark):
                        subject, period = key.split("\x1f", 1)
                        fresh.append({"subject": subject, "period": period, "mark": mark})
            outbox = []
            if fresh and user.notifications.grades and not await self._on_vacation(user, _local_now(user).date()):
                outbox.append((OutboxKind.TERMS.value, encode(OutboxKind.TERMS, fresh)))
            await self._state.save_term_marks(
                user.telegram_id,
                json.dumps({"student": student, "marks": current}, ensure_ascii=False),
                outbox,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — итоговые вторичны, проверку не роняют
            logger.info("Итоговые оценки %s не проверены: %s", user.telegram_id, exc)

    async def _snapshot_class_averages(self, user: User) -> None:
        """Снимок среднего балла класса по предметам текущего периода.

        «Сетевой город» отдаёт средний только на сейчас; история копится здесь,
        и экран предмета показывает, как он менялся после работ.
        """
        if user.effective_api_mode is not ApiMode.MOBILE:
            return
        now = self._clock()
        recent = self._last_class_averages is not None and now - self._last_class_averages < CLASS_AVERAGES_INTERVAL
        if recent and not self._class_averages_due:
            return
        self._last_class_averages = now
        self._class_averages_due = False
        try:
            today = _local_now(user).date()
            year, _ = await self._periods(user, today)
            quarter = current_period(year, today).key
            averages = await self._diary.fetch_class_averages(user, quarter)
            if averages:
                await self._state.record_class_averages(
                    user.telegram_id,
                    str(user.selected_student_id or user.student_name or ""),
                    class_average_period(quarter, today),
                    averages,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — средние класса вторичны, проверку не роняют
            logger.info("Средние класса %s не сняты: %s", user.telegram_id, exc)

    async def _check_token(self, user: User) -> None:
        """Продлить мобильный токен или заранее предупредить, что он истекает.

        Предупреждение уходит один раз: отметка хранится в базе, а не в
        памяти, иначе каждый перезапуск бота повторял бы его. Она
        сбрасывается, когда токен снова в порядке (продлён или выдан заново).
        """
        if user.effective_api_mode is not ApiMode.MOBILE:
            return
        now = self._clock()
        if self._last_token_check is not None and now - self._last_token_check < TOKEN_CHECK_INTERVAL:
            return
        self._last_token_check = now

        try:
            check = await self._diary.check_mobile_token(user)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — срок токена не повод ронять проверку оценок
            logger.info("Срок токена %s не проверен: %s", user.telegram_id, exc)
            return
        if check is None:
            return

        if check.state is not TokenState.EXPIRING:
            if await self._state.token_warned_at(user.telegram_id) is not None:
                await self._state.set_token_warned(user.telegram_id, None)
            return
        if await self._state.token_warned_at(user.telegram_id) is not None:
            return
        if self._is_quiet(user):
            # Не будим ночью: следующая проверка через несколько часов, срока хватает.
            return
        await self._notifier.send_token_expiring(user, check.expires_at)
        await self._state.set_token_warned(user.telegram_id, dt.datetime.now())

    async def _keep_session(self, user: User) -> None:
        """Госуслуги в веб-режиме: пока сессия жива (проверка только что
        прошла), обменять её на мобильный токен — он продлевается сам, а
        веб-сессию Госуслуг без человека не вернуть. Не чаще раза в 6 часов."""
        if self._keep_signed_in is None or not user.credentials.login_type.is_gosuslugi:
            return
        if user.api_mode is not ApiMode.WEB:
            return
        now = self._clock()
        if self._last_keep_attempt is not None and now - self._last_keep_attempt < TOKEN_CHECK_INTERVAL:
            return
        self._last_keep_attempt = now
        try:
            switched = await self._keep_signed_in(user)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — не повод ронять проверку оценок
            logger.info("Вход Госуслуг %s не переведён на токен: %s", user.telegram_id, exc)
            return
        if switched:
            await self._notifier.send_text(
                user,
                "🔐 Вход через Госуслуги теперь держится сам: дневник переведён на мобильное API, "
                "его токен бот продлевает без вас. Выключить: настройки приложения → "
                "«Не выходить из аккаунта».",
                family=False,
            )

    async def _diff_absences(
        self, user: User, days: list, *, first_run: bool
    ) -> tuple[set[str] | None, list[AbsenceRecord]]:
        """Все виденные отметки посещаемости и те, которых ещё не видели.

        Только мобильный режим: в веб-дневнике отметок нет. Состояние
        обновляется всегда, даже при выключенных уведомлениях, — тогда
        включение тумблера не выдаст старые пропуски за новые.
        """
        if user.effective_api_mode is not ApiMode.MOBILE:
            return None, []
        current = absences_from_days(days)
        known = await self._state.load_absences(user.telegram_id)
        identities = {a.identity for a in current}
        if first_run:
            return identities, []
        earliest = _local_now(user).date() - dt.timedelta(days=ABSENCE_NOTIFY_DAYS)
        return identities, [a for a in current if a.identity not in known and a.date >= earliest]

    def _full_sync_due(self, events: RecentEvents) -> bool:
        """Нужно ли сверять дневник целиком.

        Да — если в ленте появилось то, чего мы не видели, если полной
        сверки ещё не было или она была давно.
        """
        if self._event_keys is None or self._last_full_sync is None:
            return True
        if self._clock() - self._last_full_sync >= FULL_SYNC_INTERVAL:
            return True
        return bool(events.keys - self._event_keys)

    def _note_full_sync(self, events: RecentEvents | None) -> None:
        """Запомнить ленту, с которой сверили дневник."""
        if events is None:
            return
        self._event_keys = events.keys
        self._last_full_sync = self._clock()

    async def _refresh_student_if_needed(self, user: User) -> User:
        """Подтянуть имя ученика, если в базе лежит заглушка.

        Записи, созданные до исправления разбора `student/diary/init`,
        хранят «Ученик <id>» вместо имени. Ждать, пока человек заново
        пройдёт /login, значит не починить их никогда.
        """
        if not is_placeholder_name(user.student_name):
            return user
        try:
            updated = await self._diary.sync_students(user)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Имя — украшение; ради него нельзя терять проверку оценок.
            # Поэтому здесь широкий перехват, а не только NetSchoolError.
            logger.debug("Не удалось обновить имя ученика %s: %s", user.telegram_id, exc)
            return user
        if updated.student_name != user.student_name:
            logger.info("Имя ученика %s уточнено: %s", user.telegram_id, updated.student_name)
        return updated

    async def _confirm_deletes(self, user: User, candidates: list) -> set[str]:
        """Проверить пропавшие оценки отдельным запросом за нужную неделю.

        Подтверждение делается по неделям, а не по каждой оценке: у одной
        недели один запрос, даже если пропало десять работ.
        """
        confirmed: set[str] = set()
        by_week: dict[dt.date, list] = {}
        for record in candidates:
            by_week.setdefault(record.date, []).append(record)

        for week_day, records in by_week.items():
            try:
                present = await self._diary.marks_present_in_week(user, week_day)
            except NetSchoolError as exc:
                # Не смогли проверить — значит, не удаляем. Счётчик пропаж
                # сохранится, и в следующий раз попробуем снова.
                logger.info("Не удалось подтвердить удаление за %s: %s", week_day, exc.reason)
                continue
            for record in records:
                if record.loose_identity not in present:
                    confirmed.add(record.identity)
        return confirmed

    async def _handle_error(self, user: User, error: NetSchoolError) -> float:
        # Памяти процесса недостаточно: перезапуски и чередование ошибок
        # не должны снова слать просьбу войти при той же неисправности.
        esia_expired = user.credentials.login_type.is_gosuslugi and error.reason.needs_relogin
        should_notify = not esia_expired and (error.reason.needs_relogin or not error.reason.is_retryable)
        if error.reason is Reason.SERVER_UNAVAILABLE:
            now = self._clock()
            if self._down_since is None:
                self._down_since = now
            should_notify = now - self._down_since >= SERVER_DOWN_NOTICE_AFTER
        else:
            self._down_since = None
        if should_notify and await self._state.claim_error_notice(user.telegram_id, error.reason.value):
            try:
                await self._notifier.send_error(user, error)
            except BaseException:
                await self._state.release_error_notice(user.telegram_id, error.reason.value)
                raise

        if error.reason.needs_relogin:
            # Без участия человека дальше двигаться некуда.
            return float(MAX_BACKOFF)

        self._backoff = min(max(self._backoff * 2, float(user.check_interval)), MAX_BACKOFF)
        return self._backoff

    def _is_quiet(self, user: User) -> bool:
        return user.quiet_hours.covers(_local_now(user).time())


class WatcherRegistry:
    """Реестр запущенных проверок: по одной задаче на пользователя.

    Раньше задачи лежали в словаре в `bot/runtime.py`, куда писали и веб, и
    бот, и никто не гарантировал, что для пользователя не запустится вторая
    проверка параллельно с первой.
    """

    def __init__(
        self,
        *,
        users: UserRepository,
        state: MarkStateRepository,
        diary: DiaryService,
        notifier: Notifier | None = None,
        notes: Any = None,
    ) -> None:
        self._users = users
        self._state = state
        self._diary = diary
        self._notifier = notifier
        self._notes = notes
        # Простои школьных серверов — админу (общий на все проверки).
        self.outages = OutageMonitor()
        # Сигнал открытому приложению о новом (задаёт веб-сервер).
        self.live: Callable[[int, dict], Awaitable[None]] | None = None
        # Увиденные задания — хранилищу вложений (задаёт main.py).
        self.homework_seen: Callable[[User, list[HomeworkRecord]], None] | None = None
        # «Не выходить из аккаунта» для Госуслуг и разбор недели от помощника
        # (задаёт AppContext.create).
        self.keep_signed_in: Callable[[User], Awaitable[bool]] | None = None
        self.weekly_review: Callable[[str], Awaitable[str | None]] | None = None
        self._tasks: dict[int, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    @property
    def notifier(self) -> Notifier | None:
        """Тот же канал уведомлений, что у проверок, — для напоминаний."""
        return self._notifier

    def attach_notifier(self, notifier: Notifier) -> None:
        """Задать способ доставки уведомлений.

        Отдельный шаг нужен потому, что уведомитель знает про Telegram-бота,
        а бот создаётся позже реестра. Раньше этот круг разрывали ленивыми
        импортами внутри функций, из-за чего порядок инициализации был
        неявным и ломался при малейшей перестановке.
        """
        self._notifier = notifier

    async def start(self, user_id: int) -> None:
        """Запустить проверку. Повторный вызов перезапускает её."""
        if self._notifier is None:
            raise RuntimeError(
                "Уведомитель не задан: вызовите WatcherRegistry.attach_notifier()"
            )
        async with self._lock:
            await self._cancel(user_id)
            watcher = UserWatcher(
                user_id,
                users=self._users,
                state=self._state,
                diary=self._diary,
                notifier=self._notifier,
                outages=self.outages,
                live=self._signal,
                notes=self._notes,
                homework_seen=self.homework_seen,
                keep_signed_in=self.keep_signed_in,
                weekly_review=self.weekly_review,
            )
            task = asyncio.create_task(watcher.run(), name=f"watch-{user_id}")
            self._tasks[user_id] = task
            logger.info("Запущена проверка оценок для %s", user_id)

    async def _signal(self, user_id: int, event: dict) -> None:
        # Берём live в момент события: проверки стартуют раньше веб-сервера.
        if self.live is not None:
            await self.live(user_id, event)

    async def stop(self, user_id: int) -> None:
        async with self._lock:
            await self._cancel(user_id)

    async def start_all(self) -> int:
        users = await self._users.all_active()
        for user in users:
            if user.ready_to_check:
                await self.start(user.telegram_id)
        return len(self._tasks)

    async def stop_all(self) -> None:
        async with self._lock:
            for user_id in list(self._tasks):
                await self._cancel(user_id)

    @property
    def running(self) -> set[int]:
        return {uid for uid, task in self._tasks.items() if not task.done()}

    async def _cancel(self, user_id: int) -> None:
        task = self._tasks.pop(user_id, None)
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
