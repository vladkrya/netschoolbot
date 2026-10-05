"""Вход в «Сетевой город»: регион, школа, способ входа.

В старом проекте это был файл на 857 строк, где шаг диалога хранился строкой
в записи пользователя (`state = "await_school_search"`), а промежуточные
результаты — в глобальном словаре `_SCHOOL_SEARCH_CACHE`. Из-за этого два
параллельных входа мешали друг другу, а брошенный на середине диалог
оставлял пользователя в состоянии, из которого его не выпускала ни одна
команда.

Здесь шаг диалога держит FSM aiogram: состояние привязано к чату, живёт
своим сроком и сбрасывается одной командой.
"""

from __future__ import annotations

import asyncio
import io
import logging
import secrets
from dataclasses import replace

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)

from ...context import AppContext
from ...domain import formatting
from ...domain.models import ApiMode, Credentials, LoginType, School, User
from ...netschool.errors import NetSchoolError, Reason, wrap
from ...netschool.patches import http as http_patch
from .. import keyboards
from .common import reply, report_error

logger = logging.getLogger("netschoolbot.bot")
router = Router(name="auth")

# Сколько ждём кода подтверждения от человека.
OTP_TIMEOUT = 300
# Сколько школ показываем в списке: больше не помещается в клавиатуру.
MAX_SCHOOLS = 20


class Login(StatesGroup):
    region = State()
    school_query = State()
    school_choice = State()
    method = State()
    username = State()
    password = State()
    otp = State()
    qr_pending = State()
    qr_retry = State()


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="login:cancel")]]
    )


@router.message(Command("login"))
@router.message(Command("relogin"))
async def start_login(message: Message, state: FSMContext) -> None:
    await state.clear()
    try:
        from netschoolpy import list_regions
    except ImportError:
        await reply(message, "⚠️ Библиотека «Сетевого города» недоступна. Сообщите администратору.")
        return

    regions = list_regions()
    await state.set_state(Login.region)
    await state.update_data(regions=regions)

    rows = [
        [InlineKeyboardButton(text=name, callback_data=f"region:{index}")]
        for index, name in enumerate(regions)
    ]
    rows.append([InlineKeyboardButton(text="🔗 Ввести адрес вручную", callback_data="region:manual")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="login:cancel")])

    await message.answer(
        "🗺 <b>Шаг 1 из 3.</b> Выберите свой регион.\n\n"
        "Школы по умолчанию нет — вы выбираете её сами.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data == "login:cancel")
@router.message(Command("cancel"))
async def cancel_login(event: Message | CallbackQuery, state: FSMContext) -> None:
    """Выход из диалога с любого шага.

    В старой версии брошенный вход оставлял пользователя в состоянии, из
    которого его не выпускала ни одна команда.
    """
    if isinstance(event, CallbackQuery):
        await event.answer("Отменено")
    await state.clear()
    await reply(event, "Вход отменён. Начать заново: /login")


@router.callback_query(Login.region, F.data.startswith("region:"))
async def choose_region(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    choice = callback.data.split(":", 1)[1]

    if choice == "manual":
        await state.set_state(Login.school_query)
        await state.update_data(url=None)
        await reply(
            callback,
            "🔗 Пришлите адрес сервера «Сетевого города», например\n"
            "<code>https://sgo.example.ru</code>",
            reply_markup=_cancel_keyboard(),
        )
        return

    data = await state.get_data()
    regions: list[str] = data.get("regions") or []
    try:
        region = regions[int(choice)]
    except (ValueError, IndexError):
        await reply(callback, "Не понял, какой регион. Начните заново: /login")
        await state.clear()
        return

    from netschoolpy import REGIONS

    url = REGIONS.get(region)
    if not url:
        await reply(callback, "Для этого региона нет адреса. Введите его вручную: /login")
        await state.clear()
        return

    await state.update_data(url=url, region=region)
    await state.set_state(Login.school_query)
    await reply(
        callback,
        f"🏫 <b>Шаг 2 из 3.</b> Регион: {formatting.esc(region)}\n\n"
        "Пришлите название школы или её номер — найду подходящие.",
        reply_markup=_cancel_keyboard(),
    )


@router.message(Login.school_query)
async def search_school(message: Message, state: FSMContext, app: AppContext) -> None:
    query = (message.text or "").strip()
    if not query:
        await reply(message, "Пришлите название школы текстом.")
        return

    data = await state.get_data()
    url = data.get("url")

    # На ручном вводе первое сообщение — это адрес сервера, а не запрос.
    if url is None:
        if not query.startswith(("http://", "https://")):
            await reply(message, "Адрес должен начинаться с <code>https://</code>")
            return
        await state.update_data(url=query.rstrip("/"))
        await reply(message, "🏫 Теперь пришлите название школы или её номер.")
        return

    notice = await message.answer("⏳ Ищу школы…")
    try:
        from netschoolpy import search_schools

        schools = await search_schools(
            url,
            query,
            timeout=app.settings.netschool.http_timeout,
            proxy=app.settings.netschool.proxy_for(url),
        )
    except Exception as exc:
        # Одна неудача не должна навсегда перевести хост на прокси — именно
        # так когда-то ломался поиск школ до конца жизни процесса.
        http_patch.reset_blocked_hosts()
        await notice.delete()
        error = wrap(exc, context="поиск школ")
        await reply(
            message,
            f"❌ Не получилось найти школы.\n\n{formatting.esc(error.user_message)}\n\n"
            "Пришлите запрос ещё раз или отмените вход: /cancel",
        )
        return

    await notice.delete()
    if not schools:
        await reply(message, "Ничего не нашлось. Попробуйте другой запрос — например, номер школы.")
        return

    names = [s.short_name or s.name for s in schools[:MAX_SCHOOLS]]
    await state.update_data(schools=names)
    await state.set_state(Login.school_choice)

    rows = [
        [InlineKeyboardButton(text=f"🏫 {name}", callback_data=f"school:{index}")]
        for index, name in enumerate(names)
    ]
    rows.append([InlineKeyboardButton(text="🔄 Другой запрос", callback_data="school:retry")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="login:cancel")])

    suffix = f" (показаны первые {MAX_SCHOOLS})" if len(schools) > MAX_SCHOOLS else ""
    await message.answer(
        f"Найдено школ: {len(schools)}{suffix}. Выберите свою:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(Login.school_choice, F.data == "school:retry")
async def retry_search(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(Login.school_query)
    await reply(callback, "🏫 Пришлите другой запрос.", reply_markup=_cancel_keyboard())


@router.callback_query(Login.school_choice, F.data.startswith("school:"))
async def choose_school(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    names: list[str] = data.get("schools") or []
    try:
        school = names[int(callback.data.split(":", 1)[1])]
    except (ValueError, IndexError):
        await reply(callback, "Не понял, какая школа. Начните заново: /login")
        await state.clear()
        return

    await state.update_data(school=school)
    await state.set_state(Login.method)
    await reply(
        callback,
        f"🔐 <b>Шаг 3 из 3.</b> Школа: {formatting.esc(school)}\n\nКак будете входить?",
        reply_markup=keyboards.login_methods(),
    )


@router.callback_query(Login.method, F.data == "login:password")
async def ask_username(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.update_data(login_type=LoginType.PASSWORD.value)
    await state.set_state(Login.username)
    await reply(callback, "👤 Пришлите логин от «Сетевого города».", reply_markup=_cancel_keyboard())


@router.callback_query(Login.method, F.data == "login:esia")
async def ask_esia_username(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.update_data(login_type=LoginType.ESIA.value)
    await state.set_state(Login.username)
    await reply(callback, "👤 Пришлите логин Госуслуг (телефон, СНИЛС или почту).",
                reply_markup=_cancel_keyboard())


@router.message(Login.username)
async def receive_username(message: Message, state: FSMContext) -> None:
    await state.update_data(login=(message.text or "").strip())
    await state.set_state(Login.password)
    await message.answer(
        "🔒 Теперь пришлите пароль.\n\n"
        "Сообщение с паролем я удалю сразу после получения."
    )


@router.message(Login.password)
async def receive_password(
    message: Message, state: FSMContext, user: User, app: AppContext
) -> None:
    password = (message.text or "").strip()
    # Пароль не должен оставаться в истории чата.
    try:
        await message.delete()
    except Exception as exc:  # noqa: BLE001 — прав на удаление может не быть
        logger.debug("Не удалось удалить сообщение с паролем: %s", exc)

    data = await state.get_data()
    login_type = LoginType.parse(data.get("login_type"))
    notice = await message.answer("⏳ Вхожу…")

    candidate = replace(
        user,
        school=School(url=data.get("url", ""), name=data.get("school", "")),
        credentials=Credentials(
            login_type=login_type, login=data.get("login", ""), password=password
        ),
    )

    try:
        await _perform_login(candidate, app, message, login_type)
    except NetSchoolError as exc:
        await notice.delete()
        hint = (
            "\n\nПроверьте логин и пароль или начните заново: /login"
            if exc.reason in (Reason.AUTH, Reason.UNKNOWN)
            else "\n\nПопробуйте ещё раз чуть позже: /login"
        )
        await reply(message, f"❌ {formatting.esc(exc.user_message)}{hint}")
        await state.clear()
        return

    await notice.delete()
    await state.clear()
    await _finish_login(candidate, app, message)


async def _perform_login(
    user: User, app: AppContext, message: Message, login_type: LoginType
) -> None:
    """Выполнить вход и сохранить сессию в пуле."""
    from netschoolpy import NetSchool

    proxy = app.settings.netschool.proxy_for(user.school.url)
    client = NetSchool(user.school.url, proxy=proxy) if proxy else NetSchool(user.school.url)

    try:
        if login_type is LoginType.ESIA:
            await client.login_via_gosuslugi(
                esia_login=user.credentials.login,
                esia_password=user.credentials.password,
                school=user.school.name or None,
                timeout=60,
                otp_callback=_make_otp_callback(message, user.telegram_id),
            )
        else:
            await client.login(
                user_name=user.credentials.login,
                password=user.credentials.password,
                school=user.school.name,
            )
        payload = client.export_session()
    except Exception as exc:
        raise wrap(exc, context="вход") from exc
    finally:
        # Клиент входа больше не нужен: дальше работает пул.
        try:
            http = getattr(client, "_http", None)
            if http is not None and hasattr(http, "aclose"):
                await http.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Не удалось закрыть клиент после входа: %s", exc)

    if payload:
        await app.sessions.save(user.telegram_id, payload)


def _make_otp_callback(message: Message, telegram_id: int):
    """Запросить у человека код подтверждения Госуслуг и дождаться ответа.

    Код приходит следующим сообщением в том же чате: обработчик состояния
    Login.otp кладёт его в future, которую ждёт эта корутина.
    """

    async def callback(mfa_type: str, mfa_info: dict) -> str:
        labels = {
            "SMS": "📱 Код из SMS",
            "MAX": "📲 Код из приложения «МАКС»",
            "TOTP": "🔐 Код из приложения-аутентификатора",
        }
        label = labels.get(mfa_type, f"🔐 Код подтверждения ({mfa_type})")
        phone = mfa_info.get("phone")
        length = mfa_info.get("code_length", 6)

        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        _PENDING_OTP[telegram_id] = future

        await message.answer(
            f"{label}{f' на номер {phone}' if phone else ''} — {length} цифр.\n\n"
            "Пришлите его сюда, я удалю сообщение сразу."
        )
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=OTP_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise NetSchoolError(
                Reason.MFA, "Время ввода кода истекло. Начните вход заново: /login"
            ) from exc
        finally:
            _PENDING_OTP.pop(telegram_id, None)

    return callback


# Ожидающие ввода кода. Ключ — telegram_id, поэтому диалоги разных людей
# не пересекаются (в старой версии тут был общий словарь на модуль).
_PENDING_OTP: dict[int, asyncio.Future[str]] = {}


@router.message(F.text.regexp(r"^\s*\d{4,8}\s*$"))
async def receive_otp(message: Message) -> None:
    """Код подтверждения Госуслуг.

    Отдельного состояния FSM здесь нет намеренно: код запрашивается изнутри
    выполняющегося входа, и состояние в этот момент занято шагом пароля.
    """
    future = _PENDING_OTP.get(message.from_user.id if message.from_user else 0)
    if future is None or future.done():
        return
    try:
        await message.delete()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Не удалось удалить код подтверждения: %s", exc)
    future.set_result((message.text or "").strip())


@router.callback_query(Login.method, F.data == "login:qr")
async def login_by_qr(callback: CallbackQuery, state: FSMContext, user: User, app: AppContext) -> None:
    await callback.answer()
    data = await state.get_data()
    url, school = data.get("url", ""), data.get("school", "")
    if not url or not school:
        await state.clear()
        await reply(callback, "Выберите регион и школу: /login")
        return
    attempt = secrets.token_hex(8)
    await state.update_data(qr_attempt=attempt)
    await state.set_state(Login.qr_pending)

    candidate = replace(
        user,
        school=School(url=url, name=school),
        credentials=Credentials(login_type=LoginType.ESIA_QR),
    )
    ttl = app.settings.netschool.qr_login_ttl
    notice = await callback.message.answer("📱 Готовлю QR-код…")
    qr_message: Message | None = None

    async def show_qr(content: str) -> None:
        nonlocal qr_message
        image = _render_qr(content)
        if image is None:
            await callback.message.answer(
                "Не удалось нарисовать QR-код. Откройте ссылку вручную:\n"
                f"<code>{formatting.esc(content)}</code>",
                parse_mode="HTML",
            )
            return
        qr_message = await callback.message.answer_photo(
            BufferedInputFile(image, filename="gosuslugi.png"),
            caption=(
                "📱 Отсканируйте код в приложении «Госуслуги».\n\n"
                f"⏳ Код действует {ttl} секунд."
            ),
        )

    from netschoolpy import NetSchool

    proxy = app.settings.netschool.proxy_for(url)
    client = NetSchool(url, proxy=proxy) if proxy else NetSchool(url)

    try:
        await asyncio.wait_for(
            client.login_via_gosuslugi_qr(
                qr_callback=show_qr, qr_timeout=ttl + 30, school=school, timeout=30
            ),
            # Даём чуть больше времени, чем живёт код: иначе успешное
            # сканирование на последней секунде пропадало впустую.
            timeout=ttl + 45,
        )
        payload = client.export_session()
    except asyncio.TimeoutError:
        await _delete_quietly(qr_message)
        await notice.delete()
        await _offer_qr_retry(callback, state, attempt, "⌛️ QR-код истёк. Получите новый кнопкой ниже.")
        return
    except Exception as exc:
        await _delete_quietly(qr_message)
        await notice.delete()
        error = wrap(exc, context="QR-вход")
        await _offer_qr_retry(callback, state, attempt, f"❌ {formatting.esc(error.user_message)}")
        return
    finally:
        try:
            http = getattr(client, "_http", None)
            if http is not None and hasattr(http, "aclose"):
                await http.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Не удалось закрыть QR-клиент: %s", exc)

    await _delete_quietly(qr_message)
    await notice.delete()
    if (await state.get_data()).get("qr_attempt") != attempt:
        return
    await state.clear()
    if payload:
        await app.sessions.save(candidate.telegram_id, payload)
    await _finish_login(candidate, app, callback.message)


async def _offer_qr_retry(
    callback: CallbackQuery, state: FSMContext, attempt: str, text: str,
) -> None:
    if (await state.get_data()).get("qr_attempt") != attempt:
        return
    await state.set_state(Login.qr_retry)
    await reply(callback, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Получить новый QR-код", callback_data=f"login:qr_retry:{attempt}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="login:cancel")],
    ]))


@router.callback_query(F.data.startswith("login:qr_retry:"))
async def retry_qr(callback: CallbackQuery, state: FSMContext, user: User, app: AppContext) -> None:
    data = await state.get_data()
    if (await state.get_state()) != Login.qr_retry.state or data.get("qr_attempt") != callback.data.rsplit(":", 1)[-1]:
        await callback.answer("Эта кнопка устарела или QR-код уже запрашивается.")
        return
    await state.set_state(Login.qr_pending)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass  # Старое сообщение могли удалить; новый код всё равно нужен.
    await login_by_qr(callback, state, user, app)


async def _delete_quietly(message: Message | None) -> None:
    if message is None:
        return
    try:
        await message.delete()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Не удалось удалить сообщение: %s", exc)


def _render_qr(content: str) -> bytes | None:
    try:
        import qrcode
    except ImportError:
        logger.warning("Библиотека qrcode не установлена — QR-код не показан")
        return None
    image = qrcode.make(content)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def _settle_api_mode(user: User, app: AppContext) -> tuple[User, str]:
    """Довести вход через Госуслуги до рабочего мобильного режима.

    После входа через Госуслуги у пользователя есть только веб-сессия, а
    мобильному клиенту нужен токен. Пока пользователь в мобильном режиме,
    меняем живую веб-сессию на токен прямо сейчас — позже, без нового кода
    подтверждения, этого не сделать. Не вышло — возвращаем веб-режим и
    говорим об этом, чтобы человек не застрял в цикле «войдите заново».

    Возвращает пользователя и пояснение для сообщения (пустое, если
    сказать нечего).
    """
    if not (user.credentials.login_type.is_gosuslugi and user.api_mode is ApiMode.MOBILE):
        return user, ""
    try:
        await app.pool.switch_api(user, ApiMode.MOBILE)
    except Exception as exc:  # noqa: BLE001 — любой сбой означает «остаёмся на вебе»
        logger.info("Мобильный токен из Госуслуг не получен для %s: %s", user.telegram_id, exc)
        reverted = await app.users.save(replace(user, api_mode=ApiMode.WEB))
        return reverted, (
            "⚠️ Мобильное API через Госуслуги включить не удалось — работаю через веб-API. "
            "Попробовать снова: /changeapi."
        )
    return user, "📱 Мобильное API включено: вход держится 60 дней."


async def _finish_login(user: User, app: AppContext, message: Message) -> None:
    """Завершить вход: сохранить пользователя, найти детей, запустить проверку."""
    saved = await app.users.save(replace(user, enabled=True))
    saved, api_note = await _settle_api_mode(saved, app)

    try:
        saved = await app.diary.sync_students(saved)
    except NetSchoolError as exc:
        # Вход удался, а список детей — нет. Это не повод откатывать вход.
        logger.info("Не удалось получить список учеников для %s: %s", saved.telegram_id, exc.reason)

    # Новая школа или новый ребёнок — старое состояние слежения неприменимо.
    await app.state.forget_all(saved.telegram_id)
    await app.state.mark_baseline_pending(saved.telegram_id, True)
    await app.cache.clear(saved.telegram_id)
    await app.watchers.start(saved.telegram_id)

    lines = [
        "✅ <b>Вход выполнен</b>",
        "",
        f"• Школа: {formatting.esc(saved.school.name)}",
    ]
    if saved.student_name:
        lines.append(f"• Ученик: {formatting.esc(saved.student_name)}")
    if len(saved.available_students) > 1:
        lines.append(f"• Детей в аккаунте: {len(saved.available_students)} (/child — переключить)")
    if api_note:
        lines.extend(["", api_note])
    lines.extend(
        [
            "",
            f"Проверяю оценки каждые {saved.check_interval // 60} мин.",
            "Первая проверка пройдёт молча — я запомню текущие оценки, "
            "чтобы не прислать вам весь журнал разом.",
        ]
    )
    from .menu import miniapp_url

    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer("Открыть мини-приложение", reply_markup=keyboards.miniapp(await miniapp_url(app, saved)))
