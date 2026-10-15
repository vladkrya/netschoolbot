"""Напоминания, риск по оценке, сводки, все дети, второй родитель, заметки, выгрузка."""

import datetime as dt
import io
import zipfile
from dataclasses import replace
from xml.etree import ElementTree

import pytest

from app.db.repositories import FamilyRepository, NotesRepository
from app.domain import formatting, insights
from app.domain.export import (
    absences_workbook, build_xlsx, dynamics_workbook, export_filename, grades_workbook, homework_workbook,
    lessons_workbook, schedule_workbook, subjects_workbook, control_works_workbook,
)
from app.domain.models import NotificationPrefs, Student, parse_marks_filter
from app.domain.periods import periods
from app.domain.records import DiaryDay, HomeworkRecord, Lesson, MarkRecord
from app.notifications import digests
from app.notifications.watcher import UserWatcher
from tests.test_bot_handlers import app  # noqa: F401 — фикстура
from tests.test_repositories import make_user
from tests.test_watcher import FakeDiary, FakeNotifier, record


def mark(value, day=dt.date(2026, 10, 5), subject="Химия", weight=1, index=0):
    return MarkRecord(subject=subject, date=day, assignment_type="Ответ на уроке", content="",
                      mark=str(value), weight=weight, lesson_number=index + 1, assignment_index=index)


def homework(subject="Алгебра", day=dt.date(2026, 10, 15), kind="Домашнее задание", text="№ 12", weight=1):
    return HomeworkRecord(subject=subject, due_date=day, assignment_type=kind, text=text, lesson_number=1, weight=weight)


class TestInsights:
    def test_test_work_by_type_or_weight(self):
        assert insights.is_test_work("Контрольная работа")
        assert insights.is_test_work("Самостоятельная работа")
        assert insights.is_test_work("Ответ на уроке", 3)
        assert not insights.is_test_work("Домашнее задание", 1)

    def test_needed_marks(self):
        marks = [mark(3), mark(4, index=1)]
        assert insights.needed_marks(marks, 4.5) == 4
        assert insights.needed_marks([mark(5)], 4.5) == 0

    def test_grade_drop_detected(self):
        before = [mark(4), mark(4, index=1), mark(3, index=2)]
        after = before + [mark(2, index=3)]
        alerts = insights.grade_drops(before, after, {})
        assert len(alerts) == 1
        assert (alerts[0].previous_grade, alerts[0].grade) == (4, 3)
        assert alerts[0].fives and alerts[0].fives > 0

    def test_no_alert_when_grade_holds(self):
        before = [mark(5), mark(5, index=1)]
        assert insights.grade_drops(before, before + [mark(4, index=2)], {}) == []

    def test_own_thresholds_respected(self):
        before = [mark(5), mark(5, index=1), mark(4, index=2)]
        after = before + [mark(4, index=3)]
        prefs = {"thresholds": {"subjects": {"Химия": {"5": 4.6, "4": 3.5, "3": 2.5}}}}
        # Средний 4,5: по общим порогам всё ещё «5», по своим — уже «4».
        assert insights.grade_drops(before, after, {}) == []
        assert insights.grade_drops(before, after, prefs)[0].grade == 4

    def test_on_the_edge(self):
        edge = insights.on_the_edge([mark(5), mark(4, index=1), mark(5, index=2), mark(4, index=3)], {})
        assert edge and edge[0].grade == 5

    def test_upcoming_tests_one_per_subject(self):
        items = [homework(kind="Контрольная работа"), homework(kind="Контрольная работа", text="ещё"),
                 homework(subject="Физика")]
        found = insights.upcoming_tests(items, [dt.date(2026, 10, 15)])
        assert [i.subject for i in found] == ["Алгебра"]

    def test_streak_without_twos(self):
        marks = [mark(2, day=dt.date(2026, 10, 5)), mark(5, day=dt.date(2026, 10, 9))]
        # Пн 5 → до пятницы 9-го: вт, ср, чт, пт.
        assert insights.streak_without(marks, dt.date(2026, 10, 9)) == 4
        assert insights.streak_without([mark(5)], dt.date(2026, 10, 9)) is None


class TestDigestPeriods:
    def test_reminder_at_chosen_hour_before_school_day(self):
        assert digests.reminder_period(dt.datetime(2026, 10, 14, 18, 5), 18) == "2026-10-15"
        assert digests.reminder_period(dt.datetime(2026, 10, 14, 17, 59), 18) is None
        # Пятница вечером — суббота не учебный день.
        assert digests.reminder_period(dt.datetime(2026, 10, 16, 19, 0), 18) is None

    def test_monthly_in_first_days(self):
        assert digests.monthly_period(dt.datetime(2026, 11, 1, 9, 0)) == "2026-10-01"
        assert digests.monthly_period(dt.datetime(2026, 11, 1, 7, 0)) is None
        assert digests.monthly_period(dt.datetime(2026, 11, 5, 9, 0)) is None
        assert digests.month_bounds(dt.date(2026, 2, 1)) == (dt.date(2026, 2, 1), dt.date(2026, 2, 28))

    def test_ended_period(self):
        year = periods(dt.date(2026, 10, 1))
        assert digests.ended_period(dt.datetime(2026, 11, 3, 10, 0), year).key == "q1"
        assert digests.ended_period(dt.datetime(2026, 11, 20, 10, 0), year) is None


class TestFormatting:
    def test_reminder_lists_undone_notes_and_tests(self):
        day = dt.date(2026, 10, 15)
        text = formatting.reminder(day, [homework()], [{"text": "Циркуль", "subject": ""}],
                                   [homework(subject="Физика", kind="Контрольная работа", weight=3)])
        assert "не сделано" in text and "Циркуль" in text and "Контрольная работа" in text and "вес 3" in text
        assert formatting.reminder(day, [], [], []) is None

    def test_monthly_summary(self):
        stats = insights.period_stats([mark(5), mark(4, index=1), mark(5, subject="Физика"), mark(5, subject="Физика", index=1)])
        previous = insights.period_stats([mark(3)])
        text = formatting.monthly_summary(dt.date(2026, 10, 1), stats, previous)
        assert "октябрь 2026" in text and "↑" in text

    def test_term_summary(self):
        grades = insights.subject_grades([mark(5), mark(4, index=1)], {})
        text = formatting.term_summary("1 четверть", grades, insights.period_stats([mark(5), mark(4, index=1)]))
        assert "Химия" in text and "«5»" in text

    def test_last_marks_newest_first(self):
        text = formatting.last_marks([mark(3, day=dt.date(2026, 10, 1)), mark(5, day=dt.date(2026, 10, 9))])
        assert text.index("<b>5</b>") < text.index("<b>3</b>")


class TestExport:
    def test_workbook_is_valid_xlsx(self):
        year = periods(dt.date(2026, 10, 1))
        data = grades_workbook([mark(5), mark(4, index=1), mark("зачёт", index=2)], title="1 четверть",
                               student="Иванов Иван", periods=year)
        with zipfile.ZipFile(io.BytesIO(data)) as book:
            names = book.namelist()
            assert "xl/workbook.xml" in names and "xl/worksheets/sheet2.xml" in names
            for name in names:
                if name.endswith(".xml") or name.endswith(".rels"):
                    ElementTree.fromstring(book.read(name))
            summary = book.read("xl/worksheets/sheet1.xml").decode()
            assert "Химия" in summary and "4.5" in summary

    def test_other_reports(self):
        day = dt.date(2026, 10, 1)
        days = [DiaryDay(day=day, lessons=[
            Lesson(1, "Химия", start="08:30", end="09:15", topic="Кислоты", absence="Н", marks=(mark(5),)),
            Lesson(2, "Физика", absence="Б"),
        ], homework=[homework(), homework()])]

        def sheets(data):
            with zipfile.ZipFile(io.BytesIO(data)) as book:
                names = [n for n in book.namelist() if n.startswith("xl/worksheets/")]
                for name in book.namelist():
                    if name.endswith((".xml", ".rels")):
                        ElementTree.fromstring(book.read(name))
                return [book.read(n).decode() for n in sorted(names)]

        summary, listing = sheets(absences_workbook(days, title="1 четверть"))
        assert "Химия" in summary and "Физика" in summary and "<v>2</v>" in summary
        assert "01.10.2026" in listing
        listing, by_subject = sheets(homework_workbook(days, title="1 четверть"))
        # Одно и то же задание в двух местах дневника — одна строка.
        assert by_subject.count("<v>1</v>") >= 1 and "<v>2</v>" not in by_subject
        (lessons,) = sheets(lessons_workbook(days, title="1 четверть"))
        assert "Кислоты" in lessons and "08:30–09:15" in lessons
        assert export_filename("Иванов", "1 четверть", "Пропуски") == "Пропуски_Иванов_1_четверть.xlsx"

    def test_more_reports(self):
        sep, octo = dt.date(2026, 9, 3), dt.date(2026, 10, 1)
        test = MarkRecord(subject="Химия", date=octo, assignment_type="Контрольная работа", content="Кислоты",
                          mark="3", weight=3, lesson_number=1)
        days = [
            DiaryDay(day=sep, lessons=[Lesson(1, "Химия", start="08:30", end="09:15", room="12",
                                              teacher="Иванова А. А.", marks=(mark(5, day=sep),))],
                     marks=[mark(5, day=sep)]),
            DiaryDay(day=octo, lessons=[Lesson(1, "Химия", start="08:30", end="09:15", room="12",
                                               absence="Н", marks=(test,))], marks=[test]),
        ]

        def sheets(data):
            with zipfile.ZipFile(io.BytesIO(data)) as book:
                for name in book.namelist():
                    if name.endswith((".xml", ".rels")):
                        ElementTree.fromstring(book.read(name))
                return [book.read(n).decode() for n in sorted(book.namelist()) if n.startswith("xl/worksheets/")]

        (subjects,) = sheets(subjects_workbook(days, title="Год"))
        assert "Иванова А. А." in subjects and "<v>2</v>" in subjects and "5 3" in subjects
        (dynamics,) = sheets(dynamics_workbook(days, title="Год"))
        # Сентябрь 5, октябрь 3 — проседание на два балла.
        assert "сен 2026" in dynamics and "окт 2026" in dynamics and "<v>-2.0</v>" in dynamics
        summary, works = sheets(control_works_workbook(days, title="Год"))
        assert "Кислоты" in works and "Контрольная работа" in works and "Химия" in summary
        (schedule,) = sheets(schedule_workbook(days, title="Год"))
        assert "Химия (12)" in schedule and "08:30–09:15" in schedule and "Чт" in schedule and "Ср" not in schedule

    def test_escapes_text(self):
        data = build_xlsx([("Лист", [["<&>"]], [10])])
        with zipfile.ZipFile(io.BytesIO(data)) as book:
            ElementTree.fromstring(book.read("xl/worksheets/sheet1.xml"))


class TestPrefsStorage:
    async def test_new_prefs_roundtrip(self, users):
        prefs = NotificationPrefs(homework_reminder=True, reminder_hour=20, test_reminder=True, grade_risk=True,
                                  monthly_summary=True, all_children=True, marks_filter=(2, 3))
        await users.save(make_user(notifications=prefs))
        assert (await users.get(1)).notifications == prefs

    def test_marks_filter(self):
        assert parse_marks_filter("3,2,x,9") == (2, 3)
        prefs = NotificationPrefs(marks_filter=(2, 3))
        assert prefs.wants_mark("2") and not prefs.wants_mark("5") and prefs.wants_mark("зачёт")


class TestNotesAndFamily:
    async def test_notes_crud(self, db, users):
        await users.save(make_user())
        notes = NotesRepository(db)
        note = await notes.add(1, dt.date(2026, 10, 15), "  Принести   циркуль ", "Геометрия")
        assert note["text"] == "Принести циркуль"
        assert await notes.update(1, note["id"], done=True)
        assert not await notes.update(2, note["id"], done=False)
        items = await notes.between(1, dt.date(2026, 10, 1), dt.date(2026, 10, 31))
        assert items[0]["done"] is True
        assert await notes.delete(1, note["id"])
        assert await notes.between(1, dt.date(2026, 10, 1), dt.date(2026, 10, 31)) == []

    async def test_note_time_edit_and_reminder(self, db, users):
        await users.save(make_user())
        notes = NotesRepository(db)
        msk = dt.timezone(dt.timedelta(hours=3))
        note = await notes.add(1, dt.date(2026, 10, 15), "Кружок", at="16:30", remind=15, tz=msk)
        assert (note["time"], note["remind"]) == ("16:30", 15)
        due = dt.datetime(2026, 10, 15, 16, 15, tzinfo=msk).timestamp()
        assert await notes.due_reminders(due - 1) == []
        [(uid, found, when)] = await notes.due_reminders(due)
        assert (uid, found["text"], when) == (1, "Кружок", due)
        # Перенесли на час позже — напомнит к новому сроку.
        edited = await notes.update(1, note["id"], at="17:30", text="Кружок рисования", tz=msk)
        assert edited["time"] == "17:30" and edited["text"] == "Кружок рисования" and edited["remind"] == 15
        assert await notes.due_reminders(due) == []
        assert len(await notes.due_reminders(due + 3600)) == 1
        await notes.reminded(note["id"])
        assert await notes.due_reminders(due + 3600) == []
        # Без времени напоминать не к чему.
        cleared = await notes.update(1, note["id"], at="", tz=msk)
        assert cleared["remind"] is None
        # Выполненная задача не напоминает.
        other = await notes.add(1, dt.date(2026, 10, 15), "Сменка", at="08:00", remind=0, tz=msk)
        await notes.update(1, other["id"], done=True)
        assert await notes.due_reminders(due + 86400) == []
        assert await notes.update(2, other["id"], text="чужая") is None

    async def test_note_reminder_sending(self, db, users):
        from app.notifications.note_reminders import STALE_SECONDS, send_due

        await users.save(make_user())
        notes = NotesRepository(db)
        await notes.add(1, dt.date(2026, 10, 15), "Принести <циркуль>", "Геометрия", at="10:00", remind=10)
        await notes.add(1, dt.date(2026, 10, 14), "Давнее", at="10:00", remind=10)
        notifier = FakeNotifier()
        now = dt.datetime(2026, 10, 15, 9, 50, tzinfo=dt.timezone.utc).timestamp()
        assert await send_due(notes, users, notifier, now) == 1
        assert notifier.texts == ["⏰ Через 10 мин, в 10:00: Принести &lt;циркуль&gt; (Геометрия)"]
        # Второй раз то же не шлётся, проспанное (давнее STALE_SECONDS) — тоже.
        assert await send_due(notes, users, notifier, now + STALE_SECONDS) == 0

    async def test_invite_is_single_use(self, db, users):
        for uid in (1, 2, 3):
            await users.save(make_user(telegram_id=uid))
        family = FamilyRepository(db)
        code = await family.issue_invite(1)
        assert await family.accept(code, 1) is None  # своё приглашение
        assert await family.accept(code, 2, "Мама") == 1
        assert await family.accept(code, 3) is None  # уже использовано
        assert [f["name"] for f in await family.followers(1)] == ["Мама"]
        assert await family.following(2) == [1]
        assert await family.remove(1, 2)
        assert await family.followers(1) == []


class ChildDiary(FakeDiary):
    """Дневник, где у каждого ребёнка свои оценки."""

    def __init__(self):
        super().__init__()
        self.by_child: dict[int, list] = {}

    async def fetch_diary(self, user, **kwargs):
        self.marks = self.by_child.get(user.selected_student_id, [])
        return await super().fetch_diary(user, **kwargs)


class TestWatcherFeatures:
    async def test_marks_filter_skips_good_marks(self, users, marks):
        diary, notifier = FakeDiary(), FakeNotifier()
        await users.save(make_user(notifications=NotificationPrefs(marks_filter=(2, 3))))
        watcher = UserWatcher(1, users=users, state=marks, diary=diary, notifier=notifier)
        diary.marks = [record(0)]
        await watcher.run_once()
        diary.marks = [record(0), record(1, "5"), record(2, "2")]
        await watcher.run_once()
        assert [e.record.mark for e in notifier.mark_events] == ["2"]

    async def test_other_child_notifications_signed(self, users, marks):
        diary, notifier = ChildDiary(), FakeNotifier()
        students = (Student(1, "Аня"), Student(2, "Боря"))
        await users.save(make_user(available_students=students, selected_student_id=1, student_name="Аня",
                                   notifications=NotificationPrefs(all_children=True)))
        clock = {"t": 0.0}
        watcher = UserWatcher(1, users=users, state=marks, diary=diary, notifier=notifier, clock=lambda: clock["t"])
        diary.by_child = {1: [record(0)], 2: [record(5)]}
        await watcher.run_once()
        assert notifier.mark_events == []
        clock["t"] += 10_000
        diary.by_child = {1: [record(0)], 2: [record(5), record(6, "4")]}
        seen = []
        original = notifier.send_mark_events

        async def capture(user, events):
            seen.append(user.student_name)
            await original(user, events)

        notifier.send_mark_events = capture
        await watcher.run_once()
        assert [e.record.mark for e in notifier.mark_events] == ["4"]
        assert seen == ["Боря"]

    async def test_reminder_lists_undone_and_skips_done(self, users, marks, monkeypatch):
        import app.notifications.watcher as module

        diary, notifier = FakeDiary(), FakeNotifier()
        await users.save(make_user(notifications=NotificationPrefs(homework_reminder=True, test_reminder=True)))
        done_item = homework(subject="Русский", day=dt.date(2026, 10, 15), text="Упр. 5")
        diary.homework = [homework(day=dt.date(2026, 10, 15)), done_item,
                          homework(subject="Физика", day=dt.date(2026, 10, 16), kind="Контрольная работа")]
        await marks.set_homework_done(1, done_item.key, True)
        monkeypatch.setattr(module, "_local_now", lambda user: dt.datetime(2026, 10, 14, 18, 30))
        watcher = UserWatcher(1, users=users, state=marks, diary=diary, notifier=notifier)
        await watcher.run_once()
        await watcher.run_once()
        reminders = [t for t in notifier.texts if "не сделано" in t]
        assert len(reminders) == 1
        assert "Алгебра" in reminders[0] and "Русский" not in reminders[0] and "Физика" in reminders[0]

    async def test_grade_risk_alert(self, users, marks, monkeypatch):
        import app.notifications.watcher as module

        diary, notifier = FakeDiary(), FakeNotifier()
        await users.save(make_user(notifications=NotificationPrefs(grade_risk=True)))
        monkeypatch.setattr(module, "_local_now", lambda user: dt.datetime(2026, 10, 14, 12, 0))
        watcher = UserWatcher(1, users=users, state=marks, diary=diary, notifier=notifier)
        diary.marks = [mark(4), mark(4, index=1), mark(3, index=2)]
        await watcher.run_once()
        diary.marks = diary.marks + [mark(2, index=3)]
        await watcher.run_once()
        assert any("под угрозой" in t and "Химия" in t for t in notifier.texts)


class TestBotFeatures:
    async def test_today_shows_lessons_marks_and_notes(self, app, users, monkeypatch):
        from app.bot.handlers import menu
        from app.domain.records import DiaryDay, Lesson
        from tests.test_bot_handlers import FakeMessage

        user = await users.save(make_user())
        today = user.today()

        async def fetch_period(u, start, end, **kwargs):
            return [DiaryDay(day=today, lessons=[Lesson(number=1, subject="Химия", start="08:30", end="09:10",
                                                        room="214", marks=(mark(5, day=today),))])]

        monkeypatch.setattr(app.diary, "fetch_period", fetch_period)
        await app.notes.add(1, today, "Принести халат")
        message = FakeMessage()
        await menu.today_command(message, user, app)
        assert "Химия" in message.texts and "каб. 214" in message.texts and "<b>5</b>" in message.texts
        assert "Принести халат" in message.texts

    async def test_marks_filter_callback(self, app, users):
        from app.bot.handlers import settings as handlers
        from tests.test_bot_handlers import FakeCallback

        user = await users.save(make_user())
        await handlers.set_marks_filter(FakeCallback("markf:5"), user, app)
        assert (await users.get(1)).notifications.marks_filter == (2, 3, 4)
        user = await users.get(1)
        await handlers.set_marks_filter(FakeCallback("markf:all"), user, app)
        assert (await users.get(1)).notifications.marks_filter == ()

    async def test_follower_gets_copy_and_child_label(self, app, users):
        from app.bot.notifier import TelegramNotifier
        from tests.test_bot_handlers import TestTelegramNotifier

        bot = TestTelegramNotifier.Bot()
        owner = await users.save(make_user(
            student_name="Аня", available_students=(Student(1, "Аня"), Student(2, "Боря")), selected_student_id=1,
            notifications=NotificationPrefs(all_children=True)))
        await users.save(make_user(telegram_id=2))
        code = await app.family.issue_invite(1)
        await app.family.accept(code, 2, "Папа")
        notifier = TelegramNotifier(bot, users, family=app.family)
        await notifier.send_text(replace(owner, student_name="Боря"), "📚 Новое задание")
        assert [chat for chat, _ in bot.sent] == [1, 2]
        assert all(text.startswith("👤 <b>Боря</b>") for _, text in bot.sent)

    async def test_followers_get_only_what_owner_allowed(self, app, users):
        from app.accounts import WEB_ID_BASE
        from app.bot.notifier import TelegramNotifier
        from tests.test_bot_handlers import TestTelegramNotifier

        class Push:
            def __init__(self):
                self.sent = []

            async def send(self, telegram_id, title, body, kind=""):
                self.sent.append((telegram_id, body))
                return True

        bot, push = TestTelegramNotifier.Bot(), Push()
        owner = await users.save(make_user(student_name="Аня"))
        web_id = WEB_ID_BASE + 5
        for follower, notify in ((2, True), (3, False), (web_id, True)):
            await users.save(make_user(telegram_id=follower))
            code = await app.family.issue_invite(1, notify=notify, view=True)
            await app.family.accept(code, follower, "Родной")
        notifier = TelegramNotifier(bot, users, push=push, family=app.family)
        await notifier.send_text(owner, "📚 Новое задание", push=("Новые задания", "Алгебра"))
        # Только просмотр (3) копий не получает; без Telegram — только push.
        assert [chat for chat, _ in bot.sent] == [1, 2]
        assert [chat for chat, _ in push.sent] == [1, 2, web_id]
        assert push.sent[1][1] == "Аня: Алгебра"
