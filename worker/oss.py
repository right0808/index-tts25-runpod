from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

import requests


class OssUploadError(RuntimeError):
    pass


@dataclass(frozen=True)
class OssObject:
    file_name: str
    url: str


class OssUploader:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        expires_seconds: int,
        timeout: int,
        session: requests.Session,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.expires_seconds = expires_seconds
        self.timeout = timeout
        self.session = session

    def upload_wav(self, data: bytes, *, filename: str) -> OssObject:
        response = self.session.post(
            f"{self.base_url}/oss",
            headers={"X-API-Key": self.api_key},
            data={"expiresInSeconds": str(self.expires_seconds)},
            files={"file": (filename, data, "audio/wav")},
            timeout=self.timeout,
        )
        try:
            if response.status_code != 201:
                raise OssUploadError(f"OSS upload returned HTTP {response.status_code}")
            try:
                payload = response.json()
            except ValueError as exc:
                raise OssUploadError("OSS upload returned invalid JSON") from exc
            file_name = payload.get("fileName")
            if not isinstance(file_name, str) or not file_name.strip():
                raise OssUploadError("OSS upload response is missing fileName")
            file_name = file_name.strip()
            return OssObject(file_name, f"{self.base_url}/oss/{quote(file_name, safe='')}")
        finally:
            response.close()

