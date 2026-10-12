"""Правила чатов без базы и сети: кто в какие комнаты попадает, как его
подписывать и что писать нельзя.

Комнаты не хранятся отдельно — их id выводятся из профиля:

* ``class:<школа>:<класс>`` — свой класс («9б»);
* ``grade:<школа>:<параллель>`` — все классы параллели своей школы;
* ``school:<школа>`` — вся школа;
* ``global:<группа>`` — общий чат всех школ, но по возрастным группам:
  четвероклассник и одиннадцатиклассник в одну комнату не попадают.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# Возрастные группы общего чата: (первый класс, последний класс).
AGE_BANDS: tuple[tuple[int, int], ...] = ((1, 4), (5, 7), (8, 9), (10, 11))

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


def band_of(grade: int) -> str:
    for first, last in AGE_BANDS:
        if first <= grade <= last:
            return f"{first}-{last}"
    return f"{AGE_BANDS[-1][0]}-{AGE_BANDS[-1][1]}"


def first_name(full_name: str) -> str:
    """Только имя: «Соловьёв Владислав» → «Владислав». Фамилию не показываем."""
    parts = [part for part in re.split(r"\s+", full_name.strip()) if part]
    if len(parts) >= 2:
        return parts[1]
    return parts[0] if parts else "Ученик"


def normalize_class(class_name: str) -> str:
    return re.sub(r"\s+", "", class_name or "").lower()


@dataclass(frozen=True, slots=True)
class Room:
    id: str
    kind: str  # class | grade | school | global
    title: str
    subtitle: str = ""

    @property
    def is_global(self) -> bool:
        return self.kind == "global"


def rooms_for(school: str, class_name: str, grade: int, school_name: str = "") -> list[Room]:
    """Комнаты ученика — в порядке списка на экране."""
    first, last = map(int, band_of(grade).split("-"))
    cls = normalize_class(class_name)
    return [
        Room(f"class:{school}:{cls}", "class", f"{class_name.strip()} класс", school_name),
        Room(f"grade:{school}:{grade}", "grade", f"{grade}-е классы", school_name),
        Room(f"school:{school}", "school", "Вся школа", school_name),
        Room(f"global:{first}-{last}", "global", f"Общий чат · {first}–{last} классы", "Все школы"),
    ]


def describe(room_id: str) -> Room | None:
    """Комната по id — для администратора, у которого своих комнат нет."""
    kind, _, rest = room_id.partition(":")
    if kind == "global" and re.fullmatch(r"\d{1,2}-\d{1,2}", rest):
        first, last = rest.split("-")
        return Room(room_id, "global", f"Общий чат · {first}–{last} классы", "Все школы")
    if kind == "school" and rest:
        return Room(room_id, "school", "Вся школа")
    if kind == "grade" and ":" in rest:
        return Room(room_id, "grade", f"{rest.rsplit(':', 1)[1]}-е классы")
    if kind == "class" and ":" in rest:
        return Room(room_id, "class", f"{rest.rsplit(':', 1)[1]} класс")
    return None


def room_school(room_id: str) -> str:
    """Школа комнаты ("" у общего чата) — для модераторов школы."""
    kind, _, rest = room_id.partition(":")
    return "" if kind == "global" else rest.split(":", 1)[0]


# ─────────────────────────────── Фильтры ───────────────────────────────
# Мат разрешён. Не пропускаем наркотики и суицид, а в общем чате — ещё и
# контакты: ссылки, телефоны и @ники. Через них ребёнка уводят в личку,
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


def check_text(text: str, *, global_room: bool) -> str:
    """Почему сообщение не уйдёт ("" — всё в порядке)."""
    if not text.strip():
        return "Пустое сообщение"
    if len(text) > MAX_TEXT:
        return f"Сообщение длиннее {MAX_TEXT} символов"
    if _DRUGS.search(text):
        return "Сообщения про наркотики запрещены правилами чата"
    if _SUICIDE.search(text):
        return ("Такие сообщения запрещены правилами чата. Если тебе плохо — позвони на "
                "детский телефон доверия 8-800-2000-122, это бесплатно и анонимно")
    if global_room and (_LINK.search(text) or _PHONE.search(text) or _HANDLE.search(text)):
        return "В общем чате нельзя оставлять ссылки, телефоны и ники — это защита от мошенников"
    return ""
