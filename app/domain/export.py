"""Выгрузка оценок в Excel (.xlsx) без сторонних библиотек.

Файл .xlsx — zip с несколькими XML. Нам хватает листов с текстом и числами,
поэтому пишем его сами: тянуть openpyxl ради двух таблиц не стоит.
"""

from __future__ import annotations

import datetime as dt
import io
import zipfile
from typing import Any
from xml.sax.saxutils import escape

from . import insights
from .records import DiaryDay, MarkRecord, clean_content

Cell = str | int | float | None


def _column(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _sheet_xml(rows: list[list[Cell]], widths: list[int]) -> str:
    body = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row):
            ref = f"{_column(c)}{r}"
            style = ' s="1"' if r == 1 else ""
            if value is None or value == "":
                continue
            if isinstance(value, bool):
                value = int(value)
            if isinstance(value, (int, float)):
                cells.append(f'<c r="{ref}"{style}><v>{value}</v></c>')
            else:
                text = escape(str(value)).replace("\n", "&#10;")
                cells.append(f'<c r="{ref}" t="inlineStr"{style}><is><t xml:space="preserve">{text}</t></is></c>')
        body.append(f'<row r="{r}">{"".join(cells)}</row>')
    cols = "".join(
        f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" state="frozen"/></sheetView></sheetViews>'
        f"<cols>{cols}</cols><sheetData>{''.join(body)}</sheetData></worksheet>"
    )


def build_xlsx(sheets: list[tuple[str, list[list[Cell]], list[int]]]) -> bytes:
    """Книга из листов (название, строки, ширины колонок); первая строка — заголовок."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as book:
        overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(sheets) + 1)
        )
        book.writestr("[Content_Types].xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            f"{overrides}</Types>"
        ))
        book.writestr("_rels/.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>'
        ))
        sheet_tags = "".join(
            f'<sheet name="{escape(name[:31])}" sheetId="{i}" r:id="rId{i}"/>'
            for i, (name, _, _) in enumerate(sheets, start=1)
        )
        book.writestr("xl/workbook.xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{sheet_tags}</sheets></workbook>"
        ))
        rels = "".join(
            f'<Relationship Id="rId{i}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, len(sheets) + 1)
        )
        styles_id = len(sheets) + 1
        rels += (
            f'<Relationship Id="rId{styles_id}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/>'
        )
        book.writestr("xl/_rels/workbook.xml.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{rels}</Relationships>"
        ))
        # Два стиля: обычный и жирный для заголовка.
        book.writestr("xl/styles.xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
            '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill>'
            '<fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
            "</styleSheet>"
        ))
        for i, (_, rows, widths) in enumerate(sheets, start=1):
            book.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(rows, widths))
    return buffer.getvalue()


def grades_workbook(
    records: list[MarkRecord],
    *,
    title: str,
    student: str = "",
    prefs: dict[str, Any] | None = None,
    periods: list | None = None,
) -> bytes:
    """Книга «Оценки» (все оценки) и «Итоги» (по предметам) за выбранный период."""
    ordered = sorted(records, key=lambda r: (r.date, r.subject, r.lesson_number or 0))
    marks_rows: list[list[Cell]] = [["Дата", "Предмет", "Работа", "Оценка", "Вес", "Комментарий"]]
    for record in ordered:
        number = record.numeric_mark
        marks_rows.append([
            record.date.strftime("%d.%m.%Y"), record.subject, record.title,
            number if number is not None and str(number) == record.mark.strip() else record.mark,
            record.weight or 1, record.comment,
        ])

    period_columns = [p for p in periods or [] if any(p.covers(r.date) for r in records)]
    header: list[Cell] = ["Предмет", "Оценок", "Средний", "Выходит", "5", "4", "3", "2"]
    header += [f"Средний: {p.label}" for p in period_columns]
    summary_rows: list[list[Cell]] = [header]
    by_subject: dict[str, list[MarkRecord]] = {}
    for record in ordered:
        by_subject.setdefault(record.subject, []).append(record)
    for subject, items in sorted(by_subject.items()):
        average = insights.weighted(items)
        counts = {v: sum(1 for r in items if r.numeric_mark == v) for v in (5, 4, 3, 2)}
        row: list[Cell] = [
            subject, len(items),
            round(average, 2) if average is not None else None,
            insights.grade_for(average, insights.thresholds_for(prefs, subject)) if average is not None else None,
            counts[5], counts[4], counts[3], counts[2],
        ]
        for period in period_columns:
            value = insights.weighted([r for r in items if period.covers(r.date)])
            row.append(round(value, 2) if value is not None else None)
        summary_rows.append(row)
    overall = insights.weighted(ordered)
    summary_rows.append([])
    summary_rows.append(["Общий средний", len(ordered), round(overall, 2) if overall is not None else None])
    summary_rows.append([f"{student} · {title}".strip(" ·")])
    summary_rows.append([f"Выгружено {dt.date.today():%d.%m.%Y}"])
    return build_xlsx([
        ("Итоги", summary_rows, [28, 9, 10, 10, 6, 6, 6, 6] + [16] * len(period_columns)),
        ("Оценки", marks_rows, [12, 26, 44, 9, 6, 40]),
    ])


def _footer(student: str, title: str) -> list[list[Cell]]:
    return [[], [f"{student} · {title}".strip(" ·")], [f"Выгружено {dt.date.today():%d.%m.%Y}"]]


def _marks_text(marks) -> str:
    return " ".join(m.mark for m in marks if m.mark)


def absences_workbook(days: list[DiaryDay], *, title: str, student: str = "") -> bytes:
    """Пропуски: сколько по каждому предмету (по видам отметок) и списком."""
    rows: list[tuple[dt.date, int | None, str, str]] = [
        (day.day, lesson.number, lesson.subject, lesson.absence.strip())
        for day in sorted(days, key=lambda d: d.day)
        for lesson in day.lessons if (lesson.absence or "").strip()
    ]
    codes = sorted({code for *_, code in rows})
    by_subject: dict[str, list[str]] = {}
    for _, _, subject, code in rows:
        by_subject.setdefault(subject, []).append(code)
    summary: list[list[Cell]] = [["Предмет", "Всего", *codes]]
    for subject, items in sorted(by_subject.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        summary.append([subject, len(items), *[items.count(code) or None for code in codes]])
    summary.append([])
    summary.append(["Всего пропусков", len(rows), *[sum(1 for *_, c in rows if c == code) for code in codes]])
    summary.append(["Дней с пропусками", len({d for d, *_ in rows})])
    if not rows:
        summary.append(["Пропусков нет — или школа не отмечает посещаемость в дневнике"])
    summary += _footer(student, title)
    listing: list[list[Cell]] = [["Дата", "Урок", "Предмет", "Отметка"]]
    listing += [[d.strftime("%d.%m.%Y"), number, subject, code] for d, number, subject, code in rows]
    return build_xlsx([
        ("Итоги", summary, [28, 9] + [8] * len(codes)),
        ("Пропуски", listing, [12, 7, 28, 10]),
    ])


def homework_workbook(days: list[DiaryDay], *, title: str, student: str = "") -> bytes:
    """Все домашние задания за период: по дате сдачи и сколько по предметам."""
    seen: set[str] = set()
    items = []
    for day in sorted(days, key=lambda d: d.day):
        for record in day.homework:
            if record.key in seen or record.nothing_to_do:
                continue
            seen.add(record.key)
            items.append(record)
    items.sort(key=lambda r: (r.due_date, r.lesson_number or 0, r.subject))
    listing: list[list[Cell]] = [["Сдать к", "Предмет", "Тип", "Задание", "Вес", "Файлы"]]
    for record in items:
        listing.append([
            record.due_date.strftime("%d.%m.%Y"), record.subject, record.assignment_type,
            record.text, record.weight or 1, ", ".join(a.name for a in record.attachments),
        ])
    counts: dict[str, int] = {}
    for record in items:
        counts[record.subject] = counts.get(record.subject, 0) + 1
    summary: list[list[Cell]] = [["Предмет", "Заданий"]]
    summary += [[subject, n] for subject, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    summary.append([])
    summary.append(["Всего", len(items)])
    summary += _footer(student, title)
    return build_xlsx([
        ("Задания", listing, [12, 26, 16, 70, 6, 30]),
        ("По предметам", summary, [28, 10]),
    ])


def lessons_workbook(days: list[DiaryDay], *, title: str, student: str = "") -> bytes:
    """Журнал уроков: каждый урок с темой, учителем, оценками и пропуском."""
    rows: list[list[Cell]] = [["Дата", "Урок", "Время", "Предмет", "Тема", "Учитель", "Кабинет", "Оценки", "Пропуск"]]
    for day in sorted(days, key=lambda d: d.day):
        for lesson in sorted(day.lessons, key=lambda item: item.number or 0):
            rows.append([
                day.day.strftime("%d.%m.%Y"), lesson.number, lesson.time_range, lesson.subject,
                lesson.topic, lesson.teacher, lesson.room, _marks_text(lesson.marks), lesson.absence,
            ])
    return build_xlsx([("Уроки", rows, [12, 6, 13, 26, 50, 26, 9, 10, 9])])


MONTHS = ("янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")
WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def _rounded(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def subjects_workbook(
    days: list[DiaryDay], *, title: str, student: str = "", prefs: dict[str, Any] | None = None
) -> bytes:
    """Сводка по предметам: учитель, сколько уроков, пропусков, заданий и
    оценок, средний и что выходит — всё на одной строке."""
    stats: dict[str, dict[str, Any]] = {}
    for day in days:
        for lesson in day.lessons:
            item = stats.setdefault(lesson.subject, {
                "teachers": [], "lessons": 0, "absences": 0, "homework": 0, "marks": [],
            })
            item["lessons"] += 1
            if lesson.teacher and lesson.teacher not in item["teachers"]:
                item["teachers"].append(lesson.teacher)
            if (lesson.absence or "").strip():
                item["absences"] += 1
            item["homework"] += sum(1 for h in lesson.homework if not h.classwork and not h.nothing_to_do)
            item["marks"] += list(lesson.marks)
    rows: list[list[Cell]] = [[
        "Предмет", "Учитель", "Уроков", "Пропусков", "Заданий", "Оценок", "Средний", "Выходит", "Последние оценки",
    ]]
    for subject, item in sorted(stats.items()):
        marks = sorted(item["marks"], key=lambda r: r.date)
        average = insights.weighted(marks)
        rows.append([
            subject, ", ".join(item["teachers"]), item["lessons"], item["absences"] or None,
            item["homework"] or None, len(marks) or None, _rounded(average),
            insights.grade_for(average, insights.thresholds_for(prefs, subject)) if average is not None else None,
            _marks_text(marks[-8:]),
        ])
    rows += _footer(student, title)
    return build_xlsx([("Предметы", rows, [28, 30, 8, 10, 9, 8, 9, 9, 24])])


def dynamics_workbook(days: list[DiaryDay], *, title: str, student: str = "") -> bytes:
    """Средний по месяцам: видно, где оценки растут, а где проседают."""
    marks = sorted((r for day in days for r in day.marks), key=lambda r: r.date)
    months = sorted({(r.date.year, r.date.month) for r in marks})
    header: list[Cell] = ["Предмет", *[f"{MONTHS[m - 1]} {y}" for y, m in months], "Изменение"]
    rows: list[list[Cell]] = [header]
    by_subject: dict[str, list[MarkRecord]] = {}
    for record in marks:
        by_subject.setdefault(record.subject, []).append(record)
    for subject, items in sorted(by_subject.items()):
        values = [
            insights.weighted([r for r in items if (r.date.year, r.date.month) == month]) for month in months
        ]
        known = [v for v in values if v is not None]
        change = round(known[-1] - known[0], 2) if len(known) >= 2 else None
        rows.append([subject, *[_rounded(v) for v in values], change])
    rows.append([])
    rows.append(["Все предметы", *[
        _rounded(insights.weighted([r for r in marks if (r.date.year, r.date.month) == month])) for month in months
    ]])
    rows.append(["Оценок за месяц", *[
        sum(1 for r in marks if (r.date.year, r.date.month) == month) for month in months
    ]])
    if not marks:
        rows.append(["Оценок за этот срок нет"])
    rows += _footer(student, title)
    return build_xlsx([("По месяцам", rows, [28, *[10] * len(months), 11])])


def control_works_workbook(days: list[DiaryDay], *, title: str, student: str = "") -> bytes:
    """Контрольные, проверочные, диктанты и другие работы с большим весом."""
    marks = sorted(
        (r for day in days for r in day.marks if insights.is_test_work(r.assignment_type, r.weight)),
        key=lambda r: (r.date, r.lesson_number or 0),
    )
    listing: list[list[Cell]] = [["Дата", "Предмет", "Вид работы", "Тема", "Оценка", "Вес", "Комментарий"]]
    for record in marks:
        number = record.numeric_mark
        listing.append([
            record.date.strftime("%d.%m.%Y"), record.subject, record.assignment_type, clean_content(record.content),
            number if number is not None and str(number) == record.mark.strip() else record.mark,
            record.weight or 1, record.comment,
        ])
    summary: list[list[Cell]] = [["Предмет", "Работ", "Средний", "5", "4", "3", "2"]]
    by_subject: dict[str, list[MarkRecord]] = {}
    for record in marks:
        by_subject.setdefault(record.subject, []).append(record)
    for subject, items in sorted(by_subject.items()):
        counts = [sum(1 for r in items if r.numeric_mark == v) or None for v in (5, 4, 3, 2)]
        summary.append([subject, len(items), _rounded(insights.weighted(items)), *counts])
    if not marks:
        summary.append(["Контрольных с оценками за этот срок нет"])
    summary += _footer(student, title)
    return build_xlsx([
        ("Итоги", summary, [28, 8, 9, 6, 6, 6, 6]),
        ("Работы", listing, [12, 26, 22, 44, 8, 6, 36]),
    ])


def schedule_workbook(days: list[DiaryDay], *, title: str, student: str = "") -> bytes:
    """Обычная неделя: какой предмет чаще всего стоит в этот день этим уроком,
    со временем и кабинетом. Замены и разовые уроки сюда не попадают."""
    seen: dict[tuple[int, int], dict[str, int]] = {}
    times: dict[int, dict[str, int]] = {}
    rooms: dict[tuple[int, int, str], dict[str, int]] = {}
    for day in days:
        weekday = day.day.weekday()
        for lesson in day.lessons:
            if lesson.number is None:
                continue
            slot = (weekday, lesson.number)
            counts = seen.setdefault(slot, {})
            counts[lesson.subject] = counts.get(lesson.subject, 0) + 1
            if lesson.time_range:
                by_time = times.setdefault(lesson.number, {})
                by_time[lesson.time_range] = by_time.get(lesson.time_range, 0) + 1
            if lesson.room:
                by_room = rooms.setdefault((*slot, lesson.subject), {})
                by_room[lesson.room] = by_room.get(lesson.room, 0) + 1
    top = lambda counts: max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]  # noqa: E731
    weekdays = sorted({weekday for weekday, _ in seen})
    numbers = sorted({number for _, number in seen})
    rows: list[list[Cell]] = [["Урок", "Время", *[WEEKDAYS[w] for w in weekdays]]]
    for number in numbers:
        row: list[Cell] = [number, top(times[number]) if number in times else None]
        for weekday in weekdays:
            counts = seen.get((weekday, number))
            if not counts:
                row.append(None)
                continue
            subject = top(counts)
            room = rooms.get((weekday, number, subject))
            row.append(f"{subject} ({top(room)})" if room else subject)
        rows.append(row)
    if not seen:
        rows.append(["Уроков за этот срок нет"])
    rows += _footer(student, title)
    return build_xlsx([("Расписание", rows, [6, 13, *[26] * len(weekdays)])])


def export_filename(student: str, title: str, prefix: str = "Оценки") -> str:
    safe = "".join(ch for ch in f"{prefix} {student} {title}".strip() if ch.isalnum() or ch in " -_.").strip()
    return f"{safe or prefix}.xlsx".replace(" ", "_")
