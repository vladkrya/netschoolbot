"""Тесты конфигурации.

Отдельное внимание — тому, что приложение стартует на минимальном наборе
переменных. Требование настройки, которая ни на что не влияет, однажды уже
не дало службе подняться.
"""

import pytest

from app.settings import SettingsError, load_settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for key in list(__import__("os").environ):
        if key.startswith(("NETSCHOOL_", "TG_", "CHECK_INTERVAL", "DEBUG_MODE", "VOLGOGRAD_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NETSCHOOL_DATA_DIR", str(tmp_path))


class TestMinimalConfig:
    def test_bot_token_alone_is_enough(self, monkeypatch):
        # Ровно тот случай, который однажды не дал службе подняться.
        monkeypatch.setenv("NETSCHOOL_BOT_TOKEN", "123:abc")
        settings = load_settings()
        assert settings.telegram.bot_token == "123:abc"
        assert settings.web.enabled is True

    def test_missing_bot_token_is_reported(self, monkeypatch):
        with pytest.raises(SettingsError, match="NETSCHOOL_BOT_TOKEN"):
            load_settings()

    def test_bot_token_optional_for_import(self):
        # Перенос данных не требует токена: он не ходит в Telegram.
        assert load_settings(require_bot_token=False).telegram.bot_token == ""


class TestToleranceToExistingEnv:
    """Служба живёт с рукописным .env, который переживает обновления.

    Странное значение должно подставлять значение по умолчанию и писать в
    лог, а не мешать боту запуститься: отказ стартовать означает не
    «безопасно», а «бот лежит, пока кто-нибудь не зайдёт по SSH».
    """

    @pytest.fixture(autouse=True)
    def token(self, monkeypatch):
        monkeypatch.setenv("NETSCHOOL_BOT_TOKEN", "123:abc")

    def test_too_small_interval_is_clamped(self, monkeypatch, caplog):
        monkeypatch.setenv("CHECK_INTERVAL", "5")
        settings = load_settings()
        assert settings.netschool.default_check_interval == 180
        assert "CHECK_INTERVAL" in caplog.text

    def test_too_large_interval_is_clamped(self, monkeypatch):
        monkeypatch.setenv("CHECK_INTERVAL", "99999")
        assert load_settings().netschool.default_check_interval == 10800

    def test_non_numeric_interval_falls_back(self, monkeypatch, caplog):
        monkeypatch.setenv("CHECK_INTERVAL", "быстро")
        assert load_settings().netschool.default_check_interval == 300
        assert "не число" in caplog.text

    def test_bad_boolean_falls_back_to_default(self, monkeypatch, caplog):
        monkeypatch.setenv("NETSCHOOL_WEB_ENABLED", "может быть")
        assert load_settings().web.enabled is True
        assert "не да/нет" in caplog.text

    def test_malformed_proxy_entry_is_skipped(self, monkeypatch, caplog):
        monkeypatch.setenv("NETSCHOOL_PROXY_HOSTS", "=socks5://1:1080,b.ru=socks5://2:1080")
        settings = load_settings()
        assert settings.netschool.proxy_for("https://b.ru") == "socks5://2:1080"
        assert "без имени хоста" in caplog.text

    def test_inline_comment_in_value(self, monkeypatch):
        # Рукописные .env часто содержат "300  # комментарий".
        monkeypatch.setenv("CHECK_INTERVAL", "600  # каждые 10 минут")
        assert load_settings().netschool.default_check_interval == 600

    def test_only_missing_token_is_fatal(self, monkeypatch):
        # Единственное, без чего работать действительно нечем.
        monkeypatch.delenv("NETSCHOOL_BOT_TOKEN")
        monkeypatch.setenv("CHECK_INTERVAL", "чушь")
        monkeypatch.setenv("NETSCHOOL_WEB_ENABLED", "чушь")
        with pytest.raises(SettingsError, match="NETSCHOOL_BOT_TOKEN"):
            load_settings()


class TestProxyHosts:
    def test_default_keeps_volgograd(self, monkeypatch):
        monkeypatch.setenv("NETSCHOOL_BOT_TOKEN", "123:abc")
        settings = load_settings()
        assert settings.netschool.proxy_for("https://sgo.volganet.ru/x") is not None
        assert settings.netschool.proxy_for("https://sgo.example.ru") is None

    def test_explicit_list(self, monkeypatch):
        monkeypatch.setenv("NETSCHOOL_BOT_TOKEN", "123:abc")
        monkeypatch.setenv("NETSCHOOL_PROXY_HOSTS", "a.ru=socks5://1:1080,b.ru=socks5://2:1080")
        settings = load_settings()
        assert settings.netschool.proxy_for("https://a.ru/x") == "socks5://1:1080"

    def test_wildcard_proxy_with_host_override(self, monkeypatch):
        monkeypatch.setenv("NETSCHOOL_BOT_TOKEN", "123:abc")
        monkeypatch.setenv(
            "NETSCHOOL_PROXY_HOSTS",
            "*=socks5://127.0.0.1:1082,sgo.volganet.ru=socks5://127.0.0.1:1080",
        )
        settings = load_settings()
        assert settings.netschool.proxy_for("https://asurso.ru") == "socks5://127.0.0.1:1082"
        assert settings.netschool.proxy_for("https://sgo.volganet.ru") == "socks5://127.0.0.1:1080"

    def test_empty_host_is_skipped(self, monkeypatch):
        monkeypatch.setenv("NETSCHOOL_BOT_TOKEN", "123:abc")
        monkeypatch.setenv("NETSCHOOL_PROXY_HOSTS", "=socks5://1:1080")
        assert load_settings().netschool.proxy_hosts == {}


class TestUrls:
    def test_miniapp_url_is_composed(self, monkeypatch):
        monkeypatch.setenv("NETSCHOOL_BOT_TOKEN", "123:abc")
        monkeypatch.setenv("NETSCHOOL_PUBLIC_URL", "https://example.ru/")
        monkeypatch.setenv("NETSCHOOL_MINIAPP_PATH", "mini/x/")
        settings = load_settings()
        # Лишние слэши с обеих сторон не должны давать "//" в ссылке.
        assert settings.web.miniapp_url == "https://example.ru/mini/x"
