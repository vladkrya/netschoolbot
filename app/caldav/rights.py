"""Телефон читает только свой календарь; внутренний издатель может писать."""
from radicale.rights import BaseRights

PUBLISHER = '__netschool_publisher__'


class Rights(BaseRights):
    def authorization(self, user: str, path: str) -> str:
        path = path.strip('/')
        if user == PUBLISHER:
            return 'RW' if path.count('/') < 1 else 'rw'
        if not user:
            return ''
        if not path:
            return 'R'
        if path == user:
            return 'R'
        if path == f'{user}/schedule':
            return 'r'
        return ''
