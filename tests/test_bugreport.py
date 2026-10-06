"""Отчёт об ошибке из мини-приложения: капча проверяется на сервере, текст уходит админу."""

from dataclasses import replace
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.web import bugreport
from app.web.server import create_app
from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура
from tests.test_repositories import make_user
from tests.test_web import BASE

CAPTCHA = "https://captcha.example"


class FakeBot:
    def __init__(self, fail=False):
        self.sent = []
        self.files = []
        self.fail = fail

    async def send_message(self, chat_id, text):
        if self.fail:
            raise RuntimeError("telegram недоступен")
        self.sent.append((chat_id, text))
        return SimpleNamespace(message_id=len(self.sent))

    async def send_document(self, chat_id, document, reply_to_message_id=None):
        self.files.append([document.filename])

    async def send_media_group(self, chat_id, media, reply_to_message_id=None):
        self.files.append([item.media.filename for item in media])


@pytest.fixture
def bot():
    return FakeBot()


class Verifications(list):
    """Вызовы siteverify; ``result`` — что ответит сервис капчи."""

    result = (True, "")


@pytest.fixture
def verifications(monkeypatch):
    calls = Verifications()

    async def fake(url, token, bind, rounds):
        calls.append((url, token, bind, rounds))
        return calls.result

    monkeypatch.setattr(bugreport, "verify_captcha", fake)
    monkeypatch.setattr(bugreport, "_last_sent", {})
    return calls


async def _client(app_context, bot, captcha_url=CAPTCHA):
    web = replace(app_context.settings.web, captcha_url=captcha_url, captcha_rounds=2)
    context = replace(app_context, settings=replace(app_context.settings, web=web))
    client = TestClient(TestServer(create_app(context, bot)))
    await client.start_server()
    return client


@pytest.fixture
async def token(app_context, users):
    await users.save(make_user(student_name="Иванов И."))
    return await app_context.miniapp.issue_token(1)


async def _post(client, auth, **body):
    payload = {"text": "Не открывается вкладка <Оценки>", "token": "captcha-token", "meta": {"ua": "Test"}}
    payload.update(body)
    return await client.post(f"{BASE}/api/bug-report", params={"token": auth}, json=payload)


async def test_config_gives_bind_and_rounds(app_context, bot, token):
    client = await _client(app_context, bot)
    try:
        response = await client.get(f"{BASE}/api/bug-report", params={"token": token})
        assert (await response.json())["data"] == {"enabled": True, "bind": "netschoolbot:bug:1", "rounds": 2}
    finally:
        await client.close()


async def test_report_reaches_admin_after_captcha(app_context, bot, token, verifications):
    client = await _client(app_context, bot)
    try:
        response = await _post(client, token)
        assert response.status == 200, await response.text()
    finally:
        await client.close()
    assert verifications == [(CAPTCHA, "captcha-token", "netschoolbot:bug:1", 2)]
    ((chat_id, text),) = bot.sent
    assert chat_id == app_context.settings.telegram.admin_id
    # У бота parse_mode=HTML: текст пользователя экранирован.
    assert "&lt;Оценки&gt;" in text and "<code>1</code>" in text and "Браузер: Test" in text


async def test_rejected_captcha_sends_nothing(app_context, bot, token, verifications):
    verifications.result = (False, "bind-mismatch")
    client = await _client(app_context, bot)
    try:
        response = await _post(client, token)
        assert response.status == 400
        assert (await response.json())["reason"] == "captcha"
    finally:
        await client.close()
    assert bot.sent == []


@pytest.mark.parametrize(
    ("body", "message"),
    [({"text": "баг"}, "парой слов"), ({"token": ""}, "капчу"), ({"text": "x" * 4001}, "длинно")],
)
async def test_validation_happens_before_captcha(app_context, bot, token, verifications, body, message):
    client = await _client(app_context, bot)
    try:
        response = await _post(client, token, **body)
        assert response.status == 400
        assert message in (await response.json())["error"]
    finally:
        await client.close()
    assert verifications == [] and bot.sent == []


async def test_cooldown_between_reports(app_context, bot, token, verifications):
    client = await _client(app_context, bot)
    try:
        assert (await _post(client, token)).status == 200
        assert (await _post(client, token)).status == 429
    finally:
        await client.close()
    assert len(bot.sent) == 1


async def test_failed_delivery_does_not_start_cooldown(app_context, token, verifications):
    bot = FakeBot(fail=True)
    client = await _client(app_context, bot)
    try:
        assert (await _post(client, token)).status == 503
        bot.fail = False
        assert (await _post(client, token)).status == 200
    finally:
        await client.close()


async def test_captcha_frame_takes_script_from_settings(app_context, bot):
    client = await _client(app_context, bot)
    try:
        response = await client.get(
            f"{BASE}/captcha-frame", params={"bind": "x", "src": "https://evil.example"}
        )
        text = await response.text()
    finally:
        await client.close()
    assert response.status == 200
    assert f'src="{CAPTCHA}/sdk/kryacaptcha.js"' in text
    assert "evil.example" not in text
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"


async def test_disabled_without_captcha_url(app_context, bot, token):
    client = await _client(app_context, bot, captcha_url="")
    try:
        config = await client.get(f"{BASE}/api/bug-report", params={"token": token})
        assert (await config.json())["data"] == {"enabled": False}
        assert (await client.get(f"{BASE}/captcha-frame")).status == 404
        assert (await _post(client, token)).status == 404
    finally:
        await client.close()


def _form(*files, text="На скриншоте пустой экран"):
    form = aiohttp.FormData()
    form.add_field("text", text)
    form.add_field("token", "captcha-token")
    form.add_field("meta", '{"ua": "Test"}')
    for name, data, kind in files:
        form.add_field("file", data, filename=name, content_type=kind)
    return form


async def test_photo_and_video_reach_admin(app_context, bot, token, verifications):
    client = await _client(app_context, bot)
    try:
        response = await client.post(
            f"{BASE}/api/bug-report", params={"token": token},
            data=_form(("экран.png", b"PNG", "image/png"), ("запись.mp4", b"MP4", "video/mp4")),
        )
        assert response.status == 200, await response.text()
        response = await client.post(  # отдельный пользователь не нужен — снимаем паузу
            f"{BASE}/api/bug-report", params={"token": token}, data=_form(("один.jpg", b"JPG", "image/jpeg")),
        )
    finally:
        await client.close()
    assert "Браузер: Test" in bot.sent[0][1]
    assert bot.files[0] == ["экран.png", "запись.mp4"]


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ([("отчёт.pdf", b"PDF", "application/pdf")], "не фото и не видео"),
        ([(f"{i}.png", b"x", "image/png") for i in range(6)], "Не больше 5"),
        ([("пусто.png", b"", "image/png")], "пустой"),
        ([("большое.mp4", b"x" * (15 * 1024 * 1024 + 1), "video/mp4")], "МБ"),
    ],
)
async def test_bad_files_are_rejected_before_captcha(app_context, bot, token, verifications, files, message):
    client = await _client(app_context, bot)
    try:
        response = await client.post(f"{BASE}/api/bug-report", params={"token": token}, data=_form(*files))
        assert response.status == 400
        assert message in (await response.json())["error"]
    finally:
        await client.close()
    assert verifications == [] and bot.sent == []


async def test_single_file_goes_as_document(app_context, bot, token, verifications):
    client = await _client(app_context, bot)
    try:
        response = await client.post(
            f"{BASE}/api/bug-report", params={"token": token}, data=_form(("фото.heic", b"HEIC", "application/octet-stream")),
        )
        assert response.status == 200, await response.text()
    finally:
        await client.close()
    assert bot.files == [["фото.heic"]]
