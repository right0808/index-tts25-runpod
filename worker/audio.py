from __future__ import annotations

import base64
import binascii
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import requests


class AudioInputError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedAudio:
    data_url: str
    byte_count: int
    media_type: str
    source_host: str


def _is_public_host(hostname: str) -> bool:
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, None)}
    except socket.gaierror as exc:
        raise AudioInputError(f"Audio host cannot be resolved: {hostname}") from exc
    if not addresses:
        return False
    for raw in addresses:
        address = ipaddress.ip_address(raw)
        if not address.is_global:
            return False
    return True


def _validate_remote_url(value: str) -> tuple[str, str]:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise AudioInputError("Audio URL must use http or https")
    if not parsed.hostname or parsed.username or parsed.password:
        raise AudioInputError("Audio URL has an invalid host")
    if not _is_public_host(parsed.hostname):
        raise AudioInputError("Audio URL must resolve only to public IP addresses")
    return value, parsed.hostname


def _detect_media_type(data: bytes, content_type: str | None) -> str:
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "audio/wav"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "audio/mp4"
    if data.startswith(b"OggS"):
        return "audio/ogg"
    if data.startswith(b"fLaC"):
        return "audio/flac"
    if data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    if normalized.startswith("audio/") or normalized in {"video/mp4", "application/ogg"}:
        return normalized
    raise AudioInputError("Reference content is not a supported audio file")


def _decode_data_url(value: str, max_bytes: int) -> ResolvedAudio:
    try:
        header, encoded = value.split(",", 1)
    except ValueError as exc:
        raise AudioInputError("Malformed audio data URL") from exc
    if ";base64" not in header.lower():
        raise AudioInputError("Audio data URL must be base64 encoded")
    declared_type = header[5:].split(";", 1)[0].lower()
    if not declared_type.startswith("audio/") and declared_type != "video/mp4":
        raise AudioInputError("Data URL must declare an audio media type")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AudioInputError("Audio data URL contains invalid base64") from exc
    if not data or len(data) > max_bytes:
        raise AudioInputError(f"Reference audio must be between 1 and {max_bytes} bytes")
    media_type = _detect_media_type(data, declared_type)
    return ResolvedAudio(value, len(data), media_type, "inline")


def resolve_audio(
    value: str,
    *,
    session: requests.Session,
    timeout: int,
    max_bytes: int,
    max_redirects: int = 3,
) -> ResolvedAudio:
    if not isinstance(value, str) or not value.strip():
        raise AudioInputError("Audio reference must be a non-empty URL or data URL")
    value = value.strip()
    if value.lower().startswith("data:"):
        return _decode_data_url(value, max_bytes)

    current, source_host = _validate_remote_url(value)
    response = None
    for _ in range(max_redirects + 1):
        response = session.get(current, timeout=timeout, stream=True, allow_redirects=False)
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise AudioInputError("Audio download redirect is missing Location")
            current, source_host = _validate_remote_url(urljoin(current, location))
            continue
        break
    else:
        raise AudioInputError("Audio URL has too many redirects")

    assert response is not None
    try:
        if response.status_code != 200:
            raise AudioInputError(f"Audio download returned HTTP {response.status_code}")
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > max_bytes:
                    raise AudioInputError(f"Reference audio exceeds {max_bytes} bytes")
            except ValueError:
                pass
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise AudioInputError(f"Reference audio exceeds {max_bytes} bytes")
            chunks.append(chunk)
        data = b"".join(chunks)
        if not data:
            raise AudioInputError("Reference audio download returned an empty file")
        media_type = _detect_media_type(data, response.headers.get("Content-Type"))
    finally:
        response.close()

    encoded = base64.b64encode(data).decode("ascii")
    return ResolvedAudio(f"data:{media_type};base64,{encoded}", len(data), media_type, source_host)

