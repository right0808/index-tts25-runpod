from pathlib import Path

from fastapi.testclient import TestClient

import lb_app


def test_ping_stays_live_while_model_starts(monkeypatch):
    monkeypatch.setattr(lb_app, "_model_state", lambda: ("starting", None))
    response = TestClient(lb_app.app).get("/ping")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "model_status": "starting"}


def test_ready_reports_startup_failure(monkeypatch, tmp_path: Path):
    failure_file = tmp_path / "vllm.failed"
    failure_file.write_text("exit_code=1\nCUDA out of memory", encoding="utf-8")
    monkeypatch.setenv("VLLM_FAILURE_FILE", str(failure_file))
    response = TestClient(lb_app.app).get("/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "failed"
    assert "CUDA out of memory" in response.json()["error"]


def test_tts_rejects_request_until_model_is_ready(monkeypatch):
    monkeypatch.setattr(lb_app, "_model_state", lambda: ("starting", None))
    response = TestClient(lb_app.app).post("/tts", json={"text": "测试"})

    assert response.status_code == 503
    assert response.headers["retry-after"] == "15"
    assert response.json()["detail"]["code"] == "MODEL_STARTING"


def test_accepts_direct_and_queue_compatible_payloads():
    direct = {"text": "测试", "speaker_audio": "https://audio.example/speaker.wav"}
    assert lb_app._payload_input(direct) == direct
    assert lb_app._payload_input({"input": direct}) == direct
