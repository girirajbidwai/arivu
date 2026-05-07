"""
Sarvam Mayura — translation wrapper used by interpreter mode.

When the call has been warm-transferred, Arivu stays on the line as a
silent translator: agent says X in Hindi, citizen hears X in their
Kannada dialect, and vice versa. The endpoint is one HTTPS POST per
utterance; we reuse the same shared httpx client as the LLM path for
warm TLS.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Literal

import httpx

from .llm import get_shared_client

logger = logging.getLogger("arivu.brain.interpreter")

SARVAM_TRANSLATE_PATH = "/translate"

LangCode = Literal["kn-IN", "hi-IN", "en-IN"]


class Interpreter:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "api-subscription-key": api_key,  # legacy header, harmless if ignored
        }

    async def translate(
        self,
        text: str,
        *,
        source: LangCode,
        target: LangCode,
        mode: str = "formal",
    ) -> str:
        """One-shot translation. Returns text in the target language."""
        if not text.strip():
            return ""
        if source == target:
            return text

        client = await get_shared_client()
        body = {
            "input": text,
            "source_language_code": source,
            "target_language_code": target,
            "mode": mode,                # formal | classic-colloquial | modern-colloquial
            "model": "mayura:v1",
            "enable_preprocessing": True,
        }
        t0 = time.perf_counter()
        try:
            r = await client.post(SARVAM_TRANSLATE_PATH, json=body, headers=self._headers)
            r.raise_for_status()
            data = r.json()
            translated = data.get("translated_text") or data.get("output") or ""
            dt = (time.perf_counter() - t0) * 1000
            logger.debug("mayura ok in %.0fms (%d→%d chars)",
                         dt, len(text), len(translated))
            return translated
        except (httpx.HTTPError, KeyError) as e:
            logger.error("mayura call failed: %s", e)
            # On failure, surface the original text — better than silence.
            return text
