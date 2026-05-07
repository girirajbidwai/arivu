from fastapi import WebSocket
from typing import Dict, Set
import json

class WSHub:
    def __init__(self):
        self._connections: Dict[str, Set[WebSocket]] = {}  # agent_id -> set of websockets

    async def connect(self, agent_id: str, ws: WebSocket):
        await ws.accept()
        if agent_id not in self._connections:
            self._connections[agent_id] = set()
        self._connections[agent_id].add(ws)

    def disconnect(self, agent_id: str, ws: WebSocket):
        if agent_id in self._connections:
            self._connections[agent_id].discard(ws)
            if not self._connections[agent_id]:
                del self._connections[agent_id]

    async def broadcast_to_agent(self, agent_id: str, event: dict):
        if agent_id in self._connections:
            dead = []
            for ws in self._connections[agent_id]:
                try:
                    await ws.send_json(event)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._connections[agent_id].discard(ws)

    async def broadcast_all(self, event: dict):
        """For events like safety_alert that all agents should see."""
        for agent_id in list(self._connections.keys()):
            await self.broadcast_to_agent(agent_id, event)

hub = WSHub()
