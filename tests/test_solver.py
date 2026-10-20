"""Решения заданий: разбор задания, поиск по учебнику, файлы и API."""

import base64
import dataclasses
import io
import json
import zipfile

import pytest

from app.settings import AssistantSettings, SolverSettings
from app.solver.extract import extract_text
from app.solver.prompt import DraftFilter, Task, cache_key, user_content
from app.solver.refs import parse
from app.solver.textbooks import Library
from app.web import solver
from tests.test_web import BASE, client, token  # noqa: F401 — фикстуры
from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура


class TestRefs:
    def test_numbers_with_parts(self):
        refs = parse("п.13,№289(б,г),№290(б),№291(в).")
        assert refs.paragraphs == [13]
        assert [e.label() for e in refs.exercises] == ["№289(б,г)", "№290(б)", "№291(в)"]

    def test_paragraph_with_exercise_range(self):
        refs = parse("§5 читать, письменно упр. 2-3")
        assert refs.paragraphs == [5] and [e.number for e in refs.exercises] == [2, 3]

    def test_page_and_exercise(self):
        refs = parse("упр. 48 стр.  47")
        assert refs.pages == [47] and [e.number for e in refs.exercises] == [48]

    def test_review_is_not_a_paragraph(self):
        refs = parse("пар. 39, пересказ, повторить 38-44")
        assert refs.paragraphs == [39] and refs.review == list(range(38, 45))

    def test_comma_list_after_number_sign(self):
        assert [e.number for e in parse("№ 12, 14, 15(а)").exercises] == [12, 14, 15]

    def test_nothing_to_solve(self):
        assert not parse("Подготовить доклад") and not parse("нет д/з.")


def page(*lines):
    return "\n".join(lines)


@pytest.fixture
def library(tmp_path):
    pages = [
        page("1"),
        page("§ 1. Введение", "Текст параграфа"),
        page("Вопросы", "1. Что такое …?", "2. Почему …?"),
        page("§ 2. Уравнения", "13. Дробные рациональные уравнения"),
        page("288. Решите уравнение:", "а) x = 1"),
        page("289. Решите уравнение:", "а) …", "290", "Найдите корни"),
        page("ОТВЕТЫ"),
        page("289. а) 2; б) 4. 290. а) 10."),
    ]
    (tmp_path / "alg.pages.json").write_text(json.dumps({"pages": pages, "offset": 0, "answers_from": 6}), "utf-8")
    (tmp_path / "inf.pages.json").write_text(json.dumps({
        "pages": ["a", "b", "c", "d", "e"], "offset": 0,
        "toc": [["§ 1.1. Алгоритмы", 0], ["§ 2.4. Табличные информационные модели", 2], ["§ 2.5. Графы", 4]],
    }), "utf-8")
    (tmp_path / "library.json").write_text(json.dumps({"grade": 9, "books": [
        {"id": "alg", "subjects": ["Алгебра"], "title": "Алгебра"},
        {"id": "inf", "subjects": ["Информатика и ИКТ"], "title": "Информатика"},
    ]}), "utf-8")
    return Library(tmp_path)


class TestTextbooks:
    def test_exercises_and_answers(self, library):
        book = library.books_for("алгебра")[0]
        found = book.find(parse("№289(б),№290(а)"))
        assert 5 in found.pages and 5 in found.exercises
        assert found.answers[289] == "289. а) 2; б) 4."
        assert found.answers[290].startswith("290. а) 10")

    def test_number_in_a_box_is_found(self, library):
        # Номер в рамке распознаётся отдельной строкой без точки.
        assert library.books_for("Алгебра")[0].exercise_page(290) == 5

    def test_exercises_inside_paragraph_first(self, library):
        book = library.books_for("Алгебра")[0]
        found = book.find(parse("§1, упр. 2"))
        assert 2 in found.exercises and 1 in found.pages

    def test_dotted_paragraphs_found_by_topic(self, library):
        book = library.books_for("Информатика и ИКТ")[0]
        found = book.find(parse("§6; вопросы"), "Табличные модели")
        assert found.pages == [2, 3, 4]

    def test_teacher_numbering_is_explained(self, library):
        # «§6» у учителя — «§ 2.4» в книге: модели говорим, что это он и есть.
        book = library.books_for("Информатика и ИКТ")[0]
        task = Task(subject="Информатика и ИКТ", due="2026-10-19", text="§6; вопросы", topic="Табличные модели")
        found = book.find(task.refs(), task.topic)
        assert found.section == "§ 2.4. Табличные информационные модели"
        assert "в книге это «§ 2.4. Табличные информационные модели»" in user_content(task, [(book, found)], [])[0]["text"]

    def test_unknown_subject(self, library):
        assert library.books_for("Химия") == [] and library.grade() == 9

    def test_scan_sends_exercise_page_as_image(self, library, tmp_path):
        from PIL import Image

        (tmp_path / "alg").mkdir()
        Image.new("L", (1600, 2000), 255).save(tmp_path / "alg" / "0005.jpg")
        (tmp_path / "library.json").touch()
        library._loaded_mtime = None
        book = library.books_for("Алгебра")[0]
        task = Task(subject="Алгебра", due="2026-10-19", text="№289(б)")
        blocks = user_content(task, [(book, book.find(task.refs()))], [])
        images = [b for b in blocks if b["type"] == "image"]
        # Строк скана нет — страница целиком, уменьшенная до 1100 px.
        assert len(images) == 1
        with Image.open(io.BytesIO(base64.b64decode(images[0]["source"]["data"]))) as page:
            assert max(page.size) == 1100
        assert "№289: 289. а) 2; б) 4." in blocks[-1]["text"]

    def test_remembered_condition_replaces_picture(self, library, tmp_path):
        from PIL import Image

        (tmp_path / "alg").mkdir()
        Image.new("L", (1600, 2000), 255).save(tmp_path / "alg" / "0005.jpg")
        (tmp_path / "library.json").touch()
        library._loaded_mtime = None
        book = library.books_for("Алгебра")[0]
        task = Task(subject="Алгебра", due="2026-10-19", text="№289(б)")
        found = book.find(task.refs())

        imaged = []
        user_content(task, [(book, found)], [], {}, imaged)
        assert imaged == [("alg", 289)]

        imaged = []
        blocks = user_content(task, [(book, found)], [], {("alg", 289): "Решите уравнение: а) $x=1$; б) $x^3=8$"}, imaged)
        assert not [b for b in blocks if b["type"] == "image"] and imaged == []
        assert "№289, стр. 5 (условие, прочитано с картинки раньше) ---\nРешите уравнение: а) $x=1$; б) $x^3=8$" in blocks[0]["text"]

    def test_pictures_over_budget_go_as_text(self, library, tmp_path, monkeypatch):
        from PIL import Image

        from app.solver import prompt

        (tmp_path / "alg").mkdir()
        Image.new("L", (1600, 2000), 255).save(tmp_path / "alg" / "0005.jpg")
        (tmp_path / "library.json").touch()
        library._loaded_mtime = None
        monkeypatch.setattr(prompt, "MAX_IMAGE_BYTES", 10)
        book = library.books_for("Алгебра")[0]
        task = Task(subject="Алгебра", due="2026-10-19", text="№289(б)")
        blocks = user_content(task, [(book, book.find(task.refs()))], [])
        assert not [b for b in blocks if b["type"] == "image"]
        assert "289. Решите уравнение:" in blocks[0]["text"]


class TestDraft:
    def test_draft_is_hidden_even_split_across_chunks(self):
        draft = DraftFilter()
        shown = "".join(draft.feed(c) for c in ["<чер", "новик>x=3? нет", ", x=4</черно", "вик>\n### №291", " ответ"])
        assert shown == "### №291 ответ"

    def test_answer_without_draft_passes_through(self):
        draft = DraftFilter()
        assert draft.feed("### №") + draft.feed("291") == "### №291"

    def test_unclosed_draft_is_better_than_nothing(self):
        draft = DraftFilter()
        assert draft.feed("<черновик>решение") == "" and draft.rest() == "решение"


class TestCrop:
    def test_exercise_cut_from_scan(self, tmp_path):
        from PIL import Image

        (tmp_path / "scan").mkdir()
        Image.new("L", (100, 1000), 255).save(tmp_path / "scan" / "0000.jpg")
        (tmp_path / "scan.pages.json").write_text(json.dumps({"pages": ["290. …\n291. …\n292. …"]}), "utf-8")
        (tmp_path / "scan.lines.json").write_text(json.dumps([[
            ["290. Решите", 0.1, 0.10, 0.9, 0.12], ["291. Найдите корни", 0.1, 0.40, 0.9, 0.42],
            ["в) …", 0.1, 0.50, 0.9, 0.52], ["292. Решите", 0.1, 0.70, 0.9, 0.72],
        ]]), "utf-8")
        (tmp_path / "library.json").write_text(json.dumps({"books": [{"id": "scan", "subjects": ["Алгебра"]}]}), "utf-8")
        book = Library(tmp_path).books_for("Алгебра")[0]
        with Image.open(io.BytesIO(book.crop(0, 291))) as part:
            assert part.size[0] == 100 and 280 <= part.size[1] <= 310  # от 291 до 292
        # «292» не распознался бы — 293 режется от предыдущего номера.
        with Image.open(io.BytesIO(book.crop(0, 293))) as part:
            assert part.size[1] >= 290
        assert book.crop(0, 500) is None


class TestExtract:
    def test_pptx_slides_in_order(self):
        buffer = io.BytesIO()
        a = "http://schemas.openxmlformats.org/drawingml/2006/main"
        with zipfile.ZipFile(buffer, "w") as archive:
            for number, text in ((10, "Десятый"), (2, "Второй")):
                archive.writestr(f"ppt/slides/slide{number}.xml",
                                 f'<p:sld xmlns:p="p" xmlns:a="{a}"><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:sld>')
        text = extract_text("Эндокринная.pptx", buffer.getvalue())
        assert text.index("Второй") < text.index("Десятый") and "[Слайд 2]" in text

    def test_unknown_or_broken(self):
        assert extract_text("фото.jpg", b"x") == "" and extract_text("битый.docx", b"not a zip") == ""


class TestCacheKey:
    def test_same_task_same_key_for_classmates(self):
        a = Task(subject="Алгебра", due="2026-10-19", text="№289(б,г)", attachments=[(2, "b"), (1, "a")])
        b = Task(subject="алгебра ", due="2026-10-19", text=" №289(б,г) ", attachments=[(1, "a"), (2, "b")])
        assert cache_key("sgo", a, "1", "m") == cache_key("sgo", b, "1", "m")
        assert cache_key("sgo", a, "1", "m") != cache_key("sgo", a, "2", "m")
        assert cache_key("sgo", a, "1", "m") != cache_key("other", a, "1", "m")
        # Комментарий учителя меняет задание: «1 ряд — …» решается иначе.
        assert cache_key("sgo", a, "1", "m") != cache_key("sgo", dataclasses.replace(a, comment="1 ряд"), "1", "m")


class TestComment:
    def test_comment_goes_to_model(self):
        task = Task(subject="ОБЗР", due="2026-10-20", text="Подготовить доклад", comment="1 ряд - метеорологические ЧС")
        assert "Комментарий учителя к заданию:\n1 ряд - метеорологические ЧС" in user_content(task, [], [])[0]["text"]

    def test_comment_repeating_the_task_is_dropped(self):
        task = solver._task({**TASK, "comment": " №289(б) "})
        assert task.comment == ""


@pytest.fixture
def allowed(app_context):
    app_context.settings = dataclasses.replace(
        app_context.settings, assistant=AssistantSettings(api_key="k"),
        solver=SolverSettings(users=frozenset({1}), daily_limit=2),
    )
    return app_context


TASK = {"subject": "Алгебра", "date": "2026-10-19", "text": "№289(б)", "topic": "", "attachments": []}


def events(raw: bytes) -> list[dict]:
    return [json.loads(line) for line in raw.splitlines()]


class TestApi:
    async def test_hidden_from_others(self, client, token, app_context):
        app_context.settings = dataclasses.replace(app_context.settings, assistant=AssistantSettings(api_key="k"))
        response = await client.post(f"{BASE}/api/solve", params={"token": token}, json=TASK)
        assert response.status == 404
        profile = await (await client.get(f"{BASE}/api/profile", params={"token": token})).json()
        assert profile["data"]["solver"] is False

    async def test_solution_is_shared_and_cached(self, client, token, allowed, monkeypatch):
        calls = []

        async def fake(settings, system, content, **kwargs):
            calls.append((system, content, kwargs))
            yield "### №289\n"
            yield "**Ответ:** $y = 4$"

        monkeypatch.setattr(solver, "stream_messages", fake)
        first = await client.post(f"{BASE}/api/solve", params={"token": token}, json=TASK)
        last = events(await first.read())[-1]
        assert last == {"ok": True, "data": {"text": "### №289\n**Ответ:** $y = 4$", "cached": False, "left": 1}}
        assert "9 класса" not in calls[0][0]  # библиотеки нет — класс неизвестен
        assert "Задание: №289(б)" in calls[0][1][0]["text"]

        second = await client.post(f"{BASE}/api/solve", params={"token": token}, json=TASK)
        assert (await second.json())["data"]["cached"] is True
        assert len(calls) == 1 and allowed.solver_usage[1][1] == 1

    async def test_condition_read_from_picture_is_remembered(self, client, token, allowed, library, tmp_path, monkeypatch):
        from PIL import Image

        (tmp_path / "alg").mkdir()
        Image.new("L", (1600, 2000), 255).save(tmp_path / "alg" / "0005.jpg")
        (tmp_path / "library.json").touch()
        library._loaded_mtime = None
        allowed.textbooks = library
        calls = []

        async def fake(settings, system, content, **kwargs):
            calls.append(content)
            yield "<черновик><условие №289>Решите уравнение: а) $x=1$; б) $x^3=8$</условие> x=2</черновик>"
            yield "### №289\n**Ответ:** 2"

        monkeypatch.setattr(solver, "stream_messages", fake)
        first = await client.post(f"{BASE}/api/solve", params={"token": token}, json=TASK)
        assert events(await first.read())[-1]["data"]["text"] == "### №289\n**Ответ:** 2"
        assert [b for b in calls[0] if b["type"] == "image"]
        assert await allowed.conditions.get_many("alg", [289]) == {289: "Решите уравнение: а) $x=1$; б) $x^3=8$"}

        # Другой пункт того же номера — условие уже текстом, картинки нет.
        second = await client.post(f"{BASE}/api/solve", params={"token": token}, json={**TASK, "text": "№289(а)"})
        assert events(await second.read())[-1]["ok"] is True
        assert not [b for b in calls[1] if b["type"] == "image"]
        assert "$x^3=8$" in calls[1][0]["text"]

        # «Решить заново» читает картинку снова.
        allowed.solver_usage.clear()
        again = await client.post(f"{BASE}/api/solve", params={"token": token}, json={**TASK, "refresh": True})
        assert events(await again.read())[-1]["ok"] is True
        assert [b for b in calls[2] if b["type"] == "image"]

    async def test_comment_fetched_when_app_did_not_send_it(self, client, token, allowed, monkeypatch):
        asked, calls = [], []

        async def comment(user, assignment_id):
            asked.append(assignment_id)
            return "2 ряд - геологические ЧС"

        async def fake(settings, system, content, **kwargs):
            calls.append(content)
            yield "Доклад"

        monkeypatch.setattr(allowed.diary, "fetch_assignment_comment", comment, raising=False)
        monkeypatch.setattr(solver, "stream_messages", fake)
        response = await client.post(f"{BASE}/api/solve", params={"token": token}, json={**TASK, "id": 77})
        assert events(await response.read())[-1]["ok"] is True
        assert asked == [77] and "2 ряд - геологические ЧС" in calls[0][0]["text"]
        # Прислало приложение — в СГО не ходим.
        await client.post(f"{BASE}/api/solve", params={"token": token}, json={**TASK, "id": 77, "comment": ""})
        assert asked == [77]

    async def test_solving_goes_on_after_app_closed(self, client, token, allowed, monkeypatch):
        import asyncio

        release = asyncio.Event()

        async def slow(settings, system, content, **kwargs):
            yield "### №289\n"
            await release.wait()
            yield "**Ответ:** 4"

        monkeypatch.setattr(solver, "stream_messages", slow)
        response = await client.post(f"{BASE}/api/solve", params={"token": token}, json=TASK)
        await response.content.readline()
        response.close()
        release.set()
        for _ in range(100):
            if not solver._running:
                break
            await asyncio.sleep(0.02)
        again = await client.post(f"{BASE}/api/solve", params={"token": token}, json=TASK)
        assert (await again.json())["data"] == {"text": "### №289\n**Ответ:** 4", "cached": True}
        assert allowed.solver_usage[1][1] == 1

    async def test_resume_joins_running_solve_without_quota(self, client, token, allowed, monkeypatch):
        import asyncio

        release = asyncio.Event()

        async def slow(settings, system, content, **kwargs):
            await release.wait()
            yield "**Ответ:** 4"

        monkeypatch.setattr(solver, "stream_messages", slow)
        allowed.solver_usage[1] = ((await allowed.users.get(1)).today(), 1)
        first = asyncio.ensure_future(client.post(f"{BASE}/api/solve", params={"token": token}, json=TASK))
        for _ in range(100):
            if any(lock.locked() for lock in allowed.solving.values()):
                break
            await asyncio.sleep(0.01)
        # Последнее решение дня уже идёт — повтор ждёт его, а не «закончились».
        second = asyncio.ensure_future(client.post(f"{BASE}/api/solve", params={"token": token}, json=TASK))
        await asyncio.sleep(0.05)
        release.set()
        assert events(await (await second).read())[-1]["data"]["text"] == "**Ответ:** 4"
        assert events(await (await first).read())[-1]["ok"] is True
        assert allowed.solver_usage[1][1] == 2

    async def test_failure_refunds_quota(self, client, token, allowed, monkeypatch):
        async def broken(settings, system, content, **kwargs):
            raise solver.AssistantError("Решение сейчас недоступно. Попробуйте чуть позже.")
            yield ""

        monkeypatch.setattr(solver, "stream_messages", broken)
        response = await client.post(f"{BASE}/api/solve", params={"token": token}, json=TASK)
        assert events(await response.read())[-1]["ok"] is False
        assert allowed.solver_usage[1][1] == 0
        assert await allowed.solutions.get(cache_key("x", solver._task(TASK), "0", "m")) is None

    @pytest.mark.parametrize("body", [{}, {"subject": "Алгебра", "date": "вчера"}, {"date": "2026-10-19"}])
    async def test_rejects_bad_task(self, client, token, allowed, body):
        response = await client.post(f"{BASE}/api/solve", params={"token": token}, json=body)
        assert response.status == 400

    async def test_dropped_stream_is_retried(self, client, token, allowed, monkeypatch):
        calls = []

        async def flaky(settings, system, content, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                yield "### №2"
                raise solver.AssistantError("Решение сейчас недоступно. Попробуйте чуть позже.")
            yield "**Ответ:** 4"

        monkeypatch.setattr(solver, "stream_messages", flaky)
        response = await client.post(f"{BASE}/api/solve", params={"token": token}, json=TASK)
        stream = events(await response.read())
        assert {"type": "reset"} in stream
        assert stream[-1]["data"]["text"] == "**Ответ:** 4"
        assert len(calls) == 2 and allowed.solver_usage[1][1] == 1
        again = await client.post(f"{BASE}/api/solve", params={"token": token}, json=TASK)
        assert (await again.json())["data"] == {"text": "**Ответ:** 4", "cached": True}

    async def test_empty_answer_retried_then_refunded(self, client, token, allowed, monkeypatch):
        calls = []

        async def empty(settings, system, content, **kwargs):
            calls.append(1)
            yield "<черновик>думаю</черновик> "

        monkeypatch.setattr(solver, "stream_messages", empty)
        response = await client.post(f"{BASE}/api/solve", params={"token": token}, json=TASK)
        stream = events(await response.read())
        assert stream[-1]["ok"] is False and stream.count({"type": "reset"}) == solver.ATTEMPTS - 1
        assert len(calls) == solver.ATTEMPTS and allowed.solver_usage[1][1] == 0

    async def test_busy_is_not_retried(self, client, token, allowed, monkeypatch):
        calls = []

        async def busy(settings, system, content, **kwargs):
            calls.append(1)
            raise solver.AssistantError("Перегружена", status=503, reason="busy")
            yield ""

        monkeypatch.setattr(solver, "stream_messages", busy)
        response = await client.post(f"{BASE}/api/solve", params={"token": token}, json=TASK)
        assert events(await response.read())[-1]["reason"] == "busy" and len(calls) == 1


HOMEWORK = {"items": [{"id": 101, "subject": "Алгебра"}, {"id": 102, "subject": "Физика"}]}


class TestWholeClass:
    """Владелец — telegram_id 1 (TG_ADMIN_ID в фикстуре), в .env только он."""

    @pytest.fixture
    async def classmates(self, allowed, users):
        from tests.test_repositories import make_user

        await users.save(make_user(2, student_name="Петров П."))
        await users.save(make_user(3, student_name="Сидоров С."))
        await users.save(make_user(4, school=dataclasses.replace(make_user().school, name="Школа №2")))
        await allowed.cache.put(1, "homework", HOMEWORK)
        await allowed.cache.put(2, "homework:past", {"items": [{"id": 102}]})
        await allowed.cache.put(3, "homework", {"items": [{"id": 900}]})
        await allowed.cache.put(4, "homework", HOMEWORK)
        return {n: await allowed.miniapp.issue_token(n) for n in (2, 3, 4)}

    async def profile(self, client, token):
        return (await (await client.get(f"{BASE}/api/profile", params={"token": token})).json())["data"]

    async def switch(self, client, token, on):
        return await client.post(f"{BASE}/api/settings/solver-class", params={"token": token}, json={"on": on})

    async def test_owner_opens_and_closes_for_class(self, client, token, classmates, allowed, monkeypatch):
        async def fake(settings, system, content, **kwargs):
            yield "Ответ"

        monkeypatch.setattr(solver, "stream_messages", fake)
        assert (await self.profile(client, token))["solver_class"] == {"on": False}
        assert (await self.profile(client, classmates[2]))["solver"] is False

        assert (await (await self.switch(client, token, True)).json())["data"] == {"on": True}
        assert (await self.profile(client, classmates[2]))["solver"] is True
        response = await client.post(f"{BASE}/api/solve", params={"token": classmates[2]}, json=TASK)
        assert events(await response.read())[-1]["ok"] is True
        # Другой класс той же школы и та же параллель другой школы — нет.
        assert (await self.profile(client, classmates[3]))["solver"] is False
        assert (await self.profile(client, classmates[4]))["solver"] is False
        response = await client.post(f"{BASE}/api/solve", params={"token": classmates[3]}, json=TASK)
        assert response.status == 404

        await self.switch(client, token, False)
        assert (await self.profile(client, classmates[2]))["solver"] is False
        assert (await self.profile(client, token))["solver"] is True  # владелец в .env

    async def test_class_name_decides_when_known(self, client, token, classmates, allowed):
        await self.switch(client, token, True)
        await allowed.cache.put(1, "class:None", "9б")
        await allowed.cache.put(3, "class:None", "9 Б")
        await allowed.cache.put(2, "class:None", "9а")
        assert (await self.profile(client, classmates[3]))["solver"] is True
        assert (await self.profile(client, classmates[2]))["solver"] is False

    async def test_only_owner_switches(self, client, token, classmates):
        assert (await self.switch(client, classmates[2], True)).status == 403
        assert (await self.profile(client, classmates[2]))["solver_class"] is None
