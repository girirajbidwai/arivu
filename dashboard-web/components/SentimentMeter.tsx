"use client";
import type { Sentiment } from "@/lib/types";

const ladder: Sentiment[] = [
  "calm", "neutral", "confused", "anxious", "fearful", "distressed",
];

const colors: Record<Sentiment, string> = {
  calm:       "bg-sage",
  neutral:    "bg-muted",
  confused:   "bg-amber-400",
  anxious:    "bg-orange-400",
  fearful:    "bg-terracotta",
  distressed: "bg-red-500",
};

export function SentimentMeter({
  sentiment = "neutral", confidence = 0,
}: { sentiment?: Sentiment; confidence?: number }) {
  const idx = ladder.indexOf(sentiment);
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[11px] tracking-widest text-muted">SENTIMENT</span>
        <span className="font-display text-sm uppercase tracking-wide">{sentiment}</span>
      </div>
      <div className="flex h-1.5 gap-1">
        {ladder.map((s, i) => (
          <div
            key={s}
            className={`flex-1 rounded-full transition-all duration-300 ${
              i <= idx ? colors[s] : "bg-warmsoft"
            }`}
          />
        ))}
      </div>
      {confidence > 0 && (
        <div className="text-right font-mono text-[10px] text-muted/70">
          confidence {Math.round(confidence * 100)}%
        </div>
      )}
    </div>
  );
}
