import base64

import pytest

from worker.audio import AudioInputError, resolve_audio


class UnusedSession:
    def get(self, *args, **kwargs):
        raise AssertionError("network must not be used")


def test_accepts_valid_inline_wav():
    wav = b"RIFF" + (36).to_bytes(4, "little") + b"WAVE" + b"x" * 32
    value = "data:audio/wav;base64," + base64.b64encode(wav).decode()
    result = resolve_audio(value, session=UnusedSession(), timeout=1, max_bytes=1024)
    assert result.byte_count == len(wav)
    assert result.media_type == "audio/wav"
    assert result.source_host == "inline"


def test_rejects_non_audio_data_url():
    value = "data:text/plain;base64," + base64.b64encode(b"hello").decode()
    with pytest.raises(AudioInputError, match="audio media type"):
        resolve_audio(value, session=UnusedSession(), timeout=1, max_bytes=1024)


def test_rejects_oversized_inline_audio():
    wav = b"RIFF" + (36).to_bytes(4, "little") + b"WAVE" + b"x" * 32
    value = "data:audio/wav;base64," + base64.b64encode(wav).decode()
    with pytest.raises(AudioInputError, match="between 1 and 8 bytes"):
        resolve_audio(value, session=UnusedSession(), timeout=1, max_bytes=8)

