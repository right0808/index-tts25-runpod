from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigurationError(RuntimeError):
    pass


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigurationError(f"Missing required environment variable: {name}")
    return value


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True)
class Settings:
    oss_base_url: str
    oss_upload_api_key: str
    oss_expires_seconds: int = 3600
    oss_http_timeout: int = 30
    model_id: str = "IndexTeam/IndexTTS-2.5"
    vllm_base_url: str = "http://127.0.0.1:8092"
    vllm_request_timeout: int = 1800
    max_text_chars: int = 3000
    max_reference_audio_bytes: int = 25 * 1024 * 1024
    max_output_audio_bytes: int = 100 * 1024 * 1024

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            oss_base_url=_required("OSS_BASE_URL").rstrip("/"),
            oss_upload_api_key=_required("OSS_UPLOAD_API_KEY"),
            oss_expires_seconds=_positive_int("OSS_EXPIRES_SECONDS", 3600),
            oss_http_timeout=_positive_int("OSS_HTTP_TIMEOUT", 30),
            model_id=os.getenv("MODEL_ID", "IndexTeam/IndexTTS-2.5").strip(),
            vllm_base_url=os.getenv("VLLM_BASE_URL", "http://127.0.0.1:8092").rstrip("/"),
            vllm_request_timeout=_positive_int("VLLM_REQUEST_TIMEOUT", 1800),
            max_text_chars=_positive_int("MAX_TEXT_CHARS", 3000),
            max_reference_audio_bytes=_positive_int("MAX_REFERENCE_AUDIO_BYTES", 25 * 1024 * 1024),
            max_output_audio_bytes=_positive_int("MAX_OUTPUT_AUDIO_BYTES", 100 * 1024 * 1024),
        )

