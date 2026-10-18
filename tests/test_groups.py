"""Сообщения о «Сетевом городе» в группы: простои, обновления, выбор регионов."""

from types import SimpleNamespace

from aiogram.exceptions import TelegramMigrateToChat
from aiogram.methods import SendMessage

from app.bot.handlers.groups import add_group, group_button, groups_list
from app.db.repositories import BroadcastGroupRepository, SgoStatusRepository
from app.status.groups import GroupBroadcast
from app.status.service import PROBE_EVERY_S, Probe
from tests.test_bot_handlers import FakeCallback, FakeMessage
from tests.test_bot_handlers import app as app_context  # noqa: F401 — фикстура
from tests.test_status import DOWN, MORDOVIA, make_service

CHAT = -1001
OTHER = -1002


async def setup(db, prober):
    service, now = make_service(db, prober)
    sent: list[tuple[int, str]] = []

    async def send(chat_id, text):
        sent.append((chat_id, text))

    repo = BroadcastGroupRepository(db)
    service.groups = GroupBroadcast(repo, send, status_url="https://ns.example/status", clock=lambda: now[0])
    await service.groups.load(SgoStatusRepository(db))
    return service, now, repo, sent


async def rounds(service, now, n):
    for _ in range(n):
        await service.probe_all()
        now[0] += PROBE_EVERY_S


async def test_outage_and_update_reach_only_matching_groups(db):
    state = {"down": False, "version": "5.40"}

    async def prober(target):
        if target.host == DOWN and state["down"]:
            return Probe(False, 0, error="timeout")
        return Probe(True, 100, version=state["version"] if target.host == DOWN else "1")

    service, now, repo, sent = await setup(db, prober)
    await repo.add(CHAT, "Все", now[0])
    await repo.add(OTHER, "Мордовия, только обновления", now[0])
    await repo.set_kind(OTHER, "outages", False)
    await repo.set_regions(OTHER, [MORDOVIA])

    await rounds(service, now, 1)
    assert sent == []  # всё работает
    state["down"] = True
    await rounds(service, now, 1)
    assert sent == []  # одна неудача — ещё не простой
    await rounds(service, now, 1)
    assert [chat for chat, _ in sent] == [CHAT]
    assert "🔴 <b>Челябинская область</b> не отвечает — timeout" in sent[0][1]
    assert "https://ns.example/status" in sent[0][1]
    await rounds(service, now, 2)
    assert len(sent) == 1  # о том же простое повторно не пишем

    state.update(down=False, version="5.41")
    sent.clear()
    await rounds(service, now, 2)
    texts = "\n".join(text for _, text in sent)
    assert [chat for chat, _ in sent] == [CHAT, CHAT]
    assert "🆕 <b>Челябинская область</b>: 5.40 → 5.41" in texts
    assert f"снова работает (не отвечал ~{4 * PROBE_EVERY_S // 60} мин)" in texts

    # Мордовия обновилась — уходит и в группу с одной Мордовией.
    sent.clear()
    state["version"] = "5.41"

    async def mordovia_update(target):
        return Probe(True, 100, version="2" if target.host == MORDOVIA else ("5.41" if target.host == DOWN else "1"))

    service._prober = mordovia_update
    await rounds(service, now, 1)
    assert {chat for chat, _ in sent} == {CHAT, OTHER}


async def test_mass_failure_is_our_problem_and_never_reachable_is_ignored(db):
    state = {"all_down": False}

    async def prober(target):
        if target.host == MORDOVIA:
            return Probe(False, 0, error="connect")  # с нашего сервера не открывался никогда
        if state["all_down"]:
            return Probe(False, 0, error="connect")
        return Probe(True, 100)

    service, now, repo, sent = await setup(db, prober)
    await repo.add(CHAT, "Все", now[0])
    await rounds(service, now, 3)
    assert sent == []  # Мордовия ни разу не отвечала — о ней молчим
    state["all_down"] = True
    await rounds(service, now, 3)
    assert sent == []  # легли все сразу — значит, сеть у нас


async def test_down_state_survives_restart(db):
    state = {"down": False}

    async def prober(target):
        return Probe(False, 0) if target.host == DOWN and state["down"] else Probe(True, 100)

    service, now, repo, sent = await setup(db, prober)
    await repo.add(CHAT, "Все", now[0])
    await rounds(service, now, 1)
    state["down"] = True
    await rounds(service, now, 2)
    assert len(sent) == 1

    restarted, _, _, sent_after = await setup(db, prober)
    restarted._clock = lambda: now[0]
    await rounds(restarted, now, 3)
    assert sent_after == []  # «не отвечает» уже говорили до перезапуска
    state["down"] = False
    await rounds(restarted, now, 2)
    assert len(sent_after) == 1 and "снова работает" in sent_after[0][1]


async def test_group_migrated_to_supergroup_gets_new_id(db):
    repo = BroadcastGroupRepository(db)
    await repo.add(CHAT, "Группа", 0)
    sent = []

    async def send(chat_id, text):
        if chat_id == CHAT:
            raise TelegramMigrateToChat(method=SendMessage(chat_id=chat_id, text=text), message="", migrate_to_chat_id=-1009)
        sent.append(chat_id)

    from app.status.groups import Event

    groups = GroupBroadcast(repo, send)
    assert await groups.publish([Event(DOWN, "outages", "x")]) == 1
    assert sent == [-1009]
    assert [g["chat_id"] for g in await repo.all()] == [-1009]


class FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def get_chat(self, chat_id):
        if chat_id == 404:
            raise RuntimeError("chat not found")
        return SimpleNamespace(id=chat_id, type="supergroup", title="Учителя <школы>")

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


def admin_message(text, chat_type="private", chat_id=1, user=1):
    message = FakeMessage(text)
    message.from_user = SimpleNamespace(id=user)
    message.chat = SimpleNamespace(id=chat_id, type=chat_type)
    return message


async def test_admin_adds_and_configures_group(app_context):
    bot = FakeBot()
    repo = BroadcastGroupRepository(app_context.db)

    stranger = admin_message("/addgroup -1001", user=2)
    await add_group(stranger, app_context, SimpleNamespace(args="-1001"), bot)
    assert stranger.answers == [] and await repo.all() == []

    message = admin_message("/addgroup -1001")
    await add_group(message, app_context, SimpleNamespace(args="-1001"), bot)
    assert "Группа подключена" in message.texts and "Учителя &lt;школы&gt;" in message.texts
    group = await repo.get(-1001)
    assert group["outages"] and group["updates"] and group["regions"] == []

    message = admin_message("/addgroup 404")
    await add_group(message, app_context, SimpleNamespace(args="404"), bot)
    assert "Не вижу чат" in message.texts

    # /addgroup в самой группе: карточка — в личку админу.
    message = admin_message("/addgroup", chat_type="group", chat_id=-1002)
    await add_group(message, app_context, SimpleNamespace(args=None), bot)
    assert bot.sent[0][0] == 1 and "-1002" in bot.sent[0][1]
    assert "подключена" in message.texts

    def press(data, user=1):
        callback = FakeCallback(data)
        callback.from_user = SimpleNamespace(id=user)
        return callback

    callback = press("grp:k:-1001:outages")
    await group_button(callback, app_context, bot)
    group = await repo.get(-1001)
    assert not group["outages"] and group["updates"]
    assert "Сообщает: обновления версии" in callback.message.texts

    callback = press("grp:reg:-1001:0")
    await group_button(callback, app_context, bot)
    markup = callback.message.answers[-1][1]["reply_markup"]
    first = markup.inline_keyboard[0][0].callback_data
    assert first.startswith("grp:rt:-1001:0:") and len(first) <= 64
    host = first.split(":", 4)[4]
    await group_button(press(first), app_context, bot)
    assert (await repo.get(-1001))["regions"] == [host]
    await group_button(press(first), app_context, bot)
    assert (await repo.get(-1001))["regions"] == []

    await group_button(press("grp:test:-1001"), app_context, bot)
    assert bot.sent[-1][0] == -1001 and "Проверка" in bot.sent[-1][1]

    ignored = press("grp:delok:-1001", user=2)
    await group_button(ignored, app_context, bot)
    assert await repo.get(-1001) is not None

    await group_button(press("grp:delok:-1001"), app_context, bot)
    assert await repo.get(-1001) is None

    message = admin_message("/groups")
    await groups_list(message, app_context)
    assert "Учителя" in message.texts and "регионы: все" in message.texts
