"""Комментарий учителя к заданию из мобильного API.

Мобильный `/assignments` отдаёт поле ``description`` («комментарий к
заданию»), но netschoolpy его выбрасывает. Здесь задание из мобильного
дневника получает его отдельным полем — без лишних запросов: так главная
сразу знает, у какого задания есть комментарий.

Веб-дневник этого поля не содержит вовсе; там комментарий по-прежнему
подгружается из карточки задания при открытии экрана.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("netschoolbot.netschool")

_applied = False


def apply() -> None:
    """Применить правку. Безопасно вызывать несколько раз."""
    global _applied
    if _applied:
        return
    try:
        from netschoolpy import mobile
        from netschoolpy.models import Assignment
    except ImportError as exc:
        logger.warning("Не удалось пропатчить задания netschoolpy: %s", exc)
        return
    original = getattr(mobile, "_assignment_from_mobile", None)
    if original is None:
        logger.warning("В netschoolpy нет _assignment_from_mobile — комментарии к заданиям не придут")
        return

    class AssignmentWithDescription(Assignment):
        description: str = ""

    def patched(data: dict, attachments):
        assignment = original(data, attachments)
        if "description" in type(assignment).model_fields:
            return assignment  # библиотека уже научилась сама
        # Всегда с полем, даже пустым: "" — «комментария нет», а задание без
        # поля значит «неизвестно» (веб-API).
        return AssignmentWithDescription(**dict(assignment), description=str(data.get("description") or ""))

    mobile._assignment_from_mobile = patched
    _applied = True
