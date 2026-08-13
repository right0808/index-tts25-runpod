from __future__ import annotations

import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any

import requests
from fastapi import FastAPI, HTTPException, Response
from fastapi.concurrency import run_in_threadpool

from worker.config import Settings
from worker.service import InferenceError, InvalidJobInput, TTSService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s level=%(levelname)s logger=%(name)s %(message)s",
)
LOGGER = logging.getLogger("index_tts25_lb")

app = FastAPI(title="IndexTTS 2.5 RunPod Load Balancer", version="1.0.0")
_service: TTSService | None = None
_service_lock = threading.Lock()
_inference_lock = threading.Lock()


def _get_service() -> TTSService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = TTSService(Settings.from_env())
    return _service


def _failure_detail() -> str | None:
    failure_file = Path(os.getenv("VLLM_FAILURE_FILE", "/tmp/vllm-server.failed"))
    if not failure_file.is_file():
        return None
    try:
        return failure_file.read_text(encoding="utf-8", errors="replace")[-4000:].strip()
    except OSError:
        return "vLLM process exited; its diagnostic file could not be read"


def _model_state() -> tuple[str, str | None]:
    detail = _failure_detail()
    if detail is not None:
        return "failed", detail
    base_url = os.getenv("VLLM_BASE_URL", "http://127.0.0.1:8092").rstrip("/")
    try:
        response = requests.get(f"{base_url}/health", timeout=1)
        try:
            if response.status_code == 200:
                return "ready", None
        finally:
            response.close()
    except requests.RequestException:
        pass
    return "starting", None


def _payload_input(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("input")
    if nested is None:
        return payload
    if not isinstance(nested, dict):
        raise InvalidJobInput("input must be a JSON object")
    return nested


@app.get("/ping")
def ping() -> dict[str, Any]:
    """RunPod liveness check; stays healthy while the large model initializes."""
    state, _ = _model_state()
    return {"status": "healthy", "model_status": state}


@app.get("/ready")
def ready(response: Response) -> dict[str, Any]:
    state, detail = _model_state()
    if state != "ready":
        response.status_code = 503
    result: dict[str, Any] = {"status": state}
    if detail is not None:
        result["error"] = detail
    return result


@app.post("/tts")
async def synthesize(payload: dict[str, Any]) -> dict[str, Any]:
    state, detail = _model_state()
    if state != "ready":
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MODEL_FAILED" if state == "failed" else "MODEL_STARTING",
                "message": detail or "IndexTTS 2.5 is still initializing",
                "retryable": state != "failed",
            },
            headers={"Retry-After": "15"},
        )

    if not _inference_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail={"code": "WORKER_BUSY", "message": "This worker is already synthesizing", "retryable": True},
            headers={"Retry-After": "5"},
        )

    task_id = f"lb-{uuid.uuid4()}"
    try:
        job = {"id": task_id, "input": _payload_input(payload)}
        return await run_in_threadpool(_get_service().synthesize, job)
    except InvalidJobInput as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_INPUT", "message": str(exc), "retryable": False},
        ) from exc
    except InferenceError as exc:
        LOGGER.exception("stage=request_failed task_id=%s category=inference", task_id)
        raise HTTPException(
            status_code=502,
            detail={"code": "INFERENCE_FAILED", "message": str(exc), "retryable": True},
        ) from exc
    except Exception as exc:
        LOGGER.exception("stage=request_failed task_id=%s category=upstream", task_id)
        raise HTTPException(
            status_code=502,
            detail={"code": "UPSTREAM_FAILED", "message": str(exc), "retryable": True},
        ) from exc
    finally:
        _inference_lock.release()
