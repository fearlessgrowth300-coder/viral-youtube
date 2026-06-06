FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CLIPPER_DATA_DIR=/data \
    PIPER_MODEL_PATH=/opt/piper-voices/en_US-libritts-high.onnx \
    PORT=10000

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir ".[ai,local-voice,publish,youtube-download]" \
    && mkdir -p /opt/piper-voices \
    && python -m piper.download_voices en_US-libritts-high --download-dir /opt/piper-voices

RUN useradd --create-home --uid 10001 clipper \
    && mkdir -p /data \
    && chown -R clipper:clipper /data

USER clipper

EXPOSE 10000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl --fail --silent "http://127.0.0.1:${PORT}/healthz" > /dev/null || exit 1

CMD ["sh", "-c", "python -m clip_agent web --host 0.0.0.0 --port ${PORT:-10000}"]
