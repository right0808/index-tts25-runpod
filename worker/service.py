from __future__ import annotations

import io
import logging
import math
import re
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from worker.audio import AudioInputError, resolve_audio
from worker.config import Settings
from worker.oss import OssUploader

LOGGER = logging.getLogger("index_tts25_worker")
SUPPORTED_LANGUAGES = frozenset({"zh", "en", "zhen", "ja", "es", "ar", "yue"})


class InvalidJobInput(ValueError):
    pass


class InferenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class AudioMetadata:
    sample_rate: int
    channels: int
    duration_seconds: float


def _number(value: Any, *, field: str, minimum: float, maximum: float, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise InvalidJobInput(f"{field} must be a finite number")
    result = float(value)
    if result < minimum or result > maximum:
        raise InvalidJobInput(f"{field} must be between {minimum} and {maximum}")
    return result


def _wav_metadata(data: bytes) -> AudioMetadata:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise InferenceError("Inference response is not a RIFF/WAVE file")
    try:
        with wave.open(io.BytesIO(data), "rb") as reader:
            rate = reader.getframerate()
            channels = reader.getnchannels()
            frames = reader.getnframes()
    except (wave.Error, EOFError) as exc:
        raise InferenceError("Inference response contains an invalid WAV file") from exc
    if rate <= 0 or channels <= 0 or frames <= 0:
        raise InferenceError("Inference response contains empty WAV audio")
    return AudioMetadata(rate, channels, round(frames / rate, 3))


class TTSService:
    def __init__(self, settings: Settings, *, session: requests.Session | None = None) -> None:
        self.settings = settings
        self.session = session or requests.Session()
        self.oss = OssUploader(
            base_url=settings.oss_base_url,
            api_key=settings.oss_upload_api_key,
            expires_seconds=settings.oss_expires_seconds,
            timeout=settings.oss_http_timeout,
            session=self.session,
        )

    def _wait_for_vllm(self, *, task_id: str) -> None:
        health_url = f"{self.settings.vllm_base_url}/health"
        deadline = time.monotonic() + self.settings.vllm_startup_timeout
        failure_file = Path(self.settings.vllm_failure_file)
        LOGGER.info(
            "stage=model_server_wait task_id=%s timeout_seconds=%d",
            task_id,
            self.settings.vllm_startup_timeout,
        )
        while True:
            if failure_file.is_file():
                try:
                    detail = failure_file.read_text(encoding="utf-8", errors="replace")[-4000:].strip()
                except OSError:
                    detail = "startup process exited; diagnostic file could not be read"
                raise InferenceError(f"vLLM-Omni startup failed: {detail}")

            response = None
            try:
                response = self.session.get(health_url, timeout=5)
                if response.status_code == 200:
                    LOGGER.info("stage=model_server_ready task_id=%s", task_id)
                    return
            except requests.RequestException:
                pass
            finally:
                if response is not None:
                    response.close()

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise InferenceError(
                    f"vLLM-Omni did not become ready within {self.settings.vllm_startup_timeout} seconds"
                )
            time.sleep(min(5, remaining))

    def _validate_input(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("input")
        if not isinstance(payload, dict):
            raise InvalidJobInput("input must be a JSON object")

        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise InvalidJobInput("text must be a non-empty string")
        text = text.strip()
        if len(text) > self.settings.max_text_chars:
            raise InvalidJobInput(f"text cannot exceed {self.settings.max_text_chars} characters")

        language = payload.get("language", "zh")
        if not isinstance(language, str) or language.lower() not in SUPPORTED_LANGUAGES:
            raise InvalidJobInput(f"language must be one of: {', '.join(sorted(SUPPORTED_LANGUAGES))}")

        speaker_audio = payload.get("speaker_audio")
        if not isinstance(speaker_audio, str) or not speaker_audio.strip():
            raise InvalidJobInput("speaker_audio must be a non-empty URL or data URL")

        modes = [
            payload.get("emotion_audio") is not None,
            payload.get("emotion_vector") is not None,
            payload.get("emotion_text") is not None,
        ]
        if sum(modes) > 1:
            raise InvalidJobInput("Use only one of emotion_audio, emotion_vector, or emotion_text")

        emotion_vector = payload.get("emotion_vector")
        if emotion_vector is not None:
            if not isinstance(emotion_vector, list) or len(emotion_vector) != 8:
                raise InvalidJobInput("emotion_vector must contain exactly 8 numbers")
            emotion_vector = [
                _number(item, field="emotion_vector item", minimum=0.0, maximum=1.2, default=0.0)
                for item in emotion_vector
            ]

        emotion_text = payload.get("emotion_text")
        if emotion_text is not None and (not isinstance(emotion_text, str) or not emotion_text.strip()):
            raise InvalidJobInput("emotion_text must be a non-empty string")

        text_normalization = payload.get("text_normalization", True)
        if not isinstance(text_normalization, bool):
            raise InvalidJobInput("text_normalization must be a boolean")
        use_random = payload.get("use_random", False)
        if not isinstance(use_random, bool):
            raise InvalidJobInput("use_random must be a boolean")

        return {
            "text": text,
            "language": language.lower(),
            "speaker_audio": speaker_audio,
            "emotion_audio": payload.get("emotion_audio"),
            "emotion_vector": emotion_vector,
            "emotion_text": emotion_text.strip() if isinstance(emotion_text, str) else None,
            "emotion_alpha": _number(
                payload.get("emotion_alpha"), field="emotion_alpha", minimum=0.0, maximum=1.0, default=1.0
            ),
            "speed": _number(payload.get("speed"), field="speed", minimum=0.5, maximum=2.0, default=1.0),
            "text_normalization": text_normalization,
            "use_random": use_random,
        }

    def synthesize(self, job: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        task_id = str(job.get("id") or "unknown")
        values = self._validate_input(job)
        LOGGER.info(
            "stage=request_validated task_id=%s text_chars=%d language=%s emotion_mode=%s",
            task_id,
            len(values["text"]),
            values["language"],
            "text" if values["emotion_text"] else "vector" if values["emotion_vector"] else "audio" if values["emotion_audio"] else "speaker",
        )

        self._wait_for_vllm(task_id=task_id)

        try:
            speaker = resolve_audio(
                values["speaker_audio"],
                session=self.session,
                timeout=self.settings.oss_http_timeout,
                max_bytes=self.settings.max_reference_audio_bytes,
            )
            LOGGER.info(
                "stage=speaker_audio_ready task_id=%s source_host=%s bytes=%d media_type=%s",
                task_id,
                speaker.source_host,
                speaker.byte_count,
                speaker.media_type,
            )
            emotion = None
            if values["emotion_audio"] is not None:
                emotion = resolve_audio(
                    values["emotion_audio"],
                    session=self.session,
                    timeout=self.settings.oss_http_timeout,
                    max_bytes=self.settings.max_reference_audio_bytes,
                )
                LOGGER.info(
                    "stage=emotion_audio_ready task_id=%s source_host=%s bytes=%d media_type=%s",
                    task_id,
                    emotion.source_host,
                    emotion.byte_count,
                    emotion.media_type,
                )
        except AudioInputError as exc:
            raise InvalidJobInput(str(exc)) from exc

        extra_params: dict[str, Any] = {
            "lang": values["language"],
            "text_normalization": values["text_normalization"],
        }
        if emotion is not None:
            extra_params["emo_audio"] = emotion.data_url
            extra_params["emo_alpha"] = values["emotion_alpha"]
        elif values["emotion_vector"] is not None:
            extra_params["emo_vector"] = values["emotion_vector"]
            extra_params["emo_alpha"] = values["emotion_alpha"]
            extra_params["use_random"] = values["use_random"]
        elif values["emotion_text"] is not None:
            extra_params["use_emo_text"] = True
            extra_params["emo_text"] = values["emotion_text"]
            extra_params["emo_alpha"] = values["emotion_alpha"]

        request_body = {
            "model": self.settings.model_id,
            "input": values["text"],
            "response_format": "wav",
            "speed": values["speed"],
            "ref_audio": speaker.data_url,
            "extra_params": extra_params,
        }
        LOGGER.info("stage=inference_started task_id=%s model=%s", task_id, self.settings.model_id)
        response = self.session.post(
            f"{self.settings.vllm_base_url}/v1/audio/speech",
            json=request_body,
            timeout=self.settings.vllm_request_timeout,
            stream=True,
        )
        try:
            if response.status_code != 200:
                detail = "inference request was rejected"
                try:
                    error_payload = response.json()
                    candidate = error_payload.get("error", {}).get("message")
                    if isinstance(candidate, str) and candidate:
                        detail = candidate[:500].replace("\n", " ")
                except (ValueError, AttributeError):
                    pass
                raise InferenceError(f"vLLM-Omni returned HTTP {response.status_code}: {detail}")
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=256 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > self.settings.max_output_audio_bytes:
                    raise InferenceError(f"Inference audio exceeds {self.settings.max_output_audio_bytes} bytes")
                chunks.append(chunk)
            audio = b"".join(chunks)
        finally:
            response.close()

        if not audio or len(audio) > self.settings.max_output_audio_bytes:
            raise InferenceError(
                f"Inference audio must be between 1 and {self.settings.max_output_audio_bytes} bytes"
            )
        metadata = _wav_metadata(audio)
        LOGGER.info(
            "stage=inference_completed task_id=%s bytes=%d sample_rate=%d duration_seconds=%.3f",
            task_id,
            len(audio),
            metadata.sample_rate,
            metadata.duration_seconds,
        )

        safe_task_id = re.sub(r"[^A-Za-z0-9_.-]", "_", task_id)[:100]
        filename = f"indextts25-{safe_task_id}.wav" if task_id != "unknown" else "indextts25-output.wav"
        stored = self.oss.upload_wav(audio, filename=filename)
        elapsed_ms = round((time.monotonic() - started) * 1000)
        LOGGER.info(
            "stage=oss_upload_completed task_id=%s file_name=%s bytes=%d expires_seconds=%d elapsed_ms=%d",
            task_id,
            stored.file_name,
            len(audio),
            self.settings.oss_expires_seconds,
            elapsed_ms,
        )
        return {
            "audio_url": stored.url,
            "file_name": stored.file_name,
            "content_type": "audio/wav",
            "bytes": len(audio),
            "sample_rate": metadata.sample_rate,
            "channels": metadata.channels,
            "duration_seconds": metadata.duration_seconds,
            "expires_in_seconds": self.settings.oss_expires_seconds,
            "elapsed_ms": elapsed_ms,
        }
