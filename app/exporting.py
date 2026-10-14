"""Выгрузка оценок файлом — общая для бота (/export) и приложения."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .domain.export import export_filename, grades_workbook
from .domain.models import User
from .domain.periods import current_period, resolve_periods
from .netschool.errors import NetSchoolError

if TYPE_CHECKING:
    from .context import AppContext

SCOPES = ("period", "year")


async def export_grades(app: "AppContext", user: User, scope: str = "period") -> tuple[bytes, str]:
    """Книга Excel с оценками за текущий период или весь учебный год и имя файла."""
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
    records = [r for d in days for r in d.marks if start <= r.date <= end]
    data = grades_workbook(records, title=title, student=user.student_name, prefs=prefs, periods=year)
    return data, export_filename(user.student_name, title)
