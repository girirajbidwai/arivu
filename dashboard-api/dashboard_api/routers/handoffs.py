# dashboard-api/dashboard_api/routers/handoffs.py
import os

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from typing import Optional
from postgrest.exceptions import APIError
from ..db.client import get_supabase
from ..store import memory_store

router = APIRouter(prefix="/handoffs", tags=["handoffs"])

class HandoffPayload(BaseModel):
    call_id: str
    agent_id: Optional[str] = None
    reason: str         # safety_whisper | safety_silence | safety_third_voice | verification_complete
    time_to_pickup_ms: Optional[int] = None


def _supabase_enabled() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY"))

@router.post("/")
async def log_handoff(payload: HandoffPayload):
    if _supabase_enabled():
        sb = get_supabase()
        try:
            result = sb.table("handoffs").insert(payload.model_dump()).execute()
            row_id = result.data[0]["id"] if result.data else "mock-id"
        except APIError as e:
            raise HTTPException(status_code=400, detail=e.message)
    else:
        row = memory_store.append_handoff(payload.model_dump())
        row_id = row["id"]
    return {"status": "logged", "id": row_id}

@router.get("/")
async def get_handoffs(call_id: str = Query(...)):
    if not _supabase_enabled():
        return memory_store.list_handoffs(call_id)
    sb = get_supabase()
    try:
        result = sb.table("handoffs").select("*").eq("call_id", call_id).execute()
    except APIError as e:
        raise HTTPException(status_code=400, detail=e.message)
    return result.data
