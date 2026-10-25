"""Fan-out of security events to WebSocket clients.

Each client has a bounded queue and its own sender task, so a slow or dead client can never
block event processing or database writes: when a queue is full the message is dropped for
that client (and counted). ``publish`` is safe to call from worker threads.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import WebSocket

from app.schemas.common import utcnow

logger = logging.getLogger(__name__)

QUEUE_SIZE = 200
MESSAGE_VERSION = 1


class _Client:
    def __init__(self, ws: WebSocket) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.ws = ws
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self.task: asyncio.Task | None = None
        self.dropped = 0


class WebSocketManager:
    def __init__(self) -> None:
        self._clients: dict[str, _Client] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self.published = 0

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def connection_count(self) -> int:
        return len(self._clients)

    async def connect(self, ws: WebSocket) -> _Client:
        await ws.accept()
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        client = _Client(ws)
        self._clients[client.id] = client
        client.task = asyncio.create_task(self._sender(client))
        await client.queue.put(self.envelope("connected", {"client_id": client.id,
                                                           "message": "Subscribed to D3COY security events"}))
        logger.info("WebSocket client %s connected (%d total)", client.id, len(self._clients))
        return client

    async def disconnect(self, client: _Client) -> None:
        self._clients.pop(client.id, None)
        if client.task and not client.task.done():
            client.task.cancel()
        logger.info("WebSocket client %s disconnected", client.id)

    async def _sender(self, client: _Client) -> None:
        try:
            while True:
                msg = await client.queue.get()
                await client.ws.send_json(msg)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.info("WebSocket client %s send failed; dropping connection", client.id)
            self._clients.pop(client.id, None)

    @staticmethod
    def envelope(msg_type: str, data: dict[str, Any]) -> dict[str, Any]:
        return {"type": msg_type, "version": MESSAGE_VERSION, "sent_at": utcnow().isoformat(), "data": data}

    def _fanout(self, message: dict[str, Any]) -> None:
        for client in list(self._clients.values()):
            try:
                client.queue.put_nowait(message)
            except asyncio.QueueFull:
                client.dropped += 1

    def publish(self, msg_type: str, data: dict[str, Any]) -> None:
        """Thread-safe, non-blocking. Never raises into the caller."""
        message = self.envelope(msg_type, data)
        self.published += 1
        loop = self._loop
        if loop is None or loop.is_closed() or not self._clients:
            return
        try:
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is loop:
                self._fanout(message)
            else:
                loop.call_soon_threadsafe(self._fanout, message)
        except Exception:  # loop shutting down etc.
            logger.debug("WebSocket publish skipped", exc_info=True)
