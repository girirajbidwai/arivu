import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from .routers import calls, ws, corrections, agents, handoffs, events
from .realtime.supabase_listener import start_supabase_listener
from .services.ws_hub import hub


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Realtime is the primary broadcast path; the /events/ingest router
    # is the fallback. Either path, every event still reaches the agent.
    try:
        await start_supabase_listener()
    except Exception as e:
        print(f"Warning: Supabase listener failed to start (check .env): {e}")
    yield


app = FastAPI(title="Arivu Dashboard API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(calls.router)
app.include_router(ws.router)
app.include_router(corrections.router)
app.include_router(agents.router)
app.include_router(handoffs.router)
app.include_router(events.router)


@app.get("/health")
async def health():
    return {
        "ok": True,
        "service": "arivu-dashboard-api",
        "version": "0.1.0",
        "supabase_configured": bool(
            os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        ),
        "active_agents": sum(len(v) for v in hub._connections.values()),
    }
