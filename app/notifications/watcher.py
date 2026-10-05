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
from collections.abc import Callable
from typing import Protocol

from ..db.repositories import MarkStateRepository, UserRepository
from ..domain import formatting
from ..domain.models import ApiMode, MarkKind, NotificationPrefs, User
from ..domain.records import AbsenceRecord, HomeworkRecord, MarkEvent, RecentEvents
from ..netschool.errors import NetSchoolError, Reason
from ..netschool.mapping import absences_from_days, is_placeholder_name
from ..netschool.service import DiaryService, TokenState
from .diff import confirm_deletions, diff_homework, diff_marks
from .digests import DigestKind, evening_period, previous_week, weekly_period
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

# В мобильном режиме каждый проход смотрит лёгкую ленту событий, а полный
# дневник сверяет только при новых событиях и не реже этого срока: лента не
# показывает удалённые и исправленные давние оценки.
FULL_SYNC_INTERVAL = 3600.0

# Об отметке посещаемости сообщаем, только если урок был не раньше этого числа
# дней назад: так включение уведомлений или смена API не засыпают человека
# пропусками за прошлые недели.
ABSENCE_NOTIFY_DAYS = 7

# Срок мобильного токена меряется в днях — проверять его каждый проход незачем.
TOKEN_CHECK_INTERVAL = 6 * 3600.0


# Отложенное уведомление живёт в очереди не дольше этого: утренняя досылка
# после тихих часов укладывается, а недельной давности новость уже не нужна.
OUTBOX_MAX_AGE = dt.timedelta(days=2)
OUTBOX_MAX_ATTEMPTS = 20


def _local_now(user: User) -> dt.datetime:
    """Местное время пользователя. Отдельной функцией — чтобы тесты могли его подменить."""
    return user.now()


def _mark_event_wanted(event: MarkEvent, prefs: NotificationPrefs) -> bool:
    if event.kind is MarkKind.NEW:
        return prefs.grades
    if event.kind is MarkKind.CHANGED:
        return prefs.changes
    return prefs.deletes


class Notifier(Protocol):
    """Куда уходят уведомления. Позволяет тестировать цикл без Telegram."""

    async def send_mark_events(self, user: User, events: list[MarkEvent]) -> None: ...

    async def send_homework(self, user: User, items: list[HomeworkRecord]) -> None: ...

    async def send_absences(self, user: User, items: list[AbsenceRecord]) -> None: ...

    async def send_token_expiring(self, user: User, expires_at: dt.datetime) -> None: ...

    async def send_error(self, user: User, error: NetSchoolError) -> None: ...

    async def send_text(self, user: User, text: str) -> None: ...


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
    ) -> None:
        self._user_id = user_id
        self._users = users
        self._state = state
        self._diary = diary
        self._notifier = notifier
        self._backoff = 0.0
        # Об одной и той же проблеме сообщаем один раз, а не каждый круг.
        self._reported_reason: Reason | None = None
        # Что уже видели в ленте событий и когда в последний раз сверяли
        # дневник целиком. В памяти: после перезапуска первая проверка
        # просто пройдёт полным путём.
        self._clock = clock
        self._event_keys: frozenset[str] | None = None
        self._last_full_sync: float | None = None
        self._last_token_check: float | None = None

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

        try:
            await self._check(user)
        except NetSchoolError as exc:
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

        # Очередь разбирается после проверки, а не только при новых событиях:
        # так после тихих часов или сбоя Telegram отложенное уходит само.
        await self._flush_outbox(user)
        await self._check_token(user)
        await self._send_digests(user)

        self._backoff = 0.0
        self._reported_reason = None
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
            try:
                await self._send(user, kind, items)
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
        elif kind is OutboxKind.SCHEDULE:
            await self._notifier.send_text(user, formatting.schedule_changes(items))

    async def _send_digests(self, user: User) -> None:
        """Вечерняя сводка на завтра и недельная по понедельникам.

        Сбой сводки не трогает проверку оценок: отметка о периоде ставится
        только после отправки, поэтому следующий проход попробует снова.
        """
        if self._is_quiet(user):
            return
        now = _local_now(user)
        jobs = []
        if user.notifications.evening_summary and (period := evening_period(now)):
            jobs.append((DigestKind.EVENING, period))
        if user.notifications.weekly_summary and (period := weekly_period(now)):
            jobs.append((DigestKind.WEEKLY, period))
        for kind, period in jobs:
            if await self._state.digest_period(user.telegram_id, kind.value) == period:
                continue
            try:
                text = await self._digest_text(user, kind, dt.date.fromisoformat(period))
                if text:
                    await self._notifier.send_text(user, text)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — сводка вторична
                logger.info("Сводка %s для %s не отправлена: %s", kind.value, user.telegram_id, exc)
                continue
            await self._state.set_digest_period(user.telegram_id, kind.value, period)

    async def _digest_text(self, user: User, kind: DigestKind, day: dt.date) -> str | None:
        if kind is DigestKind.EVENING:
            days = await self._diary.fetch_period(user, day, day, today=_local_now(user).date())
            found = next((d for d in days if d.day == day), None)
            return formatting.evening_summary(found, day, tz=user.tz)
        start, end = previous_week(day)
        days = await self._diary.fetch_period(user, start, end, today=day)
        week = [r for d in days for r in d.marks if start <= r.date <= end]
        return formatting.weekly_summary(week, start, end)

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
        if error.reason is not self._reported_reason:
            # Первое появление проблемы — сообщаем. Повторы молчат, иначе
            # недоступная школа шлёт сообщение каждые пять минут.
            self._reported_reason = error.reason
            # Вход через Госуслуги требует действий пользователя. Фоновая
            # проверка не должна снова писать об этом после каждого рестарта
            # бота или перезапуска проверки; при ручном запросе ошибка видна.
            esia_expired = user.credentials.login_type.is_gosuslugi and error.reason.needs_relogin
            if not esia_expired and (error.reason.needs_relogin or not error.reason.is_retryable):
                await self._notifier.send_error(user, error)

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
    ) -> None:
        self._users = users
        self._state = state
        self._diary = diary
        self._notifier = notifier
        self._tasks: dict[int, asyncio.Task] = {}
        self._lock = asyncio.Lock()

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
            )
            task = asyncio.create_task(watcher.run(), name=f"watch-{user_id}")
            self._tasks[user_id] = task
            logger.info("Запущена проверка оценок для %s", user_id)

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
