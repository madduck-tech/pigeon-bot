"""OpenCode Go client over its OpenAI-compatible Chat Completions API (ADR-0014).

One bounded POST: an overall deadline, a capped body, no redirects. Errors never carry the key,
the prompt or the reply, so they are safe to log.
"""

from __future__ import annotations

import asyncio
import json

import aiohttp

from pigeon import __version__
from pigeon.llm.errors import ModelUnavailable

DEFAULT_BASE_URL = "https://opencode.ai/inference/go/openai/v1"
DEFAULT_MODEL = "glm-5.3-flash"
TIMEOUT_SECONDS = 20  # connection, headers and body together
BODY_LIMIT = 64 * 1024
MAX_TOKENS = 800
TEMPERATURE = 0.9
CHUNK_SIZE = 16 * 1024

__all__ = ["ModelUnavailable", "OpenCodeClient"]


class OpenCodeClient:
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._timeout = timeout
        # finish_reason and token usage of the last reply, for diagnostics; never the text.
        self.last_meta: dict = {}

    def __repr__(self) -> str:  # never show the key
        return f"OpenCodeClient(model={self._model!r}, url={self._url!r})"

    async def complete(self, system: str, user: str, *, session_id: str) -> str:
        try:
            async with asyncio.timeout(self._timeout):
                return await self._complete(system, user, session_id)
        except ModelUnavailable:
            raise
        except TimeoutError as exc:
            raise ModelUnavailable("request exceeded its deadline") from exc
        except Exception as exc:
            raise ModelUnavailable(f"request failed: {type(exc).__name__}") from exc

    async def _complete(self, system: str, user: str, session_id: str) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": MAX_TOKENS,
            "temperature": TEMPERATURE,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent": f"pigeon-bot/{__version__}",
            "x-opencode-session": session_id,
        }
        timeout = aiohttp.ClientTimeout(total=self._timeout)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(self._url, json=payload, headers=headers, allow_redirects=False) as resp,
        ):
            if resp.status // 100 != 2:
                raise ModelUnavailable(f"HTTP {resp.status}")
            body = bytearray()
            async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                body.extend(chunk)
                if len(body) > BODY_LIMIT:
                    raise ModelUnavailable("response exceeds size limit")
        return self._content(bytes(body))

    def _content(self, body: bytes) -> str:
        try:
            data = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelUnavailable("invalid JSON") from exc
        if isinstance(data, dict):
            choices = data.get("choices")
            first = choices[0] if isinstance(choices, list) and choices else {}
            self.last_meta = {
                "finish_reason": first.get("finish_reason") if isinstance(first, dict) else None,
                "usage": data.get("usage") if isinstance(data.get("usage"), dict) else None,
            }
        return _text(data)


def _text(data) -> str:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelUnavailable("unexpected response shape") from exc
    if not isinstance(content, str):
        raise ModelUnavailable("reply has no text")
    return content
