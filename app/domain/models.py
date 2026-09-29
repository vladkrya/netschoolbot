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

    WEB — веб-API сайта (netschoolpy `NetSchool`), режим по умолчанию.
    MOBILE — API приложения NetSchool (`MobileNetSchool`): токен на 60 дней
    и время публикации заданий. Переключается командой /changeapi.
    """

    WEB = "web"
    MOBILE = "mobile"

    @property
    def title(self) -> str:
        return "веб-API сайта" if self is ApiMode.WEB else "мобильное API"

    @classmethod
    def parse(cls, value: Any) -> "ApiMode":
        try:
            return cls(str(value or "").strip().lower())
        except ValueError:
            return cls.WEB


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
    # Новые пропуски — только мобильный режим: в веб-дневнике отметок посещаемости нет.
    absences: bool = False


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

    @property
    def usable(self) -> bool:
        if self.login_type.is_gosuslugi:
            # Вход по Госуслугам живёт на сохранённой сессии; логин/пароль
            # нужны только для автоповтора при обычном входе.
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
    api_mode: ApiMode = ApiMode.WEB
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
    def label(self) -> str:
        return self.student_name or self.display_name or f"ID {self.telegram_id}"

    def with_interval(self, seconds: int) -> "User":
        return replace(self, check_interval=clamp_interval(seconds))


def clamp_interval(seconds: int) -> int:
    return max(MIN_CHECK_INTERVAL, min(MAX_CHECK_INTERVAL, int(seconds)))


def normalize(value: Any) -> str:
    """Приведение названия предмета/работы к сравнимому виду.

    Раньше это были две одинаковые функции `_normalize_title` и
    `_normalize_subject` в utils плюс третья копия в miniapp.
    """
    return " ".join(str(value or "").lower().split())
