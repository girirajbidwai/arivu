import os

from fastapi import APIRouter, HTTPException
from postgrest.exceptions import APIError
from ..db.client import get_supabase
from ..store import memory_store

router = APIRouter(prefix="/calls", tags=["calls"])


def _supabase_enabled() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY"))

@router.get("/")
async def list_calls():
    if not _supabase_enabled():
        return memory_store.list_calls()
    sb = get_supabase()
    try:
        result = sb.table("calls").select("*").order("urgency", desc=True).execute()
    except APIError as e:
        raise HTTPException(status_code=400, detail=e.message)
    return result.data

@router.get("/{call_id}")
async def get_call(call_id: str):
    if not _supabase_enabled():
        call = memory_store.get_call(call_id)
        if call is None:
            raise HTTPException(status_code=404, detail="Call not found")
        return call
    sb = get_supabase()
    try:
        call = sb.table("calls").select("*").eq("id", call_id).single().execute()
        turns = sb.table("turns").select("*").eq("call_id", call_id).order("turn_index").execute()
    except APIError as e:
        raise HTTPException(status_code=400 if e.code == "22P02" else 404, detail=e.message)
    return {**call.data, "turns": turns.data}

@router.get("/{call_id}/transcript")
async def get_transcript(call_id: str):
    if not _supabase_enabled():
        return {"call_id": call_id, "turns": memory_store.get_turns(call_id)}
    sb = get_supabase()
    try:
        turns = sb.table("turns").select("speaker,transcript,created_at").eq("call_id", call_id).order("turn_index").execute()
    except APIError as e:
        raise HTTPException(status_code=400, detail=e.message)
    return {"call_id": call_id, "turns": turns.data}
