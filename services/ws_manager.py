"""
Shared WebSocket connection manager.
Imported by both main.py (endpoint) and webhooks.py (broadcast).
"""
import asyncio
import json
import logging
from typing import Dict, List
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WsManager:
    def __init__(self):
        self._conns: Dict[str, List[WebSocket]] = {}

    async def connect(self, restaurant_id: str, ws: WebSocket):
        await ws.accept()
        self._conns.setdefault(restaurant_id, []).append(ws)
        logger.info(f"[ws] connect restaurant={restaurant_id} active={len(self._conns[restaurant_id])}")

    def disconnect(self, restaurant_id: str, ws: WebSocket):
        if restaurant_id in self._conns:
            self._conns[restaurant_id] = [w for w in self._conns[restaurant_id] if w is not ws]

    async def broadcast(self, restaurant_id: str, event_type: str, data: dict):
        conns = self._conns.get(restaurant_id, [])
        if not conns:
            return
        payload = json.dumps({"type": event_type, **data}, ensure_ascii=False)
        dead = []
        for ws in conns:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(restaurant_id, ws)

    def broadcast_sync(self, restaurant_id: str, event_type: str, data: dict):
        """Schedule broadcast from synchronous code running inside the event loop."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.broadcast(restaurant_id, event_type, data))
        except Exception as e:
            logger.debug(f"[ws] broadcast_sync failed: {e}")


ws_manager = WsManager()
