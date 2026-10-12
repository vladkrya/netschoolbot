"""Правила чатов без базы и сети: кто в какие комнаты попадает, как его
подписывать и что писать нельзя.

Комнаты не хранятся отдельно — их id выводятся из профиля:

* ``grade:<школа>:<параллель>`` — своя параллель своей школы (все 9-е);
  отдельного чата класса нет — у каждой параллели каждой школы свой чат;
* ``school:<школа>`` — вся школа;
* ``global:6-11`` — общий чат всех школ, с 6 по 11 класс. Младшим он не
  виден: им общий чат с подростками ни к чему.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# Общий чат: (первый класс, последний класс). Один на всех с 6 класса.
AGE_BANDS: tuple[tuple[int, int], ...] = ((6, 11),)
GLOBAL_TITLE = "Общий чат"

MAX_TEXT = 2000
ADMIN_NAME = "admin"


def school_key(url: str, name: str) -> str:
    """Короткий устойчивый id школы: адрес сервера + название."""
    return hashlib.sha1(f"{url.rstrip('/').lower()}|{name.strip().lower()}".encode()).hexdigest()[:12]


def grade_of(class_name: str) -> int | None:
    """Номер параллели из названия класса: «9б» → 9, «10-А» → 10."""
    match = re.match(r"\s*(\d{1,2})", class_name or "")
    if not match:
        return None
    grade = int(match.group(1))
    return grade if 1 <= grade <= 11 else None


def band_of(grade: int) -> str | None:
    """Комната общего чата для параллели; None — общий чат этому классу не положен."""
    for first, last in AGE_BANDS:
        if first <= grade <= last:
            return f"{first}-{last}"
    return None


# ─────────────────────────────── Ники ───────────────────────────────
# Настоящих имён в чатах нет — только ники. Ник уникален без учёта регистра,
# «ё/е» и похожих латинских и русских букв: «Аdmin» с русской «А» не пройдёт
# как отдельный ник и не выдаст себя за администратора.

NICK_RE = re.compile(r"^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9_]{2,19}$")
NICK_HINT = "3–20 символов: буквы одного алфавита (русского или английского), цифры и _, начинается с буквы"
MENTION = re.compile(r"(?<![\w@])@([A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9_]{1,19})")
_LOOKALIKE = str.maketrans("аеорсухкмтвнё01l", "aeopcyxkmtbheoii")


def nick_key(nick: str) -> str:
    """Ключ уникальности ника."""
    return nick.strip().lower().translate(_LOOKALIKE)


_RESERVED = tuple(nick_key(word) for word in (
    "admin", "админ", "moder", "модер", "support", "поддержк", "telegram", "сетевой"))


# Проверяем также смешанные алфавиты и разделители: Aдmиn, a_d_m_i_n, m0der.
_ROLE = re.compile(r"[aа][dд][mм][iи1l][nн]|[mм][oо0][dд][eе][rрp]|[sс][uу][pрп][pрп][oо0][rр][tт]", re.I)
_SHORT_MOD = re.compile(r"^[mм][oо0][dд](?:\d+)?$", re.I)


def check_nick(nick: str) -> str:
    """Почему ник не подходит ("" — подходит)."""
    if not NICK_RE.match(nick):
        return f"Ник: {NICK_HINT}"
    if re.search(r"[A-Za-z]", nick) and re.search(r"[А-Яа-яЁё]", nick):
        return "Все буквы ника должны быть либо русскими, либо английскими"
    key = nick_key(nick)
    compact = nick.replace("_", "")
    if any(word in key.replace("_", "") for word in _RESERVED) or _ROLE.search(compact) or _SHORT_MOD.fullmatch(compact):
        return "Ник не должен выдавать участника за администратора или модератора"
    if _DRUGS.search(nick) or _SUICIDE.search(nick):
        return "Такой ник нельзя"
    return ""


def mentions(text: str) -> set[str]:
    """Ключи упомянутых ников: «@Петя_9 привет» → {"пeтя_9"}."""
    return {nick_key(name) for name in MENTION.findall(text)}


def strip_mentions(text: str, known: set[str]) -> str:
    """Текст без упоминаний известных ников — чтобы фильтр контактов в общем
    чате не принял @ник участника за ник в Telegram."""
    return MENTION.sub(lambda m: "" if nick_key(m.group(1)) in known else m.group(0), text)


# Уведомления комнаты по умолчанию: в своей параллели и школе — обо всём,
# в общем чате всех школ — только упоминания, иначе это поток без конца.
NOTIFY_MODES = ("all", "mentions", "off")


def default_notify(kind: str) -> str:
    return "mentions" if kind == "global" else "all"


def first_name(full_name: str) -> str:
    """Только имя: «Соловьёв Владислав» → «Владислав». Фамилию не показываем."""
    parts = [part for part in re.split(r"\s+", full_name.strip()) if part]
    if len(parts) >= 2:
        return parts[1]
    return parts[0] if parts else "Ученик"


def short_school(name: str) -> str:
    """Короткое имя школы для названий чатов.

    «МБУ «Школа № 93»» → «школа №93», «МБОУ "Лицей №6" г. Рубцовска» →
    «лицей №6», «МАОУ "Сотниковская СОШ"» → «Сотниковская СОШ».
    """
    text = (name or "").strip()
    number = re.search(r"№\s*(\d+)", text) or re.search(r"\b(?:СОШ|ООШ|школа)\s+(\d+)\b", text, re.IGNORECASE)
    if number:
        kind = ("лицей" if re.search(r"лице", text, re.IGNORECASE)
                else "гимназия" if re.search(r"гимнази", text, re.IGNORECASE) else "школа")
        return f"{kind} №{number.group(1)}"
    quoted = re.search(r"[«\"“]([^»\"”]+)[»\"”]", text)
    return (quoted.group(1) if quoted else text).strip()[:40]


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def grade_title(grade: int | str, school_name: str) -> str:
    short = short_school(school_name)
    return f"{grade}-е классы · {short}" if short else f"{grade}-е классы"


def school_title(school_name: str) -> str:
    short = short_school(school_name)
    return _capital(short) if short else "Вся школа"


@dataclass(frozen=True, slots=True)
class Room:
    id: str
    kind: str  # grade | school | global
    title: str
    subtitle: str = ""

    @property
    def is_global(self) -> bool:
        return self.kind == "global"


def rooms_for(school: str, class_name: str, grade: int, school_name: str = "") -> list[Room]:
    """Комнаты ученика — в порядке списка на экране."""
    rooms = [
        Room(f"grade:{school}:{grade}", "grade", grade_title(grade, school_name), school_name),
        Room(f"school:{school}", "school", school_title(school_name), "Вся школа"),
    ]
    if band := band_of(grade):
        rooms.append(_global_room(band))
    return rooms


def _global_room(band: str) -> Room:
    first, last = band.split("-")
    return Room(f"global:{band}", "global", GLOBAL_TITLE, f"Все школы · {first}–{last} классы")


def describe(room_id: str, school_name: str = "") -> Room | None:
    """Комната по id — для администратора и модераторов, у которых это не
    свои комнаты. Имя школы берётся из профилей её учеников."""
    kind, _, rest = room_id.partition(":")
    if kind == "global" and rest in {f"{first}-{last}" for first, last in AGE_BANDS}:
        return _global_room(rest)
    if kind == "school" and rest:
        return Room(room_id, "school", school_title(school_name), "Вся школа")
    if kind == "grade" and ":" in rest:
        return Room(room_id, "grade", grade_title(rest.rsplit(":", 1)[1], school_name), school_name)
    return None


def support_owner(room_id: str) -> int | None:
    kind, _, value = room_id.partition(":")
    if kind == "support" and value.isdigit() and int(value) > 0 and value == str(int(value)):
        return int(value)
    return None


def room_school(room_id: str) -> str:
    """Школа комнаты ("" у общего чата) — для модераторов школы."""
    kind, _, rest = room_id.partition(":")
    return "" if kind in {"global", "support"} else rest.split(":", 1)[0]


# ─────────────────────────────── Фильтры ───────────────────────────────
# Мат разрешён. Не пропускаем наркотики и суицид, а в общем чате — ещё и
# контакты: ссылки, телефоны и @ники из соцсетей. Через них ребёнка уводят в личку,
# где никакой модерации нет.

_DRUGS = re.compile(
    r"\b(закладк\w*|кладмен\w*|меф|мефа|мефчик\w*|мефедрон\w*|амфетамин\w*|кокаин\w*|героин\w*|"
    r"гашиш\w*|марихуан\w*|спайс\w*|соли?\s+для\s+ванн|лсд|экстази|метадон\w*|"
    r"травк[уаи]\s+(купить|продам|продаю|достать))\b",
    re.IGNORECASE,
)
_SUICIDE = re.compile(
    r"(покончить\s+с\s+собой|убить\s+себя|убей\s+себя|самоубийств\w*|суицид\w*|"
    r"выпилиться|выпились|роскомнадзорнуться|вскрыть\s+вены|вскрой\s+вены|"
    r"синий\s+кит|разбуди\s+меня\s+в\s+4[:.]?20|тихий\s+дом|f57)",
    re.IGNORECASE,
)
_LINK = re.compile(
    r"(https?://|www\.|\b[\w-]+\.(ru|рф|com|net|org|me|io|su|ly|gg|xyz|online|site|app)\b|t\.me|vk\.com)",
    re.IGNORECASE,
)
_HANDLE = re.compile(r"(^|\s)@[A-Za-z][A-Za-z0-9_]{3,}")
_PHONE = re.compile(r"(\+?\d[\s\-()]*){10,}")


def check_text(text: str, *, global_room: bool, known: set[str] | frozenset[str] = frozenset()) -> str:
    """Почему сообщение не уйдёт ("" — всё в порядке). known — ключи ников
    участников: упоминать их в общем чате можно."""
    if not text.strip():
        return "Пустое сообщение"
    if len(text) > MAX_TEXT:
        return f"Сообщение длиннее {MAX_TEXT} символов"
    if _DRUGS.search(text):
        return "Сообщения про наркотики запрещены правилами чата"
    if _SUICIDE.search(text):
        return ("Такие сообщения запрещены правилами чата. Если тебе плохо — позвони на "
                "детский телефон доверия 8-800-2000-122, это бесплатно и анонимно")
    bare = strip_mentions(text, set(known))
    if global_room and (_LINK.search(bare) or _PHONE.search(bare) or _HANDLE.search(bare)):
        return "В общем чате нельзя оставлять ссылки, телефоны и ники — это защита от мошенников"
    return ""
