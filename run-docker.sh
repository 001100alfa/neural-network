#!/usr/bin/env bash
# Run AIO in a Linux container against the CURRENT directory.
#
# The agent gets a full Linux toolchain (git, ripgrep, build tools) and the
# POSIX sandbox; your project is mounted read-write at /workspace. Provider keys
# in your environment (ANTHROPIC_API_KEY, OPENAI_API_KEY, ...) are passed through.
#
#   ./run-docker.sh                 # build if needed, then serve on :8765
#   ./run-docker.sh -p ollama       # extra flags are forwarded to `aio`
#   PORT=9000 ./run-docker.sh       # change the published port
set -euo pipefail

IMAGE="${AIO_IMAGE:-aio:local}"
PORT="${PORT:-8765}"
# Where to mount: the dir you run this from (your project), not AIO's source.
WORKSPACE="${AIO_WORKSPACE:-$PWD}"
# Locate AIO's own source (this script's dir) to build the image from.
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required. Install Docker Desktop and retry." >&2
  exit 1
fi

# Build once (or when AIO's source changes); cheap no-op when cached.
echo "Building $IMAGE from $SRC_DIR (first run only) ..."
docker build -t "$IMAGE" "$SRC_DIR"

# Forward any provider keys that are set, without requiring them.
ENVS=()
for k in ANTHROPIC_API_KEY OPENAI_API_KEY OPENROUTER_API_KEY GROQ_API_KEY \
         MISTRAL_API_KEY DEEPSEEK_API_KEY AIO_WEB_TOKEN; do
  [ -n "${!k:-}" ] && ENVS+=(-e "$k=${!k}")
done

echo "Serving AIO at http://localhost:${PORT}  (workspace: ${WORKSPACE})"
exec docker run --rm -it -p "${PORT}:8765" \
  -v "${WORKSPACE}:/workspace" "${ENVS[@]}" "$IMAGE" "$@"
