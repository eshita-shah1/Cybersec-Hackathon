# Deploying D3COY to a public HTTPS URL (for the hackathon)

This puts D3COY on a small cloud VM behind **Caddy**, which gets a free, auto-renewing
**Let's Encrypt** certificate — so you get a real `https://…` link (with a working live
WebSocket) that you can paste into your slides and that stays up 24/7.

The UI and the API are served from **one origin**, so there is no CORS to configure and the
live feed uses `wss://` automatically. Only Caddy is exposed to the internet; the gateway,
executor and frontend run only on the internal Docker network.

```
Internet ──▶ Caddy :443 (TLS)
               ├── /api/*  /ws/*  /docs  ──▶ gateway:8000 ─┬─▶ executor:8100 (sandbox)
               └── everything else (SPA)  ──▶ frontend:80  └─▶ (Gemini, if configured)
```

Budget on **2–4 hours**, most of it the first image build (~3–4 GB) and one round of checking.

---

## What you need first

- A cloud account (DigitalOcean, AWS, Azure, GCP, Hetzner, …) and a card / free credits.
- About **US$6–12 / month** for the VM (or free trial credits). Destroy it after the hackathon.
- 10 free minutes to make a DuckDNS hostname (no purchase).

---

## Step 1 — Create the VM

Create a Linux VM (Ubuntu 22.04 or 24.04) with **at least 2 GB RAM** (4 GB is comfier — the
ML model needs ~1.5–2 GB). Examples: DigitalOcean "Basic 2 GB / 1 vCPU" droplet, AWS
`t3.small`, Hetzner CX22. Note its **public IPv4 address** — call it `SERVER_IP` below.

**Firewall / security group — open ONLY these inbound ports:**

| Port | Why |
|---|---|
| 22  | SSH (you) |
| 80  | HTTP → Caddy (needed for the TLS certificate challenge; it redirects to 443) |
| 443 | HTTPS → the app |

> Do **not** open 8000 / 8100 / 8080. They are never published to the host in prod, but keep
> them closed at the cloud firewall anyway. Set this at the **cloud provider's** firewall /
> security group (host `ufw` is unreliable with Docker).

---

## Step 2 — Point a free DuckDNS name at the VM

1. Go to <https://www.duckdns.org>, sign in (GitHub/Google), and create a subdomain,
   e.g. **`d3coy-demo`** → you get `d3coy-demo.duckdns.org`.
2. In the DuckDNS box for that subdomain, set the **current ip** to your `SERVER_IP` and save.
3. Check it resolves (from your laptop):
   ```powershell
   nslookup d3coy-demo.duckdns.org
   ```
   It must return `SERVER_IP` before you continue (Caddy can't get a cert otherwise).

This `*.duckdns.org` name is what goes in your PPT.

---

## Step 3 — Put the project on the VM and install Docker

SSH in: `ssh root@SERVER_IP` (or `ssh ubuntu@SERVER_IP`), then:

```bash
# Docker Engine + Compose plugin (official convenience script)
curl -fsSL https://get.docker.com | sh

# Get the project onto the VM. Easiest: push it to a Git repo and clone it here, e.g.
#   git clone https://github.com/<you>/Cybersec_DJ.git && cd Cybersec_DJ
# Or copy it from your laptop (run THIS on your laptop, not the VM):
#   scp -r F:\Eshita\Projects\Cybersec_DJ root@SERVER_IP:/root/Cybersec_DJ
```

Then on the VM, `cd` into the project folder.

> Don't copy your local `.env`, `node_modules`, or any SQLite `*.db` files up — create a fresh
> `.env` in the next step. (`.dockerignore` already keeps junk out of the images.)

---

## Step 4 — Configure `.env`

```bash
cp .env.deploy.example .env
nano .env
```

Fill in:

```ini
# Required — generate once:  python3 -c "import secrets; print(secrets.token_hex(32))"
PERMISSION_SIGNING_KEY=<paste 64 hex chars>

# Recommended for a PUBLIC url — a strong random value. This re-enables the admin login so
# random visitors can't run agents / approve reviews. (Leave BLANK only if you WANT the demo
# fully open to anyone — see "Open vs locked" below.)
ADMIN_API_KEY=<a strong password>

# Your public hostname — BOTH of these, same name:
PUBLIC_HOST=d3coy-demo.duckdns.org
PUBLIC_URL=https://d3coy-demo.duckdns.org

# Optional — the live agent demo:
GEMINI_API_KEY=<your gemini key>
D3COY_AGENT_PROVIDER=gemini
D3COY_AGENT_MODEL=gemini-3.8-flash
```

Leave the local-only `VITE_API_BASE_URL` / `CORS_ORIGINS` lines as they are — the prod compose
file ignores them and uses `PUBLIC_URL` instead.

---

## Step 5 — Build and start

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

The first build is long (downloads PyTorch + the model). Watch it come up:

```bash
docker compose -f docker-compose.prod.yml ps        # all should become "running"/"healthy"
docker compose -f docker-compose.prod.yml logs -f caddy      # watch TLS being issued
docker compose -f docker-compose.prod.yml logs -f gateway    # ~15–30s to load the model
```

In the Caddy logs you want to see a certificate obtained for your hostname and no ACME errors.

---

## Step 6 — Verify

From your laptop:

```powershell
curl https://d3coy-demo.duckdns.org/health            # -> {"status":"ok"}
```

Then open **`https://d3coy-demo.duckdns.org`** in a browser. The Overview pills (Gateway /
Executor / Live) should be green, and the live feed (WebSocket) should connect. Run a
**Scenario** to confirm end-to-end. **Put this URL in your PPT.**

---

## Open vs locked (decide before you present)

- **Locked (recommended, default here):** `ADMIN_API_KEY` is set. Anyone can open the URL and
  watch, but running scenarios / reviews / the live agent needs **Admin login** with your key.
  You log in during the demo. Protects your Gemini quota and stops abuse of a public link.
- **Fully open:** leave `ADMIN_API_KEY` blank and re-run step 5. Judges can click and run
  everything with no login — but so can anyone who finds the URL. Only pick this for a short
  window and know the live agent will spend your Gemini quota on each run.

To switch, edit `ADMIN_API_KEY` in `.env` and re-run the `up -d --build` command.

---

## After the hackathon

```bash
docker compose -f docker-compose.prod.yml down        # stop (keeps data volumes)
```

Then **destroy the VM** in your cloud console so it stops costing money, and clear the IP in
DuckDNS.

---

## Troubleshooting

- **No certificate / `https` fails** — DNS must resolve to the VM *before* Caddy starts, and
  port **80** must be open (Let's Encrypt validates over HTTP). Fix DNS/firewall, then
  `docker compose -f docker-compose.prod.yml restart caddy` and re-check its logs.
- **Gateway keeps restarting / "killed"** — not enough RAM. Use a ≥2 GB VM, or add swap:
  `fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile`.
- **Live feed not updating** — that's the WebSocket; it rides the same HTTPS origin, so if the
  page loads over `https` the `wss` upgrade works. Check `gateway` logs for errors.
- **"Run agent" greyed out** — set `GEMINI_API_KEY` in `.env` and re-run step 5.
- **Changed `.env`?** — re-run `docker compose -f docker-compose.prod.yml up -d --build` (the
  frontend bakes `PUBLIC_URL` at build time, so rebuild after changing the hostname).
