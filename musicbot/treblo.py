"""Minimal async client for the Treblo (formerly Sonauto) Melodia v3 API.

Generation is asynchronous: POST /v1/generations/v3 returns a task id, then
/v1/generations/status/{task_id} is polled until the task finishes, and
/v1/generations/{task_id} returns the finished song URLs.

The public OpenAPI spec doesn't document response bodies, so the parsing
helpers below accept the couple of shapes the API is known to use.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import aiohttp

DEFAULT_BASE_URL = "https://api.treblo.com"

SUCCESS_STATUSES = {"SUCCESS", "COMPLETED", "COMPLETE", "DONE"}
FAILURE_STATUSES = {"FAILURE", "FAILED", "ERROR", "CANCELLED", "CANCELED"}

OUTPUT_FORMATS = ("mp3", "ogg", "wav", "flac", "m4a")


class TrebloError(Exception):
    """Raised when the API rejects a request or a generation fails."""


@dataclass
class GenerationRequest:
    prompt: str | None = None
    tags: list[str] | None = None
    lyrics: str | None = None
    instrumental: bool = False
    output_format: str = "mp3"
    length_range: tuple[int, int] | None = None
    negative_tags: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "instrumental": self.instrumental,
            "output_format": self.output_format,
        }
        if self.prompt:
            payload["prompt"] = self.prompt
        if self.tags:
            payload["tags"] = self.tags
        if self.lyrics:
            payload["lyrics"] = self.lyrics
        if self.length_range:
            payload["length_range"] = list(self.length_range)
        if self.negative_tags:
            payload["negative_tags"] = self.negative_tags
        return payload


@dataclass
class GenerationResult:
    task_id: str
    song_urls: list[str]
    raw: dict[str, Any]

    @property
    def lyrics(self) -> str | None:
        return self.raw.get("lyrics")

    @property
    def tags(self) -> list[str]:
        return list(self.raw.get("tags") or [])


def parse_task_id(body: Any) -> str:
    if isinstance(body, str) and body:
        return body
    if isinstance(body, dict):
        for key in ("task_id", "id", "taskId"):
            if body.get(key):
                return str(body[key])
    raise TrebloError(f"No task id in response: {body!r}")


def parse_status(body: Any) -> str:
    if isinstance(body, dict):
        body = body.get("status", "")
    return str(body).strip().strip('"').upper()


def parse_song_urls(body: dict[str, Any]) -> list[str]:
    for key in ("song_paths", "song_urls", "audio_urls"):
        urls = body.get(key)
        if urls:
            return [u for u in urls if isinstance(u, str)]
    for key in ("song_path", "audio_url", "url"):
        if isinstance(body.get(key), str):
            return [body[key]]
    return []


StatusCallback = Callable[[str], Awaitable[None]]


class TrebloClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._session = session
        self._owns_session = session is None

    async def __aenter__(self) -> "TrebloClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60)
            )
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        url = f"{self._base_url}{path}"
        async with self.session.request(method, url, headers=headers, **kwargs) as resp:
            if resp.content_type == "application/json":
                body = await resp.json()
            else:
                body = await resp.text()
            if resp.status >= 400:
                raise TrebloError(_error_message(resp.status, body))
            return body

    async def create_generation(self, request: GenerationRequest) -> str:
        body = await self._request(
            "POST", "/v1/generations/v3", json=request.to_payload()
        )
        return parse_task_id(body)

    async def get_status(self, task_id: str) -> str:
        return parse_status(
            await self._request("GET", f"/v1/generations/status/{task_id}")
        )

    async def get_task(self, task_id: str) -> dict[str, Any]:
        body = await self._request("GET", f"/v1/generations/{task_id}")
        if not isinstance(body, dict):
            raise TrebloError(f"Unexpected task response: {body!r}")
        return body

    async def get_balance(self) -> Any:
        return await self._request("GET", "/v1/credits/balance")

    async def wait_for_result(
        self,
        task_id: str,
        *,
        poll_interval: float = 5.0,
        timeout: float = 600.0,
        on_status: StatusCallback | None = None,
    ) -> GenerationResult:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        last_status = None
        while True:
            status = await self.get_status(task_id)
            if status != last_status:
                last_status = status
                if on_status:
                    await on_status(status)
            if status in SUCCESS_STATUSES:
                break
            if status in FAILURE_STATUSES:
                task = await self.get_task(task_id)
                reason = task.get("error_message") or task.get("error") or status
                raise TrebloError(f"Generation failed: {reason}")
            if loop.time() >= deadline:
                raise TrebloError(
                    f"Timed out after {int(timeout)}s (last status: {status})"
                )
            await asyncio.sleep(poll_interval)

        task = await self.get_task(task_id)
        urls = parse_song_urls(task)
        if not urls:
            raise TrebloError("Generation finished but returned no audio")
        return GenerationResult(task_id=task_id, song_urls=urls, raw=task)

    async def generate(
        self, request: GenerationRequest, **wait_kwargs: Any
    ) -> GenerationResult:
        task_id = await self.create_generation(request)
        return await self.wait_for_result(task_id, **wait_kwargs)

    async def download(self, url: str, max_bytes: int | None = None) -> bytes | None:
        """Fetch audio bytes; returns None if the file exceeds ``max_bytes``."""
        async with self.session.get(url) as resp:
            resp.raise_for_status()
            if max_bytes is not None and (resp.content_length or 0) > max_bytes:
                return None
            data = bytearray()
            async for chunk in resp.content.iter_chunked(64 * 1024):
                data.extend(chunk)
                if max_bytes is not None and len(data) > max_bytes:
                    return None
            return bytes(data)


def _error_message(status: int, body: Any) -> str:
    detail = body
    if isinstance(body, dict):
        detail = body.get("detail") or body.get("error") or body.get("message") or body
    if isinstance(detail, list):  # FastAPI validation errors
        detail = "; ".join(
            f"{'.'.join(str(p) for p in d.get('loc', [])[1:])}: {d.get('msg')}"
            if isinstance(d, dict)
            else str(d)
            for d in detail
        )
    hints = {401: "check TREBLO_API_KEY", 402: "out of credits", 429: "rate limited"}
    hint = f" ({hints[status]})" if status in hints else ""
    return f"Treblo API error {status}{hint}: {detail}"
