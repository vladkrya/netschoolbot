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
from ...netschool.errors import NetSchoolError, Reason, login_message, wrap
from ...netschool import login as login_flow
from ...netschool.myschool import myschool_school
from ...netschool.ownership import AccountTaken
from ...netschool.patches import http as http_patch
from .. import keyboards
from .common import reply, report_error

logger = logging.getLogger("netschoolbot.bot")
router = Router(name="auth")

# Сколько ждём кода подтверждения от человека.
OTP_TIMEOUT = 300
# Сколько школ показываем в списке: больше не помещается в клавиатуру.
MAX_SCHOOLS = 20
# Регионов на одной странице списка: весь список не помещается на экран.
REGIONS_PER_PAGE = 8


class Login(StatesGroup):
    region = State()
    school_query = State()
    school_choice = State()
    method = State()
    remember = State()
    username = State()
    password = State()
    otp = State()
    qr_pending = State()
    qr_retry = State()


async def _step(target: Message | CallbackQuery, state: FSMContext, text: str, **kwargs) -> None:
    """Следующий шаг входа: прошлый шаг удаляется, чтобы в чате не путаться."""
    message = getattr(target, "message", target)
    await _drop_step(message, state)
    sent = await message.answer(text, parse_mode="HTML", **kwargs)
    if getattr(sent, "message_id", None) is not None:
        await state.update_data(step_id=sent.message_id)


async def _drop_step(message: Message, state: FSMContext) -> None:
    """Удалить сообщение прошлого шага, если оно ещё есть."""
    step_id = (await state.get_data()).get("step_id")
    bot, chat = getattr(message, "bot", None), getattr(message, "chat", None)
    if step_id is None or bot is None or chat is None:
        return
    try:
        await bot.delete_message(chat.id, step_id)
    except Exception as exc:  # noqa: BLE001 — сообщение могли удалить сами
        logger.debug("Не удалось удалить прошлый шаг входа: %s", exc)


def _regions_keyboard(regions: list[str], page: int) -> InlineKeyboardMarkup:
    pages = max(1, -(-len(regions) // REGIONS_PER_PAGE))
    page = max(0, min(page, pages - 1))
    start = page * REGIONS_PER_PAGE
    rows = [
        [InlineKeyboardButton(text=name, callback_data=f"region:{index}")]
        for index, name in enumerate(regions[start:start + REGIONS_PER_PAGE], start)
    ]
    if pages > 1:
        rows.append([
            InlineKeyboardButton(text="◀️", callback_data=f"region:page:{(page - 1) % pages}"),
            InlineKeyboardButton(text=f"{page + 1} из {pages}", callback_data="region:noop"),
            InlineKeyboardButton(text="▶️", callback_data=f"region:page:{(page + 1) % pages}"),
        ])
    rows.append([InlineKeyboardButton(text="🔗 Ввести адрес вручную", callback_data="region:manual")])
    rows.append([InlineKeyboardButton(text="🎓 Госуслуги «Моя школа»", callback_data="region:myschool")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="login:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _consent(app: AppContext) -> str:
    """Согласие перед вводом пароля — как подпись под кнопкой «Войти» на сайте."""
    url = f"{app.settings.web.miniapp_url}/privacy"
    return (f"<i>Отправляя логин и пароль, вы соглашаетесь с "
            f'<a href="{url}">политикой конфиденциальности</a>. '
            "Это неофициальный бот, он не связан с ИРТех.</i>")


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

    await _step(
        message, state,
        "🗺 <b>Шаг 1 из 3.</b> Выберите свой регион.\n\n"
        "Школы по умолчанию нет — вы выбираете её сами.",
        reply_markup=_regions_keyboard(regions, 0),
    )


@router.callback_query(F.data == "relogin:start")
async def relogin_from_notice(callback: CallbackQuery, state: FSMContext) -> None:
    """Кнопка «Войти заново» под уведомлением, что вход не удался."""
    await callback.answer()
    await _drop_buttons(callback)
    await start_login(callback.message, state)


@router.callback_query(F.data == "relogin:keep")
async def keep_without_login(callback: CallbackQuery) -> None:
    """«Продолжить так»: в приложении остаются сохранённые данные."""
    await callback.answer("Хорошо. Войти можно в любой момент: /login")
    await _drop_buttons(callback)


async def _drop_buttons(callback: CallbackQuery) -> None:
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001 — старое сообщение могли удалить
        pass


@router.callback_query(F.data == "login:cancel")
@router.message(Command("cancel"))
async def cancel_login(event: Message | CallbackQuery, state: FSMContext) -> None:
    """Выход из диалога с любого шага.

    В старой версии брошенный вход оставлял пользователя в состоянии, из
    которого его не выпускала ни одна команда.
    """
    if isinstance(event, CallbackQuery):
        await event.answer("Отменено")
    await _drop_step(getattr(event, "message", event), state)
    await state.clear()
    await reply(event, "Вход отменён. Начать заново: /login")


@router.callback_query(Login.region, F.data == "region:myschool")
async def choose_myschool(callback: CallbackQuery, state: FSMContext, app: AppContext) -> None:
    """«Госуслуги Моя школа»: региона и школы нет, вход только через Госуслуги."""
    await callback.answer()
    await state.update_data(login_type=LoginType.ESIA.value, remember=False, myschool=True)
    await state.set_state(Login.username)
    await _step(callback, state,
        "👤 Пришлите логин Госуслуг (телефон, СНИЛС или почту).\n\n"
        "Логин и пароль нужны только на этот вход и не сохраняются.\n\n"
        + _consent(app),
        reply_markup=_cancel_keyboard(), disable_web_page_preview=True)


@router.callback_query(Login.region, F.data.startswith("region:"))
async def choose_region(callback: CallbackQuery, state: FSMContext) -> None:
    choice = callback.data.split(":", 1)[1]
    if choice == "noop":
        await callback.answer()
        return
    if choice.startswith("page:"):
        await callback.answer()
        regions = (await state.get_data()).get("regions") or []
        try:
            page = int(choice.split(":", 1)[1])
        except ValueError:
            page = 0
        try:
            await callback.message.edit_reply_markup(reply_markup=_regions_keyboard(regions, page))
        except TelegramBadRequest:
            pass  # та же страница — Telegram не даёт «изменить» на то же самое
        return
    await callback.answer()

    if choice == "manual":
        await state.set_state(Login.school_query)
        await state.update_data(url=None)
        await _step(
            callback, state,
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
    await _step(
        callback, state,
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
        await _step(message, state, "🏫 Теперь пришлите название школы или её номер.",
                    reply_markup=_cancel_keyboard())
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
    await _step(
        message, state,
        f"Найдено школ: {len(schools)}{suffix}. Выберите свою:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(Login.school_choice, F.data == "school:retry")
async def retry_search(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(Login.school_query)
    await _step(callback, state, "🏫 Пришлите другой запрос.", reply_markup=_cancel_keyboard())


@router.callback_query(Login.school_choice, F.data.startswith("school:"))
async def choose_school(callback: CallbackQuery, state: FSMContext, user: User) -> None:
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
    markup = keyboards.login_methods()
    if (user.credentials.remember and user.credentials.login and user.credentials.password
            and user.school.url == data.get("url") and user.school.name == school):
        markup.inline_keyboard.insert(1, [InlineKeyboardButton(
            text="🔐 Войти с сохранёнными данными", callback_data="login:saved")])
    await _step(
        callback, state,
        f"🔐 <b>Шаг 3 из 3.</b> Школа: {formatting.esc(school)}\n\nКак будете входить?",
        reply_markup=markup,
    )


@router.callback_query(Login.method, F.data == "login:saved")
async def login_saved(callback: CallbackQuery, state: FSMContext, user: User, app: AppContext) -> None:
    await callback.answer()
    data = await state.get_data()
    if (not user.credentials.remember or not user.credentials.login or not user.credentials.password
            or user.school.url != data.get("url") or user.school.name != data.get("school")):
        await reply(callback, "Сохранённых данных для этой школы нет. Выберите другой способ входа.")
        return
    await state.update_data(login_type=user.credentials.login_type.value,
                            login=user.credentials.login, remember=True)
    await state.set_state(Login.password)
    await _login_with_password(callback.message, state, user, app, user.credentials.password)


@router.callback_query(Login.method, F.data.in_({"login:password", "login:esia"}))
async def ask_save_credentials(callback: CallbackQuery, state: FSMContext, app: AppContext) -> None:
    await callback.answer()
    login_type = LoginType.ESIA if callback.data == "login:esia" else LoginType.PASSWORD
    await state.update_data(login_type=login_type.value)
    if login_type.is_gosuslugi:
        # Пароль Госуслуг не храним: спрашивать не о чем.
        await state.update_data(remember=False)
        await state.set_state(Login.username)
        await _step(callback, state,
            "👤 Пришлите логин Госуслуг (телефон, СНИЛС или почту).\n\n"
            "Логин и пароль Госуслуг нужны только на этот вход и не сохраняются.\n\n"
            + _consent(app),
            reply_markup=_cancel_keyboard(), disable_web_page_preview=True)
        return
    await state.set_state(Login.remember)
    explanation = "С сохранёнными данными бот сможет войти снова, когда сессия истечёт."
    await _step(callback, state,
        "🔒 Сохранить логин и пароль для следующих входов?\n\n"
        + explanation + "\n\nЕсли не сохранять — текущая сессия останется для дневника "
        "и уведомлений, а после её истечения понадобится /login. "
        "Сохранённые данные доступны серверу бота.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Не сохранять логин и пароль", callback_data="login:remember:no")],
            [InlineKeyboardButton(text="Сохранить логин и пароль", callback_data="login:remember:yes")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="login:cancel")],
        ]))


@router.callback_query(Login.remember, F.data.in_({"login:remember:yes", "login:remember:no"}))
async def choose_save_credentials(callback: CallbackQuery, state: FSMContext, app: AppContext) -> None:
    await callback.answer()
    await state.update_data(remember=callback.data.endswith(":yes"))
    await state.set_state(Login.username)
    await _step(callback, state, "👤 Пришлите логин от «Сетевого города».\n\n" + _consent(app),
                reply_markup=_cancel_keyboard(), disable_web_page_preview=True)


@router.message(Login.username)
async def receive_username(message: Message, state: FSMContext) -> None:
    await state.update_data(login=(message.text or "").strip())
    await state.set_state(Login.password)
    await _step(
        message, state,
        "🔒 Теперь пришлите пароль.\n\n"
        "Сообщение с паролем я удалю сразу после получения.",
        reply_markup=_cancel_keyboard(),
    )


@router.message(Login.password)
async def receive_password(
    message: Message, state: FSMContext, user: User, app: AppContext
) -> None:
    if await _take_otp(message):
        return
    password = (message.text or "").strip()
    # Пароль не должен оставаться в истории чата.
    try:
        await message.delete()
    except Exception as exc:  # noqa: BLE001 — прав на удаление может не быть
        logger.debug("Не удалось удалить сообщение с паролем: %s", exc)

    await _login_with_password(message, state, user, app, password)


async def _login_with_password(message, state, user, app, password: str) -> None:
    async with app.login_attempt(user.telegram_id):
        await _password_attempt(message, state, user, app, password)


async def _password_attempt(message, state, user, app, password: str) -> None:
    data = await state.get_data()
    login_type = LoginType.parse(data.get("login_type"))
    attempt = secrets.token_hex(8)
    await state.update_data(password_attempt=attempt)
    await _drop_step(message, state)
    notice = await message.answer("⏳ Вхожу…")

    myschool = bool(data.get("myschool"))
    candidate = replace(
        user,
        school=myschool_school() if myschool else School(url=data.get("url", ""), name=data.get("school", "")),
        api_mode=ApiMode.MYSCHOOL if myschool else (
            ApiMode.MOBILE if user.api_mode is ApiMode.MYSCHOOL else user.api_mode),
        selected_student_id=None if myschool or user.api_mode is ApiMode.MYSCHOOL else user.selected_student_id,
        credentials=Credentials(
            login_type=login_type, login=data.get("login", ""), password=password,
            remember=bool(data.get("remember", False))
        ),
    )

    try:
        payload = await _perform_login(candidate, app, message, login_type)
    except NetSchoolError as exc:
        await notice.delete()
        hint = (
            "\n\nПроверьте логин и пароль или начните заново: /login"
            if exc.reason in (Reason.AUTH, Reason.CREDENTIALS, Reason.UNKNOWN)
            else "\n\nПопробуйте ещё раз чуть позже: /login"
        )
        await reply(message, f"❌ {formatting.esc(login_message(exc))}{hint}")
        await state.clear()
        return

    await notice.delete()
    if (await state.get_data()).get("password_attempt") != attempt:
        return
    await state.clear()
    if payload:
        await app.sessions.save(candidate.telegram_id, payload)
    await _finish_login(candidate, app, message)


async def _perform_login(
    user: User, app: AppContext, message: Message, login_type: LoginType
) -> str | None:
    """Выполнить вход и вернуть сессию для сохранения после проверки попытки."""
    callback = _make_otp_callback(message, user.telegram_id)
    if user.api_mode is ApiMode.MYSCHOOL:
        return await login_flow.myschool_login(app, user, callback)
    return await login_flow.password_login(app, user, callback)


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


async def _take_otp(message: Message) -> bool:
    """Отдать сообщение входу, который ждёт код подтверждения Госуслуг.

    Состояние FSM в этот момент занято шагом пароля, поэтому код приходит в
    обработчик пароля: без этой проверки он уходил бы туда как новый пароль.
    """
    sender = getattr(message, "from_user", None)
    future = _PENDING_OTP.get(sender.id if sender else 0)
    if future is None or future.done():
        return False
    try:
        await message.delete()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Не удалось удалить код подтверждения: %s", exc)
    future.set_result((message.text or "").strip())
    return True


@router.message(F.text.regexp(r"^\s*\d{4,8}\s*$"))
async def receive_otp(message: Message) -> None:
    """Код подтверждения Госуслуг, если состояние не занято шагом пароля."""
    await _take_otp(message)


@router.callback_query(Login.method, F.data == "login:qr")
async def login_by_qr(callback: CallbackQuery, state: FSMContext, user: User, app: AppContext) -> None:
    async with app.login_attempt(user.telegram_id):
        await _qr_attempt(callback, state, user, app)


async def _qr_attempt(callback: CallbackQuery, state: FSMContext, user: User, app: AppContext) -> None:
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
        credentials=Credentials(login_type=LoginType.ESIA_QR, remember=False),
    )
    ttl = app.settings.netschool.qr_login_ttl
    await _drop_step(callback.message, state)
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
                f"⏳ Код действует {ttl} секунд.\n\n"
                + _consent(app).replace("Отправляя логин и пароль", "Подтверждая вход")
            ),
            parse_mode="HTML",
        )

    try:
        payload = await login_flow.qr_login(app, url, school, show_qr, telegram_id=candidate.telegram_id)
    except asyncio.TimeoutError:
        await _delete_quietly(qr_message)
        await notice.delete()
        await _offer_qr_retry(callback, state, attempt, "⌛️ QR-код истёк. Получите новый кнопкой ниже.")
        return
    except AccountTaken as error:
        await _delete_quietly(qr_message)
        await notice.delete()
        await state.clear()
        await reply(callback, f"❌ {formatting.esc(error.user_message)}")
        return
    except NetSchoolError as error:
        await _delete_quietly(qr_message)
        await notice.delete()
        await _offer_qr_retry(callback, state, attempt, f"❌ {formatting.esc(error.user_message)}")
        return

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
    return await login_flow.settle_api_mode(app, user)


async def _finish_login(user: User, app: AppContext, message: Message) -> None:
    """Завершить вход: сохранить пользователя, найти детей, запустить проверку."""
    saved, api_note = await login_flow.finish_login(app, user, None)

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
