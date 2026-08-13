import io
import wave

import pytest

from worker.audio import ResolvedAudio
from worker.config import Settings
from worker.oss import OssObject
from worker.service import InvalidJobInput, TTSService


def make_wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(22050)
        writer.writeframes(b"\x00\x00" * 2205)
    return output.getvalue()


class FakeResponse:
    text = ""

    def __init__(self, content=b"", status_code=200):
        self.content = content
        self.status_code = status_code
        self.closed = False

    def close(self):
        self.closed = True

    def iter_content(self, chunk_size):
        yield self.content


class FakeSession:
    def __init__(self, audio, health_statuses=None):
        self.audio = audio
        self.json = None
        self.health_statuses = list(health_statuses or [200])
        self.health_checks = 0

    def get(self, url, **kwargs):
        assert url.endswith("/health")
        assert kwargs["timeout"] == 5
        self.health_checks += 1
        status = self.health_statuses.pop(0) if self.health_statuses else 200
        return FakeResponse(status_code=status)

    def post(self, url, **kwargs):
        assert url.endswith("/v1/audio/speech")
        assert kwargs["stream"] is True
        self.json = kwargs["json"]
        return FakeResponse(self.audio)


class FakeOss:
    def __init__(self):
        self.data = None

    def upload_wav(self, data, *, filename):
        self.data = data
        return OssObject("generated.wav", "http://oss.example/oss/generated.wav")


@pytest.fixture
def settings():
    return Settings(oss_base_url="http://oss.example", oss_upload_api_key="secret")


def test_rejects_multiple_emotion_modes(settings):
    service = TTSService(settings, session=FakeSession(make_wav()))
    with pytest.raises(InvalidJobInput, match="only one"):
        service._validate_input(
            {
                "input": {
                    "text": "hello",
                    "speaker_audio": "https://audio.example/speaker.wav",
                    "emotion_audio": "https://audio.example/emotion.wav",
                    "emotion_vector": [0.0] * 8,
                }
            }
        )


def test_rejects_speed_outside_native_range(settings):
    service = TTSService(settings, session=FakeSession(make_wav()))
    with pytest.raises(InvalidJobInput, match="speed must be between"):
        service._validate_input(
            {"input": {"text": "hello", "speaker_audio": "https://audio.example/speaker.wav", "speed": 2.1}}
        )


def test_synthesis_maps_request_and_returns_oss_url(monkeypatch, settings):
    session = FakeSession(make_wav())
    service = TTSService(settings, session=session)
    fake_oss = FakeOss()
    service.oss = fake_oss

    def fake_resolve(value, **kwargs):
        return ResolvedAudio("data:audio/mp4;base64,AAAA", 4, "audio/mp4", "audio.example")

    monkeypatch.setattr("worker.service.resolve_audio", fake_resolve)
    result = service.synthesize(
        {
            "id": "job-1",
            "input": {
                "text": "测试文字",
                "language": "zh",
                "speaker_audio": "https://audio.example/speaker.m4a",
                "emotion_vector": [1.0, 0, 0, 0, 0, 0, 0, 0],
                "emotion_alpha": 0.8,
                "speed": 1.2,
            },
        }
    )
    assert session.json["model"] == "IndexTeam/IndexTTS-2.5"
    assert session.json["ref_audio"].startswith("data:audio/mp4")
    assert session.json["extra_params"]["lang"] == "zh"
    assert session.json["extra_params"]["emo_vector"][0] == 1.0
    assert result["audio_url"] == "http://oss.example/oss/generated.wav"
    assert result["sample_rate"] == 22050
    assert result["channels"] == 1
    assert fake_oss.data.startswith(b"RIFF")


def test_synthesis_waits_for_model_server(monkeypatch, settings):
    session = FakeSession(make_wav(), health_statuses=[503, 200])
    service = TTSService(settings, session=session)
    service.oss = FakeOss()

    monkeypatch.setattr("worker.service.time.sleep", lambda _: None)
    monkeypatch.setattr(
        "worker.service.resolve_audio",
        lambda value, **kwargs: ResolvedAudio("data:audio/mp4;base64,AAAA", 4, "audio/mp4", "audio.example"),
    )

    service.synthesize(
        {"id": "job-wait", "input": {"text": "测试", "speaker_audio": "https://audio.example/speaker.m4a"}}
    )
    assert session.health_checks == 2
