# Arivu — AI for the 1092 Helpline

> **Understand first. Respond right.**

Arivu is an AI-assisted voice-to-voice layer for the Karnataka 1092 helpline. 
It acts as an intelligent intermediary that transcribes speech, identifies dialects, and paraphrases issues back to citizens in their own register for verification. Once verified, it provides human officers with a concise, accurate summary.

### Key Capabilities:
- **Dialect-Aware Verification**: Responds to citizens in their specific Kannada dialect/register to build trust.
- **Sentiment & Urgency Detection**: Real-time analysis of the caller's state to prioritize critical cases.
- **Safety Triggers**: Detects distress signals (whispers, unexplained silences) and transitions to a silent translator mode for covert human intervention.
- **Correction Loop**: Human-in-the-loop dashboard allows officers to correct AI summaries, improving future model accuracy.

---

## System Architecture

The project is composed of three primary services:

1.  **Voice Service (`voice/`)**: Handles the real-time audio pipeline, including STT (Speech-to-Text), TTS (Text-to-Speech), and the core "Brain" logic with safety triggers.
2.  **Dashboard API (`dashboard-api/`)**: A FastAPI hub that manages WebSocket connections, Supabase persistence, and correction forwarding.
3.  **Agent Dashboard (`dashboard-web/`)**: A Next.js console for human officers to view live transcripts, analysis, and manage active calls.

---

## Quick Start

### 1. Prerequisites
- Python 3.11+
- Node.js (pnpm recommended)
- `uv` (Fast Python package manager)

### 2. Installation
```bash
# Python Services (voice + brain + dashboard-api)
cd voice          && uv pip install -e ".[dev]"
cd dashboard-api  && uv pip install -e ".[dev]"

# Frontend
cd dashboard-web  && pnpm install
```

### 3. Configuration
Copy the environment template and fill in your API keys (Sarvam, Gemini, Supabase, Twilio):
```bash
cp .env.example .env
```

### 4. Running Locally
Run all services in parallel using the provided Makefile:
```bash
make dev
```

---

## Service Endpoints

Once running, the following endpoints are available:

| Service | URL |
|---|---|
| **Voice Health** | `http://localhost:8000/health` |
| **Web Turn (Audio)** | `http://localhost:8000/web/turn` |
| **Brain Health** | `http://localhost:8002/health` |
| **Dashboard API** | `http://localhost:8001/health` |
| **Agent Console** | `http://localhost:3000` |
| **Mic Demo Path** | `http://localhost:3000/talk` |

---

## Testing & Quality Assurance

To run the full suite of logic tests (FSM, dialect classifier, safety triggers):
```bash
make test
```

---

## Prototype Scope

The current prototype demonstrates the full end-to-end citizen-to-agent journey:
1.  **Incoming Call**: Citizen speaks Kannada; Arivu responds with verification in the matching dialect.
2.  **Live Analysis**: Dashboard updates instantly with transcript, sentiment, and urgency.
3.  **Confirmation & Handoff**: Agent receives the call with full context after citizen confirmation.
4.  **Covert Mode**: Specialized triggers for high-stress/whisper scenarios.

**Understand first. Respond right.**
