"""Доменные модели.

Раньше всё было `dict[str, Any]`, который каждый модуль трактовал по-своему:
`user_data.get("filters", {}).get("exclude")` встречалось в пяти местах с
разными дефолтами. Здесь форма данных зафиксирована один раз.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError



DEFAULT_TIMEZONE = "Europe/Moscow"

# Пояса регионов, где работает «Сетевой город»: город — для подписи кнопки.
TIMEZONES: tuple[tuple[str, str], ...] = (
    ("Europe/Kaliningrad", "Калининград"),
    ("Europe/Moscow", "Москва"),
    ("Europe/Samara", "Самара"),
    ("Asia/Yekaterinburg", "Екатеринбург"),
    ("Asia/Omsk", "Омск"),
    ("Asia/Novosibirsk", "Новосибирск"),
    ("Asia/Krasnoyarsk", "Красноярск"),
    ("Asia/Irkutsk", "Иркутск"),
    ("Asia/Yakutsk", "Якутск"),
    ("Asia/Vladivostok", "Владивосток"),
    ("Asia/Magadan", "Магадан"),
    ("Asia/Kamchatka", "Камчатка"),
)


def zone(name: str) -> dt.tzinfo:
    """Пояс по имени; неизвестное имя — Москва, а не падение проверки."""
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def timezone_label(name: str) -> str:
    for key, city in TIMEZONES:
        if key == name:
            return city
    return name


# Границы пользовательского интервала проверки. Живут в домене, а не в
# настройках: это правило предметной области, а не параметр развёртывания.
MIN_CHECK_INTERVAL = 180
MAX_CHECK_INTERVAL = 10800


class LoginType(str, Enum):
    PASSWORD = "password"
    ESIA = "esia"
    ESIA_QR = "esia_qr"

    @property
    def is_gosuslugi(self) -> bool:
        return self in (LoginType.ESIA, LoginType.ESIA_QR)

    @classmethod
    def parse(cls, value: Any) -> "LoginType":
        try:
            return cls(str(value or "").strip().lower())
        except ValueError:
            return cls.PASSWORD


class ApiMode(str, Enum):
    """Через какое API «Сетевого города» бот получает данные пользователя.

    WEB — веб-API сайта (netschoolpy `NetSchool`), резервный режим.
    MOBILE — API приложения NetSchool (`MobileNetSchool`): токен на 60 дней
    и время публикации заданий. Режим по умолчанию; смена через /changeapi.
    """

    WEB = "web"
    MOBILE = "mobile"
    # Не «Сетевой город», а «Госуслуги Моя школа» (библиотека myschoolpy).
    MYSCHOOL = "myschool"

    @property
    def title(self) -> str:
        if self is ApiMode.MYSCHOOL:
            return "«Моя школа»"
        return "веб-API сайта" if self is ApiMode.WEB else "мобильное API"

    @classmethod
    def parse(cls, value: Any) -> "ApiMode":
        try:
            return cls(str(value or "").strip().lower())
        except ValueError:
            return cls.MOBILE


class MarkKind(str, Enum):
    """Что именно произошло с оценкой — вместо строковых литералов в коде."""

    NEW = "new"
    CHANGED = "changed"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class Student:
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class QuietHours:
    """Окно, в которое уведомления не отправляются. Может пересекать полночь."""

    start: dt.time | None = None
    end: dt.time | None = None

    @property
    def enabled(self) -> bool:
        return self.start is not None and self.end is not None

    def covers(self, moment: dt.time) -> bool:
        if not self.enabled:
            return False
        assert self.start is not None and self.end is not None
        if self.start == self.end:
            # Вырожденный случай: одинаковые границы = круглосуточная тишина.
            return True
        if self.start < self.end:
            return self.start <= moment < self.end
        # Окно через полночь, например 22:00–07:00.
        return moment >= self.start or moment < self.end

    def as_text(self) -> str:
        if not self.enabled:
            return "выключены"
        assert self.start is not None and self.end is not None
        return f"{self.start:%H:%M}–{self.end:%H:%M}"


@dataclass(frozen=True, slots=True)
class NotificationPrefs:
    grades: bool = True
    changes: bool = True
    deletes: bool = True
    homework: bool = True
    mail: bool = False
    weekly_summary: bool = False
    # Вечером — расписание и задания на следующий учебный день.
    evening_summary: bool = False
    # Замены, отмены и смена кабинета на ближайшую неделю.
    schedule: bool = False
    # Новые пропуски — только мобильный режим: в веб-дневнике отметок посещаемости нет.
    absences: bool = False
    # Канал: присылать ли всё это в Telegram. Push в браузер включается
    # подпиской на устройстве, и у кого он есть, могут выключить Telegram.
    telegram: bool = True
    # Напоминание в `reminder_hour` накануне учебного дня: что из заданий
    # на завтра ещё не отмечено выполненным.
    homework_reminder: bool = False
    reminder_hour: int = 18
    # Контрольные и другие работы с большим весом — за один-два дня.
    test_reminder: bool = False
    # Средний по предмету опустился ниже порога оценки.
    grade_risk: bool = False
    # Сводка за месяц (1-го числа) и итоги учебного периода.
    monthly_summary: bool = False
    # Следить за всеми детьми аккаунта, а не только за выбранным.
    all_children: bool = False
    # Разбор недели от ИИ-помощника в понедельничной сводке: где просел,
    # что подтянуть, какие работы впереди. Нужна включённая сводка недели.
    weekly_review: bool = False
    # Тихий режим на каникулы: в каникулы и летом уведомления не приходят.
    vacation_quiet: bool = False
    # О каких новых оценках сообщать: пусто — обо всех, иначе только об этих
    # значениях (например, (2, 3)). Изменения и удаления — всегда.
    marks_filter: tuple[int, ...] = ()

    def wants_mark(self, mark: str) -> bool:
        """Подходит ли новая оценка под фильтр значений."""
        if not self.marks_filter:
            return True
        from .records import mark_to_int

        value = mark_to_int(mark)
        # «Зачёт», «н/а» и прочее без числа фильтр не отсекает.
        return value is None or value in self.marks_filter


@dataclass(frozen=True, slots=True)
class Filters:
    """Что не показывать. `exclude_titles` — типы работ, `include_subjects` —
    белый список предметов (пустой = все предметы)."""

    exclude_titles: frozenset[str] = frozenset()
    include_subjects: frozenset[str] = frozenset()

    def allows_subject(self, subject: str) -> bool:
        if not self.include_subjects:
            return True
        return normalize(subject) in self.include_subjects

    def allows_title(self, *titles: str) -> bool:
        if not self.exclude_titles:
            return True
        return not any(normalize(title) in self.exclude_titles for title in titles if title)


@dataclass(frozen=True, slots=True)
class School:
    """Куда ходит конкретный пользователь. Значения по умолчанию нет."""

    url: str
    name: str

    @property
    def configured(self) -> bool:
        return bool(self.url and self.name)


@dataclass(frozen=True, slots=True)
class Credentials:
    login_type: LoginType
    login: str = ""
    password: str = ""
    remember: bool = True

    @property
    def stored(self) -> bool:
        """Можно ли хранить логин и пароль. Госуслуги — никогда: их пароль
        нужен только на один вход, дальше дневник живёт на сессии."""
        return self.remember and not self.login_type.is_gosuslugi

    @property
    def can_relogin(self) -> bool:
        return self.remember and not self.login_type.is_gosuslugi and bool(self.login and self.password)

    @property
    def usable(self) -> bool:
        if self.login_type.is_gosuslugi or not self.remember:
            # Госуслуги и вход без сохранения пароля работают на сессии.
            # После её истечения понадобится ручной вход.
            return True
        return bool(self.login and self.password)


@dataclass(frozen=True, slots=True)
class User:
    telegram_id: int
    school: School
    credentials: Credentials
    enabled: bool = False
    display_name: str = ""
    student_name: str = ""
    selected_student_id: int | None = None
    available_students: tuple[Student, ...] = ()
    check_interval: int = 300
    notifications: NotificationPrefs = field(default_factory=NotificationPrefs)
    filters: Filters = field(default_factory=Filters)
    quiet_hours: QuietHours = field(default_factory=QuietHours)
    api_mode: ApiMode = ApiMode.MOBILE
    # Часовой пояс IANA: регионы разные, а тихие часы и «сегодня» — местные.
    timezone: str = DEFAULT_TIMEZONE
    created_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None

    @property
    def ready_to_check(self) -> bool:
        """Можно ли запускать для пользователя фоновую проверку оценок."""
        return self.enabled and self.school.configured and self.credentials.usable

    @property
    def effective_api_mode(self) -> ApiMode:
        """Через какое API на деле ходить за данными.

        Сейчас совпадает с выбором в базе. Отдельное свойство осталось, чтобы
        код спрашивал «через что ходить», а не «что выбрано».

        Пользователю Госуслуг мобильный режим достаётся только вместе с
        токеном: сразу после входа `_finish_login` меняет живую веб-сессию
        на мобильный токен, а если не вышло — возвращает веб-режим. Иначе
        он застревал бы в цикле «войдите заново».
        """
        return self.api_mode

    @property
    def tz(self) -> dt.tzinfo:
        return zone(self.timezone)

    def now(self) -> dt.datetime:
        """Местное время пользователя."""
        return dt.datetime.now(self.tz)

    def today(self) -> dt.date:
        return self.now().date()

    @property
    def label(self) -> str:
        return self.student_name or self.display_name or f"ID {self.telegram_id}"

    def with_interval(self, seconds: int) -> "User":
        return replace(self, check_interval=clamp_interval(seconds))


REMINDER_HOURS = (15, 16, 17, 18, 19, 20, 21)


def clamp_reminder_hour(hour: int) -> int:
    return max(REMINDER_HOURS[0], min(REMINDER_HOURS[-1], int(hour)))


def parse_marks_filter(raw: str) -> tuple[int, ...]:
    """«2,3» из базы → (2, 3); мусор пропускается."""
    values = {int(part) for part in str(raw or "").split(",") if part.strip().isdigit()}
    return tuple(sorted(v for v in values if 1 <= v <= 5))


def clamp_interval(seconds: int) -> int:
    return max(MIN_CHECK_INTERVAL, min(MAX_CHECK_INTERVAL, int(seconds)))


def normalize(value: Any) -> str:
    """Приведение названия предмета/работы к сравнимому виду.

    Раньше это были две одинаковые функции `_normalize_title` и
    `_normalize_subject` в utils плюс третья копия в miniapp.
    """
    return " ".join(str(value or "").lower().split())
