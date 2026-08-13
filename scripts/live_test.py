#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def upload_reference(path: Path, *, base_url: str, key: str, expires: int, timeout: int) -> str:
    with path.open("rb") as handle:
        response = requests.post(
            f"{base_url.rstrip('/')}/oss",
            headers={"X-API-Key": key},
            data={"expiresInSeconds": str(expires)},
            files={"file": (path.name, handle, "audio/mp4")},
            timeout=timeout,
        )
    response.raise_for_status()
    if response.status_code != 201:
        raise RuntimeError(f"OSS upload returned HTTP {response.status_code}")
    file_name = response.json()["fileName"]
    from urllib.parse import quote

    return f"{base_url.rstrip('/')}/oss/{quote(file_name, safe='')}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload test references and run a real RunPod IndexTTS request")
    parser.add_argument("--endpoint-id", required=True)
    parser.add_argument("--speaker", type=Path, required=True)
    parser.add_argument("--emotion", type=Path)
    parser.add_argument("--text", required=True)
    parser.add_argument("--language", default="zh")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--timeout", type=int, default=2400)
    args = parser.parse_args()

    load_env(args.env_file)
    runpod_key = os.getenv("RUNPOD_API_KEY") or os.getenv("RunPodKey")
    oss_base = os.environ["OSS_BASE_URL"]
    oss_key = os.environ["OSS_UPLOAD_API_KEY"]
    if not runpod_key:
        raise RuntimeError("RUNPOD_API_KEY or RunPodKey is required")

    expires = max(int(os.getenv("OSS_EXPIRES_SECONDS", "3600")), args.timeout + 600)
    http_timeout = int(os.getenv("OSS_HTTP_TIMEOUT", "30"))
    speaker_url = upload_reference(args.speaker, base_url=oss_base, key=oss_key, expires=expires, timeout=http_timeout)
    emotion_url = None
    if args.emotion:
        emotion_url = upload_reference(args.emotion, base_url=oss_base, key=oss_key, expires=expires, timeout=http_timeout)

    payload = {
        "input": {
            "text": args.text,
            "language": args.language,
            "speaker_audio": speaker_url,
            "emotion_audio": emotion_url,
            "emotion_alpha": 0.8,
            "speed": 1.0,
        }
    }
    if emotion_url is None:
        payload["input"].pop("emotion_audio")

    response = requests.post(
        f"https://api.runpod.ai/v2/{args.endpoint_id}/run",
        headers={"Authorization": f"Bearer {runpod_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    job_id = response.json()["id"]
    print(json.dumps({"job_id": job_id}, ensure_ascii=False))

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        status = requests.get(
            f"https://api.runpod.ai/v2/{args.endpoint_id}/status/{job_id}",
            headers={"Authorization": f"Bearer {runpod_key}"},
            timeout=30,
        )
        status.raise_for_status()
        data = status.json()
        state = data.get("status")
        if state in {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}:
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0 if state == "COMPLETED" and not data.get("output", {}).get("error") else 1
        time.sleep(5)
    raise TimeoutError(f"RunPod job did not finish within {args.timeout} seconds")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"live test failed: {exc}", file=sys.stderr)
        raise

