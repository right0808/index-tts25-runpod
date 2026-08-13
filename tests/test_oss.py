import pytest

from worker.oss import OssUploadError, OssUploader


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.closed = False

    def json(self):
        return self._payload

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.call = None

    def post(self, *args, **kwargs):
        self.call = (args, kwargs)
        return self.response


def test_upload_uses_expected_contract_and_canonical_url():
    response = FakeResponse(201, {"fileName": "abc 1.wav", "url": "http://untrusted.invalid/file"})
    session = FakeSession(response)
    uploader = OssUploader(
        base_url="http://oss.example",
        api_key="secret-key",
        expires_seconds=3600,
        timeout=30,
        session=session,
    )
    result = uploader.upload_wav(b"wav", filename="result.wav")
    _, kwargs = session.call
    assert kwargs["headers"] == {"X-API-Key": "secret-key"}
    assert kwargs["data"] == {"expiresInSeconds": "3600"}
    assert kwargs["files"]["file"] == ("result.wav", b"wav", "audio/wav")
    assert result.url == "http://oss.example/oss/abc%201.wav"
    assert response.closed


def test_upload_requires_created_status():
    session = FakeSession(FakeResponse(200, {"fileName": "abc.wav"}))
    uploader = OssUploader(
        base_url="http://oss.example",
        api_key="secret-key",
        expires_seconds=3600,
        timeout=30,
        session=session,
    )
    with pytest.raises(OssUploadError, match="HTTP 200"):
        uploader.upload_wav(b"wav", filename="result.wav")

