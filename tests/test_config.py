import pytest

from worker.config import ConfigurationError, Settings


def test_settings_require_secret(monkeypatch):
    monkeypatch.setenv("OSS_BASE_URL", "http://oss.example")
    monkeypatch.delenv("OSS_UPLOAD_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="OSS_UPLOAD_API_KEY"):
        Settings.from_env()


def test_settings_load_without_exposing_secret(monkeypatch):
    monkeypatch.setenv("OSS_BASE_URL", "http://oss.example/")
    monkeypatch.setenv("OSS_UPLOAD_API_KEY", "super-secret")
    settings = Settings.from_env()
    assert settings.oss_base_url == "http://oss.example"
    assert settings.oss_upload_api_key == "super-secret"

