"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setStatus("sending");
    setError(null);
    try {
      await api.loginMagicLink(email);
      setStatus("sent");
    } catch (err: any) {
      setStatus("error");
      setError(err.message || "Failed to send magic link.");
    }
  }

  return (
    <main className="grid min-h-screen place-items-center bg-warmdark text-[#EFE9DB]">
      <div className="w-full max-w-sm space-y-6 px-6">
        <div className="text-center">
          <h1 className="font-display text-4xl text-gold">Arivu</h1>
          <p className="font-mono text-[10px] tracking-widest text-muted">
            1092 HELPLINE CONSOLE
          </p>
        </div>

        <form onSubmit={submit} className="space-y-3">
          <input
            type="email"
            required
            placeholder="agent@karnataka.gov.in"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-md border border-warmsoft bg-warmsoft/40 px-3 py-2 text-sm focus:border-gold focus:outline-none"
          />
          <button
            type="submit"
            disabled={status === "sending"}
            className="w-full rounded-md bg-gold px-3 py-2 font-mono text-sm tracking-widest text-warmdark hover:opacity-90 disabled:opacity-50"
          >
            {status === "sending" ? "SENDING…" : "SEND MAGIC LINK"}
          </button>
        </form>

        {status === "sent" && (
          <div className="rounded-md border border-sage/40 bg-sage/10 p-3 font-mono text-[11px] text-sage">
            Magic link sent. Check your inbox.
          </div>
        )}
        {status === "error" && error && (
          <div className="rounded-md border border-red-400/40 bg-red-500/10 p-3 font-mono text-[11px] text-red-300">
            {error}
          </div>
        )}

        <button
          onClick={() => router.push("/queue")}
          className="w-full font-mono text-[11px] tracking-widest text-muted hover:text-gold"
        >
          continue without login →
        </button>

        <p className="text-center font-mono text-[10px] tracking-widest text-muted/60">
          Understand first. Respond right.
        </p>
      </div>
    </main>
  );
}
