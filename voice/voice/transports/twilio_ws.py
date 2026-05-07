"""
Twilio Media Stream transport — thin adapter over Pipecat's
TwilioFrameSerializer.

The voice pipeline reads μ-law 8kHz frames in and writes μ-law 8kHz
frames out. This module just constructs the right Pipecat WebSocket
transport with the right parameters.

When we move to Exotel, only this module changes. Everything downstream
(VAD → STT → Brain → TTS) stays identical.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("arivu.transports.twilio")


@dataclass
class TwilioStartFrame:
    """Decoded payload of Twilio's `start` event — what we need from it."""
    stream_sid: str
    call_sid: str
    account_sid: str
    from_number: str = ""
    to_number: str = ""


def parse_twilio_start(message: dict) -> Optional[TwilioStartFrame]:
    """Extract the bits we care about from Twilio's start event."""
    if message.get("event") != "start":
        return None
    start = message.get("start", {})
    if not start:
        return None
    return TwilioStartFrame(
        stream_sid=start.get("streamSid", ""),
        call_sid=start.get("callSid", ""),
        account_sid=start.get("accountSid", ""),
        from_number=start.get("customParameters", {}).get("From", ""),
        to_number=start.get("customParameters", {}).get("To", ""),
    )


def build_twiml(*, ws_url: str, greet_with: Optional[str] = None) -> str:
    """
    Build the TwiML returned to Twilio when a call comes in.
    `<Connect><Stream/></Connect>` ties the call to our WebSocket.
    """
    greet = (
        f'  <Say language="kn-IN">{greet_with}</Say>\n' if greet_with else ""
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Response>\n'
        f'{greet}'
        '  <Connect>\n'
        f'    <Stream url="{ws_url}">\n'
        '      <Parameter name="service" value="arivu" />\n'
        '    </Stream>\n'
        '  </Connect>\n'
        '</Response>'
    )


def build_pipecat_transport(serializer=None, vad_analyzer=None):
    """
    Lazy import + construction of the Pipecat WebSocketServerTransport.
    Returns `None` if Pipecat isn't installed (lets the rest of the
    service start in dev / test environments).
    """
    try:
        from pipecat.serializers.twilio import TwilioFrameSerializer
        from pipecat.transports.network.websocket_server import (
            WebSocketServerTransport,
            WebSocketServerParams,
        )
    except ImportError as e:
        logger.warning("pipecat not installed: %s — transport unavailable", e)
        return None

    return WebSocketServerTransport(
        params=WebSocketServerParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            serializer=serializer or TwilioFrameSerializer(),
            vad_enabled=True,
            vad_analyzer=vad_analyzer,
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
        )
    )
