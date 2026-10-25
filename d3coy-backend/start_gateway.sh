#!/usr/bin/env bash
# start_gateway.sh — Render startup script for the D3COY gateway.
#
# Why this script exists:
#   The Render Blueprint can inject the executor's private host:port via
#   EXECUTOR_HOSTPORT (fromService property: hostport), but the gateway's
#   ExecutorClient expects a full URL like "http://host:port".
#   This script builds EXECUTOR_URL from EXECUTOR_HOSTPORT and then starts
#   uvicorn on the port Render assigns via $PORT.
#
# Also sets SELF_BASE_URL from RENDER_EXTERNAL_URL so the AgentRunner
# can call back to the gateway's public URL.
set -euo pipefail

# Build executor URL from private host:port injected by Render.
if [ -n "${EXECUTOR_HOSTPORT:-}" ]; then
  export EXECUTOR_URL="http://${EXECUTOR_HOSTPORT}"
  echo "[start_gateway] EXECUTOR_URL resolved to: ${EXECUTOR_URL}"
else
  # Fallback: use the explicit EXECUTOR_URL if someone set it manually.
  echo "[start_gateway] EXECUTOR_HOSTPORT not set; using EXECUTOR_URL=${EXECUTOR_URL:-not set}"
fi

# SELF_BASE_URL: the gateway calls its own /api/events for the live agent demo.
# Render sets RENDER_EXTERNAL_URL = https://<service>.onrender.com
if [ -n "${RENDER_EXTERNAL_URL:-}" ]; then
  export SELF_BASE_URL="${RENDER_EXTERNAL_URL}"
  echo "[start_gateway] SELF_BASE_URL resolved to: ${SELF_BASE_URL}"
fi

echo "[start_gateway] Starting uvicorn on 0.0.0.0:${PORT}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
