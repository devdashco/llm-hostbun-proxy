FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml claudectl_server.py _auth.py _e2e_probe.py entrypoint.sh ./
RUN uv pip install --system "mcp[cli]>=1.3.0" "httpx>=0.27" "starlette>=0.40" "uvicorn[standard]>=0.30"
RUN chmod +x entrypoint.sh
ENV PORT=8000 CLAUDECTL_TRANSPORT=http
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=8s --start-period=40s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1
CMD ["./entrypoint.sh"]
