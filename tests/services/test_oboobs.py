import io
import json
import threading
import time
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError

import pytest

from pigeon.services import oboobs
from pigeon.services.oboobs import GiftUnavailable, OboobsClient


class FakeResponse:
    def __init__(self, data, content_type, length=None):
        self._stream = io.BytesIO(data)
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        if length is not False:
            self.headers["Content-Length"] = str(len(data) if length is None else length)

    def read1(self, size):
        return self._stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


class FakeOpener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request.full_url, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _metadata(preview="boobs_preview/123.jpg"):
    return json.dumps([{"preview": preview}]).encode()


def _client(*responses):
    client = OboobsClient()
    client._opener = FakeOpener(*responses)
    return client


async def test_random_image_uses_fixed_media_host_and_returns_bytes():
    client = _client(
        FakeResponse(_metadata(), "application/json"),
        FakeResponse(b"jpeg", "image/jpeg"),
    )
    image = await client.random_image()
    assert image.data == b"jpeg"
    assert image.filename == "gift.jpg"
    assert [call[0] for call in client._opener.calls] == [
        oboobs.API_URL,
        "http://media.oboobs.ru/boobs_preview/123.jpg",
    ]
    assert all(call[1] == 10 for call in client._opener.calls)


def test_media_goes_over_plain_http_to_the_fixed_host():
    # ADR-0012: HTTPS media breaks off in the production network; the PO chose HTTP, no fallback.
    assert oboobs.MEDIA_URL == "http://media.oboobs.ru/"
    assert oboobs.TIMEOUT_SECONDS == 10


@pytest.mark.parametrize(
    ("preview", "content_type", "filename"),
    [
        ("boobs_preview/10663.JPG", "image/jpeg", "gift.jpg"),
        ("boobs_preview/10663.PNG", "image/png", "gift.png"),
        ("boobs_preview/10663.JPG", "image/png", "gift.png"),
        ("boobs_preview/10663.Jpeg", "image/jpeg", "gift.jpg"),
    ],
)
async def test_upper_case_extension_is_accepted_and_type_comes_from_mime(
    preview, content_type, filename
):
    client = _client(
        FakeResponse(_metadata(preview), "application/json"),
        FakeResponse(b"img", content_type),
    )
    image = await client.random_image()
    assert image.filename == filename
    assert client._opener.calls[1][0] == oboobs.MEDIA_URL + preview


async def test_upper_case_extension_still_needs_an_image_mime():
    client = _client(
        FakeResponse(_metadata("boobs_preview/1.JPG"), "application/json"),
        FakeResponse(b"<html>", "text/html"),
    )
    with pytest.raises(GiftUnavailable):
        await client.random_image()


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(_metadata(), "text/plain"),
        FakeResponse(b"not-json", "application/json"),
        FakeResponse(b"{}", "application/json"),
        FakeResponse(b"[]", "application/json"),
    ],
)
async def test_bad_metadata_is_unavailable(response):
    client = _client(response)
    with pytest.raises(GiftUnavailable):
        await client.random_image()
    assert len(client._opener.calls) == 1


@pytest.mark.parametrize(
    "preview",
    [
        "https://example.com/x.jpg",
        "//example.com/x.jpg",
        "../secret.jpg",
        "other/123.jpg",
        "boobs_preview/abc.jpg",
        "boobs_preview/123.gif",
        "boobs_preview/123.GIF",
        "BOOBS_PREVIEW/123.jpg",
        "boobs_preview/123.jpg?x",
    ],
)
async def test_untrusted_preview_never_selects_download_url(preview):
    client = _client(FakeResponse(_metadata(preview), "application/json"))
    with pytest.raises(GiftUnavailable):
        await client.random_image()
    assert len(client._opener.calls) == 1


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("timeout"),
        HTTPError(oboobs.API_URL, 302, "redirect", {}, None),
        HTTPError(oboobs.API_URL, 503, "unavailable", {}, None),
    ],
)
async def test_network_errors_and_redirects_are_unavailable(failure):
    client = _client(failure)
    with pytest.raises(GiftUnavailable):
        await client.random_image()


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(b"image", "text/plain"),
        FakeResponse(b"image", "image/jpeg", oboobs.IMAGE_LIMIT + 1),
        FakeResponse(b"too-large", "image/jpeg", length=False),
        FakeResponse(b"short", "image/jpeg", 100),
    ],
)
async def test_bad_image_type_size_and_truncation_are_unavailable(response, monkeypatch):
    monkeypatch.setattr(oboobs, "IMAGE_LIMIT", 8)
    client = _client(FakeResponse(_metadata(), "application/json"), response)
    with pytest.raises(GiftUnavailable):
        await client.random_image()


class _DripHandler(BaseHTTPRequestHandler):
    """Sends one byte every DRIP seconds: each socket read succeeds, the request never ends."""

    DRIP = 0.3
    stage = "body"
    stop: threading.Event

    def do_GET(self):
        body = b"x" * 8
        head = (
            b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
        )
        try:
            if self.stage == "headers":
                for byte in head:
                    if self.stop.is_set():
                        return
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(self.DRIP)
            else:
                self.wfile.write(head)
                self.wfile.flush()
            for byte in body:
                if self.stop.is_set():
                    return
                time.sleep(self.DRIP)
                self.wfile.write(bytes([byte]))
                self.wfile.flush()
        except OSError:
            pass

    def log_message(self, *args):
        pass


@pytest.fixture
def drip_server():
    servers = []
    stop = threading.Event()

    def start(stage):
        handler = type("Handler", (_DripHandler,), {"stage": stage, "stop": stop})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        server.block_on_close = False
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}/"

    start.stop = stop
    yield start
    stop.set()
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("stage", ["body", "headers"])
async def test_slow_drip_is_cut_off_at_the_overall_deadline(drip_server, monkeypatch, stage):
    # Regression (D2): urllib's timeout bounds each socket read, so a byte every 0.3 s used to
    # stretch a 0.5 s request to 2.4 s and beyond the gift lease.
    monkeypatch.setattr(oboobs, "TIMEOUT_SECONDS", 0.5)
    client = OboobsClient(api_url=drip_server(stage))
    started = time.monotonic()
    with pytest.raises(GiftUnavailable):
        await client.random_image()
    elapsed = time.monotonic() - started
    # Going silent lets the abandoned worker thread hit its socket timeout and exit before the
    # event loop waits for it on shutdown.
    drip_server.stop.set()
    assert 0.45 <= elapsed < 1.2


async def test_body_reader_stops_at_the_deadline_in_its_own_thread():
    # The worker thread must not keep reading a drip after the caller gave up.
    class Endless:
        def read1(self, size):
            time.sleep(0.05)
            return b"x"

    started = time.monotonic()
    with pytest.raises(GiftUnavailable):
        OboobsClient._read_body(Endless(), 1024, time.monotonic() + 0.3)
    assert time.monotonic() - started < 0.6
