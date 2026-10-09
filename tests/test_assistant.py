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
