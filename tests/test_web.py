"""Тесты веб-приложения.

Проверяются, в частности, авторизация по токену и поведение при недоступной
школе: раньше при ошибке в браузер улетал то JSON, то HTML-страница Flask,
то текст исключения.
"""

import asyncio
import datetime as dt
import json
import re
import time
from dataclasses import replace

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
async def client(app_context, tmp_path) -> TestClient:
    # Кэш вложений лежит в data_dir: у каждого теста свой, иначе файл
    # из одного теста отдавался бы в другом.
    from dataclasses import replace
    app_context.settings = replace(app_context.settings, data_dir=tmp_path)
    server = TestServer(create_app(app_context))
    client = TestClient(server)
    await client.start_server()
    yield client
    await client.close()


@pytest.fixture
async def token(app_context, users):
    await users.save(make_user(student_name="Иванов И."))
    return await app_context.miniapp.issue_token(1)


class TestSurvey:
    async def _show(self, client, headers):
        response = await client.post(f"{BASE}/api/notices/survey", headers=headers)
        assert response.headers["Cache-Control"] == "no-store"
        return (await response.json())["data"]["show"]

    async def test_later_keeps_survey_and_answer_is_accepted_once(self, client, token, db):
        await db.execute("INSERT INTO app_survey (telegram_id) VALUES (?)", (1,))
        headers = {"X-Netschool-Token": token}
        # «Не сейчас» ничего не отправляет — опрос будет и при следующем открытии.
        assert await self._show(client, headers) is True
        assert await self._show(client, headers) is True
        for bad in (0, 11, "7", 7.5, None):
            response = await client.post(f"{BASE}/api/survey", headers=headers, json={"score": bad})
            assert response.status == 400
        assert (await client.post(f"{BASE}/api/survey", headers=headers, json={"score": 8})).status == 200
        assert (await client.post(f"{BASE}/api/survey", headers=headers, json={"score": 2})).status == 409
        row = await db.fetch_one("SELECT score, shown_at FROM app_survey WHERE telegram_id=1")
        assert row["score"] == 8 and row["shown_at"]
        assert await self._show(client, headers) is False

    async def test_decline_removes_survey_for_good(self, client, token, db):
        await db.execute("INSERT INTO app_survey (telegram_id) VALUES (?)", (1,))
        headers = {"X-Netschool-Token": token}
        assert await self._show(client, headers) is True
        assert (await client.post(f"{BASE}/api/survey/decline", headers=headers)).status == 200
        assert await self._show(client, headers) is False
        assert (await client.post(f"{BASE}/api/survey", headers=headers, json={"score": 5})).status == 409

    async def test_new_user_never_gets_survey(self, client, token):
        headers = {"X-Netschool-Token": token}
        assert await self._show(client, headers) is False
        assert (await client.post(f"{BASE}/api/survey", headers=headers, json={"score": 5})).status == 409


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

    async def test_halloween_for_everyone_in_season(self, client, app_context, users, token):
        from app.web.api import halloween_season

        await users.save(make_user(2, student_name="Петров П."))
        other = await app_context.miniapp.issue_token(2)
        response = await client.get(f"{BASE}/api/profile", params={"token": other})
        user = await users.get(2)
        assert (await response.json())["data"]["halloween"] is halloween_season(user.today())

    def test_halloween_season(self):
        from app.web.api import halloween_season

        assert halloween_season(dt.date(2026, 10, 18))
        assert halloween_season(dt.date(2026, 10, 31))
        assert halloween_season(dt.date(2026, 11, 8))
        assert not halloween_season(dt.date(2026, 11, 9))
        assert not halloween_season(dt.date(2026, 10, 17))
        assert not halloween_season(dt.date(2026, 6, 1))

    async def test_halloween_notice_once(self, client, app_context, token, monkeypatch):
        from app.web.api import prefs as web_api

        monkeypatch.setattr(web_api, "halloween_season", lambda today: True)
        response = await client.post(f"{BASE}/api/notices/halloween", params={"token": token})
        assert (await response.json())["data"]["show"] is True
        response = await client.post(f"{BASE}/api/notices/halloween", params={"token": token})
        assert (await response.json())["data"]["show"] is False

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

    async def test_guest_index_is_ready_for_search(self, client):
        response = await client.get(f"{BASE}/")
        text = await response.text()
        assert "X-Robots-Tag" not in response.headers
        assert '<link rel="canonical" href="https://example.ru/mini/">' in text
        assert '<meta name="description"' in text
        assert '"@type": "WebApplication"' in text
        assert '"NetSchool"' in text
        assert '"url": "https://example.ru/mini/"' in text

    async def test_index_with_token_is_hidden_from_search(self, client, token):
        response = await client.get(f"{BASE}/", params={"token": token})
        text = await response.text()
        assert response.headers["X-Robots-Tag"] == "noindex"
        # У вошедших прежний заголовок: от него зависит имя иконки на iOS.
        assert "canonical" not in text

    async def test_robots_lets_in_only_public_pages(self, client):
        response = await client.get("/robots.txt")
        text = await response.text()
        assert "Allow: /mini/$" in text
        assert "Allow: /mini/privacy$" in text
        assert "Disallow: /" in text
        assert "Sitemap: https://example.ru/sitemap.xml" in text

    async def test_google_verification_file(self, client):
        response = await client.get("/googled7947c6e3217a546.html")
        assert await response.text() == "google-site-verification: googled7947c6e3217a546.html"
        assert (await client.get("/google0000.html")).status == 404

    async def test_yandex_verification_file(self, client):
        response = await client.get("/yandex_08b9430a7e269f67.html")
        assert "Verification: 08b9430a7e269f67" in await response.text()
        # sgoshka.ru — свой файл.
        response = await client.get("/yandex_cb3c3e55bc7efa33.html")
        assert "Verification: cb3c3e55bc7efa33" in await response.text()
        assert (await client.get("/yandex_0000.html")).status == 404

    async def test_sitemap_lists_main_page(self, client):
        response = await client.get("/sitemap.xml")
        assert response.content_type == "application/xml"
        text = await response.text()
        assert "<loc>https://example.ru/mini/</loc>" in text
        assert "<loc>https://example.ru/mini/status</loc>" in text
        assert "<loc>https://example.ru/mini/privacy</loc>" in text

    async def test_privacy_page(self, client):
        response = await client.get(f"{BASE}/privacy")
        assert response.status == 200
        text = await response.text()
        assert '<link rel="canonical" href="https://example.ru/mini/privacy">' in text
        assert "152-ФЗ" in text
        assert "не связаны с ООО «ИРТех»" in text

    async def test_search_pages_say_unofficial(self, client):
        index = await (await client.get(f"{BASE}/")).text()
        # В выдаче по «Сетевому городу» сайт не должен выдавать себя за дневник.
        assert "неофициальное приложение)</title>" in index
        status = await (await client.get(f"{BASE}/status")).text()
        assert "Неофициальный сервис." in status
        assert f"{BASE}/privacy" in status

    async def test_status_page_has_canonical(self, client):
        text = await (await client.get(f"{BASE}/status")).text()
        assert '<link rel="canonical" href="https://example.ru/mini/status">' in text
        # По этим словам страницу и ищут.
        assert "<title>Сетевой город не работает?" in text
        assert "Почему не работает «Сетевой город»" in text

    async def test_index_with_token_renders_app(self, client, token):
        response = await client.get(f"{BASE}/", params={"token": token})
        assert response.status == 200
        text = await response.text()
        assert "js/app.js" in text
        # Имя приезжает в скрипт-блоке, а не в разметке, и Jinja экранирует
        # кириллицу в \uXXXX — сравниваем в том же виде.
        assert json.dumps("Иванов И.") in text
        # Домены для вложений в PWA: на свой iOS открыл бы файл внутри приложения.
        assert "attachmentHosts: [" in text

    def test_attachment_hosts_prefer_files_url_without_duplicates(self, app_context):
        from dataclasses import replace

        web_settings = replace(
            app_context.settings.web, public_url="https://ns.ikrya.ru",
            mirror_url="https://ns.ikrya.ru", files_url="https://ns2.ikrya.ru",
        )
        assert web_settings.attachment_hosts == ["https://ns2.ikrya.ru", "https://ns.ikrya.ru"]
        assert replace(web_settings, files_url="", mirror_url="").attachment_hosts == ["https://ns.ikrya.ru"]

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

    async def test_server_time_without_login(self, client, monkeypatch):
        from app.web import clock
        monkeypatch.setattr(clock, "_refresh", lambda: None)
        monkeypatch.setattr(clock, "_offset", None)
        monkeypatch.setattr(clock, "_checked_at", 0.0)
        before = time.time()
        response = await client.get(f"{BASE}/time")
        assert response.status == 200
        assert response.headers["Cache-Control"] == "no-store"
        # Таймер урока сверяет по ним часы телефона.
        payload = await response.json()
        assert before <= payload["now"] <= time.time()
        assert payload["synced"] is False

    async def test_server_time_corrected_by_ntp(self, client, monkeypatch):
        # Часы сервера спешили на 3 с — отдаём точное время.
        from app.web import clock
        monkeypatch.setattr(clock, "_refresh", lambda: -3.0)
        monkeypatch.setattr(clock, "_offset", None)
        monkeypatch.setattr(clock, "_checked_at", 0.0)
        await client.get(f"{BASE}/time")
        await clock._task
        before = time.time()
        payload = await (await client.get(f"{BASE}/time")).json()
        assert payload["synced"] is True
        assert before - 3.0 <= payload["now"] <= time.time() - 3.0

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

    async def test_break_loads_next_period_and_hides_partial_counts(self, client, app_context, token, monkeypatch):
        from app.domain.models import User
        from app.domain.records import DiaryDay, Lesson

        # Осенние каникулы: первая четверть кончилась, вторая ещё не началась.
        now = dt.datetime(2026, 10, 28, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=3)))
        monkeypatch.setattr(User, "now", lambda self: now)
        app_context.diary.terms = [
            {"name": f"{n} четверть", "startDate": start, "endDate": end}
            for n, start, end in (
                (1, "2026-09-01", "2026-10-24"), (2, "2026-11-05", "2026-12-28"),
                (3, "2027-01-12", "2027-03-20"), (4, "2027-03-30", "2027-05-26"),
            )
        ]
        asked = []

        async def fetch_period(user, start, end, **kwargs):
            asked.append(end)
            return [DiaryDay(day=day, lessons=[Lesson(number=1, subject="Алгебра")])
                    for day in (dt.date(2026, 9, 7), dt.date(2026, 11, 9), dt.date(2026, 11, 10), dt.date(2027, 1, 12))]

        app_context.diary.fetch_period = fetch_period
        data = (await (await client.get(f"{BASE}/api/marks", params={"token": token})).json())["data"]
        # Вторая четверть загружена целиком, третья — нет и без числа уроков.
        assert asked == [dt.date(2027, 1, 11)]
        assert data["subjects"][0]["lessons"] == {"q1": {"total": 1, "done": 1}, "q2": {"total": 2, "done": 0}}

        # Школа не отдала недели до конца второй четверти — числа уроков нет.
        async def truncated(user, start, end, **kwargs):
            return [DiaryDay(day=day, lessons=[Lesson(number=1, subject="Алгебра")])
                    for day in (dt.date(2026, 9, 7), dt.date(2026, 11, 9))]

        app_context.diary.fetch_period = truncated
        params = {"token": token, "refresh": "1"}
        data = (await (await client.get(f"{BASE}/api/marks", params=params)).json())["data"]
        assert data["subjects"][0]["lessons"] == {"q1": {"total": 1, "done": 1}}

    async def test_upcoming_period_lessons_are_loaded_on_demand(self, client, app_context, token, monkeypatch):
        from app.domain.models import User
        from app.domain.records import DiaryDay, Lesson

        # Первая четверть ещё идёт — «Итоги» вторую не загружают.
        now = dt.datetime(2026, 10, 23, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=3)))
        monkeypatch.setattr(User, "now", lambda self: now)
        app_context.diary.terms = [
            {"name": f"{n} четверть", "startDate": start, "endDate": end}
            for n, start, end in (
                (1, "2026-09-01", "2026-10-24"), (2, "2026-11-05", "2026-12-28"),
                (3, "2027-01-12", "2027-03-20"), (4, "2027-03-30", "2027-05-26"),
            )
        ]
        asked = []
        weeks = [dt.date(2026, 11, 9), dt.date(2026, 11, 10), dt.date(2026, 12, 28)]

        async def fetch_period(user, start, end, **kwargs):
            asked.append((start, end))
            return [DiaryDay(day=day, lessons=[Lesson(number=1, subject="Алгебра"),
                                               Lesson(number=2, subject="Кружок", is_elective=True)])
                    for day in weeks]

        app_context.diary.fetch_period = fetch_period
        response = await client.get(f"{BASE}/api/marks/lessons", params={"token": token, "quarter": "q2"})
        data = (await response.json())["data"]
        assert asked == [(dt.date(2026, 11, 5), dt.date(2027, 1, 11))]
        assert data["lessons"] == {"Алгебра": {"total": 3, "done": 0}}

        # Школа не отдала последние недели — числа нет, а не заниженное.
        weeks[:] = weeks[:2]
        params = {"token": token, "quarter": "q2", "refresh": "1"}
        data = (await (await client.get(f"{BASE}/api/marks/lessons", params=params)).json())["data"]
        assert data["lessons"] is None
        bad = await client.get(f"{BASE}/api/marks/lessons", params={"token": token, "quarter": "x"})
        assert (await bad.json())["ok"] is False

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

    async def test_homework_comment(self, client, app_context, token):
        app_context.diary.comments = {34192144: "выполнить диагностику"}
        response = await client.get(f"{BASE}/api/homework/34192144/comment", params={"token": token})
        assert (await response.json())["data"]["comment"] == "выполнить диагностику"
        bad = await client.get(f"{BASE}/api/homework/abc/comment", params={"token": token})
        assert bad.status in (400, 404)


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
        assert (item["date"], item["lesson"]) == ("2026-03-02", 1)
        assert item["seen"].endswith("Z")
        profile = await (await client.get(f"{BASE}/api/profile", params={"token": token})).json()
        assert profile["data"]["status"]["last_ok_at"].endswith("Z")
        assert profile["data"]["timezone"] == "Europe/Moscow"

    async def test_manual_diary_load_counts_as_check(self, client, app_context, token):
        next_at = dt.datetime(2030, 1, 1, 12, 0)
        await app_context.state.set_watch_status(1, ok=False, error="timeout", next_check_at=next_at)
        await client.get(f"{BASE}/api/diary", params={"token": token})
        status = await app_context.state.watch_status(1)
        assert status.last_ok_at is not None and status.last_ok_at >= status.last_error_at
        assert status.next_check_at == next_at

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

    async def test_homework_done_reports_first_marking_day(self, client, token):
        url = f"{BASE}/api/homework/done"
        assert (await (await client.get(url, params={"token": token})).json())["data"]["since"] is None
        await client.post(url, params={"token": token}, json={"key": "0123456789abcdef", "done": True})
        since = (await (await client.get(url, params={"token": token})).json())["data"]["since"]
        assert since == dt.datetime.now(dt.timezone.utc).date().isoformat()

    async def test_due_from_pref_is_validated(self, client, token):
        url = f"{BASE}/api/prefs"
        ok = await client.post(url, params={"token": token}, json={"dueFrom": "2026-10-28"})
        assert (await ok.json())["ok"] is True
        bad = await client.post(url, params={"token": token}, json={"dueFrom": "вчера"})
        assert (await bad.json())["ok"] is False

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
        # Новая ссылка — свой токен календаря, а не токен приложения.
        calendar = link["data"]["url"].split("token=")[1]
        assert calendar != token
        again = await (await client.get(f"{BASE}/api/calendar-link", params={"token": token})).json()
        assert again["data"]["url"] == link["data"]["url"]
        response = await client.get(f"{BASE}/api/calendar.ics", params={"token": calendar})
        assert "SUMMARY:1. Алгебра" in await response.text()
        # Кроме расписания он ничего не открывает — ни API, ни страницу.
        assert (await client.get(f"{BASE}/api/prefs", params={"token": calendar})).status == 401
        assert await app_context.miniapp.resolve_token(calendar) is None
        # С защитой входа календарь по своему токену работает (PIN он ввести
        # не может), а прежняя ссылка с токеном приложения — как раньше.
        await app_context.db.execute(
            "INSERT INTO app_locks (telegram_id, salt, password_hash) VALUES (1, ?, ?)", (b"s" * 16, b"h"))
        response = await client.get(f"{BASE}/api/calendar.ics", params={"token": calendar})
        assert response.status == 200
        assert (await client.get(f"{BASE}/api/calendar.ics", params={"token": token})).status == 423

    async def test_prefs_are_shared_and_merged(self, client, token):
        url = f"{BASE}/api/prefs"
        assert (await (await client.get(url, params={"token": token})).json())["data"] == {}
        await client.post(url, params={"token": token}, json={"accent": "#A8434F", "theme": "dark"})
        response = await client.post(url, params={"token": token}, json={"weights": [1, 2, 1.5, 2]})
        data = (await response.json())["data"]
        assert data == {"accent": "#a8434f", "theme": "dark", "weights": [1, 2, 1.5]}
        assert (await (await client.get(url, params={"token": token})).json())["data"] == data

    async def test_halloween_choice_is_shared(self, client, token):
        url = f"{BASE}/api/prefs"
        response = await client.post(url, params={"token": token}, json={"halloween": "off", "halloweenAccent": "#8E4FD6"})
        assert (await response.json())["data"] == {"halloween": "off", "halloweenAccent": "#8e4fd6"}
        for bad in ({"halloween": "yes"}, {"halloweenAccent": "orange"}):
            assert (await client.post(url, params={"token": token}, json=bad)).status == 400

    async def test_saved_accents(self, client, token):
        url = f"{BASE}/api/prefs"
        response = await client.post(url, params={"token": token}, json={"accents": ["#123ABC", "#123abc", "#00ff00"]})
        assert (await response.json())["data"]["accents"] == ["#123abc", "#00ff00"]
        for bad in (["red"], "#123abc", ["#000000"] * 9):
            response = await client.post(url, params={"token": token}, json={"accents": bad})
            assert response.status == 400, bad

    async def test_design_pref_is_validated(self, client, token):
        url = f"{BASE}/api/prefs"
        response = await client.post(url, params={"token": token}, json={"design": "new"})
        assert (await response.json())["data"]["design"] == "new"
        response = await client.post(url, params={"token": token}, json={"design": "retro"})
        assert response.status == 400

    async def test_pwa_pref_is_validated(self, client, token):
        url = f"{BASE}/api/prefs"
        response = await client.post(url, params={"token": token}, json={"pwa": "installed"})
        assert (await response.json())["data"]["pwa"] == "installed"
        response = await client.post(url, params={"token": token}, json={"pwa": "maybe"})
        assert response.status == 400

    async def test_pwa_visit_marks_app_installed(self, client, token):
        url = f"{BASE}/api/prefs"
        assert "pwa" not in (await (await client.get(url, params={"token": token})).json())["data"]
        await client.post(f"{BASE}/api/visit", params={"token": token}, json={"design": "new", "client": "pwa"})
        assert (await (await client.get(url, params={"token": token})).json())["data"]["pwa"] == "installed"

    async def test_apple_push_marks_app_installed(self, client, token, app_context):
        await app_context.miniapp.add_push_subscription(1, "https://web.push.apple.com/abc", "p", "a")
        data = (await (await client.get(f"{BASE}/api/prefs", params={"token": token})).json())["data"]
        assert data["pwa"] == "installed"

    async def test_nav_tabs_pref_is_validated(self, client, token):
        url = f"{BASE}/api/prefs"
        tabs = ["day", "assistant", "homework", "totals", "mail", "settings"]
        response = await client.post(url, params={"token": token}, json={"tabs": tabs})
        assert (await response.json())["data"]["tabs"] == tabs
        # Все семь вкладок разом — тоже можно.
        every = ["day", "schedule", "assistant", "homework", "totals", "mail", "settings"]
        response = await client.post(url, params={"token": token}, json={"tabs": every})
        assert (await response.json())["data"]["tabs"] == every
        for bad in (
            ["day", "homework"],  # без «Настроек» панель уже не вернуть
            ["day", "day", "settings"],
            ["day", "schedule", "assistant", "homework", "totals", "mail", "settings", "settings"],
            ["settings"],
            ["day", "news", "settings"],
            [{"x": 1}, "settings"],
            "day,settings",
        ):
            response = await client.post(url, params={"token": token}, json={"tabs": bad})
            assert response.status == 400, bad

    async def test_pinned_subjects_pref_is_validated(self, client, token):
        url = f"{BASE}/api/prefs"
        response = await client.post(url, params={"token": token}, json={"pins": ["Алгебра", "Физика", "Алгебра"]})
        assert (await response.json())["data"]["pins"] == ["Алгебра", "Физика"]
        for bad in ("Алгебра", [1], [""], ["x" * 121], ["П"] * 41):
            response = await client.post(url, params={"token": token}, json={"pins": bad})
            assert response.status == 400, bad

    async def test_design_notice_pref(self, client, token):
        url = f"{BASE}/api/prefs"
        response = await client.post(url, params={"token": token}, json={"designNotice": "seen"})
        assert (await response.json())["data"]["designNotice"] == "seen"
        response = await client.post(url, params={"token": token}, json={"designNotice": "again"})
        assert (await response.json())["ok"] is False

    async def test_homework_status_pref_is_validated(self, client, token):
        url = f"{BASE}/api/prefs"
        response = await client.post(url, params={"token": token}, json={"hwStatus": "hide"})
        assert (await response.json())["data"]["hwStatus"] == "hide"
        response = await client.post(url, params={"token": token}, json={"hwStatus": "red"})
        assert (await response.json())["ok"] is False

    async def test_motion_pref_is_validated(self, client, token):
        url = f"{BASE}/api/prefs"
        response = await client.post(url, params={"token": token}, json={"motion": "off"})
        assert (await response.json())["data"]["motion"] == "off"
        response = await client.post(url, params={"token": token}, json={"motion": "50"})
        assert (await response.json())["data"]["motion"] == "50"
        response = await client.post(url, params={"token": token}, json={"motion": "slow"})
        assert (await response.json())["ok"] is False
        response = await client.post(url, params={"token": token}, json={"motion": "40"})
        assert (await response.json())["ok"] is False

    async def test_bell_shift_pref_is_validated(self, client, token):
        url = f"{BASE}/api/prefs"
        response = await client.post(url, params={"token": token}, json={"bellShift": "3"})
        assert (await response.json())["data"]["bellShift"] == "3"
        for bad in (3, "11", "-6", "1.5"):
            response = await client.post(url, params={"token": token}, json={"bellShift": bad})
            assert (await response.json())["ok"] is False

    async def test_confetti_pref_is_validated(self, client, token):
        url = f"{BASE}/api/prefs"
        response = await client.post(url, params={"token": token}, json={"confetti": "hide"})
        assert (await response.json())["data"]["confetti"] == "hide"
        response = await client.post(url, params={"token": token}, json={"confetti": False})
        assert (await response.json())["ok"] is False

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

    async def test_opened_mail_is_read_in_cached_list(self, client, app_context, token):
        """Иначе значок «Почты» до часа возвращал прочитанное письмо в счётчик."""
        uid = await app_context.miniapp.resolve_token(token)
        await app_context.cache.put(uid, "mail", {"messages": [
            {"id": 7, "read": False}, {"id": 8, "read": False}]})
        _, before = await app_context.cache.get(uid, "mail")
        response = await client.get(f"{BASE}/api/mail/7", params={"token": token})
        assert response.status == 200
        payload, updated = await app_context.cache.get(uid, "mail")
        assert [m["read"] for m in payload["messages"]] == [True, False]
        assert updated == before

    async def test_read_all_opens_unread_mail_at_school(self, client, app_context, token):
        """Отдельной команды у школы нет — «Прочитать все» открывает каждое письмо."""
        uid = await app_context.miniapp.resolve_token(token)
        await app_context.cache.put(uid, "mail", {"messages": [
            {"id": 7, "read": False}, {"id": 8, "read": False}, {"id": 9, "read": False}]})
        opened = []

        async def mark_mail_read(user, ids):
            opened.extend(ids)
            return [i for i in ids if i != 9]  # одно письмо школа не отдала

        app_context.diary.mark_mail_read = mark_mail_read
        response = await client.post(
            f"{BASE}/api/mail/read-all", params={"token": token}, json={"ids": [7, "8", 9, 7]}
        )
        assert (await response.json())["data"]["read"] == [7, 8]
        assert opened == [7, 8, 9]
        payload, _ = await app_context.cache.get(uid, "mail")
        assert [m["read"] for m in payload["messages"]] == [True, True, False]

    async def test_deleted_mail_leaves_cached_list(self, client, app_context, token):
        uid = await app_context.miniapp.resolve_token(token)
        await app_context.cache.put(uid, "mail", {"messages": [{"id": 7}, {"id": 8}], "page": 1, "pages": 1})
        _, before = await app_context.cache.get(uid, "mail")
        moved = []

        async def delete_mail(user, message_id):
            moved.append(message_id)

        app_context.diary.delete_mail = delete_mail
        response = await client.delete(f"{BASE}/api/mail/7", params={"token": token})
        assert response.status == 200
        assert moved == [7]
        payload, updated = await app_context.cache.get(uid, "mail")
        assert [m["id"] for m in payload["messages"]] == [8]
        assert updated == before

    async def test_restored_mail_makes_cached_list_stale(self, client, app_context, token):
        """Куда вернулось письмо, знает школа — список перечитывается, но
        сохранённый остаётся на случай сбоя."""
        uid = await app_context.miniapp.resolve_token(token)
        await app_context.cache.put(uid, "mail", {"messages": [{"id": 8}]})
        restored = []

        async def restore_mail(user, message_id):
            restored.append(message_id)

        app_context.diary.restore_mail = restore_mail
        response = await client.post(f"{BASE}/api/mail/7/restore", params={"token": token})
        assert response.status == 200
        assert restored == [7]
        payload, updated = await app_context.cache.get(uid, "mail")
        assert payload["messages"] == [{"id": 8}]
        assert updated.year == 2000

    async def test_mail_pages_and_deleted_folder_skip_cache(self, client, app_context, token):
        uid = await app_context.miniapp.resolve_token(token)
        asked = []

        async def fetch_mail_page(user, folder="Inbox", page=1, page_size=50):
            asked.append((folder, page))
            return {"messages": [{"id": page}], "page": page, "pages": 3}

        app_context.diary.fetch_mail_page = fetch_mail_page
        response = await client.get(f"{BASE}/api/mail", params={"token": token, "folder": "Deleted", "page": 2})
        assert (await response.json())["data"] == {"messages": [{"id": 2}], "page": 2, "pages": 3}
        assert asked == [("Deleted", 2)]
        assert await app_context.cache.get(uid, "mail") is None
        for params in ({"folder": "Sent"}, {"page": "0"}, {"page": "x"}):
            response = await client.get(f"{BASE}/api/mail", params={"token": token, **params})
            assert response.status == 400, params

    async def test_mail_move_rejects_bad_id(self, client, token):
        response = await client.delete(f"{BASE}/api/mail/не-число", params={"token": token})
        assert response.status == 400

    async def test_read_all_rejects_bad_ids(self, client, token):
        for body in ({"ids": ["abc"]}, {"ids": list(range(101))}, []):
            response = await client.post(f"{BASE}/api/mail/read-all", params={"token": token}, json=body)
            assert response.status == 400, body

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

    async def test_pwa_link_is_personal(self, client, token, app_context):
        response = await client.get(f"{BASE}/api/pwa-link", params={"token": token})
        url = (await response.json())["data"]["url"]
        assert token not in url and "?device=" in url and "install=1" in url
        assert await app_context.miniapp.use_device_code(url.split("device=")[1].split("&")[0])

    async def test_migrate_link_signs_in_on_the_new_domain_and_opens_twice(self, client, token, app_context):
        """Плашка о переезде: ссылка на основной домен с входом, install=1 и
        несколькими открытиями (окно в приложении, затем Safari)."""
        response = await client.get(f"{BASE}/api/migrate-link", params={"token": token})
        url = (await response.json())["data"]["url"]
        assert url.startswith(app_context.settings.web.miniapp_url) and "install=1" in url and token not in url
        code = url.split("enter=")[1].split("&")[0]
        for _ in range(2):
            client.session.cookie_jar.clear()
            page = await client.get(f"{BASE}/", params={"enter": code, "install": "1"})
            assert page.status == 200 and "netschool_token" in client.session.cookie_jar.filter_cookies(client.make_url("/"))

    async def test_install_link_puts_device_token_and_clean_icon(self, client, token, app_context):
        """Ссылка установки → свой токен устройства в cookie; в иконке вместо
        токена одноразовый код, первый запуск меняет его на новый токен."""
        import re
        code = await app_context.miniapp.issue_enter_code(1)
        client.session.cookie_jar.clear()
        page = await (await client.get(f"{BASE}/", params={"enter": code})).text()
        device = re.search(r'token: "([^"]+)"', page).group(1)
        assert device != token and await app_context.miniapp.is_device_token(device)
        assert client.session.cookie_jar.filter_cookies(client.make_url("/"))["netschool_token"].value == device
        # Ссылку открыли ещё раз в другом браузере (из Telegram — «в Safari»).
        client.session.cookie_jar.clear()
        other = re.search(r'token: "([^"]+)"', await (await client.get(f"{BASE}/", params={"enter": code})).text()).group(1)
        assert other not in (device, token)

        manifest = await (await client.get(f"{BASE}/manifest.webmanifest", params={"token": device})).json()
        start = manifest["start_url"]
        assert device not in start and "?icon=" in start
        # Пока иконку не запустили, манифест отдаёт тот же код.
        again = await (await client.get(f"{BASE}/manifest.webmanifest", params={"token": device})).json()
        assert again["start_url"] == start
        icon = start.split("icon=")[1]
        client.session.cookie_jar.clear()
        response = await client.get(f"{BASE}/", params={"icon": icon}, allow_redirects=False)
        assert response.status == 302 and response.headers["Location"] == f"{BASE}/#own"
        launched = response.cookies["netschool_token"].value
        assert await app_context.miniapp.resolve_token(launched) == 1 and launched not in (device, other)
        # Второй раз код ничего не открывает: «Поделиться» иконкой безопасно.
        client.session.cookie_jar.clear()
        response = await client.get(f"{BASE}/", params={"icon": icon}, allow_redirects=False)
        assert response.status == 200 and 'token: ""' in await response.text()
        # Старые общие токены — как раньше: в манифесте сам токен.
        old = await (await client.get(f"{BASE}/manifest.webmanifest", params={"token": token})).json()
        assert old["start_url"].endswith(f"?token={token}")
        # /app_reset гасит и ссылки установки.
        await app_context.miniapp.revoke_tokens(1)
        assert await app_context.miniapp.use_enter_code(code) is None

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
        from app.web.api import base as api

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
        from app.web.api import base as api

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
        from app.web.api import base as api

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
        # Safari на iPhone показывает Office сам — только с «inline».
        assert disposition.startswith("inline;")


    async def test_attachment_opens_by_ticket_in_outside_browser(self, client, users, app_context):
        # Внешний браузер не знает ни initData, ни заголовка разблокировки:
        # билет на вложения выдаётся приложению и работает только для них.
        await users.save(make_user())
        response = await client.get(
            f"{BASE}/api/attachment-ticket", headers={"X-Telegram-Init-Data": self.init_data(1)}
        )
        ticket = (await response.json())["data"]["ticket"]
        response = await client.get(f"{BASE}/api/attachment/1", params={"ticket": ticket, "name": "лист.pdf"})
        assert response.status == 200
        assert response.headers["Content-Disposition"].startswith("inline;")
        # Подделка, чужой id и истёкший билет не проходят; на остальное API билет не действует.
        user_id, expires, signature = ticket.split(".")
        for bad in (f"{user_id}.{expires}.{'0' * 32}", f"2.{expires}.{signature}", "garbage"):
            response = await client.get(f"{BASE}/api/attachment/1", params={"ticket": bad})
            assert response.status == 401, bad
        from app.web.api import issue_attachment_ticket
        old = issue_attachment_ticket(app_context, 1, now=0)
        assert (await client.get(f"{BASE}/api/attachment/1", params={"ticket": old})).status == 401
        assert (await client.get(f"{BASE}/api/profile", params={"ticket": ticket})).status == 401


class TestAttachmentWaitPage:
    """Вложение открывается страницей ожидания: сервер школы бывает
    перегружен, и прямая ссылка минутами держала пустую вкладку."""

    async def test_page_renders_without_identity(self, client):
        # Данных в странице нет — файл качает её скрипт с билетом из адреса.
        response = await client.get(f"{BASE}/attachment/42", params={"ticket": "x", "name": "Мозг <b>.pptx"})
        assert response.status == 200
        assert response.headers["Cache-Control"] == "no-store"
        text = await response.text()
        assert "Мозг &lt;b&gt;.pptx" in text and "<b>.pptx" not in text
        assert "/api/attachment/$1" in text

    async def test_export_page_uses_same_wait_page(self, client):
        response = await client.get(f"{BASE}/export/year", params={"ticket": "x"})
        assert response.status == 200
        text = await response.text()
        assert "Оценки за год" in text and "/api/export/$1.xlsx" in text
        assert (await client.get(f"{BASE}/export/forever")).status == 404

    async def test_page_accepts_only_numeric_id(self, client):
        assert (await client.get(f"{BASE}/attachment/abc")).status == 404

    async def test_link_leads_to_wait_page(self):
        from pathlib import Path
        core = Path("app/web/static/js/core.js").read_text(encoding="utf-8")
        assert "fileUrl(`attachment/${id}`" in core and "${base}/${path}?" in core

    async def test_slow_school_server_gives_504(self, client, users, app_context, monkeypatch):
        import app.web.api.attachments as api_module
        await users.save(make_user())
        monkeypatch.setattr(api_module, "ATTACHMENT_TIMEOUT_S", 0.05)

        async def hang(user, attachment_id):
            await asyncio.sleep(10)

        monkeypatch.setattr(app_context.diary, "download_attachment", hang)
        response = await client.get(
            f"{BASE}/api/attachment/7", params={"tgWebAppData": TestTelegramMiniApp.init_data(1)}
        )
        assert response.status == 504
        body = await response.json()
        assert body["ok"] is False and "перегружен" in body["error"]
        # Неудача в кэш не попадает.
        assert not list(app_context.settings.attachments_dir.glob("attachment-*"))

    async def test_repeat_is_served_from_cache(self, client, users, app_context, monkeypatch):
        await users.save(make_user())
        await users.save(make_user(2))
        calls = []

        async def download(user, attachment_id):
            calls.append((user.telegram_id, attachment_id))
            return b"slides"

        monkeypatch.setattr(app_context.diary, "download_attachment", download)
        params = {"tgWebAppData": TestTelegramMiniApp.init_data(1), "name": "Мозг.pptx"}
        for _ in range(2):
            response = await client.get(f"{BASE}/api/attachment/7", params=params)
            assert response.status == 200
            assert await response.read() == b"slides"
            assert response.headers["Content-Length"] == "6"
        assert calls == [(1, 7)]
        # Кэш у каждого пользователя свой: чужой файл из него не отдаётся.
        response = await client.get(
            f"{BASE}/api/attachment/7", params={"tgWebAppData": TestTelegramMiniApp.init_data(2)}
        )
        assert response.status == 200 and calls == [(1, 7), (2, 7)]

    async def test_prepare_waits_for_file_then_link_is_instant(self, client, users, app_context, monkeypatch):
        # Страница сперва ждёт файл (prepare → JSON), потом открывает прямую
        # ссылку — та отдаётся из кэша, без второго похода в «Сетевой город».
        await users.save(make_user())
        calls = []

        async def download(user, attachment_id):
            calls.append(attachment_id)
            return b"%PDF-1.4"

        monkeypatch.setattr(app_context.diary, "download_attachment", download)
        params = {"tgWebAppData": TestTelegramMiniApp.init_data(1), "name": "лист.pdf"}
        response = await client.get(f"{BASE}/api/attachment/7", params={**params, "prepare": "1"})
        assert response.status == 200
        assert await response.json() == {"ok": True, "data": {"size": 8}}
        response = await client.get(f"{BASE}/api/attachment/7", params=params)
        assert await response.read() == b"%PDF-1.4"
        assert response.headers["Content-Disposition"].startswith("inline;")
        assert calls == [7]

    async def test_stale_cache_is_downloaded_again_and_cleaned(self, client, users, app_context, monkeypatch):
        import os
        import time
        import app.web.api.attachments as api_module
        await users.save(make_user())
        cache = app_context.settings.attachments_dir
        cache.mkdir(parents=True, exist_ok=True)
        old = time.time() - api_module.ATTACHMENT_CACHE_S - 10
        for name, data in (("attachment-1-7", b"old"), ("attachment-5-9", b"other")):
            (cache / name).write_bytes(data)
            os.utime(cache / name, (old, old))

        async def download(user, attachment_id):
            return b"new"

        monkeypatch.setattr(app_context.diary, "download_attachment", download)
        response = await client.get(
            f"{BASE}/api/attachment/7", params={"tgWebAppData": TestTelegramMiniApp.init_data(1)}
        )
        assert await response.read() == b"new"
        assert sorted(p.name for p in cache.iterdir()) == ["attachment-1-7"]


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

    async def test_api_json_is_compressed(self, client, token, app_context):
        today = dt.date.today()
        app_context.diary.marks = [record(index, date=today) for index in range(20)]
        response = await client.get(f"{BASE}/api/diary", params={"token": token}, headers={'Accept-Encoding': 'gzip'})
        assert response.status == 200
        assert response.headers.get('Content-Encoding') == 'gzip'
        assert 'Accept-Encoding' in response.headers['Vary']
        assert (await response.json())["ok"] is True
        # Маленький ответ сжимать незачем.
        small = await client.get(f"{BASE}/api/prefs", params={"token": token}, headers={'Accept-Encoding': 'gzip'})
        assert 'Content-Encoding' not in small.headers

    async def test_app_modules_are_the_static_graph_of_app_js(self, client):
        from app.web.server import APP_MODULES, BOOT_MODULES
        assert 'day.js' in APP_MODULES and 'games/kit.js' in APP_MODULES
        # Открываются по требованию через import() — качаются после первой отрисовки.
        assert 'account.js' not in APP_MODULES
        assert not set(APP_MODULES) & set(BOOT_MODULES)
        html = await (await client.get(f'{BASE}/')).text()
        assert '/js/day.js' in html.split('appModules:')[1].split(']')[0]

    async def test_api_carries_static_version_for_live_updates(self, client, token):
        from app.web.server import STATIC_VERSION
        response = await client.get(f"{BASE}/api/prefs", params={"token": token})
        assert response.headers['X-Netschool-Version'] == STATIC_VERSION
        html = await (await client.get(f'{BASE}/')).text()
        assert f'version: "{STATIC_VERSION}"' in html
        from app.web.server import APP_MODULES
        assert 'update.js' in APP_MODULES

    async def test_telegram_script_only_inside_telegram(self, client):
        html = await (await client.get(f'{BASE}/')).text()
        # Не обычный тег: в браузере и с экрана «Домой» скрипт не грузится.
        assert not re.search(r'^<script src="[^"]*telegram-web-app\.js"', html, re.M)
        assert "document.write('<script src=\"/mini/static/v" in html
        assert "tgWebApp/.test(location.hash + location.search)" in html


class TestSecurityHeaders:
    async def test_pages_and_api_carry_security_headers(self, client):
        page = await client.get(f'{BASE}/')
        assert page.headers['X-Content-Type-Options'] == 'nosniff'
        assert page.headers['Referrer-Policy'] == 'no-referrer'
        assert "frame-ancestors 'self'" in page.headers['Content-Security-Policy']
        api = await client.get(f'{BASE}/api/profile')
        assert api.headers['Cache-Control'] == 'no-store'


class TestStudentClass:
    async def test_class_is_cached_and_survives_school_outage(self, client, token, app_context, monkeypatch):
        calls = []

        async def fetch(user):
            calls.append(user.telegram_id)
            return "9б"

        monkeypatch.setattr(app_context.diary, "fetch_class_name", fetch, raising=False)
        url = f"{BASE}/api/student-class"
        data = (await (await client.get(url, params={"token": token})).json())["data"]
        assert data == {"name": "9б", "label": "9 «Б» класс"}
        await client.get(url, params={"token": token})
        assert len(calls) == 1

    async def test_outage_without_cache_gives_empty_class(self, client, token, app_context, monkeypatch):
        async def fetch(user):
            raise NetSchoolError(Reason.UNKNOWN, "нет связи")

        monkeypatch.setattr(app_context.diary, "fetch_class_name", fetch, raising=False)
        response = await client.get(f"{BASE}/api/student-class", params={"token": token})
        assert (await response.json())["data"] == {"name": "", "label": ""}


async def test_every_report_can_be_sent_to_chat(app_context, users, tmp_path, monkeypatch):
    """Подпись была не у всех отчётов: «Контрольные» и другие падали KeyError."""
    from dataclasses import replace

    import app.web.extras as extras
    from app.exporting import REPORTS

    await users.save(make_user(1))
    app_context.settings = replace(app_context.settings, data_dir=tmp_path)

    async def fake_report(app, user, scope, report):
        return b"xlsx", f"{report}.xlsx"

    monkeypatch.setattr(extras, "export_report", fake_report)
    sent = []

    class Bot:
        async def send_document(self, chat_id, document, caption=""):
            sent.append(caption)

    client = TestClient(TestServer(create_app(app_context, bot=Bot())))
    await client.start_server()
    try:
        token = await app_context.miniapp.issue_token(1)
        for report in REPORTS:
            response = await client.post(f"{BASE}/api/export/send", headers={"X-Netschool-Token": token},
                                         json={"scope": "period", "report": report})
            assert response.status == 200, report
    finally:
        await client.close()
    assert len(sent) == len(REPORTS) and all(sent)
    assert set(extras.EXPORT_CAPTIONS) == set(REPORTS)


async def test_moved_banner_only_on_legacy_hosts(app_context):
    web_settings = replace(app_context.settings.web, public_url='https://new.example', legacy_urls=('https://old.example',))
    client = TestClient(TestServer(create_app(replace(app_context, settings=replace(app_context.settings, web=web_settings)))))
    await client.start_server()
    try:
        old = await (await client.get('/mini/demo', headers={'Host': 'old.example'})).text()
        assert '/moved.js" data-to="https://new.example"' in old
        new = await (await client.get('/mini/demo', headers={'Host': 'new.example'})).text()
        assert 'moved.js' not in new
        local = await (await client.get('/mini/demo')).text()
        assert 'moved.js' not in local
    finally:
        await client.close()


async def test_old_address_gets_a_deadline_only_after_the_new_app_is_launched(app_context, users):
    """Срок отключения прежнего адреса: moved_days дней с первого запуска иконки на основном домене."""
    await users.save(make_user(student_name="Иванов И."))
    token = await app_context.miniapp.issue_token(1, device=True)
    web_settings = replace(app_context.settings.web, public_url='https://new.example', legacy_urls=('https://old.example',), moved_days=3)
    client = TestClient(TestServer(create_app(replace(app_context, settings=replace(app_context.settings, web=web_settings)))))
    await client.start_server()
    try:
        async def status(host, pwa, who=token):
            headers = {'Host': host, 'X-Netschool-Token': who}
            if pwa:
                headers['X-Netschool-Client'] = 'pwa'
            return (await (await client.get(f'{BASE}/api/migrate-status', headers=headers)).json())['data']['deadline']

        assert await status('old.example', True) is None   # старый адрес срока не назначает
        assert await status('new.example', False, await app_context.miniapp.issue_token(1, device=True)) is None  # вкладка — не приложение
        deadline = await status('new.example', True, await app_context.miniapp.issue_token(1, device=True))
        assert abs(deadline - time.time() - 3 * 86400) < 60
        assert await status('old.example', False) == deadline  # дальше срок не пересчитывается
    finally:
        await client.close()
