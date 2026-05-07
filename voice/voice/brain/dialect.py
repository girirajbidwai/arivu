"""
Dialect classifier — lexical markers + LID hint.

For the prototype we use a deterministic rule list (markers loaded from
`dialect_markers.json`) optionally augmented by a Sarvam LID call. The
heuristic is honest about its limits and we surface confidence so the
agent dashboard can downgrade trust on low-confidence dialect tags.

A fine-tuned wav2vec2 dialect head is the Phase 2 path; the classifier
interface here is exactly what that head would slot into.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Optional

from .schemas import Dialect, DialectResult

logger = logging.getLogger("arivu.brain.dialect")

_MARKERS_PATH = Path(__file__).parent / "dialect_markers.json"


class DialectClassifier:
    def __init__(self, markers_path: Optional[Path] = None) -> None:
        path = markers_path or _MARKERS_PATH
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Compile patterns once per dialect for hot-path speed.
        self._dialects: dict[str, dict] = {}
        for dialect, cfg in data.items():
            if dialect.startswith("_"):
                continue
            compiled = []
            for m in cfg["markers"]:
                compiled.append({
                    "pattern": m["pattern"],
                    "weight": float(m["weight"]),
                    "note": m.get("note", ""),
                })
            self._dialects[dialect] = {
                "weight_floor": float(cfg.get("weight_floor", 0.10)),
                "markers": compiled,
            }
        logger.info(
            "dialect classifier ready — %d dialects, %d total markers",
            len(self._dialects),
            sum(len(c["markers"]) for c in self._dialects.values()),
        )

    @staticmethod
    def _normalise(text: str) -> str:
        # NFC keeps Kannada akshara order stable for substring match.
        return unicodedata.normalize("NFC", text or "").lower()

    def classify(self, transcript: str, lid_hint: Optional[str] = None) -> DialectResult:
        """
        Classify a single utterance. Pure function — no I/O. The LID hint
        is an optional language code coming back from Sarvam Saaras; we
        only use it to nudge confidence, never to override the markers.
        """
        if not transcript or not transcript.strip():
            return DialectResult(dialect="unknown", confidence=0.0)

        text = self._normalise(transcript)
        scores: dict[str, float] = {}
        matched: dict[str, list[str]] = {}

        for dialect, cfg in self._dialects.items():
            score = 0.0
            hits: list[str] = []
            for marker in cfg["markers"]:
                if marker["pattern"].lower() in text:
                    score += marker["weight"]
                    hits.append(marker["pattern"])
            scores[dialect] = score
            matched[dialect] = hits

        # Pick winner. Cap score at 1.0; require it to clear the floor.
        if not scores:
            return DialectResult(dialect="unknown", confidence=0.0)

        winner = max(scores, key=scores.get)
        raw = min(scores[winner], 1.0)
        floor = self._dialects[winner]["weight_floor"]

        if raw < floor:
            return DialectResult(dialect="unknown", confidence=raw, markers_matched=[])

        # Confidence — soft normalisation against the runner-up so we
        # don't overstate certainty when two dialects tie.
        runner_up = sorted(scores.values(), reverse=True)
        margin = runner_up[0] - (runner_up[1] if len(runner_up) > 1 else 0.0)
        confidence = min(1.0, raw + 0.5 * margin)

        return DialectResult(
            dialect=winner,  # type: ignore[arg-type]
            confidence=round(confidence, 3),
            markers_matched=matched[winner],
        )
