#!/usr/bin/env bash
# start_render_free.sh — single-service launcher for Render's FREE tier.
#
# Render free has no private services, so the sandbox executor runs here as an internal
# subprocess on loopback (127.0.0.1:8100). The gateway reaches it via EXECUTOR_URL, and both
# processes share PERMISSION_SIGNING_KEY from the same service environment — so the signed,
# single-use permission slips still verify exactly as in the multi-container deployment.
set -euo pipefail

export EXECUTOR_URL="http://127.0.0.1:8100"
export SANDBOX_DATABASE_URL="${SANDBOX_DATABASE_URL:-sqlite:////tmp/sandbox.db}"

# The in-process live agent calls this gateway's own API over loopback.
export SELF_BASE_URL="http://127.0.0.1:${PORT}"

echo "[start] launching sandbox executor on 127.0.0.1:8100"
uvicorn sandbox_executor.main:app --host 127.0.0.1 --port 8100 &
EXECUTOR_PID=$!

# If the executor dies, take the whole service down so Render restarts it cleanly.
trap 'kill "$EXECUTOR_PID" 2>/dev/null || true' EXIT

# Give the executor a moment to bind before the gateway starts accepting tool calls.
sleep 2

echo "[start] launching D3COY gateway on 0.0.0.0:${PORT}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
