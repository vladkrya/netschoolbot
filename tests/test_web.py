"""Тесты веб-приложения.

Проверяются, в частности, авторизация по токену и поведение при недоступной
школе: раньше при ошибке в браузер улетал то JSON, то HTML-страница Flask,
то текст исключения.
"""

import asyncio
import datetime as dt
import json
import re

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.context import AppContext
from app.domain.models import Student
from app.netschool.errors import NetSchoolError, Reason
from app.web.server import create_app
from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура
from tests.test_repositories import make_user
from tests.test_watcher import record

# Тот же префикс, что в настройках тестового контекста. Раньше тесты ходили
# в корень, а страница строила ссылки от префикса — из-за этого полностью
# нерабочее приложение проходило проверки.
BASE = "/mini"


@pytest.fixture
async def client(app_context) -> TestClient:
    server = TestServer(create_app(app_context))
    client = TestClient(server)
    await client.start_server()
    yield client
    await client.close()


@pytest.fixture
async def token(app_context, users):
    await users.save(make_user(student_name="Иванов И."))
    return await app_context.miniapp.issue_token(1)


class TestAuth:
    async def test_api_without_token_is_rejected(self, client):
        response = await client.get(f"{BASE}/api/profile")
        assert response.status == 401
        body = await response.json()
        assert body["reason"] == "auth"
        assert "/app" in body["error"]

    async def test_invalid_token_is_rejected(self, client):
        response = await client.get(f"{BASE}/api/profile", params={"token": "выдумка"})
        assert response.status == 401

    async def test_valid_token_works(self, client, token):
        response = await client.get(f"{BASE}/api/profile", params={"token": token})
        assert response.status == 200
        assert (await response.json())["data"]["name"] == "Иванов И."

    async def test_token_accepted_in_header(self, client, token):
        response = await client.get(f"{BASE}/api/profile", headers={"X-Netschool-Token": token})
        assert response.status == 200

    async def test_token_moves_into_cookie(self, client, token):
        response = await client.get(f"{BASE}/api/profile", params={"token": token})
        # Дальше токен не нужен в URL — он не утекает в историю и Referer.
        assert "netschool_token" in response.cookies

    async def test_revoked_token_stops_working(self, client, app_context, token):
        await app_context.miniapp.revoke_tokens(1)
        client.session.cookie_jar.clear()
        response = await client.get(f"{BASE}/api/profile", params={"token": token})
        assert response.status == 401


class TestPages:
    async def test_index_renders_without_token(self, client):
        """Страница обязана отдаваться и без токена.

        Внутри Telegram токена в адресе нет: личность подтверждает
        initData уже в браузере. Отказ на этом шаге закрыл бы вход
        мини-приложением полностью.
        """
        response = await client.get(f"{BASE}/")
        assert response.status == 200
        text = await response.text()
        assert "telegram-web-app.js" in text
        # Без VPN telegram.org заблокирован, а скрипт в head держит отрисовку.
        assert "src=\"https://telegram.org" not in text

    async def test_index_with_token_renders_app(self, client, token):
        response = await client.get(f"{BASE}/", params={"token": token})
        assert response.status == 200
        text = await response.text()
        assert "js/app.js" in text
        # Имя приезжает в скрипт-блоке, а не в разметке, и Jinja экранирует
        # кириллицу в \uXXXX — сравниваем в том же виде.
        assert json.dumps("Иванов И.") in text

    async def test_root_redirects_into_app_keeping_query(self, client, token):
        # Ссылку открывают без завершающего слэша; потерять при переадресации
        # токен означало бы показать «не удалось вас опознать».
        response = await client.get("/", params={"token": token}, allow_redirects=False)
        assert response.status == 302
        assert response.headers["Location"] == f"{BASE}/?token={token}"

    async def test_prefix_without_slash_redirects(self, client):
        response = await client.get(BASE, allow_redirects=False)
        assert response.status == 302
        assert response.headers["Location"] == f"{BASE}/"

    async def test_root_app_serves_legacy_prefix(self, app_context, token):
        # Приложение переехало с /mini/netschool в корень, а старые ссылки
        # остались в чатах и на домашних экранах.
        from dataclasses import replace

        settings = replace(app_context.settings, web=replace(app_context.settings.web, miniapp_path=""))
        root_context = replace(app_context, settings=settings)
        client = TestClient(TestServer(create_app(root_context)))
        await client.start_server()
        try:
            response = await client.get("/", params={"token": token})
            assert response.status == 200
            assert '/static/v' in await response.text()

            response = await client.get(
                "/mini/netschool", params={"token": token}, allow_redirects=False
            )
            assert response.status == 302
            assert response.headers["Location"] == f"/?token={token}"

            response = await client.post("/mini/netschool/api/x?a=1", allow_redirects=False)
            assert response.status == 307
            assert response.headers["Location"] == "/api/x?a=1"

            # Service worker на месте: иначе отвалились бы push-подписки.
            response = await client.get("/mini/netschool/sw.js")
            assert response.status == 200
        finally:
            await client.close()

    async def test_manifest_keeps_token_in_start_url(self, client, token):
        response = await client.get(f"{BASE}/manifest.webmanifest", params={"token": token})
        payload = await response.json(content_type=None)
        # Иначе установленное на экран приложение открывалось бы на
        # странице «ссылка недействительна».
        assert token in payload["start_url"]

    async def test_service_worker_allows_root_scope(self, client):
        response = await client.get(f"{BASE}/sw.js")
        assert response.status == 200
        # Область видимости — префикс приложения, а не корень домена.
        assert response.headers["Service-Worker-Allowed"] == f"{BASE}/"

    async def test_health(self, client):
        response = await client.get("/health")
        assert (await response.json())["ok"] is True


class TestPageLinksResolve:
    """Каждая ссылка, которую строит страница, обязана вести на живой ресурс.

    Именно этой проверки не хватало: маршруты висели в корне, а страница
    ссылалась на них от префикса, поэтому в браузере 404 отдавали и стили,
    и скрипт, и все запросы данных — работал только пустой каркас. При этом
    тесты, ходившие в корень напрямую, были зелёными.
    """

    @staticmethod
    def _links(html: str) -> list[str]:
        """Собрать из разметки все локальные адреса, которые запросит браузер."""
        found = re.findall(r'(?:href|src)="([^"]+)"', html)
        # Плюс базовый путь, от которого скрипт строит запросы к API.
        base = re.search(r'base:\s*"([^"]+)"', html)
        if base:
            found.append(base.group(1) + "/api/profile")
        return [link for link in found if link.startswith("/")]

    async def test_every_asset_on_the_page_is_reachable(self, client, token):
        page = await client.get(f"{BASE}/", params={"token": token})
        links = self._links(await page.text())
        # Защита от самообмана: если ссылок не нашлось, проверять нечего.
        assert len(links) >= 4

        for link in links:
            response = await client.get(link, params={"token": token})
            assert response.status == 200, f"{link} отдал {response.status}"

    async def test_page_loads_assets_by_versioned_path(self, client, token):
        from app.web.server import STATIC_VERSION

        page = await (await client.get(f"{BASE}/", params={"token": token})).text()
        assert f"/static/v{STATIC_VERSION}/js/app.js" in page
        # Относительные импорты модулей разрешаются в тот же путь с версией.
        response = await client.get(f"{BASE}/static/v{STATIC_VERSION}/js/details.js")
        assert response.status == 200

    async def test_static_is_revalidated(self, client):
        # Модули импортируются без версии в адресе — без этого заголовка
        # WebView показывал старые экраны после обновления.
        response = await client.get(f"{BASE}/static/js/details.js")
        assert response.headers["Cache-Control"] == "no-cache"

    async def test_versioned_static_is_cached_for_good(self, client, token):
        # Путь с версией не меняется — телефон не перепроверяет его при запуске.
        from app.web.server import STATIC_VERSION, _static_version

        response = await client.get(f"{BASE}/static/v{STATIC_VERSION}/js/details.js")
        assert "immutable" in response.headers["Cache-Control"]
        # Версия — по содержимому: перезапуск без изменений её не меняет.
        assert _static_version() == STATIC_VERSION
        page = await (await client.get(f"{BASE}/", params={"token": token})).text()
        assert f'rel="modulepreload" href="{BASE}/static/v{STATIC_VERSION}/js/core.js"' in page

    async def test_stylesheet_and_script_have_content(self, client, token):
        for asset in ("app.css", "js/app.js"):
            response = await client.get(f"{BASE}/static/{asset}")
            assert response.status == 200
            assert len(await response.text()) > 500, f"{asset} подозрительно пуст"

    async def test_every_module_import_resolves(self, client, token):
        """Модули импортируют друг друга, и этих ссылок в разметке нет.

        Обход только по разметке пропустил бы 404 на core.js — а без него
        не работает ничего. Поэтому идём по графу импортов от точки входа.
        """
        seen: set[str] = set()
        queue = ["js/app.js"]

        while queue:
            path = queue.pop()
            if path in seen:
                continue
            seen.add(path)

            response = await client.get(f"{BASE}/static/{path}")
            assert response.status == 200, f"{path} отдал {response.status}"
            source = await response.text()

            directory = path.rsplit("/", 1)[0] if "/" in path else ""
            for target in re.findall(r"""from\s+['"]\./([^'"]+)['"]""", source):
                queue.append(f"{directory}/{target}" if directory else target)

        # Защита от самообмана: если граф не обошёлся, проверять нечего.
        assert len(seen) >= 5, f"обойдено модулей: {len(seen)}"

    async def test_api_base_matches_mount_point(self, client, token):
        page = await client.get(f"{BASE}/", params={"token": token})
        base = re.search(r'base:\s*"([^"]+)"', await page.text()).group(1)
        # Ровно то расхождение, из-за которого приложение не работало.
        response = await client.get(f"{base}/api/profile", params={"token": token})
        assert response.status == 200


class TestData:
    async def test_electives_stay_in_diary_but_not_in_totals(self, client, app_context, token):
        from app.domain.records import DiaryDay, Lesson

        elective_mark = record(0, "2", subject="Курс английского")
        regular_mark = record(0, "5", subject="Алгебра")
        days = [DiaryDay(day=regular_mark.date, lessons=[
            Lesson(number=1, subject="Алгебра", marks=(regular_mark,)),
            Lesson(number=2, subject="Курс английского", is_elective=True, marks=(elective_mark,)),
            Lesson(number=3, subject="Разговоры о важном", is_elective=True),
        ], marks=[regular_mark, elective_mark])]

        async def fetch_days(*args, **kwargs):
            return days

        app_context.diary.fetch_diary = fetch_days
        app_context.diary.fetch_period = fetch_days
        app_context.diary.totals = {"Курс английского": {"terms": {}, "year": "2", "final": ""}}
        diary = (await (await client.get(f"{BASE}/api/diary", params={"token": token})).json())["data"]
        assert diary["days"][0]["lessons"][1]["is_elective"] is True
        assert diary["days"][0]["lessons"][0]["is_elective"] is False
        totals = (await (await client.get(f"{BASE}/api/marks", params={"token": token})).json())["data"]
        assert [s["subject"] for s in totals["subjects"]] == ["Алгебра"]
        assert totals["average"] == 5
        assert totals["has_totals"] is False

    async def test_regular_lessons_with_same_name_as_elective_remain_in_totals(self, client, app_context, token):
        from app.domain.records import DiaryDay, Lesson

        regular = record(0, "5", subject="Английский язык")
        elective = record(1, "2", subject="Английский язык")

        async def fetch_period(*args, **kwargs):
            return [DiaryDay(day=regular.date, lessons=[
                Lesson(number=1, subject=regular.subject, marks=(regular,)),
                Lesson(number=2, subject=elective.subject, is_elective=True, marks=(elective,)),
            ], marks=[regular, elective])]

        app_context.diary.fetch_period = fetch_period
        totals = (await (await client.get(f"{BASE}/api/marks", params={"token": token})).json())["data"]
        assert len(totals["subjects"]) == 1
        assert totals["subjects"][0]["count"] == 1
        assert totals["average"] == 5

    async def test_diary(self, client, app_context, token):
        app_context.diary.marks = [record(0)]
        response = await client.get(f"{BASE}/api/diary", params={"token": token})
        assert response.status == 200
        assert (await response.json())["ok"] is True

    async def test_marks_are_grouped_with_averages(self, client, app_context, token):
        app_context.diary.marks = [
            record(0, "5", subject="Алгебра"),
            record(1, "3", subject="Алгебра"),
            record(2, "4", subject="Химия"),
        ]
        response = await client.get(f"{BASE}/api/marks", params={"token": token})
        data = (await response.json())["data"]
        algebra = next(s for s in data["subjects"] if s["subject"] == "Алгебра")
        assert algebra["count"] == 2
        assert algebra["average"] == pytest.approx(4.0)

    async def test_marks_cover_year_with_lessons_and_teacher(self, client, app_context, token):
        from app.domain.records import DiaryDay, Lesson

        day = dt.date(2026, 9, 7)
        lesson = Lesson(number=1, subject="Алгебра", teacher="Циркулев А. Г.", absence="Б")

        async def fetch_period(user, start, end, **kwargs):
            # Год целиком: от 1 сентября, а не пять недель назад.
            assert start == dt.date(start.year, 9, 1)
            return [DiaryDay(day=day, lessons=[lesson], marks=[record(0, "5", subject="Алгебра")])]

        app_context.diary.fetch_period = fetch_period
        response = await client.get(f"{BASE}/api/marks", params={"token": token})
        data = (await response.json())["data"]
        algebra = data["subjects"][0]
        assert algebra["teacher"] == "Циркулев А. Г."
        assert [(a["mark"], a["date"]) for a in algebra["attendance"]] == [("Б", "2026-09-07")]
        assert algebra["lessons"]["q1"]["total"] == 1
        assert [q["key"] for q in data["quarters"]] == ["q1", "q2", "q3", "q4"]

    async def test_marks_use_chosen_scheme(self, client, app_context, token):
        bad = await client.post(
            f"{BASE}/api/prefs", params={"token": token},
            json={"periods": {"scheme": "halves", "starts": ["01-09", "09-01"]}},
        )
        assert (await bad.json())["ok"] is False
        saved = await client.post(
            f"{BASE}/api/prefs", params={"token": token},
            json={"periods": {"scheme": "trimesters"}},
        )
        assert (await saved.json())["ok"] is True
        data = (await (await client.get(f"{BASE}/api/marks", params={"token": token})).json())["data"]
        assert [q["key"] for q in data["quarters"]] == ["t1", "t2", "t3"]
        assert data["quarters"][0]["short"] == "1/3"

    async def test_marks_use_school_terms_when_known(self, client, app_context, token):
        year = 2026 if dt.date.today().month >= 9 else 2025
        app_context.diary.terms = [
            {"name": f"{n} полугодие", "startDate": start, "endDate": end}
            for n, start, end in (
                (1, f"{year}-09-01", f"{year}-12-27"), (2, f"{year + 1}-01-10", f"{year + 1}-05-25"),
            )
        ]
        data = (await (await client.get(f"{BASE}/api/marks", params={"token": token})).json())["data"]
        assert [q["key"] for q in data["quarters"]] == ["h1", "h2"]
        assert data["periods_source"] == "school"

    async def test_announcements(self, client, app_context, token):
        app_context.diary.announcements = [{"title": "Собрание", "text": "В 18:00"}]
        response = await client.get(f"{BASE}/api/announcements", params={"token": token})
        assert (await response.json())["data"]["items"][0]["title"] == "Собрание"

    async def test_homework(self, client, app_context, token):
        from app.domain.records import HomeworkRecord

        item = HomeworkRecord(
            "Алгебра", dt.date(2026, 3, 3), "ДЗ", "Упр. 1", weight=2, topic="Дроби",
            published_at=dt.datetime(2026, 3, 1, 9, 30, tzinfo=dt.timezone.utc),
        )
        app_context.diary.homework = [item]
        await app_context.state.replace_homework(1, {item.identity}, {item.identity})
        response = await client.get(f"{BASE}/api/homework", params={"token": token})
        items = (await response.json())["data"]["items"]
        assert items[0]["subject"] == "Алгебра"
        assert items[0]["observed_at"].endswith("Z")
        # Экран «Домашнее задание»: вес, тема урока и время публикации.
        assert items[0]["weight"] == 2
        assert items[0]["topic"] == "Дроби"
        assert items[0]["published_at"] == "2026-03-01T09:30:00+00:00"


class TestNewFeatures:
    async def test_notification_toggle_is_saved(self, client, users, token):
        response = await client.post(
            f"{BASE}/api/settings/notifications",
            params={"token": token},
            json={"key": "mail", "value": True},
        )
        assert (await response.json())["data"]["mail"] is True
        assert (await users.get(1)).notifications.mail is True

    async def test_timezone_is_validated_and_saved(self, client, users, token):
        url = f"{BASE}/api/settings/timezone"
        bad = await client.post(url, params={"token": token}, json={"timezone": "Mars/Base"})
        assert (await bad.json())["ok"] is False
        await client.post(url, params={"token": token}, json={"timezone": "Asia/Omsk"})
        assert (await users.get(1)).timezone == "Asia/Omsk"
        # Приложение присылает пояс устройства — годится любой из IANA.
        await client.post(url, params={"token": token}, json={"timezone": "Europe/Berlin"})
        assert (await users.get(1)).timezone == "Europe/Berlin"
        for junk in ("UTC", "../etc/passwd", "Europe/" + "x" * 80):
            bad = await client.post(url, params={"token": token}, json={"timezone": junk})
            assert (await bad.json())["ok"] is False

    async def test_history_and_status_in_profile(self, client, app_context, token):
        import json as _json

        from app.notifications.outbox import mark_to_dict
        from tests.test_watcher import record

        await app_context.state.save_check(
            1, tracked={}, homework=set(), observed_new_homework=set(), absences=None,
            outbox=[], history=[("changed", _json.dumps(mark_to_dict(record(0))), "3", "5")],
            baseline_done=False,
        )
        await app_context.state.set_watch_status(1, ok=True)
        history = await (await client.get(f"{BASE}/api/history", params={"token": token})).json()
        item = history["data"]["items"][0]
        assert (item["kind"], item["old"], item["new"]) == ("changed", "3", "5")
        assert item["seen"].endswith("Z")
        profile = await (await client.get(f"{BASE}/api/profile", params={"token": token})).json()
        assert profile["data"]["status"]["last_ok_at"].endswith("Z")
        assert profile["data"]["timezone"] == "Europe/Moscow"

    async def test_homework_done_roundtrip(self, client, token):
        url = f"{BASE}/api/homework/done"
        bad = await client.post(url, params={"token": token}, json={"key": "../x", "done": True})
        assert (await bad.json())["ok"] is False
        await client.post(url, params={"token": token}, json={"key": "0123456789abcdef", "done": True})
        keys = (await (await client.get(url, params={"token": token})).json())["data"]["keys"]
        assert keys == ["0123456789abcdef"]
        await client.post(url, params={"token": token}, json={"key": "0123456789abcdef", "done": False})
        keys = (await (await client.get(url, params={"token": token})).json())["data"]["keys"]
        assert keys == []

    async def test_calendar_feed(self, client, app_context, token):
        from app.domain.records import DiaryDay, Lesson

        async def fetch_diary(user, **kwargs):
            return [DiaryDay(day=dt.date(2026, 10, 6), lessons=[
                Lesson(number=1, subject="Алгебра", start="08:00", end="08:40"),
            ])]

        app_context.diary.fetch_diary = fetch_diary
        response = await client.get(f"{BASE}/api/calendar.ics", params={"token": token})
        assert response.content_type == "text/calendar"
        assert "SUMMARY:1. Алгебра" in await response.text()
        link = await (await client.get(f"{BASE}/api/calendar-link", params={"token": token})).json()
        assert link["data"]["webcal"].startswith("webcal://")

    async def test_prefs_are_shared_and_merged(self, client, token):
        url = f"{BASE}/api/prefs"
        assert (await (await client.get(url, params={"token": token})).json())["data"] == {}
        await client.post(url, params={"token": token}, json={"accent": "#A8434F", "theme": "dark"})
        response = await client.post(url, params={"token": token}, json={"weights": [1, 2, 1.5, 2]})
        data = (await response.json())["data"]
        assert data == {"accent": "#a8434f", "theme": "dark", "weights": [1, 2, 1.5]}
        assert (await (await client.get(url, params={"token": token})).json())["data"] == data

    async def test_design_pref_is_validated(self, client, token):
        url = f"{BASE}/api/prefs"
        response = await client.post(url, params={"token": token}, json={"design": "new"})
        assert (await response.json())["data"]["design"] == "new"
        response = await client.post(url, params={"token": token}, json={"design": "retro"})
        assert response.status == 400

    async def test_weekends_pref_is_validated(self, client, token):
        url = f"{BASE}/api/prefs"
        response = await client.post(url, params={"token": token}, json={"weekends": "show"})
        assert (await response.json())["data"]["weekends"] == "show"
        response = await client.post(url, params={"token": token}, json={"weekends": True})
        assert response.status == 400

    async def test_grade_thresholds_are_saved(self, client, token):
        url = f"{BASE}/api/prefs"
        thresholds = {
            "all": {"5": 4.6, "4": 3.6, "3": 2.6},
            "subjects": {"Алгебра": {"5": 4.5, "4": 3.5, "3": 2.5}},
        }
        response = await client.post(url, params={"token": token}, json={"thresholds": thresholds})
        assert (await response.json())["data"]["thresholds"] == thresholds
        # Общие пороги необязательны: свои только у одного предмета.
        only_subject = {"subjects": {"Физика": {"5": 4.7, "4": 3.7, "3": 2.7}}}
        response = await client.post(url, params={"token": token}, json={"thresholds": only_subject})
        assert (await response.json())["data"]["thresholds"] == only_subject

    async def test_bad_prefs_are_rejected(self, client, token):
        url = f"{BASE}/api/prefs"
        for body in ({"accent": "red"}, {"theme": "pink"}, {"weights": [0]}, {"weights": "1"}, {},
                     {"thresholds": {"all": {"5": 3.5, "4": 3.5, "3": 2.5}}},
                     {"thresholds": {"subjects": {"Алгебра": {"5": 6, "4": 3.5, "3": 2.5}}}}):
            response = await client.post(url, params={"token": token}, json=body)
            assert response.status == 400, body

    async def test_unknown_notification_key_is_rejected(self, client, token):
        response = await client.post(
            f"{BASE}/api/settings/notifications", params={"token": token}, json={"key": "enabled"}
        )
        assert response.status == 400

    async def test_mail_send_requires_recipient_and_subject(self, client, token):
        response = await client.post(
            f"{BASE}/api/mail/send", params={"token": token}, json={"to": [], "subject": "Тема"}
        )
        assert response.status == 400
        response = await client.post(
            f"{BASE}/api/mail/send", params={"token": token}, json={"to": ["abc"], "subject": ""}
        )
        assert response.status == 400

    async def test_mail_send_goes_to_school(self, client, app_context, token):
        sent = []

        async def send_mail(user, to, subject, text, attachments=None, *, notify=False):
            sent.append((to, subject, text))

        app_context.diary.send_mail = send_mail
        response = await client.post(
            f"{BASE}/api/mail/send",
            params={"token": token},
            json={"to": ["abc"], "subject": "Вопрос", "text": "Здравствуйте"},
        )
        assert (await response.json())["ok"] is True
        assert sent == [(["abc"], "Вопрос", "Здравствуйте")]

    async def test_pwa_link_is_personal(self, client, token):
        response = await client.get(f"{BASE}/api/pwa-link", params={"token": token})
        assert f"token={token}" in (await response.json())["data"]["url"]

    async def test_demo_page_needs_no_login(self, client):
        response = await client.get(f"{BASE}/demo")
        assert response.status == 200
        assert "demo: true" in await response.text()

    async def test_teacher_comes_from_assignment_card(self, client, app_context, token):
        from app.domain.records import DiaryDay, Lesson

        async def fetch_diary(user, **kwargs):
            lesson = Lesson(number=1, subject="Алгебра", assignment_ids=(42,))
            return [DiaryDay(day=dt.date(2026, 9, 23), lessons=[lesson])]

        app_context.diary.fetch_diary = fetch_diary
        response = await client.get(f"{BASE}/api/diary", params={"token": token})
        lesson = (await response.json())["data"]["days"][0]["lessons"][0]
        assert lesson["teacher"] == "Учитель"


class TestErrors:
    async def test_school_error_is_json_not_html(self, client, app_context, token):
        app_context.diary.error = NetSchoolError(Reason.SERVER_UNAVAILABLE, "Школа не отвечает")
        response = await client.get(f"{BASE}/api/diary", params={"token": token})
        assert response.status == 503
        body = await response.json()
        assert body["ok"] is False
        assert body["reason"] == "server"

    async def test_auth_error_uses_401(self, client, app_context, token):
        app_context.diary.error = NetSchoolError(Reason.AUTH, "Войдите заново")
        response = await client.get(f"{BASE}/api/diary", params={"token": token})
        assert response.status == 401
        assert (await response.json())["reason"] == "auth"

    async def test_unexpected_error_does_not_leak_details(self, client, app_context, token):
        app_context.diary.error = ValueError("пароль в тексте исключения")
        response = await client.get(f"{BASE}/api/diary", params={"token": token})
        assert response.status == 500
        assert "пароль" not in await response.text()


class TestCache:
    async def test_slow_refresh_serves_saved_data_and_runs_only_once(
        self, client, app_context, token, monkeypatch
    ):
        from app.web import api

        monkeypatch.setattr(api, "CACHE_RESPONSE_WAIT", 0.01)
        monkeypatch.setattr(api, "REFRESH_RESPONSE_WAIT", 0.01)
        saved = {"days": [{"date": "2026-09-30"}], "week": 0}
        await app_context.cache.put(1, "diary:0", saved)
        release = asyncio.Event()
        calls = 0

        async def slow(*args, **kwargs):
            nonlocal calls
            calls += 1
            await release.wait()
            return []

        monkeypatch.setattr(app_context.diary, "fetch_diary", slow)
        for _ in range(2):
            response = await asyncio.wait_for(
                client.get(f"{BASE}/api/diary", params={"token": token, "refresh": "1"}),
                timeout=1,
            )
            body = await response.json()
            assert body["data"] == saved
            assert body["stale"] is True
            assert body["refreshing"] is True
        assert calls == 1
        tasks = list(app_context.cache_refreshes.values())
        assert len(tasks) == 1
        release.set()
        await asyncio.gather(*tasks)
        response = await client.get(f"{BASE}/api/diary", params={"token": token})
        body = await response.json()
        assert body["stale"] is False
        assert body["data"]["days"] == []

    async def test_pull_to_refresh_waits_for_fresh_data(
        self, client, app_context, token, monkeypatch
    ):
        from app.web import api

        # Обычное открытие отдаёт кэш сразу, а жест обновления ждёт школу:
        # раньше он тоже получал старое через 0,2 с, и экран не менялся.
        monkeypatch.setattr(api, "CACHE_RESPONSE_WAIT", 0.01)
        await app_context.cache.put(1, "diary:0", {"days": [{"date": "2026-09-30"}], "week": 0})

        async def slow(*args, **kwargs):
            await asyncio.sleep(0.1)
            return []

        monkeypatch.setattr(app_context.diary, "fetch_diary", slow)
        response = await client.get(f"{BASE}/api/diary", params={"token": token, "refresh": "1"})
        body = await response.json()
        assert body["stale"] is False
        assert body["data"]["days"] == []

    async def test_background_refresh_does_not_restore_previous_child_cache(
        self, client, app_context, token, monkeypatch
    ):
        from dataclasses import replace
        from app.web import api

        monkeypatch.setattr(api, "CACHE_RESPONSE_WAIT", 0.01)
        monkeypatch.setattr(api, "REFRESH_RESPONSE_WAIT", 0.01)
        await app_context.cache.put(1, "diary:0", {"days": [], "week": 0})
        release = asyncio.Event()

        async def slow(*args, **kwargs):
            await release.wait()
            return []

        monkeypatch.setattr(app_context.diary, "fetch_diary", slow)
        await client.get(f"{BASE}/api/diary", params={"token": token, "refresh": "1"})
        tasks = list(app_context.cache_refreshes.values())
        user = await app_context.users.get(1)
        await app_context.users.save(replace(user, selected_student_id=99))
        await app_context.cache.clear(1)
        release.set()
        await asyncio.gather(*tasks)
        assert await app_context.cache.get(1, "diary:0") is None

    @pytest.mark.parametrize("section", ["diary", "homework", "marks"])
    async def test_real_week_outage_preserves_saved_data(
        self, client, app_context, token, monkeypatch, section
    ):
        from app.netschool.service import DiaryService, _monday_of, msk_today
        from app.netschool.session import SessionPool
        from tests.test_service import DiaryClient, day, _no_sleep
        import app.netschool.service as module

        monkeypatch.setattr(module.asyncio, "sleep", _no_sleep)
        pool = SessionPool(
            app_context.settings.netschool, app_context.sessions,
            client_factory=lambda url, proxy: DiaryClient(url, proxy),
        )
        app_context.diary = DiaryService(pool, app_context.users)
        user = await app_context.users.get(1)
        today = msk_today()
        async with pool.acquire(user) as school:
            school.week_payloads[_monday_of(today)] = [day(today)]
        try:
            params = {"token": token, "refresh": "1"}
            initial = await client.get(f"{BASE}/api/{section}", params=params)
            saved = (await initial.json())["data"]
            cached_before = await app_context.cache.get(
                1, "marks:year" if section == "marks" else "diary:0" if section == "diary" else section
            )
            school.week_errors[_monday_of(today)] = Exception("503 Service Unavailable")
            response = await client.get(f"{BASE}/api/{section}", params=params)
            body = await response.json()
            assert response.status == 200
            assert body["stale"] is True
            assert body["reason"] == "server"
            assert body["data"] == saved
            cached_after = await app_context.cache.get(
                1, "marks:year" if section == "marks" else "diary:0" if section == "diary" else section
            )
            assert cached_after == cached_before
            school.week_errors.clear()
            recovered = await client.get(f"{BASE}/api/{section}", params=params)
            assert (await recovered.json())["stale"] is False
        finally:
            await pool.close_all()
    async def test_second_request_uses_cache(self, client, app_context, token):
        app_context.diary.marks = [record(0)]
        await client.get(f"{BASE}/api/marks", params={"token": token})
        # Школа отвалилась — но кэш свежий, ответ должен прийти как обычно.
        app_context.diary.error = NetSchoolError(Reason.SERVER_UNAVAILABLE, "лежит")
        response = await client.get(f"{BASE}/api/marks", params={"token": token})
        body = await response.json()
        assert response.status == 200
        assert body["stale"] is False

    async def test_stale_cache_served_when_school_is_down(self, client, app_context, token):
        app_context.diary.marks = [record(0)]
        await client.get(f"{BASE}/api/marks", params={"token": token})
        app_context.diary.error = NetSchoolError(Reason.SERVER_UNAVAILABLE, "лежит")
        # refresh обходит кэш по свежести, но при ошибке отдаётся сохранённое.
        response = await client.get(f"{BASE}/api/marks", params={"token": token, "refresh": "1"})
        body = await response.json()
        assert response.status == 200
        assert body["stale"] is True
        assert body["reason"] == "server"

    async def test_no_cache_and_school_down_is_an_error(self, client, app_context, token):
        app_context.diary.error = NetSchoolError(Reason.SERVER_UNAVAILABLE, "лежит")
        response = await client.get(f"{BASE}/api/marks", params={"token": token})
        assert response.status == 503


class TestStudentSwitch:
    async def test_switch_resets_tracking(self, client, app_context, users, marks, token):
        from app.domain.records import TrackedMark

        await users.save(
            make_user(
                available_students=(Student(10, "А"), Student(11, "Б")),
                selected_student_id=10,
            )
        )
        r = record(0)
        await marks.replace_marks(1, {r.identity: TrackedMark(r)})

        response = await client.post(
            f"{BASE}/api/student", params={"token": token}, json={"id": 11}
        )
        assert response.status == 200
        assert (await users.get(1)).selected_student_id == 11
        assert await marks.load_marks(1) == {}
        assert await marks.is_baseline_pending(1) is True

    async def test_unknown_student_is_rejected(self, client, users, token):
        await users.save(make_user(available_students=(Student(10, "А"),)))
        response = await client.post(f"{BASE}/api/student", params={"token": token}, json={"id": 99})
        assert response.status == 404

    async def test_missing_id_is_rejected(self, client, token):
        response = await client.post(f"{BASE}/api/student", params={"token": token}, json={})
        assert response.status == 400


class TestPush:
    async def test_key_unavailable_without_vapid(self, client, token):
        response = await client.get(f"{BASE}/api/push/key", params={"token": token})
        assert response.status == 503

    async def test_subscribe_requires_complete_data(self, client, token):
        response = await client.post(
            f"{BASE}/api/push/subscribe", params={"token": token}, json={"endpoint": "https://x"}
        )
        assert response.status == 400

    async def test_subscribe_and_unsubscribe(self, client, app_context, token):
        payload = {
            "endpoint": "https://push.example/1",
            "keys": {"p256dh": "ключ", "auth": "секрет"},
        }
        response = await client.post(f"{BASE}/api/push/subscribe", params={"token": token}, json=payload)
        assert response.status == 200
        assert len(await app_context.miniapp.push_subscriptions(1)) == 1

        await client.post(
            f"{BASE}/api/push/unsubscribe",
            params={"token": token},
            json={"endpoint": payload["endpoint"]},
        )
        assert await app_context.miniapp.push_subscriptions(1) == []


class TestTelegramMiniApp:
    """Вход мини-приложением: сервер опознаёт человека по подписанной
    initData, без токена в адресе."""

    @staticmethod
    def init_data(telegram_id: int, token: str = "t"):
        import hashlib
        import hmac
        import json as _json
        import time
        from urllib.parse import urlencode

        fields = {
            "auth_date": str(int(time.time())),
            "user": _json.dumps({"id": telegram_id, "first_name": "Иван"}, separators=(",", ":")),
        }
        check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        return urlencode(fields)

    async def test_init_data_alone_is_enough(self, client, users):
        await users.save(make_user(student_name="Иванов И."))
        response = await client.get(
            f"{BASE}/api/profile",
            headers={"X-Telegram-Init-Data": self.init_data(1)},
        )
        assert response.status == 200
        assert (await response.json())["data"]["name"] == "Иванов И."

    async def test_forged_init_data_is_rejected(self, client, users):
        await users.save(make_user())
        # Подпись чужим токеном — ровно попытка выдать себя за другого.
        response = await client.get(
            f"{BASE}/api/profile",
            headers={"X-Telegram-Init-Data": self.init_data(1, token="чужой")},
        )
        assert response.status == 401

    async def test_init_data_of_unknown_user_asks_to_login(self, client):
        response = await client.get(
            f"{BASE}/api/profile",
            headers={"X-Telegram-Init-Data": self.init_data(777)},
        )
        assert response.status == 401
        assert (await response.json())["reason"] == "login"

    async def test_init_data_wins_over_token(self, client, users, app_context):
        """Токен из ссылки не должен перебивать подписанную личность."""
        await users.save(make_user(1, student_name="Первый"))
        await users.save(make_user(2, student_name="Второй"))
        other_token = await app_context.miniapp.issue_token(2)

        response = await client.get(
            f"{BASE}/api/profile",
            params={"token": other_token},
            headers={"X-Telegram-Init-Data": self.init_data(1)},
        )
        assert (await response.json())["data"]["name"] == "Первый"

    async def test_attachment_accepts_init_data_in_query(self, client, users):
        # Файл скачивает браузер обычной ссылкой — заголовок туда не положить.
        await users.save(make_user())
        response = await client.get(
            f"{BASE}/api/attachment/1", params={"tgWebAppData": self.init_data(1)}
        )
        assert response.status == 200

    async def test_attachment_keeps_name_and_type(self, client, users):
        # Без имени файл сохранялся как «attachment-1» и не открывался как docx.
        await users.save(make_user())
        response = await client.get(
            f"{BASE}/api/attachment/1",
            params={"tgWebAppData": self.init_data(1), "name": "../ССП \"1\".docx"},
        )
        assert response.status == 200
        assert response.headers["Content-Type"] == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        disposition = response.headers["Content-Disposition"]
        assert "filename*=UTF-8''_%D0%A1%D0%A1%D0%9F%20_1_.docx" in disposition
        assert 'filename="____ _1_.docx"' in disposition


class TestDiaryScreen:
    """Экран дневника: неделя целиком с расписанием."""

    async def test_diary_returns_lessons(self, client, app_context, token):
        app_context.diary.marks = [record(0)]
        response = await client.get(f"{BASE}/api/diary", params={"token": token})
        data = (await response.json())["data"]
        assert "days" in data
        assert data["week"] == 0

    async def test_week_offset_is_accepted(self, client, app_context, token):
        response = await client.get(f"{BASE}/api/diary", params={"token": token, "week": "-2"})
        assert (await response.json())["data"]["week"] == -2

    async def test_absurd_offset_is_clamped(self, client, token):
        # Иначе случайный параметр заставил бы перебирать сотни недель.
        response = await client.get(f"{BASE}/api/diary", params={"token": token, "week": "9999"})
        assert (await response.json())["data"]["week"] == 30

    async def test_broken_offset_falls_back_to_current(self, client, token):
        response = await client.get(f"{BASE}/api/diary", params={"token": token, "week": "позавчера"})
        assert (await response.json())["data"]["week"] == 0

    async def test_weeks_are_cached_separately(self, client, app_context, token):
        """Соседние недели не должны вытеснять друг друга из кэша."""
        await client.get(f"{BASE}/api/diary", params={"token": token, "week": "0"})
        await client.get(f"{BASE}/api/diary", params={"token": token, "week": "-1"})
        app_context.diary.error = NetSchoolError(Reason.SERVER_UNAVAILABLE, "лежит")

        for week in ("0", "-1"):
            response = await client.get(f"{BASE}/api/diary", params={"token": token, "week": week})
            body = await response.json()
            assert response.status == 200
            assert body["stale"] is False, f"неделя {week} потеряла кэш"


class TestMarksQuarters:
    async def test_marks_carry_quarter(self, client, app_context, token):
        app_context.diary.marks = [record(0, date=dt.date(2026, 11, 20))]
        response = await client.get(f"{BASE}/api/marks", params={"token": token})
        data = (await response.json())["data"]
        assert data["subjects"][0]["marks"][0]["quarter"] == "q2"
        assert data["current_quarter"] in {"q1", "q2", "q3", "q4"}


class TestMailMessage:
    async def test_message_is_returned(self, client, app_context, users, token):
        app_context.diary.message = {
            "id": 7, "subject": "Тема", "author": "Учитель",
            "text": "Текст письма", "sent": "2026-03-02", "read": False, "attachments": [],
        }
        response = await client.get(f"{BASE}/api/mail/7", params={"token": token})
        assert response.status == 200
        assert (await response.json())["data"]["text"] == "Текст письма"

    async def test_bad_id_is_rejected(self, client, token):
        response = await client.get(f"{BASE}/api/mail/не-число", params={"token": token})
        assert response.status in (400, 404)

class TestFastStartup:
    async def test_compact_diary_requests_only_current_week(self, client, app_context, token):
        calls = []
        async def fetch(user, **kwargs):
            calls.append(kwargs)
            return []
        app_context.diary.fetch_diary = fetch
        response = await client.get(f'{BASE}/api/diary', params={'token': token, 'window': 'current'})
        assert response.status == 200
        assert calls[0]['weeks_back'] == 0
        assert calls[0]['weeks_forward'] == 0

    async def test_compact_diary_reuses_full_window_cache(self, client, app_context, token):
        saved = {'days': [{'date': '2026-10-07', 'lessons': []}], 'week': 0}
        await app_context.cache.put(1, 'diary:0', saved)
        async def fail(*args, **kwargs): raise AssertionError('school must not be queried')
        app_context.diary.fetch_diary = fail
        response = await client.get(f'{BASE}/api/diary', params={'token': token, 'window': 'current'})
        assert (await response.json())['data'] == saved

    async def test_compact_diary_does_not_wait_for_teacher_cards(self, client, app_context, token):
        from app.domain.records import DiaryDay, Lesson
        release = asyncio.Event()
        async def fetch(user, **kwargs):
            return [DiaryDay(day=dt.date(2026, 10, 7), lessons=[Lesson(number=1, subject='Математика', assignment_ids=(42,))])]
        async def teachers(user, wanted):
            await release.wait()
            return {'Математика': 'Учитель'}
        app_context.diary.fetch_diary = fetch
        app_context.diary.fetch_teachers = teachers
        response = await asyncio.wait_for(client.get(f'{BASE}/api/diary', params={'token': token, 'window': 'current'}), timeout=1)
        assert response.status == 200
        assert (await response.json())['data']['days'][0]['lessons'][0]['subject'] == 'Математика'
        release.set()
        await asyncio.gather(*list(app_context.cache_refreshes.values()))
        assert (await app_context.cache.get(1, 'teachers'))[0] == {'Математика': 'Учитель'}

    async def test_boot_has_visible_splash_and_preloads_only_lock_modules(self, client):
        response = await client.get(f'{BASE}/')
        html = await response.text()
        assert 'id="bootSplash"' in html
        assert '/js/boot.js" type="module"' in html
        preloads = re.findall(r'<link rel="modulepreload" href="([^"]+)"', html)
        assert any('/js/lock.js' in path for path in preloads)
        assert not any('/js/totals.js' in path for path in preloads)

    async def test_static_compression_and_range_requests(self, client):
        from app.web.server import STATIC_VERSION
        path = f'{BASE}/static/v{STATIC_VERSION}/app.css'
        response = await client.get(path, headers={'Accept-Encoding': 'gzip'})
        assert response.status == 200
        assert response.headers.get('Content-Encoding') == 'gzip'
        assert 'Accept-Encoding' in response.headers['Vary']
        assert 'pin-biometric' in await response.text()
        partial = await client.get(path, headers={'Accept-Encoding': 'gzip', 'Range': 'bytes=0-99'})
        assert partial.status == 206
        assert 'Content-Encoding' not in partial.headers
        assert len(await partial.read()) == 100


class TestSecurityHeaders:
    async def test_pages_and_api_carry_security_headers(self, client):
        page = await client.get(f'{BASE}/')
        assert page.headers['X-Content-Type-Options'] == 'nosniff'
        assert page.headers['Referrer-Policy'] == 'no-referrer'
        assert "frame-ancestors 'self'" in page.headers['Content-Security-Policy']
        api = await client.get(f'{BASE}/api/profile')
        assert api.headers['Cache-Control'] == 'no-store'
