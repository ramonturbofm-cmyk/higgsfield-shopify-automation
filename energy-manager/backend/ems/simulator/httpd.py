"""Tiny asyncio HTTP/1.1 server for the test environment's virtual devices (JSON in/out, one request per
connection). Only used on 127.0.0.1 in the Testomgeving; never for production traffic."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qsl, urlsplit

log = logging.getLogger(__name__)
Handler = Callable[[str, str, dict, dict | None], Awaitable[tuple[int, dict | list | None]]]


class MiniHTTPServer:
    def __init__(self, handler: Handler, host: str = "127.0.0.1", port: int = 0) -> None:
        self.handler, self.host, self.port = handler, host, port
        self.server: asyncio.base_events.Server | None = None
        self.last_headers: dict[str, str] = {}

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._conn, self.host, self.port)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    async def _conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            lines = head.decode("latin-1").split("\r\n")
            method, target, _ = lines[0].split(" ", 2)
            headers = {k.strip().lower(): v.strip() for k, v in (ln.split(":", 1) for ln in lines[1:] if ":" in ln)}
            body = None
            n = int(headers.get("content-length", "0") or 0)
            if n:
                raw = await asyncio.wait_for(reader.readexactly(min(n, 1_000_000)), 10)
                body = json.loads(raw or b"null")
            url = urlsplit(target)
            self.last_headers = headers          # read synchronously by handlers that need them (auth)
            status, payload = await self.handler(method.upper(), url.path, dict(parse_qsl(url.query)), body)
        except (asyncio.IncompleteReadError, TimeoutError, ValueError, json.JSONDecodeError):
            status, payload = 400, {"error": "bad request"}
        except Exception:
            log.exception("lab http handler failed")
            status, payload = 500, {"error": "internal"}
        data = b"" if payload is None else json.dumps(payload).encode()
        reason = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found",
                  500: "Internal Server Error"}.get(status, "OK")
        writer.write(f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\n"
                     f"Connection: close\r\n\r\n".encode() + data)
        try:
            await writer.drain()
        finally:
            writer.close()
