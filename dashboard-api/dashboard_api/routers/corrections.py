# dashboard-api/dashboard_api/routers/corrections.py
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from postgrest.exceptions import APIError
from ..db.client import get_supabase
from ..services.correction_forwarder import forward_to_brain
from ..store import memory_store

router = APIRouter(prefix="/corrections", tags=["corrections"])

class CorrectionPayload(BaseModel):
    call_id: str
    turn_index: int
    agent_id: str
    field: str        # issue_summary | urgency | dialect | sentiment
    old_value: str
    new_value: str


def _supabase_enabled() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY"))

@router.post("/")
async def create_correction(payload: CorrectionPayload):
    if _supabase_enabled():
        sb = get_supabase()
        try:
            result = sb.table("corrections").insert(payload.model_dump()).execute()
            row_id = result.data[0]["id"] if result.data else "mock-id"
        except APIError as e:
            raise HTTPException(status_code=400, detail=e.message)
    else:
        row = memory_store.append_correction(payload.model_dump())
        row_id = row["id"]
    
    # Fire G6: forward to Brain (asynchronous fire-and-forget)
    await forward_to_brain(payload)
    
    return {"id": row_id, "status": "saved"}
