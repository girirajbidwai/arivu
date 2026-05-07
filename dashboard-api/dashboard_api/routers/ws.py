from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from ..services.ws_hub import hub

router = APIRouter(prefix="/ws", tags=["websocket"])

@router.websocket("/agent/{agent_id}")
async def websocket_endpoint(websocket: WebSocket, agent_id: str):
    await hub.connect(agent_id, websocket)
    try:
        while True:
            # Keep the connection open and wait for messages (though we mostly send)
            data = await websocket.receive_text()
            # Handle incoming messages if needed
    except WebSocketDisconnect:
        hub.disconnect(agent_id, websocket)
