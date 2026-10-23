"""Тесты классификации ошибок.

Раньше пять разных предикатов в трёх модулях расходились в трактовке одной
и той же ошибки: бот считал её временной, PWA — фатальной.
"""

import pytest

from app.netschool.errors import NetSchoolError, Reason, classify, wrap


class TestClassify:
    @pytest.mark.parametrize(
        "message,expected",
        [
            ("401 Unauthorized", Reason.AUTH),
            ("Session expired", Reason.AUTH),
            ("Пользователь не авторизован", Reason.AUTH),
            ("403 Forbidden", Reason.AUTH),
            ("Read timeout", Reason.SERVER_UNAVAILABLE),
            ("503 Service Unavailable", Reason.SERVER_UNAVAILABLE),
            # Школьный сервер падал с 500, и приложение писало «Не удалось
            # получить данные» вместо повтора и сохранённых данных.
            ("Server error '500 Internal Server Error' for url '/webapi/context'", Reason.SERVER_UNAVAILABLE),
            ("/users: сервер ответил 500", Reason.SERVER_UNAVAILABLE),
            ("All connection attempts failed", Reason.SERVER_UNAVAILABLE),
            ("Server disconnected", Reason.SERVER_UNAVAILABLE),
            ("429 Too Many Requests", Reason.RATE_LIMIT),
            ("ESIA login failed", Reason.ESIA),
            ("Госуслуги недоступны", Reason.ESIA),
            ("Что-то пошло не так", Reason.UNKNOWN),
        ],
    )
    def test_by_message(self, message, expected):
        assert classify(Exception(message)) is expected

    def test_esia_timeout_is_esia_not_server(self):
        # Текст содержит и "esia", и "timeout" — реагировать надо как на ЕСИА.
        assert classify(Exception("ESIA request timeout")) is Reason.ESIA

    def test_rate_limit_wins_over_auth(self):
        # 429 с упоминанием авторизации — это всё равно «подождать».
        assert classify(Exception("429 too many requests, unauthorized")) is Reason.RATE_LIMIT

    def test_builtin_connection_errors(self):
        assert classify(ConnectionRefusedError()) is Reason.SERVER_UNAVAILABLE
        assert classify(TimeoutError()) is Reason.SERVER_UNAVAILABLE


class TestReasonBehaviour:
    def test_only_transient_reasons_retry(self):
        retryable = {r for r in Reason if r.is_retryable}
        assert retryable == {Reason.SERVER_UNAVAILABLE, Reason.RATE_LIMIT}

    def test_auth_requires_relogin(self):
        assert Reason.AUTH.needs_relogin is True
        assert Reason.SERVER_UNAVAILABLE.needs_relogin is False


class TestWrap:
    def test_message_is_human_readable(self):
        error = wrap(Exception("401 Unauthorized"))
        assert error.reason is Reason.AUTH
        # Ни кода ошибки, ни traceback в тексте для пользователя.
        assert "401" not in error.user_message
        assert "/login" in error.user_message

    def test_original_exception_is_kept(self):
        original = Exception("503")
        assert wrap(original).cause is original

    def test_already_wrapped_passes_through(self):
        error = NetSchoolError(Reason.MFA, "нужен код")
        assert wrap(error) is error


@pytest.mark.parametrize('text', ['Технические работы', 'Maintenance', 'Expecting value: line 1 column 1 (char 0)'])
def test_maintenance_is_not_expired_session(text):
    from netschoolpy.exceptions import SessionExpired
    assert classify(Exception(text)) is Reason.SERVER_UNAVAILABLE
    assert classify(SessionExpired(f'Сессия истекла или невалидна: {text}')) is Reason.SERVER_UNAVAILABLE


class TestLoginErrorClasses:
    """С netschoolpy 9.6.0 причина неудачного входа видна по классу."""

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("InvalidCredentials", Reason.CREDENTIALS),
            ("ESIAInvalidCredentials", Reason.CREDENTIALS),
            ("SchoolNotFound", Reason.CREDENTIALS),
            ("AccountLocked", Reason.RATE_LIMIT),
            ("ESIAAccountLocked", Reason.RATE_LIMIT),
            ("RefreshTokenRejected", Reason.AUTH),
            ("LoginRejected", Reason.UNKNOWN),
            ("ServerUnavailable", Reason.SERVER_UNAVAILABLE),
            ("ESIAError", Reason.ESIA),
        ],
    )
    def test_class_gives_reason(self, name, expected):
        from netschoolpy import exceptions as ns

        assert classify(getattr(ns, name)("текст сервера")) is expected

    def test_wrong_password_needs_relogin_but_is_not_retried(self):
        from netschoolpy import exceptions as ns

        error = wrap(ns.InvalidCredentials("Неправильный пароль или логин"))
        assert error.reason.needs_relogin and not error.reason.is_retryable
        assert "не принял логин или пароль" in error.user_message

    def test_login_message_for_typed_password(self):
        from netschoolpy import exceptions as ns

        from app.netschool.errors import login_message

        assert login_message(wrap(ns.InvalidCredentials("x"))) == "Неверный логин или пароль от «Сетевого города»."
        assert login_message(wrap(ns.ESIAInvalidCredentials("x"))) == "Неверный логин или пароль от Госуслуг."
        assert "не нашёл школу" in login_message(wrap(ns.SchoolNotFound("x")))
        assert "/login" not in login_message(wrap(ns.SchoolNotFound("x")))

    def test_rejected_login_keeps_server_text(self):
        from netschoolpy import exceptions as ns

        assert wrap(ns.LoginRejected("Нет доступа")).user_message.endswith("Нет доступа")



class TestLoginRefusalWithoutPasswordWords:
    """409 без слов о пароле — сбой «Сетевого города», а не «смените пароль»."""

    def setup_method(self):
        from app.netschool.patches import login

        login.apply()

    @pytest.mark.parametrize("message", [None, "", "Произошла ошибка", "Ошибка входа в систему", "Object reference not set to an instance of an object"])
    def test_unexplained_refusal_is_server_failure(self, message):
        from netschoolpy import client

        error = wrap(client.conflict_error(message))
        assert error.reason is Reason.SERVER_UNAVAILABLE and not error.reason.needs_relogin

    @pytest.mark.parametrize("message", ["Неправильный пароль или логин", "Неверное имя пользователя или пароль"])
    def test_wrong_password_words_still_mean_credentials(self, message):
        from netschoolpy import client

        assert wrap(client.conflict_error(message)).reason is Reason.CREDENTIALS

    def test_lock_and_mobile_login_use_the_same_rule(self):
        from netschoolpy import client, mobile

        assert mobile.conflict_error is client.conflict_error
        assert wrap(client.conflict_error("Вы совершили 3 неудачные попытки входа")).reason is Reason.RATE_LIMIT
