FROM python:3.13-slim

WORKDIR /app

# Install system deps for audio processing
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip uv

COPY pyproject.toml uv.lock /app/
COPY voice /app/voice
COPY tests /app/tests

RUN uv pip install --system -e ".[dev]"

EXPOSE 8000

CMD ["uvicorn", "voice.main:app", "--host", "0.0.0.0", "--port", "8000"]
