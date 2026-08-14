#!/usr/bin/env python3
"""End-to-end API test for the IndexTTS 2.5 RunPod load balancer."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


def log(stage: str, message: str, **fields: Any) -> None:
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    suffix = " ".join(
        f"{name}={json.dumps(value, ensure_ascii=False)}" for name, value in fields.items()
    )
    print(f"[{timestamp}] stage={stage} {message}{' ' + suffix if suffix else ''}", flush=True)


def load_env(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"environment file not found: {path}")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def response_body(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text[:2000]


def wait_until_ready(
    session: requests.Session,
    base_url: str,
    *,
    cold_start_timeout: int,
    request_timeout: int,
    poll_interval: int,
) -> None:
    deadline = time.monotonic() + cold_start_timeout
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        started = time.monotonic()
        try:
            response = session.get(f"{base_url}/ready", timeout=request_timeout)
            elapsed = round(time.monotonic() - started, 3)
            body = response_body(response)
            log(
                "readiness",
                "GET /ready completed",
                attempt=attempt,
                http_status=response.status_code,
                elapsed_seconds=elapsed,
                response=body,
            )
            if response.status_code == 200 and isinstance(body, dict) and body.get("status") == "ready":
                return
            if isinstance(body, dict) and body.get("status") == "failed":
                raise RuntimeError(f"worker model initialization failed: {json.dumps(body, ensure_ascii=False)}")
        except requests.RequestException as exc:
            log(
                "readiness",
                "worker is not reachable yet; cold start continues",
                attempt=attempt,
                error=f"{type(exc).__name__}: {exc}",
            )

        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(poll_interval, remaining))

    raise TimeoutError(f"worker did not become ready within {cold_start_timeout} seconds")


def upload_reference(
    session: requests.Session,
    path: Path,
    *,
    label: str,
    base_url: str,
    api_key: str,
    expires: int,
    timeout: int,
) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"{label} audio not found: {path}")
    log("oss_upload", "upload started", label=label, file=str(path), bytes=path.stat().st_size)
    started = time.monotonic()
    with path.open("rb") as handle:
        response = session.post(
            f"{base_url.rstrip('/')}/oss",
            headers={"X-API-Key": api_key},
            data={"expiresInSeconds": str(expires)},
            files={"file": (path.name, handle, "audio/mp4")},
            timeout=timeout,
        )
    body = response_body(response)
    log(
        "oss_upload",
        "upload completed",
        label=label,
        http_status=response.status_code,
        elapsed_seconds=round(time.monotonic() - started, 3),
        response=body,
    )
    response.raise_for_status()
    if response.status_code != 201 or not isinstance(body, dict) or "fileName" not in body:
        raise RuntimeError(f"unexpected OSS upload response for {label}: {body!r}")
    return f"{base_url.rstrip('/')}/oss/{quote(str(body['fileName']), safe='')}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Call the IndexTTS 2.5 load-balancing API and print every test stage"
    )
    parser.add_argument("--endpoint-id", required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--speaker", type=Path, required=True)
    parser.add_argument("--emotion", type=Path)
    parser.add_argument("--text", required=True)
    parser.add_argument("--language", default="zh")
    parser.add_argument("--emotion-alpha", type=float, default=0.8)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--output", type=Path, default=Path("index-tts25-test-output.wav"))
    parser.add_argument("--cold-start-timeout", type=int, default=1800)
    parser.add_argument("--request-timeout", type=int, default=1800)
    parser.add_argument("--health-request-timeout", type=int, default=45)
    parser.add_argument("--poll-interval", type=int, default=15)
    args = parser.parse_args()

    load_env(args.env_file)
    runpod_key = os.getenv("RUNPOD_API_KEY") or os.getenv("RunPodKey")
    oss_base_url = os.getenv("OSS_BASE_URL")
    oss_api_key = os.getenv("OSS_UPLOAD_API_KEY")
    if not runpod_key:
        raise RuntimeError("RUNPOD_API_KEY or RunPodKey is required")
    if not oss_base_url or not oss_api_key:
        raise RuntimeError("OSS_BASE_URL and OSS_UPLOAD_API_KEY are required")

    base_url = f"https://{args.endpoint_id}.api.runpod.ai"
    runpod_session = requests.Session()
    runpod_session.headers.update({"Authorization": f"Bearer {runpod_key}"})
    public_session = requests.Session()

    log(
        "configuration",
        "test configuration loaded",
        endpoint=base_url,
        speaker=str(args.speaker),
        emotion=str(args.emotion) if args.emotion else None,
        output=str(args.output),
        api_key="[REDACTED_SECRET]",
        oss_api_key="[REDACTED_SECRET]",
    )
    log(
        "cold_start",
        "waiting for a scale-to-zero worker; the first calls may time out or report no workers",
        timeout_seconds=args.cold_start_timeout,
    )
    wait_until_ready(
        runpod_session,
        base_url,
        cold_start_timeout=args.cold_start_timeout,
        request_timeout=args.health_request_timeout,
        poll_interval=args.poll_interval,
    )

    expires = max(
        int(os.getenv("OSS_EXPIRES_SECONDS", "3600")),
        args.request_timeout + 600,
    )
    oss_timeout = int(os.getenv("OSS_HTTP_TIMEOUT", "30"))
    speaker_url = upload_reference(
        public_session,
        args.speaker,
        label="speaker",
        base_url=oss_base_url,
        api_key=oss_api_key,
        expires=expires,
        timeout=oss_timeout,
    )
    emotion_url = None
    if args.emotion:
        emotion_url = upload_reference(
            public_session,
            args.emotion,
            label="emotion",
            base_url=oss_base_url,
            api_key=oss_api_key,
            expires=expires,
            timeout=oss_timeout,
        )

    payload: dict[str, Any] = {
        "text": args.text,
        "language": args.language,
        "speaker_audio": speaker_url,
        "emotion_alpha": args.emotion_alpha,
        "speed": args.speed,
    }
    if emotion_url:
        payload["emotion_audio"] = emotion_url

    log("tts_request", "POST /tts started", url=f"{base_url}/tts", payload=payload)
    started = time.monotonic()
    response = runpod_session.post(
        f"{base_url}/tts",
        json=payload,
        timeout=args.request_timeout,
    )
    result = response_body(response)
    log(
        "tts_response",
        "POST /tts completed",
        http_status=response.status_code,
        elapsed_seconds=round(time.monotonic() - started, 3),
        response=result,
    )
    response.raise_for_status()
    if not isinstance(result, dict) or not result.get("audio_url"):
        raise RuntimeError(f"TTS response has no audio_url: {result!r}")

    log("audio_download", "generated audio download started", url=result["audio_url"])
    started = time.monotonic()
    # Never forward the RunPod bearer token to the external OSS host.
    audio_response = public_session.get(result["audio_url"], timeout=60)
    audio_response.raise_for_status()
    audio = audio_response.content
    is_wave = audio[:4] == b"RIFF" and audio[8:12] == b"WAVE"
    log(
        "audio_download",
        "generated audio download completed",
        http_status=audio_response.status_code,
        elapsed_seconds=round(time.monotonic() - started, 3),
        bytes=len(audio),
        riff_wave=is_wave,
    )
    if not is_wave:
        raise RuntimeError("generated output is not a RIFF/WAVE file")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(audio)
    log("complete", "API test passed", output=str(args.output.resolve()), result=result)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log("failed", "API test failed", error=f"{type(exc).__name__}: {exc}")
        raise
