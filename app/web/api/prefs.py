"""Общие настройки приложения (тема, вкладки, веса…), разовые карточки и опрос."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from aiohttp import web

from ...domain.periods import SCHEME_BOUNDS, PeriodScheme
from .base import context, current_user, halloween_season, json_error, routes


THEMES = ("auto", "light", "dark")
# Вкладки нижней панели (tabs.js); «Настройки» убрать нельзя — иначе их не вернуть.
NAV_TABS = ("day", "schedule", "assistant", "homework", "totals", "mail", "games", "settings")
MAX_NAV_TABS = 8
# Закреплённые предметы в «Оценках» — с запасом на все предметы года.
MAX_PINS = 40
# Свои цвета акцента, сохранённые рядом с готовыми.
MAX_SAVED_ACCENTS = 8
DESIGNS = ("classic", "new")
# Сдвиг звонка для таймера урока, с (BELL_SHIFTS в timer.js).
BELL_SHIFTS = tuple(str(value) for value in range(-5, 11))
ACCENT_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _clean_weights(value: Any) -> list[float] | None:
    """Веса калькулятора «что если»: до 12 положительных чисел не больше 100."""
    if not isinstance(value, list) or len(value) > 12:
        return None
    weights: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not 0 < item <= 100:
            return None
        number = round(float(item), 2)
        if number not in weights:
            weights.append(int(number) if number.is_integer() else number)
    return weights


def _clean_grade_thresholds(value: Any) -> dict[str, float] | None:
    """С какого среднего «5», «4», «3»: числа от 1 до 5 по убыванию."""
    if not isinstance(value, dict):
        return None
    result: dict[str, float] = {}
    for grade in ("5", "4", "3"):
        number = value.get(grade)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not 1 <= number <= 5:
            return None
        result[grade] = round(float(number), 2)
    return result if result["5"] > result["4"] > result["3"] else None


def _clean_thresholds(value: Any) -> dict[str, Any] | None:
    """Пороги оценок: общие (`all`, необязательно) и свои у предметов (`subjects`)."""
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {}
    if value.get("all") is not None:
        if (common := _clean_grade_thresholds(value["all"])) is None:
            return None
        result["all"] = common
    subjects = value.get("subjects") or {}
    if not isinstance(subjects, dict) or len(subjects) > 60:
        return None
    result["subjects"] = {}
    for name, item in subjects.items():
        if not name or len(name) > 120 or (own := _clean_grade_thresholds(item)) is None:
            return None
        result["subjects"][name] = own
    return result


_MONTH_DAY = re.compile(r"^(\d{2})-(\d{2})$")


def _clean_periods(value: Any) -> dict[str, Any] | None:
    """Схема учебного года и, по желанию, свои даты начала периодов («MM-DD»)."""
    if not isinstance(value, dict):
        return None
    scheme = value.get("scheme")
    if scheme == "auto":
        return {"scheme": "auto"}
    try:
        bounds = SCHEME_BOUNDS[PeriodScheme(scheme)]
    except ValueError:
        return None
    starts = value.get("starts")
    if not starts:
        return {"scheme": scheme}
    if not isinstance(starts, list) or len(starts) != len(bounds):
        return None
    dates = []
    for item in starts:
        match = _MONTH_DAY.match(str(item))
        if not match:
            return None
        month, day = int(match.group(1)), int(match.group(2))
        try:
            # Високосный год: 29 февраля допустимо.
            dt.date(2028, month, day)
        except ValueError:
            return None
        dates.append((month, day))
    # Учебный год идёт с сентября: порядок проверяем по «учебному» месяцу.
    order = [((m - 9) % 12, d) for m, d in dates]
    if order != sorted(order) or len(set(order)) != len(order):
        return None
    return {"scheme": scheme, "starts": [f"{m:02d}-{d:02d}" for m, d in dates]}


_VERSION = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}$")
# Цели на четверть по предметам: {предмет: "5" | "4" | "3"}.
MAX_GOALS = 60


def _clean_goals(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict) or len(value) > MAX_GOALS:
        return None
    goals = {}
    for subject, grade in value.items():
        if not isinstance(subject, str) or not 0 < len(subject) <= 120 or grade not in ("5", "4", "3"):
            return None
        goals[subject] = grade
    return goals


MAX_LESSON_EDITS = 100
_EDIT_ID = re.compile(r"^[0-9a-z]{1,24}$")
_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _clean_lesson_edit(item: Any) -> dict[str, Any] | None:
    """Правка расписания (edits.js): своё время урока, скрытый урок или свой
    урок — на один день (`date`) или каждую неделю (`weekday`, 0 — понедельник)."""
    if not isinstance(item, dict):
        return None
    kind, subject, edit_id = item.get("kind"), item.get("subject"), item.get("id")
    if kind not in ("time", "hide", "add") or not isinstance(edit_id, str) or not _EDIT_ID.match(edit_id):
        return None
    if not isinstance(subject, str) or not 0 < len(subject.strip()) <= 120:
        return None
    edit: dict[str, Any] = {"id": edit_id, "kind": kind, "subject": subject.strip()}
    date, weekday = item.get("date"), item.get("weekday")
    if date is not None:
        try:
            edit["date"] = dt.date.fromisoformat(str(date)).isoformat()
        except ValueError:
            return None
    elif isinstance(weekday, int) and not isinstance(weekday, bool) and 0 <= weekday <= 6:
        edit["weekday"] = weekday
    else:
        return None
    if kind in ("time", "hide"):
        number = item.get("number")
        if number is not None and (isinstance(number, bool) or not isinstance(number, int) or not 0 <= number <= 30):
            return None
        edit["number"] = number
    if kind in ("time", "add"):
        start, end = item.get("start"), item.get("end")
        if not all(isinstance(t, str) and _CLOCK.match(t) for t in (start, end)) or end <= start:
            return None
        edit["start"], edit["end"] = start, end
    if kind == "add" and item.get("room"):
        room = item["room"]
        if not isinstance(room, str) or len(room) > 40:
            return None
        edit["room"] = room.strip()
    return edit


def _clean_lesson_edits(value: Any) -> list[dict[str, Any]] | None:
    if not isinstance(value, list) or len(value) > MAX_LESSON_EDITS:
        return None
    edits = [_clean_lesson_edit(item) for item in value]
    return None if any(edit is None for edit in edits) else edits  # type: ignore[return-value]


@routes.get("/api/prefs")
async def get_prefs(request: web.Request) -> web.Response:
    """Тема, дизайн, цвет шапки и веса калькулятора — общие для Telegram и сайта."""
    app, user = context(request), current_user(request)
    data = await app.miniapp.prefs(user.telegram_id)
    # Поставили приложение до того, как оно стало отмечать это само.
    if "pwa" not in data and await app.miniapp.has_installed_app(user.telegram_id):
        data = await app.miniapp.save_prefs(user.telegram_id, {"pwa": "installed"})
    return web.json_response({"ok": True, "data": data})


@routes.post("/api/visit")
async def visit(request: web.Request) -> web.Response:
    """Приложение открыли: какой дизайн и где (для статистики админа)."""
    try:
        body = await request.json()
    except ValueError:
        body = {}
    design = body.get("design") if isinstance(body, dict) else None
    client = body.get("client") if isinstance(body, dict) else None
    halloween = body.get("halloween") if isinstance(body, dict) else None
    if design not in {"new", "classic"} or client not in {"telegram", "pwa", "web"}:
        return json_error("Некорректный запрос")
    miniapp, telegram_id = context(request).miniapp, current_user(request).telegram_id
    await miniapp.record_visit(telegram_id, design, client, halloween if isinstance(halloween, bool) else None)
    if client == "pwa" and (await miniapp.prefs(telegram_id)).get("pwa") != "installed":
        await miniapp.save_prefs(telegram_id, {"pwa": "installed"})
    return web.json_response({"ok": True})


async def _chats(request: web.Request) -> bool:
    """Можно ли звать «написать админу» в чаты — они могут быть выключены."""
    from ..chat import available

    return await available(request)


@routes.post("/api/notices/chats")
async def chat_launch_notice(request: web.Request) -> web.Response:
    from ..chat import available

    app, user = context(request), current_user(request)
    # Пока чаты выключены, окно не тратим: покажется, когда их включат.
    show = await available(request) and await app.miniapp.claim_chat_launch_notice(user.telegram_id)
    return web.json_response({"ok": True, "data": {"show": show}}, headers={"Cache-Control": "no-store"})


@routes.post("/api/notices/halloween")
async def halloween_notice(request: web.Request) -> web.Response:
    """Разовая карточка «включили хеллоуинское оформление» — по одной на
    человека, флаг в общих настройках, чтобы не повторять на других устройствах."""
    app, user = context(request), current_user(request)
    show = halloween_season(user.today()) and not (await app.miniapp.prefs(user.telegram_id)).get("halloween_notice")
    if show:
        await app.miniapp.save_prefs(user.telegram_id, {"halloween_notice": True})
    data = {"show": show, "bug_report": bool(app.settings.web.captcha_url), "chats": await _chats(request)}
    return web.json_response({"ok": True, "data": data}, headers={"Cache-Control": "no-store"})


@routes.post("/api/notices/keep-files")
async def keep_files_notice(request: web.Request) -> web.Response:
    """Разовая карточка «файлы заданий теперь сохраняются у всех» — тем, у кого
    сохранение есть и не выключено; флаг в общих настройках, как у хеллоуина."""
    app, user = context(request), current_user(request)
    prefs = await app.miniapp.prefs(user.telegram_id)
    show = (app.attachments is not None and app.attachments.enabled
            and not prefs.get("keep_files_notice") and await app.keep_attachments_wanted(user))
    if show:
        await app.miniapp.save_prefs(user.telegram_id, {"keep_files_notice": True})
    return web.json_response({"ok": True, "data": {"show": show}}, headers={"Cache-Control": "no-store"})


@routes.post("/api/notices/survey")
async def survey_notice(request: web.Request) -> web.Response:
    """Показать ли опрос «нравится ли приложение»: пока нет ни ответа, ни отказа."""
    app, user = context(request), current_user(request)
    show = await app.miniapp.survey_pending(user.telegram_id)
    # Куда звать с идеями и ошибками: форма есть, только если настроена капча.
    data = {"show": show, "bug_report": bool(app.settings.web.captcha_url), "chats": await _chats(request)}
    return web.json_response({"ok": True, "data": data}, headers={"Cache-Control": "no-store"})


@routes.post("/api/survey/decline")
async def survey_decline(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    await app.miniapp.decline_survey(user.telegram_id)
    return web.json_response({"ok": True})


@routes.post("/api/survey")
async def survey_answer(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except ValueError:
        body = {}
    score = body.get("score") if isinstance(body, dict) else None
    if type(score) is not int or not 1 <= score <= 10:
        return json_error("Оценка — число от 1 до 10")
    app, user = context(request), current_user(request)
    if not await app.miniapp.answer_survey(user.telegram_id, score):
        return json_error("Опрос недоступен", status=409)
    return web.json_response({"ok": True})


@routes.post("/api/prefs")
async def save_prefs(request: web.Request) -> web.Response:
    app, user = context(request), current_user(request)
    try:
        body = await request.json()
    except ValueError:
        return json_error("Некорректный запрос")
    if not isinstance(body, dict):
        return json_error("Некорректный запрос")
    changes: dict[str, Any] = {}
    if "theme" in body:
        if body["theme"] not in THEMES:
            return json_error("Неизвестная тема")
        changes["theme"] = body["theme"]
    if "weekends" in body:
        if body["weekends"] not in ("show", "hide"):
            return json_error("Некорректная настройка выходных")
        changes["weekends"] = body["weekends"]
    if "designNotice" in body:
        if body["designNotice"] != "seen":
            return json_error("Некорректная настройка")
        changes["designNotice"] = "seen"
    if "pwa" in body:
        if body["pwa"] != "installed":
            return json_error("Некорректная настройка")
        changes["pwa"] = "installed"
    if "hwStatus" in body:
        if body["hwStatus"] not in ("show", "hide"):
            return json_error("Некорректная настройка подсветки заданий")
        changes["hwStatus"] = body["hwStatus"]
    if "motion" in body:
        # Доля анимаций: края — 'on' и 'off', между ними — проценты.
        if body["motion"] not in ("on", "75", "50", "25", "off"):
            return json_error("Некорректная настройка анимаций")
        changes["motion"] = body["motion"]
    if "halloween" in body:
        if body["halloween"] not in ("on", "off"):
            return json_error("Некорректная настройка Хеллоуина")
        changes["halloween"] = body["halloween"]
    if "halloweenAccent" in body:
        if not isinstance(body["halloweenAccent"], str) or not ACCENT_RE.match(body["halloweenAccent"]):
            return json_error("Некорректный цвет")
        changes["halloweenAccent"] = body["halloweenAccent"].lower()
    if "retro" in body:
        if body["retro"] not in ("on", "off"):
            return json_error("Некорректная настройка ретро-сайта")
        changes["retro"] = body["retro"]
    if "bellShift" in body:
        # Сдвиг звонка для таймера урока, секунды строкой: -5…+10.
        if body["bellShift"] not in BELL_SHIFTS:
            return json_error("Некорректный сдвиг звонка")
        changes["bellShift"] = body["bellShift"]
    if "confetti" in body:
        if body["confetti"] not in ("show", "hide"):
            return json_error("Некорректная настройка конфетти")
        changes["confetti"] = body["confetti"]
    if "answers" in body:
        if body["answers"] not in ("show", "hide"):
            return json_error("Некорректная настройка ответа на задание")
        changes["answers"] = body["answers"]
    if "answerHint" in body:
        # Сколько раз показали подсказку «можно скрыть блок ответа».
        if body["answerHint"] not in ("1", "2", "3"):
            return json_error("Некорректная настройка")
        changes["answerHint"] = body["answerHint"]
    if "scheduleTab" in body:
        if body["scheduleTab"] not in ("bells", "week"):
            return json_error("Некорректная настройка вкладки")
        changes["scheduleTab"] = body["scheduleTab"]
    if "design" in body:
        if body["design"] not in DESIGNS:
            return json_error("Неизвестный дизайн")
        changes["design"] = body["design"]
    if "accent" in body:
        if not isinstance(body["accent"], str) or not ACCENT_RE.match(body["accent"]):
            return json_error("Некорректный цвет")
        changes["accent"] = body["accent"].lower()
    if "accents" in body:
        accents = body["accents"]
        if (
            not isinstance(accents, list)
            or len(accents) > MAX_SAVED_ACCENTS
            or not all(isinstance(color, str) and ACCENT_RE.match(color) for color in accents)
        ):
            return json_error("Некорректные сохранённые цвета")
        changes["accents"] = list(dict.fromkeys(color.lower() for color in accents))
    if "weights" in body:
        weights = _clean_weights(body["weights"])
        if weights is None:
            return json_error("Некорректные веса")
        changes["weights"] = weights
    if "tabs" in body:
        tabs = body["tabs"]
        if (
            not isinstance(tabs, list)
            or not all(isinstance(tab, str) for tab in tabs)
            or not 2 <= len(tabs) <= MAX_NAV_TABS
            or len(set(tabs)) != len(tabs)
            or any(tab not in NAV_TABS for tab in tabs)
            or "settings" not in tabs
        ):
            return json_error("Некорректный набор вкладок")
        changes["tabs"] = tabs
    if "pins" in body:
        pins = body["pins"]
        if (
            not isinstance(pins, list)
            or len(pins) > MAX_PINS
            or not all(isinstance(pin, str) and 0 < len(pin) <= 120 for pin in pins)
        ):
            return json_error("Некорректный список закреплённых предметов")
        changes["pins"] = list(dict.fromkeys(pins))
    if "periods" in body:
        setting = _clean_periods(body["periods"])
        if setting is None:
            return json_error("Некорректные учебные периоды")
        changes["periods"] = setting
        # Таблица «Итогов» посчитана по старым границам.
        await app.cache.clear(user.telegram_id)
    if "thresholds" in body:
        thresholds = _clean_thresholds(body["thresholds"])
        if thresholds is None:
            return json_error("Некорректные пороги оценок")
        changes["thresholds"] = thresholds
    if "keepSignedIn" in body:
        # «Не выходить из аккаунта» (Госуслуги): по умолчанию включено.
        if not isinstance(body["keepSignedIn"], bool):
            return json_error("Некорректная настройка")
        changes["keepSignedIn"] = body["keepSignedIn"]
    if "goals" in body:
        goals = _clean_goals(body["goals"])
        if goals is None:
            return json_error("Некорректные цели")
        changes["goals"] = goals
    if "whatsNew" in body:
        # Какую версию «Что нового» уже открывали — точка у строки в настройках.
        if not isinstance(body["whatsNew"], str) or not _VERSION.match(body["whatsNew"]):
            return json_error("Некорректная настройка")
        changes["whatsNew"] = body["whatsNew"]
    if "countdown" in body:
        if body["countdown"] not in ("show", "hide"):
            return json_error("Некорректная настройка отсчёта")
        changes["countdown"] = body["countdown"]
    if "lessonEdits" in body:
        edits = _clean_lesson_edits(body["lessonEdits"])
        if edits is None:
            return json_error("Некорректные правки расписания")
        changes["lessonEdits"] = edits
    if not changes:
        return json_error("Нечего сохранять")
    data = await app.miniapp.save_prefs(user.telegram_id, changes)
    return web.json_response({"ok": True, "data": data})
