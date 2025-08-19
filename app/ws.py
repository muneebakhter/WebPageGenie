from __future__ import annotations

from typing import Set
from fastapi import WebSocket


class ReloadWebSocketManager:
    def __init__(self) -> None:
        self.active: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.active.discard(ws)

    async def broadcast_reload(self) -> None:
        stale: list[WebSocket] = []
        for ws in list(self.active):
            try:
                await ws.send_text("reload")
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)
