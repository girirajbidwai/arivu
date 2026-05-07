"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Live audio waveform visualizer.
 * Draws gold bars on a transparent canvas using the mic's AnalyserNode.
 */
export function CallWaveform({
  analyser,
  active,
}: {
  analyser: AnalyserNode | null;
  active: boolean;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef = useRef<number>(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !analyser) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const bufLen = analyser.frequencyBinCount;
    const data = new Uint8Array(bufLen);
    const BAR_W = 3;
    const GAP = 2;

    function draw() {
      if (!ctx || !canvas) return;
      rafRef.current = requestAnimationFrame(draw);

      analyser!.getByteFrequencyData(data);

      const w = canvas.width;
      const h = canvas.height;
      ctx.clearRect(0, 0, w, h);

      const barCount = Math.floor(w / (BAR_W + GAP));
      const step = Math.floor(bufLen / barCount);

      for (let i = 0; i < barCount; i++) {
        const val = active ? data[i * step] / 255 : 0;
        const barH = Math.max(2, val * h * 0.85);
        const x = i * (BAR_W + GAP);
        const y = (h - barH) / 2;

        ctx.fillStyle = active
          ? `rgba(232, 163, 61, ${0.4 + val * 0.6})`
          : "rgba(138, 131, 120, 0.3)";
        ctx.beginPath();
        ctx.roundRect(x, y, BAR_W, barH, 1.5);
        ctx.fill();
      }
    }

    draw();
    return () => cancelAnimationFrame(rafRef.current);
  }, [analyser, active]);

  return (
    <canvas
      ref={canvasRef}
      width={320}
      height={48}
      className="w-full max-w-xs h-12 opacity-90"
    />
  );
}
