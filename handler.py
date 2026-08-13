from __future__ import annotations

import logging
import threading
from typing import Any

import runpod

from worker.config import Settings
from worker.service import InvalidJobInput, TTSService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s level=%(levelname)s logger=%(name)s %(message)s",
)
LOGGER = logging.getLogger("index_tts25_handler")
_service: TTSService | None = None
_service_lock = threading.Lock()


def _get_service() -> TTSService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = TTSService(Settings.from_env())
    return _service


def handler(job: dict[str, Any]) -> dict[str, Any]:
    task_id = str(job.get("id") or "unknown")
    try:
        return _get_service().synthesize(job)
    except InvalidJobInput as exc:
        LOGGER.warning("stage=request_rejected task_id=%s reason=%s", task_id, exc)
        return {
            "error": {
                "code": "INVALID_INPUT",
                "message": str(exc),
                "retryable": False,
            }
        }
    except Exception:
        LOGGER.exception("stage=request_failed task_id=%s", task_id)
        raise


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})

