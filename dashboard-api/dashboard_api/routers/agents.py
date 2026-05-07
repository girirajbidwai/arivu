# dashboard-api/dashboard_api/routers/agents.py
import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from ..db.client import get_supabase

router = APIRouter(prefix="/agents", tags=["agents"])

class LoginPayload(BaseModel):
    email: str


def _supabase_enabled() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY"))

@router.post("/login")
async def login(payload: LoginPayload):
    if not _supabase_enabled():
        return {"message": "Supabase auth is disabled in this local demo build."}
    sb = get_supabase()
    # Trigger Supabase magic link login
    res = sb.auth.sign_in_with_otp({"email": payload.email})
    return {"message": "Magic link sent", "debug": str(res)}

@router.get("/me")
async def get_me(authorization: str = Header(None)):
    if not _supabase_enabled():
        raise HTTPException(status_code=503, detail="Supabase auth not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    
    token = authorization.split(" ")[1]
    sb = get_supabase()
    
    try:
        user_res = sb.auth.get_user(token)
        if not user_res.user:
            raise HTTPException(status_code=401, detail="User not found")
        
        # Fetch agent profile from our agents table
        profile = sb.table("agents").select("*").eq("email", user_res.user.email).single().execute()
        return {"user": user_res.user, "profile": profile.data}
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))
