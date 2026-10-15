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
from .records import DiaryDay, MarkRecord

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


def export_filename(student: str, title: str, prefix: str = "Оценки") -> str:
    safe = "".join(ch for ch in f"{prefix} {student} {title}".strip() if ch.isalnum() or ch in " -_.").strip()
    return f"{safe or prefix}.xlsx".replace(" ", "_")
