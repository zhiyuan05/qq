# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.12

# ===== Build stage =====
FROM python:${PYTHON_VERSION}-slim AS builder

ARG INSTALL_VOICE=false

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

RUN python -m venv "$VIRTUAL_ENV"
WORKDIR /build
COPY requirements.txt requirements-voice.txt ./
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt \
    && if [ "$INSTALL_VOICE" = "true" ]; then \
        pip install --no-cache-dir --disable-pip-version-check -r requirements-voice.txt; \
    fi

# ===== Runtime stage =====
FROM python:${PYTHON_VERSION}-slim

ARG UID=1000
ARG GID=1000
ARG INSTALL_VOICE=false

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV HOME=/app/data/home
ENV XDG_CACHE_HOME=/app/data/cache
ENV TMPDIR=/app/data/tmp
ENV HF_HOME=/app/data/hf
ENV WHISPER_CACHE_DIR=/app/data/whisper

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid "$GID" chatbot \
    && useradd --uid "$UID" --gid "$GID" --home-dir /home/chatbot --create-home --shell /usr/sbin/nologin chatbot

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=chatbot:chatbot . .

RUN mkdir -p data/logs data/chroma data/learning data/chatbot data/home data/cache \
    data/media/tts data/media/outbound data/hf data/whisper data/tmp \
    && chown -R chatbot:chatbot /app/data /app/characters

USER chatbot:chatbot

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/v1/health/ready')" || exit 1

CMD ["python", "main.py"]
