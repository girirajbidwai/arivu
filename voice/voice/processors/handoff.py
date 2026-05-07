"""
HandoffController — issues Twilio REST `Calls.update` with a TwiML
payload that warm-transfers the citizen to a human agent's mobile.

The original Media Stream WebSocket stays open via a parallel `<Stream/>`
so Arivu can keep listening (interpreter mode), even after the bridge
is established.

For the prototype we treat the agent's mobile number as a single env var
(`AGENT_PHONE_NUMBER`); production would pick from a queue of available
agents in Supabase.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("arivu.processors.handoff")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")
DEFAULT_AGENT_NUMBER = os.getenv("AGENT_PHONE_NUMBER", "")


def _build_handoff_twiml(
    *,
    agent_number: str,
    media_stream_url: str,
    record: bool = True,
) -> str:
    """
    TwiML that:
      1. Re-opens the same media stream so Arivu stays on the line.
      2. Dials the agent's mobile, bridging both legs.
      3. Optionally records from answer for the audit trail.
    """
    record_attr = ' record="record-from-answer"' if record else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Response>\n'
        '  <Start>\n'
        f'    <Stream url="{media_stream_url}">\n'
        '      <Parameter name="mode" value="interpreter" />\n'
        '    </Stream>\n'
        '  </Start>\n'
        f'  <Dial answerOnBridge="true"{record_attr}>\n'
        f'    <Number>{agent_number}</Number>\n'
        '  </Dial>\n'
        '</Response>'
    )


class HandoffController:
    """Owns the Twilio REST client and the handoff TwiML mutation."""

    def __init__(
        self,
        *,
        account_sid: str = TWILIO_ACCOUNT_SID,
        auth_token: str = TWILIO_AUTH_TOKEN,
        agent_number: str = DEFAULT_AGENT_NUMBER,
    ) -> None:
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.agent_number = agent_number
        self._client = None  # lazy — twilio sdk is heavy

    def _get_client(self):
        if self._client is None:
            try:
                from twilio.rest import Client  # type: ignore
                self._client = Client(self.account_sid, self.auth_token)
            except Exception as e:
                logger.error("twilio sdk import failed: %s", e)
                raise
        return self._client

    async def warm_transfer(
        self,
        *,
        call_sid: str,
        media_stream_url: str,
        agent_number: Optional[str] = None,
        reason: str = "verification_complete",
    ) -> dict:
        """
        Issue the REST `Calls.update` to swap the active TwiML. Returns
        a small dict for observability — not a full Twilio resource.
        """
        target = agent_number or self.agent_number
        if not target:
            raise ValueError("AGENT_PHONE_NUMBER not configured")
        if not (self.account_sid and self.auth_token):
            raise ValueError("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not configured")

        twiml = _build_handoff_twiml(
            agent_number=target,
            media_stream_url=media_stream_url,
        )

        client = self._get_client()
        # The twilio python sdk is sync; run it off the event loop.
        import asyncio
        def _do() -> dict:
            call = client.calls(call_sid).update(twiml=twiml)
            return {
                "sid": call.sid,
                "status": call.status,
                "to": target,
                "reason": reason,
            }

        result = await asyncio.to_thread(_do)
        logger.info("handoff: call %s → agent %s (reason=%s)", call_sid, target, reason)
        return result
