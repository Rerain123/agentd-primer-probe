FROM python:3.12-slim

LABEL org.opencontainers.image.title="Agent D 引物探针服务" \
      org.opencontainers.image.version="1.2.15" \
      org.opencontainers.image.description="OligoSmith 可流式传输 HTTP MCP 服务"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    AGENTD_PROJECT_ROOT=/app \
    AGENTD_STATE_DIR=/data/jobs \
    BLASTDB=/data/blastdb

ARG AGENTD_PYPI_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

RUN sed -i "s|deb.debian.org|mirrors.aliyun.com|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ncbi-blast+ mafft fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY config ./config
COPY schemas ./schemas
COPY src ./src

RUN pip install --index-url "${AGENTD_PYPI_INDEX_URL}" --no-cache-dir ".[scientific,api,mcp]" \
    && useradd --create-home --uid 10001 agentd \
    && mkdir -p /data/jobs /data/blastdb \
    && chown -R agentd:agentd /data

USER agentd
EXPOSE 8000
VOLUME ["/data/jobs", "/data/blastdb"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["uvicorn", "agentd.mcp_server:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
