# Deploying D3COY (Docker Compose)

This runs the whole stack in three containers:

| Service | Container | Published port | Role |
|---|---|---|---|
| `executor` | sandbox tool executor | 8100 | runs real tools against a dummy DB; holds the signing key |
| `gateway` | D3COY FastAPI gateway | 8000 | the security pipeline + REST/WebSocket API |
| `frontend` | nginx serving the React build | 8080 | the dashboard the browser opens |

The browser talks **only** to the gateway (`:8000`) and loads the UI from `:8080`. The executor
is internal (its port is published only so the dashboard can show the sandbox state). Secrets
stay in the gateway/executor environment — never in the frontend bundle.

## Prerequisites

- Docker Desktop (or Docker Engine) running, with Compose v2.
- Internet access for the first build (downloads PyTorch, the MiniLM model, and npm/pip deps).
- First build is large (~3–4 GB image) because of the ML stack; it is cached after that.

## Steps

```powershell
cd F:\Eshita\Projects\Cybersec_DJ

# 1. configuration
Copy-Item .env.deploy.example .env
# generate a signing key and paste it into .env as PERMISSION_SIGNING_KEY:
python -c "import secrets; print(secrets.token_hex(32))"
# leave ADMIN_API_KEY BLANK for open mode (no login — anyone can run everything);
# optionally set GEMINI_API_KEY for the live agent.

# 2. build and start
docker compose up --build        # add -d to run in the background

# 3. open the dashboard
#    http://localhost:8080
#    (gateway API/docs at http://localhost:8000/docs)
```

Stop with `docker compose down` (add `-v` to also delete the SQLite volumes and start fresh).

## Using it

1. Open http://localhost:8080 — the Overview pills (Gateway / Executor / Live) should be green.
2. **Open mode (default):** `ADMIN_API_KEY` is blank, so the top bar shows "Open mode" and no
   login is needed — every action is available. To lock it down, set `ADMIN_API_KEY` in `.env`,
   `docker compose up -d`, then use **Admin login** with that key.
3. **Scenarios** → run one (e.g. `unauthorized_delete`) and watch events appear.
4. **Live Agent Demo** → **Run procurement task** (needs `GEMINI_API_KEY` set) — the agent runs
   inside the gateway container and streams its actions live. No terminal command needed.

## How configuration maps in

- `PERMISSION_SIGNING_KEY` is passed to **both** gateway and executor (they must match, or the
  executor rejects every tool call).
- Gateway → executor over the compose network at `http://executor:8100` (`EXECUTOR_URL`).
- The in-process agent calls the gateway at `http://localhost:8000` inside its own container
  (`SELF_BASE_URL`).
- `VITE_API_BASE_URL` is baked into the frontend at build time, so the browser calls
  `http://localhost:8000`; the WebSocket URL is derived from it. `CORS_ORIGINS` must include the
  frontend origin (`http://localhost:8080`).
- SQLite data persists in the `gateway-data` and `executor-data` volumes. The trained behavioral
  baseline and the semantic model are baked into the image, so startup needs no network.

## Deploying to a server / different host

Serving to other machines means the browser no longer uses `localhost`. Rebuild the frontend
with the gateway's public URL and allow that origin:

```powershell
# in .env
VITE_API_BASE_URL=https://d3coy.example.com:8000
CORS_ORIGINS=https://d3coy.example.com:8080
docker compose up --build
```

For public internet exposure you would additionally: put the gateway and frontend behind a TLS
reverse proxy (so the WebSocket uses `wss://`), set strong `ADMIN_API_KEY`/`PERMISSION_SIGNING_KEY`,
and keep the executor off the public network. This remains a prototype — see the backend README
§11 for the security limitations (per-analyst auth, process isolation, tamper-evident audit).

## Troubleshooting

- **Gateway unhealthy at first** — it loads the semantic model on boot (~15–30 s). Compose waits
  for the executor to be healthy before starting the gateway; give the gateway a moment too.
- **Executor pill red / tool calls fail** — `PERMISSION_SIGNING_KEY` differs between services or
  is unset. It is one value in `.env`, passed to both.
- **CORS errors** — `CORS_ORIGINS` must list the exact frontend origin, and `VITE_API_BASE_URL`
  must be the URL the browser can reach the gateway at. Rebuild the frontend after changing it.
- **"Run agent" greyed out** — set `GEMINI_API_KEY` (or `ANTHROPIC_API_KEY`) in `.env` and
  `docker compose up -d` again.
