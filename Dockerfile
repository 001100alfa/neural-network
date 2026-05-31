# Minimal container image for the AIO dashboard. Zero runtime dependencies, so
# the image is just Python + the package.
FROM python:3.12-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

# The dashboard binds inside the container; access is still protected by the
# per-session token (printed to the logs on startup, or set AIO_WEB_TOKEN).
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/health',timeout=2).status==200 else 1)"

# Provide a provider API key at runtime, e.g.:
#   docker run -e ANTHROPIC_API_KEY=sk-... -p 8765:8765 aio
ENTRYPOINT ["aio", "--web", "--host", "0.0.0.0", "--port", "8765"]
