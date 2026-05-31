# Portable Linux container for AIO. Gives the agent a full Linux toolchain
# (git, ripgrep, build tools) and the POSIX sandbox that Windows can't provide,
# while mounting YOUR project read-write at /workspace.
#
# Quick use (from your project folder):
#   docker run --rm -it -p 8765:8765 -v "$PWD:/workspace" \
#       -e ANTHROPIC_API_KEY=sk-... ghcr.io/001100alfa/aio
# or just: run-docker.bat   (Windows)  /  ./run-docker.sh  (mac/Linux)
FROM python:3.12-slim

# Common tools the coding agent reaches for. Kept lean; apt lists removed.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git ripgrep curl ca-certificates procps \
    && rm -rf /var/lib/apt/lists/*

# Install AIO itself (zero runtime deps; this is just the package).
WORKDIR /opt/aio
COPY . .
RUN pip install --no-cache-dir .

# The agent operates here; bind-mount your project onto it at run time.
RUN mkdir -p /workspace
WORKDIR /workspace

# git refuses to operate in a bind-mounted dir owned by another uid; allow it.
RUN git config --system --add safe.directory '*'

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/health',timeout=2).status==200 else 1)"

# Serve the dashboard, operating on the mounted project. Auth token is printed
# to the logs on startup (or set AIO_WEB_TOKEN). Pass extra flags after the image
# name, e.g. `-p ollama` to use a local model.
ENTRYPOINT ["aio", "--web", "--host", "0.0.0.0", "--port", "8765", "-C", "/workspace"]
