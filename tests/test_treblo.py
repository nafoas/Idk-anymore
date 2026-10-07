import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from musicbot.treblo import (
    GenerationRequest,
    TrebloClient,
    TrebloError,
    parse_song_urls,
    parse_status,
    parse_task_id,
)

TASK = "0b4f5a8e-1111-4c3b-9d2e-123456789abc"


def test_payload_omits_unset_fields():
    payload = GenerationRequest(prompt="lofi beat").to_payload()
    assert payload == {"prompt": "lofi beat", "instrumental": False, "output_format": "mp3"}


def test_payload_with_tags_lyrics_and_length():
    payload = GenerationRequest(
        tags=["rock"], lyrics="la la", length_range=(30, 90)
    ).to_payload()
    assert payload["tags"] == ["rock"]
    assert payload["lyrics"] == "la la"
    assert payload["length_range"] == [30, 90]
    assert "prompt" not in payload


@pytest.mark.parametrize(
    "body", [{"task_id": TASK}, {"id": TASK}, TASK]
)
def test_parse_task_id(body):
    assert parse_task_id(body) == TASK


def test_parse_task_id_missing():
    with pytest.raises(TrebloError):
        parse_task_id({"oops": 1})


@pytest.mark.parametrize("body", ["SUCCESS", '"SUCCESS"', {"status": "success"}])
def test_parse_status(body):
    assert parse_status(body) == "SUCCESS"


def test_parse_song_urls():
    assert parse_song_urls({"song_paths": ["a.mp3", "b.mp3"]}) == ["a.mp3", "b.mp3"]
    assert parse_song_urls({"audio_url": "c.mp3"}) == ["c.mp3"]
    assert parse_song_urls({}) == []


async def fake_api(routes):
    """Start a local server that serves ``routes``: {(method, path): handler}."""
    app = web.Application()
    for (method, path), handler in routes.items():
        app.router.add_route(method, path, handler)
    server = TestServer(app)
    await server.start_server()
    return server


def json_once(*bodies, status=200):
    """Handler returning each body in turn (the last one repeats)."""
    calls = []

    async def handler(request):
        body = await request.json() if request.can_read_body else None
        calls.append((request.headers, body))
        body = bodies[min(len(calls), len(bodies)) - 1]
        if isinstance(body, str):
            return web.Response(text=body, status=status)
        return web.json_response(body, status=status)

    handler.calls = calls
    return handler


async def test_generate_happy_path():
    seen = []

    async def on_status(s):
        seen.append(s)

    create = json_once({"task_id": TASK})
    server = await fake_api({
        ("POST", "/v1/generations/v3"): create,
        ("GET", f"/v1/generations/status/{TASK}"): json_once('"GENERATING"', "SUCCESS"),
        ("GET", f"/v1/generations/{TASK}"): json_once(
            {"status": "SUCCESS", "song_paths": ["https://cdn/x.mp3"], "lyrics": "hi"}
        ),
    })
    async with TrebloClient("key", base_url=str(server.make_url(""))) as client:
        result = await client.generate(
            GenerationRequest(prompt="test"), poll_interval=0, on_status=on_status
        )
    headers, body = create.calls[0]
    assert headers["Authorization"] == "Bearer key"
    assert body["prompt"] == "test"
    assert result.song_urls == ["https://cdn/x.mp3"]
    assert result.lyrics == "hi"
    assert seen == ["GENERATING", "SUCCESS"]
    await server.close()


async def test_generation_failure_reports_reason():
    server = await fake_api({
        ("GET", f"/v1/generations/status/{TASK}"): json_once({"status": "FAILURE"}),
        ("GET", f"/v1/generations/{TASK}"): json_once({"error_message": "bad prompt"}),
    })
    async with TrebloClient("key", base_url=str(server.make_url(""))) as client:
        with pytest.raises(TrebloError, match="bad prompt"):
            await client.wait_for_result(TASK, poll_interval=0)
    await server.close()


async def test_timeout():
    server = await fake_api({
        ("GET", f"/v1/generations/status/{TASK}"): json_once("GENERATING"),
    })
    async with TrebloClient("key", base_url=str(server.make_url(""))) as client:
        with pytest.raises(TrebloError, match="Timed out"):
            await client.wait_for_result(TASK, poll_interval=0, timeout=0)
    await server.close()


async def test_http_error_includes_validation_detail():
    server = await fake_api({
        ("POST", "/v1/generations/v3"): json_once(
            {"detail": [{"loc": ["body", "prompt"], "msg": "too long"}]}, status=422
        ),
    })
    async with TrebloClient("key", base_url=str(server.make_url(""))) as client:
        with pytest.raises(TrebloError, match="422.*prompt: too long"):
            await client.create_generation(GenerationRequest(prompt="x"))
    await server.close()


async def test_unauthorized_hint():
    server = await fake_api({
        ("POST", "/v1/generations/v3"): json_once({"detail": "Invalid key"}, status=401),
    })
    async with TrebloClient("bad", base_url=str(server.make_url(""))) as client:
        with pytest.raises(TrebloError, match="TREBLO_API_KEY"):
            await client.create_generation(GenerationRequest(prompt="x"))
    await server.close()


async def test_download_respects_size_limit():
    async def big(request):
        return web.Response(body=b"x" * 2000)

    async def small(request):
        return web.Response(body=b"x" * 10)

    server = await fake_api({("GET", "/big.mp3"): big, ("GET", "/small.mp3"): small})
    async with TrebloClient("key") as client:
        assert await client.download(str(server.make_url("/big.mp3")), max_bytes=1000) is None
        assert await client.download(str(server.make_url("/small.mp3")), max_bytes=1000) == b"x" * 10
    await server.close()
