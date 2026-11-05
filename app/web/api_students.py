"""Выбор ребёнка только на время запроса интеграции, без записи в аккаунт."""
from dataclasses import replace
import hashlib

from .api.base import _cache_owner

# Общие данные аккаунта (почта, заметки, история наблюдений) сюда не входят.
STUDENT_RESOURCES = frozenset({
    'profile', 'diary', 'schedule', 'marks', 'marks/period', 'marks/subject',
    'marks/lessons', 'subject', 'subject/class-history', 'class-averages',
    'homework', 'announcements', 'bells', 'events', 'student-class', 'countdown',
    'references/types', 'homework/{assignment_id}/comment', 'homework/{assignment_id}/answer',
})


class StudentCache:
    """Отдельное пространство всех кэшей, включая учителей и средние класса."""
    def __init__(self, cache, user):
        self._cache = cache
        fingerprint = hashlib.sha256(repr(_cache_owner(user)).encode()).hexdigest()[:20]
        self.prefix = f'api-student:{user.selected_student_id}:{fingerprint}:'

    async def get(self, owner, section):
        return await self._cache.get(owner, self.prefix + section)

    async def put(self, owner, section, payload):
        await self._cache.put(owner, self.prefix + section, payload)

    async def patch(self, owner, section, payload):
        await self._cache.patch(owner, self.prefix + section, payload)


class StudentUsers:
    """Проверка актуального владельца кэша с учётом выбора этого запроса."""
    def __init__(self, users, owner, student_id):
        self._users, self._owner, self._student_id = users, owner, student_id

    async def get(self, owner):
        user = await self._users.get(owner)
        if user is None or owner != self._owner:
            return user
        student = next((s for s in user.available_students if s.id == self._student_id), None)
        if student is None:
            # Доступ к ребёнку отозван: фоновый ответ больше не кэшируется.
            return None
        return replace(user, selected_student_id=student.id, student_name=student.name)


class StudentContext:
    def __init__(self, app, user):
        self._app = app
        self.cache = StudentCache(app.cache, user)
        self.users = StudentUsers(app.users, user.telegram_id, user.selected_student_id)

    def __getattr__(self, name):
        return getattr(self._app, name)
