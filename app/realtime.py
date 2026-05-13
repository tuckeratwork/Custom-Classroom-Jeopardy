from __future__ import annotations

from typing import Dict, Set

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, session_code: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.setdefault(session_code, set()).add(websocket)

    def disconnect(self, session_code: str, websocket: WebSocket) -> None:
        sockets = self._connections.get(session_code)
        if not sockets:
            return
        sockets.discard(websocket)
        if not sockets:
            self._connections.pop(session_code, None)

    async def broadcast(self, session_code: str, message: dict) -> None:
        sockets = list(self._connections.get(session_code, set()))
        for websocket in sockets:
            try:
                await websocket.send_json(message)
            except RuntimeError:
                self.disconnect(session_code, websocket)
