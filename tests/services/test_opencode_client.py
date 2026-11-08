"""OpenCode Go client against a local HTTP server on loopback. No external network."""

import asyncio
import json
import time

import pytest
from aiohttp import web

from pigeon.llm import opencode
from pigeon.llm.opencode import ModelUnavailable, OpenCodeClient

KEY = "sk-very-secret"


def reply(text) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


@pytest.fixture
async def server():
    """A loopback server whose handler each test sets; yields (base_url, state)."""
    state: dict = {"requests": []}

    async def handle(request: web.Request) -> web.StreamResponse:
        state["requests"].append(
            {"path": request.path, "headers": dict(request.headers), "body": await request.read()}
        )
        return await state["handler"](request)

    app = web.Application()
    app.router.add_post("/v1/chat/completions", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}/v1/", state
    await runner.cleanup()


async def test_request_shape_and_reply(server) -> None:
    base, state = server

    async def ok(request):
        return web.json_response(reply("Голуби курлыкнули."))

    state["handler"] = ok
    client = OpenCodeClient(KEY, "glm-5.3-flash", base)
    assert await client.complete("SYS", "USER", session_id="s-1") == "Голуби курлыкнули."
    sent = state["requests"][0]
    assert sent["path"] == "/v1/chat/completions"
    assert sent["headers"]["Authorization"] == f"Bearer {KEY}"
    assert sent["headers"]["x-opencode-session"] == "s-1"
    assert sent["headers"]["User-Agent"].startswith("pigeon-bot/")
    body = json.loads(sent["body"])
    assert body["model"] == "glm-5.3-flash"
    assert body["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USER"},
    ]
    assert body["max_tokens"] == opencode.MAX_TOKENS and body["stream"] is False


def _status(code):
    async def handler(request):
        return web.Response(status=code, text="nope")

    return handler


def _raw(text, content_type="application/json"):
    async def handler(request):
        return web.Response(text=text, content_type=content_type)

    return handler


async def _redirect(request):
    raise web.HTTPFound("http://127.0.0.1:1/elsewhere")


async def test_redirect_is_not_followed(server) -> None:
    base, state = server

    async def to_ok(request):
        if request.path.endswith("/chat/completions"):
            raise web.HTTPFound(base + "chat/completions?again=1")
        return web.json_response(reply("followed"))

    state["handler"] = to_ok
    with pytest.raises(ModelUnavailable, match="HTTP 302"):
        await OpenCodeClient(KEY, base_url=base).complete("s", "u", session_id="x")
    assert len(state["requests"]) == 1


async def _huge(request):
    return web.json_response(reply("x" * (opencode.BODY_LIMIT + 10)))


@pytest.mark.parametrize(
    "handler",
    [
        _status(401),
        _status(429),
        _status(500),
        _redirect,
        _raw("not json"),
        _raw(json.dumps({"choices": []})),
        _raw(json.dumps({"error": {"message": "bad"}})),
        _raw(json.dumps(reply(None))),
        _raw(json.dumps(reply(["list"]))),
        _huge,
    ],
    ids=[
        "401",
        "429",
        "500",
        "redirect",
        "not-json",
        "no-choices",
        "error-body",
        "null-content",
        "non-text",
        "huge",
    ],
)
async def test_bad_replies_raise_model_unavailable(server, handler) -> None:
    base, state = server
    state["handler"] = handler
    with pytest.raises(ModelUnavailable) as caught:
        await OpenCodeClient(KEY, base_url=base).complete("s", "u", session_id="x")
    assert KEY not in str(caught.value)
    assert len(state["requests"]) == 1  # no retries, no redirect followed


async def test_slow_headers_hit_the_deadline(server) -> None:
    base, state = server

    async def slow(request):
        await asyncio.sleep(2)
        return web.json_response(reply("late"))

    state["handler"] = slow
    started = time.monotonic()
    with pytest.raises(ModelUnavailable, match="deadline"):
        await OpenCodeClient(KEY, base_url=base, timeout=0.3).complete("s", "u", session_id="x")
    assert time.monotonic() - started < 2


async def test_slow_drip_body_hits_the_deadline(server) -> None:
    base, state = server

    async def drip(request):
        response = web.StreamResponse(headers={"Content-Type": "application/json"})
        await response.prepare(request)
        for _ in range(50):
            await response.write(b" ")
            await asyncio.sleep(0.1)
        return response

    state["handler"] = drip
    started = time.monotonic()
    with pytest.raises(ModelUnavailable):
        await OpenCodeClient(KEY, base_url=base, timeout=0.3).complete("s", "u", session_id="x")
    assert time.monotonic() - started < 2


async def test_connection_refused() -> None:
    client = OpenCodeClient(KEY, base_url="http://127.0.0.1:1/v1")
    with pytest.raises(ModelUnavailable) as caught:
        await client.complete("s", "u", session_id="x")
    assert KEY not in str(caught.value) and KEY not in repr(client)
