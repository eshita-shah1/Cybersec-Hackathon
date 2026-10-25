# D3COY Security Monitor — Frontend

A React + Vite dashboard for the D3COY AI-agent identity-drift detection backend. It is a
**read-and-control console over the real backend**: every security decision, risk score and
execution outcome it shows comes from the gateway's API or WebSocket. It never recomputes risk,
never fakes events, and never talks to the sandbox executor or the LLM directly.

## Requirements

- Node.js 18+ (built and tested on Node 24).
- The D3COY backend in `../d3coy-backend` (gateway on :8000, sandbox executor on :8100).

## Install & run

```powershell
cd F:\Eshita\Projects\Cybersec_DJ\frontend
npm install
Copy-Item .env.example .env   # optional; defaults to http://127.0.0.1:8000
npm run dev                   # http://localhost:5173
```

Other commands: `npm run build`, `npm run preview`, `npm test`.

## Environment

`frontend/.env` (all optional):

```env
VITE_API_BASE_URL=http://127.0.0.1:8000      # gateway base URL
# VITE_WS_URL=ws://127.0.0.1:8000/ws/events  # otherwise derived from the base URL
```

The frontend holds **no** secrets. There is no Gemini key, signing key or agent token here. The
admin key is entered at runtime (Admin login), kept in memory for the tab only, sent as the
`X-API-Key` header, and clearable from the top bar or Settings.

## Start the backend (two terminals, from `../d3coy-backend`)

Set `PERMISSION_SIGNING_KEY` and `ADMIN_API_KEY` to the **same values in both** terminals (they
are already in the backend `.env`).

```powershell
uvicorn sandbox_executor.main:app --port 8100   # sandbox tool executor (real dummy data)
uvicorn app.main:app --port 8000                # D3COY gateway
```

Reseed the demo databases if needed: `python scripts\seed_data.py --reset --yes`.

## Demonstrating D3COY

1. Open **Overview** — confirm Gateway / Executor / Live pills are green and the metrics load.
2. **Scenarios** → Admin login → run `unauthorized_delete`. Watch events appear in **Activity
   Monitor** (the blocked delete shows `BLOCK` / `NOT_EXECUTED`; open it to see policy, detector
   signals and the audit trail).
3. **Live Agent Demo** — run the real Gemini agent in a third terminal:
   `python -m agent.run_procurement_demo`. Its tool calls stream into the live table; reads are
   allowed, the send is held for **Review**, delete/export are blocked.
4. **Human Reviews** → Admin login → approve the held send. The UI shows whether it was executed
   or blocked on revalidation — approval and execution are reported separately.
5. Refresh the page; persisted data is still there (it comes from the backend, not React state).

## Backend contract this UI uses

| Area | Method · path | Auth |
|---|---|---|
| Health / status | `GET /health`, `GET /api/system/status` | none |
| Analytics | `GET /api/analytics/overview` | none |
| Agents | `GET /api/agents`, `GET /api/agents/{id}` | none |
| Agent lifecycle | `POST /api/agents/{id}/suspend|reactivate|deactivate` | `X-API-Key` |
| Events | `GET /api/events` (paginated/filtered), `GET /api/events/{id}` | none |
| Alerts | `GET /api/alerts`, `GET /api/alerts/{id}`, `POST /api/alerts/{id}/review` | review = `X-API-Key` |
| Reviews | `GET /api/reviews/pending`, `POST /api/reviews/{id}/approve|reject` | `X-API-Key` |
| Scenarios | `GET /api/scenarios`, `POST /api/scenarios/{id}/run` | run = `X-API-Key` |
| Live events | `WS /ws/events` (envelope `{type,version,sent_at,data}`) | none |

Decision values: `ALLOW · MONITOR · REVIEW · BLOCK`. Execution values include `SUCCEEDED ·
NOT_EXECUTED · AWAITING_REVIEW · FAILED`. Decision and execution are always shown separately.

## Troubleshooting

- **"Unable to reach the D3COY gateway."** — start `uvicorn app.main:app --port 8000`, or fix
  `VITE_API_BASE_URL`. Overview shows a disconnected state rather than a false "secure" badge.
- **Admin actions return "not authorized"** — click **Admin login** and enter the backend
  `ADMIN_API_KEY`.
- **CORS errors in the console** — the gateway allows `http://localhost:5173` and
  `http://127.0.0.1:5173` by default (`CORS_ORIGINS` in the backend `.env`). Open the app on one
  of those origins, or add yours to `CORS_ORIGINS` and restart the gateway.
- **Live pill stuck on "Connecting"** — the WebSocket URL is derived from `VITE_API_BASE_URL`;
  set `VITE_WS_URL` explicitly if your gateway is reachable at a different host.
- **Sandbox tools fail / executor pill red** — start the executor on :8100 with the same
  `PERMISSION_SIGNING_KEY` as the gateway.

## Notes

- One shared WebSocket connection (reconnects with backoff, dedupes events by `event_id`).
- The backend is the source of truth; the browser never contacts the executor or the LLM.
- This is a local prototype console, not production user authentication.
