"""ИИ-помощник: данные для модели, лимиты и ответы API."""

import dataclasses

import pytest

from app.settings import AssistantSettings
from app.web import assistant
from tests.test_repositories import make_user
from tests.test_web import BASE, client, token  # noqa: F401 — фикстуры
from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура


def mark(value, quarter="q1", weight=1):
    return {"mark": str(value), "numeric": value, "weight": weight, "quarter": quarter}


GRADES = {
    "current_quarter": "q1",
    "quarters": [{"key": "q1", "label": "1 четверть"}, {"key": "q2", "label": "2 четверть"}],
    "average": 4.0,
    "subjects": [
        {"subject": "Алгебра", "teacher": "Петрова А. А.", "average": 3.75,
         "marks": [mark(4), mark(4), mark(3, weight=2), mark(5, quarter="q2")],
         "totals": {"terms": {"q1": {"mark": "4", "average": 3.5}}, "year": "", "final": ""}},
        {"subject": "Физика", "teacher": "", "average": None, "marks": []},
    ],
}


class TestNeededMarks:
    def test_matches_calculator(self):
        # 4, 4, 3 (вес 2) → 3,5; до 4,5 нужно четыре пятёрки: 30/7≈4,29, 35/8≈4,38…
        marks = [(4, 1), (4, 1), (3, 2)]
        assert assistant.needed_marks(marks, 4.5) == 8
        assert assistant.needed_marks(marks, 3.5) == 0

    def test_unreachable(self):
        assert assistant.needed_marks([(2, 1)], 4.5, value=4) is None


class TestContext:
    async def test_grades_without_personal_data(self, app_context, users):
        user = make_user(student_name="Иванов Иван")
        await users.save(user)
        await app_context.cache.put(1, "marks:year", GRADES)
        text = await assistant.build_context(app_context, user)
        assert "Иванов" not in text and "Школа №1" not in text and "ivan" not in text
        assert "Алгебра (учитель: Петрова А. А.): 1 четверть: 4 4 3(вес 2)" in text
        assert "средний 3,5, сейчас выходит «4»" in text
        assert "до «5» (от 4,5) нужно пятёрок: 8" in text
        assert "выставленные итоговые: 1 четверть: 4" in text
        assert "Физика: 1 четверть: оценок нет" in text

    async def test_own_thresholds(self, app_context, users):
        user = make_user()
        await users.save(user)
        await app_context.cache.put(1, "marks:year", GRADES)
        await app_context.miniapp.save_prefs(1, {"thresholds": {"subjects": {"Алгебра": {"5": 4.6, "4": 3.6, "3": 2.6}}}})
        text = await assistant.build_context(app_context, user)
        assert "сейчас выходит «3»" in text

    async def test_missing_cache_is_said_out_loud(self, app_context, users):
        user = make_user()
        await users.save(user)
        text = await assistant.build_context(app_context, user)
        assert "ещё не загружены" in text


@pytest.fixture
def enabled(app_context):
    app_context.settings = dataclasses.replace(
        app_context.settings, assistant=AssistantSettings(api_key="k", daily_limit=2)
    )
    return app_context


class TestApi:
    async def test_disabled_without_key(self, client, token):
        response = await client.post(f"{BASE}/api/assistant", params={"token": token},
                                     json={"messages": [{"role": "user", "content": "привет"}]})
        assert response.status == 404

    async def test_reply_and_limit(self, client, token, enabled, monkeypatch):
        seen = []

        async def fake(settings, messages):
            seen.append(messages)
            return "Ответ"

        monkeypatch.setattr(assistant, "complete", fake)
        body = {"messages": [{"role": "user", "content": "Что задали?"}]}
        first = await client.post(f"{BASE}/api/assistant", params={"token": token}, json=body)
        assert (await first.json())["data"] == {"reply": "Ответ", "left": 1}
        assert seen[0][0]["role"] == "system" and seen[0][-1]["content"] == "Что задали?"
        await client.post(f"{BASE}/api/assistant", params={"token": token}, json=body)
        third = await client.post(f"{BASE}/api/assistant", params={"token": token}, json=body)
        assert third.status == 429

    @pytest.mark.parametrize(("on", "season", "styled"), [(True, True, True), (False, True, False), (True, False, False)])
    async def test_halloween_style(self, client, token, enabled, monkeypatch, on, season, styled):
        seen = []

        async def fake(settings, messages):
            seen.append(messages[0]["content"])
            return "Ответ"

        monkeypatch.setattr(assistant, "complete", fake)
        monkeypatch.setattr(assistant, "halloween_season", lambda today: season)
        body = {"messages": [{"role": "user", "content": "Что задали?"}], "ui": {"halloween": on}}
        await client.post(f"{BASE}/api/assistant", params={"token": token}, json=body)
        assert (assistant.HALLOWEEN_STYLE in seen[0]) is styled

    async def test_failure_does_not_spend_quota(self, client, token, enabled, monkeypatch):
        async def broken(settings, messages):
            raise assistant.AssistantError("нет", status=503, reason="busy")

        monkeypatch.setattr(assistant, "complete", broken)
        body = {"messages": [{"role": "user", "content": "?"}]}
        for _ in range(3):
            response = await client.post(f"{BASE}/api/assistant", params={"token": token}, json=body)
            assert response.status == 503
        assert enabled.assistant_usage[1][1] == 0

    @pytest.mark.parametrize("messages", [
        [], [{"role": "system", "content": "x"}], [{"role": "assistant", "content": "x"}],
        [{"role": "user", "content": "   "}], "строка",
    ])
    async def test_rejects_bad_history(self, client, token, enabled, messages):
        response = await client.post(f"{BASE}/api/assistant", params={"token": token}, json={"messages": messages})
        assert response.status == 400

    async def test_profile_flag(self, client, token, enabled):
        response = await client.get(f"{BASE}/api/profile", params={"token": token})
        assert (await response.json())["data"]["assistant"] is True


class TestGuide:
    def test_new_design_has_no_hamburger_menu(self):
        text = assistant.app_guide("new", ["day", "assistant", "mail", "settings"])
        assert "в новом дизайне НЕТ" in text
        assert "«Настройки» → «Оформление» → переключатель «Новый дизайн»" in text
        assert "убрал с панели" in text and "«Оценки»" in text

    def test_classic_design_points_to_menu(self):
        text = assistant.app_guide()
        assert "Меню ☰ (три полоски) слева вверху" in text
        assert "«Помощник»" not in text


class TestModerationVerdict:
    async def test_verdict_is_retried(self, client, token, enabled, monkeypatch):
        replies = iter(["User Safety: safe\nResponse Safety: safe", "Настоящий ответ"])
        prompts = []

        async def fake(settings, messages):
            prompts.append(messages[0]["content"])
            return next(replies)

        monkeypatch.setattr(assistant, "complete", fake)
        body = {"messages": [{"role": "user", "content": "как выключить новый дизайн"}],
                "ui": {"design": "new", "tabs": ["day", "assistant", "settings"]}}
        response = await client.post(f"{BASE}/api/assistant", params={"token": token}, json=body)
        assert (await response.json())["data"]["reply"] == "Настоящий ответ"
        assert "в новом дизайне НЕТ" in prompts[0]

    async def test_only_verdicts_give_an_error(self, client, token, enabled, monkeypatch):
        async def fake(settings, messages):
            return "safe"

        monkeypatch.setattr(assistant, "complete", fake)
        body = {"messages": [{"role": "user", "content": "?"}]}
        response = await client.post(f"{BASE}/api/assistant", params={"token": token}, json=body)
        assert response.status == 502
        assert enabled.assistant_usage[1][1] == 0


def test_mixed_script_words_become_cyrillic():
    from app.web.assistant import fix_mixed_script

    assert fix_mixed_script("Зdesь есть две оценки") == "Здесь есть две оценки"
    assert fix_mixed_script("ПРИВЕТ МИР, ПРИВEТ") == "ПРИВЕТ МИР, ПРИВЕТ"
    # Чисто латинские слова и слова через дефис остаются как есть.
    assert fix_mixed_script("Откройте Gmail, PDF-файл и e-mail") == "Откройте Gmail, PDF-файл и e-mail"


class TestStreaming:
    async def test_text_arrives_before_generation_finishes(self, client, token, enabled, monkeypatch):
        import asyncio
        import json
        release = asyncio.Event()

        async def fake(settings, messages):
            yield "Первая часть "
            await release.wait()
            yield "ответа"

        monkeypatch.setattr(assistant, "stream_complete", fake)
        response = await client.post(f"{BASE}/api/assistant", params={"token": token},
            json={"stream": True, "messages": [{"role": "user", "content": "Что задали?"}]})
        try:
            assert response.headers["X-Accel-Buffering"] == "no"
            assert response.headers["Cache-Control"] == "no-store"
            assert json.loads(await response.content.readline())["type"] == "start"
            event = json.loads(await asyncio.wait_for(response.content.readline(), 2))
            assert event == {"type": "delta", "text": "Первая часть "}
        finally:
            release.set()
        events = [json.loads(line) for line in (await response.read()).splitlines()]
        assert events[-1] == {"ok": True, "data": {"reply": "Первая часть ответа", "left": 1}}
        assert enabled.assistant_usage[1][1] == 1

    async def test_interrupted_generation_refunds_quota(self, client, token, enabled, monkeypatch):
        import json
        async def fake(settings, messages):
            yield "Начало"
            raise assistant.AssistantError("Ответ прервался", reason="busy")
        monkeypatch.setattr(assistant, "stream_complete", fake)
        for _ in range(3):
            response = await client.post(f"{BASE}/api/assistant", params={"token": token},
                json={"stream": True, "messages": [{"role": "user", "content": "?"}]})
            events = [json.loads(line) for line in (await response.read()).splitlines()]
            assert events[-1] == {"ok": False, "error": "Ответ прервался", "reason": "busy"}
        assert enabled.assistant_usage[1][1] == 0

    async def test_provider_stream_and_truncation(self):
        from aiohttp import web
        from aiohttp.test_utils import TestServer
        import json
        truncated = False
        async def handle(request):
            body = await request.json()
            assert body["stream"] is True
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            for text in ("При", "вет"):
                data = json.dumps({"choices": [{"delta": {"content": text}}]}, ensure_ascii=False)
                # В том числе сетевой разрыв посередине UTF-8 символа.
                for byte in ("data: " + data + "\n\n").encode():
                    await response.write(bytes([byte]))
            if not truncated:
                await response.write(b"data: [DONE]\n\n")
            await response.write_eof()
            return response
        app = web.Application()
        app.router.add_post("/chat/completions", handle)
        async with TestServer(app) as server:
            settings = AssistantSettings(api_key="test", base_url=str(server.make_url("/")).rstrip("/"))
            assert "".join([chunk async for chunk in assistant.stream_complete(settings, [])]) == "Привет"
            truncated = True
            with pytest.raises(assistant.AssistantError, match="прервался"):
                async for _ in assistant.stream_complete(settings, []):
                    pass
