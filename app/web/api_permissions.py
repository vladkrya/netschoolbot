"""Неизменяемые права read-only токена, задаваемые при его создании."""
import json

PERMISSIONS = {
    'profile': 'Профиль и список учеников',
    'diary': 'Дневник и расписание',
    'marks': 'Оценки и аналитика',
    'homework': 'Задания, ответы и отметки выполнения',
    'mail': 'Школьная почта и адресная книга',
    'attachments': 'Файлы заданий, писем и объявлений',
    'school': 'Объявления, звонки, события и каникулы',
    'notes': 'Личные задачи',
    'history': 'История изменений оценок',
    'status': 'Статус школьных серверов',
}


def permissions_for(row):
    if not row['permissions']:
        return {'status'} if row['scope'] == 'status' else set(PERMISSIONS)
    try:
        values = json.loads(row['permissions'])
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            return set()
        return set(values) & PERMISSIONS.keys()
    except (ValueError, TypeError):
        return set()


def required_permissions(resource):
    if resource == 'status' or resource.startswith('status/'):
        return {'status'}
    if resource in ('profile', 'student-class'):
        return {'profile'}
    if resource in ('diary', 'schedule'):
        return {'diary'}
    if resource in ('marks', 'subject', 'class-averages') or resource.startswith(('marks/', 'subject/')):
        return {'marks'}
    if resource == 'homework' or resource.startswith('homework/'):
        return {'homework'}
    if resource == 'mail' or resource.startswith('mail/'):
        return {'mail'}
    if resource.startswith('attachment/'):
        return {'attachments'}
    if resource in ('announcements', 'bells', 'events', 'countdown', 'references/types'):
        return {'school'}
    if resource in ('notes', 'history'):
        return {resource}
    if resource == 'search':
        return {'homework', 'mail'}
    if resource == 'children':
        return {'profile', 'diary', 'marks', 'homework'}
    return set()
