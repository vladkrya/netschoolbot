"""Тесты проверки подписи Telegram Mini App.

Подпись — единственное, что отделяет «это точно тот пользователь» от
«кто угодно подставил чужой id в запрос», поэтому проверяется подробно.
"""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl, urlencode

import pytest

from app.web.telegram_auth import InitDataError, verify_init_data, verify_login_widget

TOKEN = "123456:AAHtestbottokenvaluehere"


def make_init_data(token=TOKEN, *, user=None, auth_date=None, extra=None, tamper=None):
    """Собрать подписанную initData так же, как это делает Telegram."""
    fields = {
        "auth_date": str(int(auth_date if auth_date is not None else time.time())),
        "query_id": "AAF",
        "user": json.dumps(
            user or {"id": 42, "first_name": "Иван", "last_name": "Петров", "username": "ivan"},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    fields.update(extra or {})
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if tamper:
        fields.update(tamper)
    return urlencode(fields)


class TestValidSignature:
    def test_returns_user(self):
        user = verify_init_data(make_init_data(), TOKEN)
        assert user.id == 42
        assert user.full_name == "Иван Петров"
        assert user.username == "ivan"

    def test_user_without_last_name(self):
        data = make_init_data(user={"id": 7, "first_name": "Анна"})
        assert verify_init_data(data, TOKEN).full_name == "Анна"

    def test_unicode_names_survive(self):
        data = make_init_data(user={"id": 7, "first_name": "Ёлка", "last_name": "Ёжикова"})
        assert verify_init_data(data, TOKEN).full_name == "Ёлка Ёжикова"

    def test_unknown_extra_fields_are_included_in_check(self):
        # Telegram со временем добавляет поля; они участвуют в подписи и не
        # должны ломать проверку.
        data = make_init_data(extra={"chat_type": "private", "start_param": "x"})
        assert verify_init_data(data, TOKEN).id == 42


class TestRejection:
    def test_wrong_token(self):
        data = make_init_data(token="999999:другойтокен")
        with pytest.raises(InitDataError, match="Подпись"):
            verify_init_data(data, TOKEN)

    def test_tampered_user_id(self):
        """Главный сценарий атаки: подставить чужой id."""
        data = make_init_data(
            tamper={"user": json.dumps({"id": 999999, "first_name": "Чужой"})}
        )
        with pytest.raises(InitDataError, match="Подпись"):
            verify_init_data(data, TOKEN)

    def test_missing_hash(self):
        with pytest.raises(InitDataError, match="подписи"):
            verify_init_data("user=%7B%22id%22%3A1%7D&auth_date=1", TOKEN)

    def test_empty(self):
        with pytest.raises(InitDataError, match="пуста"):
            verify_init_data("", TOKEN)

    def test_garbage(self):
        with pytest.raises(InitDataError):
            verify_init_data("не-строка-запроса", TOKEN)

    def test_no_bot_token(self):
        with pytest.raises(InitDataError, match="Токен бота"):
            verify_init_data(make_init_data(), "")

    def test_expired(self):
        old = time.time() - 48 * 3600
        with pytest.raises(InitDataError, match="устарела"):
            verify_init_data(make_init_data(auth_date=old), TOKEN)

    def test_fresh_within_window(self):
        recent = time.time() - 3600
        assert verify_init_data(make_init_data(auth_date=recent), TOKEN).id == 42

    def test_max_age_zero_disables_check(self):
        old = time.time() - 10 * 24 * 3600
        assert verify_init_data(make_init_data(auth_date=old), TOKEN, max_age=0).id == 42

    def test_missing_user(self):
        fields = {"auth_date": str(int(time.time()))}
        check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
        secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
        fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        with pytest.raises(InitDataError, match="нет пользователя"):
            verify_init_data(urlencode(fields), TOKEN)

    def test_user_without_id(self):
        # Подпись верна, но опознать пользователя не по чему.
        data = make_init_data(user={"first_name": "Безымянный"})
        with pytest.raises(InitDataError, match="прочитать пользователя"):
            verify_init_data(data, TOKEN)

    def test_any_corruption_breaks_signature_first(self):
        # Изменение любого подписанного поля обязано отвергаться подписью,
        # не доходя до разбора содержимого.
        data = make_init_data()
        with pytest.raises(InitDataError, match="Подпись"):
            verify_init_data(data.replace("%D0%98%D0%B2%D0%B0%D0%BD", "%D0%9E%D0%BB%D0%B5%D0%B3"), TOKEN)


def make_widget_data(token=TOKEN, *, auth_date=None, **fields):
    """Ответ Telegram Login Widget: плоские поля и hash по SHA256 от токена."""
    data = {"id": "42", "first_name": "Иван", "username": "ivan",
            "auth_date": str(int(auth_date if auth_date is not None else time.time())), **fields}
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    data["hash"] = hmac.new(hashlib.sha256(token.encode()).digest(), check.encode(), hashlib.sha256).hexdigest()
    return data


class TestLoginWidget:
    def test_valid_data_gives_user(self):
        user = verify_login_widget(make_widget_data(photo_url="https://t.me/i/x.jpg"), TOKEN)
        assert (user.id, user.first_name, user.username) == (42, "Иван", "ivan")

    def test_number_fields_are_signed_as_text(self):
        # Из JSON id и auth_date приходят числами — подпись от этого не меняется.
        data = make_widget_data()
        data["id"], data["auth_date"] = int(data["id"]), int(data["auth_date"])
        assert verify_login_widget(data, TOKEN).id == 42

    @pytest.mark.parametrize("change", [{"id": "43"}, {"hash": "0" * 64}, {"hash": ""}])
    def test_tampered_data_rejected(self, change):
        with pytest.raises(InitDataError):
            verify_login_widget({**make_widget_data(), **change}, TOKEN)

    def test_other_bot_rejected(self):
        with pytest.raises(InitDataError):
            verify_login_widget(make_widget_data("999:other"), TOKEN)

    def test_mini_app_signature_is_not_accepted(self):
        # Ключи у двух схем разные: initData не годится вместо виджета.
        data = dict(parse_qsl(make_init_data()))
        with pytest.raises(InitDataError):
            verify_login_widget(data, TOKEN)

    def test_old_data_rejected(self):
        with pytest.raises(InitDataError):
            verify_login_widget(make_widget_data(auth_date=time.time() - 3600), TOKEN, max_age=600)
