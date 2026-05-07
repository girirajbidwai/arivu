"""
Lightweight in-memory latency tracker.

Every turn the SSE/WS handler records the per-stage timings here. A rolling
window (last 100 turns) is kept so the demo can display p50 / p95 live.

Reading: /health/latency. Writing: telemetry.record(...).

Why not Prometheus / OpenTelemetry: this is the prototype. One file, no
deps, nothing to deploy. Promote to OTel when we move to production.
"""

from __future__ import annotations

import statistics
import threading
import time
from collections import deque
from typing import Optional


class LatencyTracker:
    def __init__(self, window: int = 100) -> None:
        self._window = window
        self._lock = threading.Lock()
        # Rolling samples per stage name → deque of float ms
        self._samples: dict[str, deque[float]] = {}
        self._turns: int = 0
        self._sub_1s_hits: int = 0
        self._started_at: float = time.time()
        self._last_providers: dict[str, str] = {}

    def record(self, latency: dict[str, float], *, providers: Optional[dict[str, str]] = None) -> None:
        """Append one turn's measurements (stage_ms keys) to the rolling window."""
        with self._lock:
            self._turns += 1
            for stage, ms in (latency or {}).items():
                if not isinstance(ms, (int, float)):
                    continue
                buf = self._samples.setdefault(stage, deque(maxlen=self._window))
                buf.append(float(ms))
            # Track sub-1s success rate
            total = latency.get("total_ms", 0)
            if isinstance(total, (int, float)) and total > 0 and total < 1000:
                self._sub_1s_hits += 1
            if providers:
                self._last_providers.update(providers)

    def snapshot(self) -> dict:
        """Return p50/p95/avg per stage + turn count + uptime."""
        with self._lock:
            stages = {}
            for stage, buf in self._samples.items():
                if not buf:
                    continue
                vals = list(buf)
                vals.sort()
                stages[stage] = {
                    "n": len(vals),
                    "p50_ms": round(statistics.median(vals), 1),
                    "p95_ms": round(_percentile(vals, 0.95), 1),
                    "avg_ms": round(statistics.mean(vals), 1),
                    "min_ms": round(vals[0], 1),
                    "max_ms": round(vals[-1], 1),
                }
            sub_1s_rate = (
                round(self._sub_1s_hits / self._turns, 3)
                if self._turns > 0 else 0.0
            )
            return {
                "turns": self._turns,
                "sub_1s_count": self._sub_1s_hits,
                "sub_1s_rate": sub_1s_rate,
                "uptime_s": round(time.time() - self._started_at, 1),
                "window": self._window,
                "stages": stages,
                "last_providers": dict(self._last_providers),
            }


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = max(0, min(len(sorted_vals) - 1, int(round(p * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


# Module-level singleton.
tracker = LatencyTracker()
