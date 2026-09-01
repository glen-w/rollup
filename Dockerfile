FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

ARG INSTALL_LLM=0
RUN if [ "$INSTALL_LLM" = "1" ]; then \
        pip install --no-cache-dir ".[web,llm]"; \
    else \
        pip install --no-cache-dir ".[web]"; \
    fi

RUN useradd --create-home --uid 1000 rollup \
    && mkdir -p /data/config /data/state /data/output /data/logs /data/mail \
    && chown -R rollup:rollup /data

USER rollup

VOLUME ["/data/config", "/data/state", "/data/output", "/data/logs", "/data/mail"]

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8765/rollups || exit 1

ENTRYPOINT ["rollup"]
CMD ["--config", "/data/config/config.toml", "web", "--host", "0.0.0.0", "--allow-non-loopback-bind", "--port", "8765", "--mail-root", "/data/mail", "--root", "/data/mail/Newsletters.sbd", "--state-dir", "/data/state", "--output-dir", "/data/output", "--log-dir", "/data/logs"]
