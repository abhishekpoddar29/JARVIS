import pytest
from fastapi import HTTPException

from config import get_settings
from security.app_auth import _token_matches, auth_is_enabled


@pytest.fixture(autouse=True)
def reset_settings_cache(monkeypatch):
    monkeypatch.delenv("JARVIS_APP_TOKEN", raising=False)
    monkeypatch.delenv("JARVIS_AUTH_REQUIRED", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_auth_is_disabled_by_default():
    assert auth_is_enabled() is False
    assert _token_matches(None) is True


def test_auth_is_enabled_when_token_exists(monkeypatch):
    monkeypatch.setenv("JARVIS_APP_TOKEN", "secret-token")
    get_settings.cache_clear()

    assert auth_is_enabled() is True
    assert _token_matches("secret-token") is True
    assert _token_matches("wrong-token") is False


def test_auth_required_without_token_reports_configuration_error(monkeypatch):
    monkeypatch.setenv("JARVIS_AUTH_REQUIRED", "true")
    get_settings.cache_clear()

    with pytest.raises(HTTPException) as exc_info:
        _token_matches("anything")

    assert exc_info.value.status_code == 503
