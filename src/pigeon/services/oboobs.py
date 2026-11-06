"""Bounded OBOOBS client. Gameplay never depends on a successful download."""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from urllib.request import HTTPRedirectHandler, Request, build_opener

API_URL = "http://api.oboobs.ru/boobs/0/1/random/"
# HTTPS media is unstable in the production network: some larger transfers break off at 16078
# bytes and hang until the timeout (the real client timed out 4 of 4 times over HTTPS); plain
# HTTP delivers the same files whole. The host, path shape, size limits and MIME check stay
# fixed (ADR-0012).
MEDIA_URL = "http://media.oboobs.ru/"
TIMEOUT_SECONDS = 10  # overall deadline of one HTTP request: headers and body together
CHUNK_SIZE = 16 * 1024
METADATA_LIMIT = 64 * 1024
IMAGE_LIMIT = 10 * 1024 * 1024
# The API sometimes returns an upper-case extension; the real type comes from Content-Type.
PREVIEW_PATH = re.compile(r"boobs_preview/[0-9]+\.(?i:jpg|jpeg|png)")


class GiftUnavailable(Exception):
    """The current gift could not be fetched safely."""


@dataclass(frozen=True)
class GiftImage:
    data: bytes
    filename: str


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OboobsClient:
    def __init__(self, api_url: str = API_URL, media_url: str = MEDIA_URL) -> None:
        self._api_url = api_url
        self._media_url = media_url
        self._opener = build_opener(_NoRedirects)

    async def random_image(self) -> GiftImage:
        try:
            return await self._random_image()
        except GiftUnavailable:
            raise
        except Exception as exc:
            raise GiftUnavailable("gift download failed") from exc

    async def _random_image(self) -> GiftImage:
        metadata, content_type = await self._fetch(self._api_url, METADATA_LIMIT)
        if content_type != "application/json":
            raise GiftUnavailable("unexpected metadata content type")
        try:
            payload = json.loads(metadata)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GiftUnavailable("invalid metadata JSON") from exc
        if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
            raise GiftUnavailable("unexpected metadata shape")
        preview = payload[0].get("preview")
        if not isinstance(preview, str) or PREVIEW_PATH.fullmatch(preview) is None:
            raise GiftUnavailable("unexpected preview path")
        image, content_type = await self._fetch(self._media_url + preview, IMAGE_LIMIT)
        if content_type not in {"image/jpeg", "image/png"}:
            raise GiftUnavailable("unexpected image content type")
        extension = "jpg" if content_type == "image/jpeg" else "png"
        return GiftImage(image, f"gift.{extension}")

    async def _fetch(self, url: str, limit: int) -> tuple[bytes, str]:
        # urllib's timeout bounds each socket operation, not the request. The caller-side
        # deadline bounds the whole request, headers included; the worker thread checks the
        # same deadline between body chunks, so a slow drip stops instead of running on.
        timeout = TIMEOUT_SECONDS
        deadline = time.monotonic() + timeout
        try:
            async with asyncio.timeout(timeout):
                return await asyncio.to_thread(self._read, url, limit, deadline)
        except TimeoutError as exc:
            raise GiftUnavailable("HTTP request exceeded its deadline") from exc

    def _read(self, url: str, limit: int, deadline: float) -> tuple[bytes, str]:
        request = Request(url, headers={"User-Agent": "pigeon-bot/0.1"})
        try:
            with self._opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                content_type = response.headers.get_content_type()
                length = response.headers.get("Content-Length")
                if length is not None and (not length.isdecimal() or int(length) > limit):
                    raise GiftUnavailable("response size is invalid or too large")
                data = self._read_body(response, limit, deadline)
        except GiftUnavailable:
            raise
        except Exception as exc:
            raise GiftUnavailable("HTTP request failed") from exc
        if len(data) > limit:
            raise GiftUnavailable("response exceeds size limit")
        if length is not None and len(data) != int(length):
            raise GiftUnavailable("response is truncated")
        return data, content_type

    @staticmethod
    def _read_body(response, limit: int, deadline: float) -> bytes:
        body = bytearray()
        while len(body) <= limit:
            if time.monotonic() >= deadline:
                raise GiftUnavailable("HTTP request exceeded its deadline")
            # read1 returns whatever has arrived instead of waiting for the full chunk.
            chunk = response.read1(min(CHUNK_SIZE, limit + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        return bytes(body)
