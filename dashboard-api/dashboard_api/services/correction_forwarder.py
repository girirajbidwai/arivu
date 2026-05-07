# dashboard-api/dashboard_api/services/correction_forwarder.py
import httpx
import os
import logging
from pydantic import BaseModel

logger = logging.getLogger(__name__)
BRAIN_URL = (
    os.environ.get("BRAIN_URL")
    or os.environ.get("BRAIN_SERVICE_URL")
    or "http://localhost:8002"
)

async def forward_to_brain(correction: BaseModel):
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.post(
                f"{BRAIN_URL}/correction_received",
                json=correction.model_dump()
            )
    except Exception as e:
        # Log and continue — the correction is already in the database.
        # The Brain will pick it up on the next turn if the POST fails.
        logger.warning(f"Brain unreachable for correction: {e}")
