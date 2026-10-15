"""Отчёты файлом Excel — общие для бота (/export) и приложения: оценки,
сводка по предметам, динамика, контрольные, пропуски, домашние задания,
журнал уроков и обычное расписание недели."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .domain.export import (
    absences_workbook, dynamics_workbook, export_filename, grades_workbook, homework_workbook,
    lessons_workbook, schedule_workbook, subjects_workbook, control_works_workbook,
)
from .domain.models import User
from .domain.periods import current_period, resolve_periods
from .netschool.errors import NetSchoolError

if TYPE_CHECKING:
    from .context import AppContext

SCOPES = ("period", "year")
# Отчёт → начало имени файла.
REPORTS = {
    "grades": "Оценки", "subjects": "Предметы", "dynamics": "Динамика", "tests": "Контрольные",
    "absences": "Пропуски", "homework": "Задания", "lessons": "Уроки", "schedule": "Расписание",
}


async def export_grades(app: "AppContext", user: User, scope: str = "period") -> tuple[bytes, str]:
    """Книга Excel с оценками за текущий период или весь учебный год и имя файла."""
    return await export_report(app, user, scope, "grades")


async def export_report(app: "AppContext", user: User, scope: str = "period",
                        report: str = "grades") -> tuple[bytes, str]:
    """Отчёт `report` за текущий период или весь учебный год и имя файла."""
    today = user.today()
    prefs = await app.miniapp.prefs(user.telegram_id)
    setting = prefs.get("periods") or {}
    terms = None
    if (setting.get("scheme") or "auto") == "auto":
        try:
            terms = await app.diary.fetch_terms(user)
        except NetSchoolError:
            terms = None
    year = resolve_periods(setting, terms, today)
    period = current_period(year, today)
    if scope == "year":
        start = year[0].start
        title = f"{start.year}-{start.year + 1} учебный год"
    else:
        start = period.start
        title = period.label
    end = min(today, period.end)
    days = await app.diary.fetch_period(user, start, end, today=today)
    student = user.student_name
    if report == "absences":
        data = absences_workbook(days, title=title, student=student)
    elif report == "homework":
        data = homework_workbook(days, title=title, student=student)
    elif report == "lessons":
        data = lessons_workbook(days, title=title, student=student)
    elif report == "subjects":
        data = subjects_workbook(days, title=title, student=student, prefs=prefs)
    elif report == "dynamics":
        data = dynamics_workbook(days, title=title, student=student)
    elif report == "tests":
        data = control_works_workbook(days, title=title, student=student)
    elif report == "schedule":
        data = schedule_workbook(days, title=title, student=student)
    else:
        records = [r for d in days for r in d.marks if start <= r.date <= end]
        data = grades_workbook(records, title=title, student=student, prefs=prefs, periods=year)
    return data, export_filename(student, title, REPORTS.get(report, "Оценки"))
