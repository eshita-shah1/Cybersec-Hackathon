# Deploying D3COY free on Render (no credit card)

This gives you a public, always-reachable URL (e.g. `https://d3coy-frontend.onrender.com`) to
put in your competition slides — on Render's **free** tier, **no credit card required**.

It uses the `render.yaml` Blueprint at the repo root, which creates **two free services**:

| Service | Type | What it is |
|---|---|---|
| `d3coy-gateway` | Web (free) | FastAPI gateway **+** sandbox executor in one process |
| `d3coy-frontend` | Static site | The React dashboard (served from Render's CDN, always on) |

**The URL you put in your PPT is the `d3coy-frontend` one.**

## What runs — and the honest trade-offs of the free tier

The free gateway has only 512 MB RAM, so it installs `requirements-lite.txt` (no PyTorch).
Everything still works — the four detection layers, policy enforcement, signed permission
slips, scenarios, reviews, audit, the live WebSocket feed, and the live Gemini agent — with
**one** change: the semantic detector runs in its **lexical** mode (token overlap) instead of
the transformer. (Verified: the `semantic_mismatch` and `unauthorized_delete` scenarios still
reach their expected decisions.)

Expected free-tier behaviour (not bugs):
- **Cold start:** the gateway sleeps after ~15 min idle; the first click wakes it (~30–60 s).
  Open your URL a minute *before* you present so it's warm.
- **Data resets** on each deploy/restart (no persistent disk on free) — fine for a demo.
- Want the full transformer semantic layer? That needs ~2 GB RAM → deploy `render-paid.yaml`
  on a paid plan instead (a credit card is required for that).

## Steps

1. **Sign up** at <https://render.com> with your GitHub account (no card needed for free).
2. Dashboard → **New +** → **Blueprint**.
3. **Connect** the repo **`eshita-shah1/Cybersec-Hackathon`**. Render finds `render.yaml` and
   shows the two services.
4. Render will prompt for the two `sync: false` values:
   - **`ADMIN_API_KEY`** — **leave it BLANK** for open mode (judges can run everything with no
     login). *(Or type a strong value to require the Admin login in the dashboard.)*
   - **`GEMINI_API_KEY`** — paste your Google AI Studio key to enable the **Live Agent Demo**
     button, or leave blank to disable just that one feature.
   - `PERMISSION_SIGNING_KEY` is generated automatically — nothing to enter.
5. Click **Apply** / **Create**. First build takes a few minutes (installing deps + npm build).
6. When both services are **live**, open the **`d3coy-frontend`** URL. The Overview pills should
   go green once the gateway finishes waking. Run a **Scenario** to confirm end-to-end.

   **That frontend URL is what goes in your slides.**

## Notes

- The browser talks only to the gateway over HTTPS; the gateway talks to the executor over
  loopback inside its own container. The Gemini key and signing key never reach the browser.
- If the live agent ever errors with memory pressure on the 512 MB box, it's the one feature
  that's tight on free — the scenarios are the reliable core of the demo.
- To redeploy after pushing changes: Render auto-deploys on each push to `main`, or click
  **Manual Deploy** in the dashboard.

## Troubleshooting

- **Frontend loads but pills are red / "disconnected"** — the gateway is still cold-starting;
  wait ~60 s and retry. Check the `d3coy-gateway` logs in the Render dashboard.
- **Gateway "Exited" / out of memory** — the free box is 512 MB. Make sure it's using
  `requirements-lite.txt` (it is, per `render.yaml`); if you enabled the live agent and it
  OOMs during a run, remove `GEMINI_API_KEY` to disable that one feature.
- **CORS error in the browser console** — the gateway's `CORS_ORIGINS` is wired to the
  frontend's URL automatically by the Blueprint; if you renamed a service, redeploy so the
  reference resolves.
