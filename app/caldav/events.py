"""Стабильные UID и снимки: замена предмета сохраняет идентичность урока."""
from __future__ import annotations
import datetime as dt
import hashlib
from app.domain.calendar import PRODID, _event, _fold


def snapshot(days, user, previous, *, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    stamp = now.strftime('%Y%m%dT%H%M%SZ')
    student = user.selected_student_id if user.selected_student_id is not None else user.student_name
    scope = f'{user.school.url}|{student}'
    # После успешной смены ребёнка/школы старые события не переносятся.
    old = previous if previous.get('scope') == scope else {}
    events = dict(old.get('events', {}))
    empty = dict(old.get('empty', {}))
    if not days or not any(day.lessons for day in days):
        raise ValueError('Школа вернула пустое расписание; календарь сохранён')
    for day in days:
        date = day.day.isoformat()
        if not day.lessons:
            empty[date] = empty.get(date, 0) + 1
            # Подтверждаем пустой день второй независимой проверкой.
            if empty[date] < 2:
                continue
        else:
            empty.pop(date, None)
        current = {}
        invalid = False
        slots = set()
        for index, lesson in enumerate(day.lessons, 1):
            slot = lesson.number or index
            if slot in slots:
                raise ValueError('Повторяющиеся номера уроков; календарь сохранён')
            slots.add(slot)
            uid = hashlib.sha256(f'{scope}|{date}|{slot}'.encode()).hexdigest()[:32]
            lines = _event(day.day, lesson, user.tz, '20000101T000000Z')
            if lines is None or lesson.end <= lesson.start:
                invalid = True
                break
            lines[1] = f'UID:{uid}@netschoolbot'
            signature = '\r\n'.join(lines)
            prior = events.get(uid)
            if prior and prior['signature'] == signature:
                current[uid] = prior
                continue
            sequence = prior['sequence'] + 1 if prior else 0
            lines[2] = f'DTSTAMP:{stamp}'
            lines.insert(3, f'SEQUENCE:{sequence}')
            lines.insert(4, f'LAST-MODIFIED:{stamp}')
            text = ['BEGIN:VCALENDAR', 'VERSION:2.0', f'PRODID:{PRODID}', 'CALSCALE:GREGORIAN', *lines, 'END:VCALENDAR']
            current[uid] = {'day': date, 'signature': signature, 'sequence': sequence,
                            'ics': '\r\n'.join(part for line in text for part in _fold(line)) + '\r\n'}
        if invalid:
            # Неполные времена — сбой данных, а не отмена остальных уроков дня.
            continue
        events = {uid: event for uid, event in events.items() if event['day'] != date}
        events.update(current)
    cutoff = (user.today() - dt.timedelta(days=30)).isoformat()
    return {'scope': scope,
            'events': {uid: event for uid, event in events.items() if event['day'] >= cutoff},
            'empty': {day: count for day, count in empty.items() if day >= cutoff}}


def collection_ics(state):
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', f'PRODID:{PRODID}', 'CALSCALE:GREGORIAN']
    for uid, event in sorted(state['events'].items()):
        text = event['ics']
        lines.extend(text[text.index('BEGIN:VEVENT'):text.index('END:VEVENT') + len('END:VEVENT')].split('\r\n'))
    return '\r\n'.join([*lines, 'END:VCALENDAR', ''])
