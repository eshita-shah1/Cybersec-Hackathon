# D3COY — AI-Agent Identity Drift Detection & Prevention (prototype backend)

D3COY is a security layer that sits between autonomous AI agents and the tools they call.
Every attempted tool call is checked against the agent's trusted identity, scored for
behavioral drift and task mismatch, and then **allowed, monitored, held for human review,
or blocked before the tool runs**.

> **Status:** hackathon prototype. It runs locally, it's tested, and it works end to end, but
> it is **not production-ready**. Two tool backends exist: harmless **simulated** tools that run
> in-process, and **sandboxed real** tools that run in a separate executor service against a
> dummy database (no real customer data; no real email or network egress). D3COY is in the
> execution path for both — see [§14, real-agent integration](#14-real-agent-integration-d3coy-in-the-execution-path).

---

## 1. The problem

An agent gets an identity: a role, a task, tools and resource permissions. During a run it
can drift. It might reach for resources outside its job, call tools it shouldn't, or propose
actions that have nothing to do with its task. That can come from prompt injection, a bug,
or a compromised tool chain. D3COY doesn't try to read an agent's "intent". It describes
**observable deviations** and acts on them before execution.

D3COY is **not** a second AI agent watching the first. It is a deterministic Python service
with ML signals added on top.

## 2. Architecture

```
Agent simulator / external integration
        │  POST /api/events  (attempted action — no identity or permission data accepted)
        ▼
[1] Agent Identity & Role Registry ── trusted profile resolved server-side
        ▼
[2] Activity Monitor ── validate, persist attempt (ATTEMPTED) BEFORE anything runs
        ▼
[3] Hybrid Drift Detection
      A. Rule-based policy engine          (deterministic, authoritative)
      B. Behavioral anomaly detection      (Isolation Forest + River Half-Space Trees)
      C. Semantic task-intent validation   (Sentence Transformers all-MiniLM-L6-v2)
        ▼
[4] Risk Assessment & Response ── R = 0.40P + 0.25B + 0.20S + 0.15C, then hard rules
        ▼
[5] Enforcement Gateway ── BLOCK │ HOLD (pending review) │ EXECUTE simulated tool
        ▼
SQLite audit trail ──► WebSocket /ws/events
```

The REST endpoint, the scenario API and the CLI demo all call the same function,
`EventService.process` ([app/services/event_service.py](app/services/event_service.py)).
There is no demo-only path.

### The five components

| # | Component | Code | What it does |
|---|-----------|------|--------------|
| 1 | Identity & Role Registry | [app/registry/](app/registry/) | Profiles: role, task, allowed/forbidden tools and operations, resource scopes, per-resource operation permissions with sensitivity ceilings, review-required tools, destinations. Only admin endpoints can change them, and every change is versioned and audited. |
| 2 | Activity Monitor | [app/monitoring/](app/monitoring/) | Strict Pydantic schema (unknown fields rejected). Resolves resource sensitivity from a server catalog (callers can only *raise* it). Redacts secret-looking parameters. Persists the attempt before evaluation. Serves history. |
| 3 | Hybrid detection | [app/detection/](app/detection/) | Policy engine, feature extractor, Isolation Forest, River, semantic validator. |
| 4 | Risk & response | [app/risk/](app/risk/) | Contextual risk, weighted score, risk level, recommended response, final decision. |
| 5 | Enforcement & audit | [app/enforcement/](app/enforcement/), [app/services/](app/services/) | Gateway state machine, simulated tool registry with signed single-use execution grants, reviews, escalation, append-only audit, WebSocket fan-out. |

## 3. Detection methods

### A. Policy engine ([policy_engine.py](app/detection/policy_engine.py))
The checks run in order, and every violation gets recorded: unknown agent → inactive/suspended
agent → unknown tool → operation the tool can't perform → forbidden operation/tool → tool not
authorized for the role → restricted resource → resource outside scope → no permission for
*this operation on this resource* → sensitivity above clearance → external data transfer /
unapproved destination → tool requires per-call human authorization.

The outcome is one of `ALLOW`, `RESTRICTED` (hold for authorization) or `DENY`. **A DENY can
never be overridden** by a score, by semantic similarity, or by a human approval. If the
engine itself raises an error, the result is DENY (fail-closed).

### B. Behavioral anomaly detection
- **Features** ([feature_extractor.py](app/detection/feature_extractor.py)): 14 bounded
  features computed over a sliding window (the last 9 prior events plus the current one).
  They cover tool and operation rarity for the role, rarity of the tool-to-tool transition,
  tool and resource diversity, the share of sensitive resources, the share of prior policy
  denials, the share of off-role tools, the category mix, and the Jensen–Shannon divergence
  of the tool mix from the role baseline. One more feature counts resource areas never
  seen before for this agent. Wall-clock rates (calls in the last 5 minutes) are reported
  but **not** used by the model, because simulated scenarios run at machine speed.
- **Training** ([baseline_training.py](app/detection/baseline_training.py)): a synthetic,
  seeded (42) set of normal work sessions for each role. Each synthetic record's policy
  status comes from the *real* policy engine. 5,346 training windows. Validation uses
  different seeds, and the drift sequences are never used for training.
- **Isolation Forest**: 200 trees. Normalized score: `raw ≤ q50 → 0`, `q99 → 50`, and
  `100` at double that distance. This is **not a calibrated probability**.
- **River Half-Space Trees** ([online_detector.py](app/detection/online_detector.py)):
  pre-trained on the same baseline, then blended in: `B = 0.75·IF + 0.25·River` once River
  is mature. **Trust policy**: River only learns from events that were ALLOW with a policy
  ALLOW, executed successfully, scored below the monitor threshold, had an IF score under
  25 and no prior denials in the window. It learns in batches of 25 from at least two
  agents, and one agent can supply at most half of a batch. Blocked, held and denied events
  are never learned.
- **Insufficient history** (fewer than 3 prior events): status `insufficient_data` with no
  score. No score is invented.

Validation report printed by `scripts/train_baseline.py`, on **synthetic data only**:
held-out normal windows score ≥ 50 in 0.5% of cases. Synthetic drift averages 42 after one
drift step, 56 after two, and ~70 after five or more.

### C. Semantic intent validation ([intent_validator.py](app/detection/intent_validator.py))
Cosine similarity between the trusted task (from the registry) and the caller's
`action_description`, using all-MiniLM-L6-v2. The model loads once per process from the
local cache. `sim ≥ 0.45 → mismatch 0`, `sim ≤ 0.10 → 100`, linear in between. The
similarity to a server-generated description of the actual tool call is also reported, as
information only. In testing, that check could not reliably separate misleading descriptions
from legitimate ones.

**Limitation:** a caller can write a misleading description. That is why policy and
contextual risk always judge the actual tool call. If the model is unavailable, the status
is `unavailable` and no score is produced. An optional lexical fallback
(`SEMANTIC_FALLBACK=lexical`) is reported under its own detector name.

## 4. Risk scoring

```
R = 0.40·P + 0.25·B + 0.20·S + 0.15·C      (rounded half-up to 0.1; weights configurable)
```
| Signal | Meaning | Range |
|---|---|---|
| P | Highest policy violation severity (allow 0, medium 50, high 75, critical 100). Taken as the max, **never summed**. | 0–100 |
| B | Behavioral anomaly (IF, blended with River) | 0–100 or unavailable |
| S | Semantic task mismatch | 0–100 or unavailable |
| C | Context ([context_risk.py](app/risk/context_risk.py)): sensitivity 0.30, operation impact 0.25, exfiltration 0.15, irreversibility 0.10, tool privilege 0.10, out-of-scope 0.10. Out-of-scope is zeroed when policy already denied for scope, so it isn't counted twice. | 0–100 |

**Unavailable signals** are left out and the remaining weights are renormalized. The result
records `available`, `coverage` and `renormalized`. This weighting is a provisional
engineering choice, not a validated metric.

## 5. Response thresholds and enforcement

| Score | Risk level | Recommended response |
|---|---|---|
| 0–24 | LOW | ALLOW |
| 25–49 | MEDIUM | MONITOR (execute, alert, extra scrutiny) |
| 50–74 | HIGH | REVIEW (hold before execution) |
| 75–100 | CRITICAL | BLOCK |

The final decision is the recommendation, raised (never lowered) by these rules
([response_engine.py](app/risk/response_engine.py)):
1. policy DENY → **BLOCK**
2. policy RESTRICTED → at least REVIEW
3. behavior ≥ 50 → at least MONITOR (an anomaly isn't treated as proof of an attack)
4. behavior ≥ 65 on a confidential or restricted resource → at least REVIEW
5. **Fail-closed:** a high-impact action (confidential or restricted data, send, export,
   delete, or external) with any ML detector unavailable → at least REVIEW

**Escalation:** 3 high-severity BLOCKs within 30 minutes suspend the agent (configurable).
Reactivation needs an admin.

**Reviews:** approval **re-runs the policy** against the agent's *current* profile and
status. DENY stays DENY (`revalidation_failed`). RESTRICTED becomes executable, because the
approval *is* the explicit authorization. Each review is claimed atomically, so it can't
execute twice.

**Execution lifecycle** (`request_status`): `ATTEMPTED → EVALUATED → BLOCKED | PENDING_REVIEW
→ (APPROVED → EXECUTING | REJECTED) | EXECUTING → EXECUTED | FAILED`. Every move is a
compare-and-set update. `tool_executions.event_id` is unique, which adds a database-level
guard against double execution. Re-sending the same `request_id` returns the original result
with `duplicate: true`.

**Tool registry** ([tool_registry.py](app/enforcement/tool_registry.py)): every tool is
harmless and works on synthetic data. The attack tools (`delete_customer_records`,
`export_confidential_data`, `access_payroll`) only return a simulation notice. Handlers run
only with an HMAC-signed, single-use grant minted by the gateway. This is an in-process
architectural guard, not a sandbox.

## 6. Database (SQLite + SQLAlchemy, [models.py](app/database/models.py))

| Table | Contents |
|---|---|
| `agents` | trusted profiles, status, version |
| `events` | each attempt: request, resolved sensitivity, task snapshot, all detector results, risk breakdown, decision, statuses, execution result |
| `detection_results` | one row per detector per event |
| `alerts` | MONITOR / REVIEW / BLOCK / ESCALATION / UNKNOWN_AGENT alerts and their triage |
| `pending_reviews` | held actions and analyst decisions, including revalidation |
| `tool_executions` | exactly one row per executed tool call |
| `audit_records` | append-only history (no API updates or deletes it) |

The audit log is append-oriented. It is **not** cryptographically tamper-proof.

## 7. API reference
Interactive docs live at **http://127.0.0.1:8000/docs**. 🔑 = needs the `X-API-Key: <ADMIN_API_KEY>` header.

| Method | Path | |
|---|---|---|
| GET | `/health` | lightweight liveness |
| GET | `/api/system/status` | components, detector and model status, DB status, thresholds (no secrets) |
| GET | `/api/agents` · `/api/agents/{id}` | list or get profiles (`?status=`) |
| POST 🔑 | `/api/agents` | register |
| PATCH 🔑 | `/api/agents/{id}` | update permitted fields (not status) |
| POST 🔑 | `/api/agents/{id}/suspend` · `/reactivate` · `/deactivate` | body `{"reason": "...", "actor": "..."}` |
| POST | `/api/events` | run an attempted action through the pipeline (optional `X-Agent-Key` if `EVENT_API_KEY` is set) |
| GET | `/api/events` | paginated; filters `agent_id, decision, risk_level, execution_status, request_status, since, until, limit, offset` |
| GET | `/api/events/{id}` | full detail, including detections, tool execution and audit trail |
| GET | `/api/audit` | read-only audit history |
| GET | `/api/alerts` · `/api/alerts/{id}` | filters `status, severity, agent_id, alert_type` |
| POST 🔑 | `/api/alerts/{id}/review` | `{"status": "acknowledged\|resolved\|false_positive\|escalated"}`. Triage only; never executes anything. |
| GET | `/api/reviews/pending` · `/api/reviews/{id}` | |
| POST 🔑 | `/api/reviews/{id}/approve` · `/reject` | `{"reviewer": "...", "notes": "..."}` |
| GET | `/api/analytics/overview` | dashboard statistics computed from the DB on every request |
| GET | `/api/scenarios` | demo scenarios |
| POST 🔑 | `/api/scenarios/{id}/run` | run a scenario through the normal pipeline |
| WS | `/ws/events` | live messages (see below) |

Example event:
```json
{"agent_id": "finance-agent-01", "tool_name": "read_expenses", "action": "read",
 "target_resource": "finance:expenses/2026-09",
 "action_description": "Read the expense records for September",
 "request_id": "my-unique-request-0001"}
```

WebSocket envelope: `{"type": "connected|security_event|security_alert|alert_update|agent_status|pong", "version": 1, "sent_at": "...", "data": {...}}`.
`security_event` data includes `event_id, agent_id, timestamp, tool_name, action, target_resource, policy_status, risk_score, risk_level, decision, request_status, execution_status, explanations, review_id, alert_ids, escalation`.
Send the text `ping` to get a `pong`. Each client has a bounded queue, so a slow client
has messages dropped rather than blocking processing.

Errors always look like `{"error": {"code": "...", "message": "...", "details": ...}}`.
Validation errors never echo the submitted values.

## 8. Project structure
```
d3coy-backend/
  app/
    main.py, config.py
    api/          health, agents, events(+audit), alerts(+reviews), analytics, scenarios, websocket, deps
    registry/     agent_registry, seed_agents, resource_catalog, credentials
    monitoring/   activity_monitor, event_normalizer
    detection/    policy_engine, feature_extractor, behavior_detector, online_detector,
                  intent_validator, synthetic_baseline, baseline_training
    risk/         context_risk, risk_scorer, response_engine
    enforcement/  gateway, tool_registry, permissions, executor_client
    simulator/    scenarios, agent_simulator, demo_runner
    database/     db, models, repositories
    services/     container, event_service, review_service, websocket_manager
    schemas/      agents, events, alerts, common
  sandbox_executor/   SEPARATE service: main (FastAPI), store (real dummy DB), tools (real ops)
  agent/              real LLM agent: procurement_agent (LangGraph), d3coy_client, run_procurement_demo
  scripts/        train_baseline.py, seed_data.py
  tests/          11 test modules + conftest, 134 tests
  artifacts/      trained models (generated)
  data/           D3COY SQLite db + sandbox SQLite db (generated)
```

## 9. Windows setup (PowerShell, VS Code)

Run every command **from the `d3coy-backend` folder**.

```powershell
cd F:\Eshita\Projects\Cybersec_DJ\d3coy-backend

# 1. virtual environment (Python 3.12+; tested on 3.14.3)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
# if activation is blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

# 2. dependencies (CPU-only PyTorch first keeps the download small)
python -m pip install --upgrade pip
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

# 3. configuration — then edit .env and set your own ADMIN_API_KEY
Copy-Item .env.example .env

# 4. semantic model (one-time ~90 MB download into the Hugging Face cache)
python -c "from sentence_transformers import SentenceTransformer as S; print(S('sentence-transformers/all-MiniLM-L6-v2').encode(['ok']).shape)"
#    should print (1, 384). After this, startup loads from the local cache (no re-download).

# 5. train the behavioral baseline (~20 s; also done automatically on first start if missing)
python scripts\train_baseline.py

#    optional: also export the synthetic data as CSV to data\synthetic\
#    python scripts\train_baseline.py --export-csv

# 6. create and seed the database
python scripts\seed_data.py

# 7. start the API
uvicorn app.main:app --reload
#    docs: http://127.0.0.1:8000/docs
#    verify models: http://127.0.0.1:8000/api/system/status  -> detectors.*.status == "ready"
```

Startup takes about 10–20 s, mostly loading the semantic model. After that, evaluating an
event takes tens of milliseconds on a laptop CPU.

**Tests:** `pytest -q`. Tests use temporary databases and model copies, so `data/` and
`artifacts/` stay untouched. If the semantic model isn't cached, the 5 semantic-quality
tests are skipped and say why.

**Reset the demo database:** `python scripts\seed_data.py --reset` (asks for confirmation).

## 10. Running the demonstrations

```powershell
python -m app.simulator.demo_runner --list
python -m app.simulator.demo_runner normal_research          # in-process, same pipeline
python -m app.simulator.demo_runner all
# through the running server, so a WebSocket dashboard sees the events:
python -m app.simulator.demo_runner all --api http://127.0.0.1:8000
```
Or call `POST /api/scenarios/{id}/run` with `X-API-Key` from `/docs`.

Results below were measured on a fresh database (`seed_data.py --reset`), running each
scenario alone. B = behavior score, S = semantic mismatch.

| Scenario | What happens (observed) |
|---|---|
| **A. `normal_research`** | 6 steps, all policy ALLOW, risk 0.2–12.1, all **ALLOW** and executed. The first 3 steps report `insufficient_data` for behavior. |
| **B. `unauthorized_delete`** | 3 normal finance steps run. `delete_customer_records` → policy DENY (`forbidden_operation`, critical), risk 89.4, **BLOCK**, `NOT_EXECUTED`. No `tool_executions` row is written, and the audit reads `action_attempted → action_evaluated → action_blocked`. |
| **C. `behavioral_drift`** | 6 normal steps (B 13–30), then vendor-payment reads across many months (B 56 → 64 → 77) → **MONITOR**, then **REVIEW** (anomalous + confidential). Payroll, customer and HR attempts → **BLOCK**. The third high-severity block → agent **suspended** (escalation), and the final step is blocked because the agent is suspended. |
| **D. `semantic_mismatch`** | "Export the entire customer database to an external destination" → S 85, context high, policy DENY → **BLOCK**. |
| **E. `legitimate_unusual`** | Rarely-used but authorized `read_vendor_payments` → policy ALLOW, B 56.5, S 0 → **MONITOR** (executed and flagged, not blocked). |
| **F. `human_review`** | Draft steps execute. `send_email` → RESTRICTED → **REVIEW**, `AWAITING_REVIEW`, no execution. Approve via `POST /api/reviews/{review_id}/approve` → policy revalidated → executed (simulated outbox). Reject → `REJECTED`, never executed. |

Behavior uses each agent's real history, so running scenarios **one after another on the
same database** changes later scores. For example, D or E after C shows more MONITOR
decisions. That is expected behavioral-monitoring behavior. If a suspended scenario agent
needs it, the scenario runner reactivates it, and that reactivation is audited.

**Dashboard readiness:** `GET /api/agents`, `/api/events`, `/api/alerts`,
`/api/analytics/overview`, plus `ws://127.0.0.1:8000/ws/events`. CORS allows
`http://localhost:5173` by default.

## 11. Limitations (honest list)
- **Simulated agents and tools only.** No real LLM agent is connected yet.
- **Synthetic baseline.** Model thresholds and validation figures describe the simulated
  world. They are **not** real-world detection accuracy.
- **Agent identity is asserted, not authenticated.** `agent_id` in the request is trusted
  for lookup. The optional shared `EVENT_API_KEY` is not per-agent identity. Production
  would need per-agent credentials (mTLS, signed tokens or workload identity).
- **Admin auth is one shared API key.** That is not identity management: there are no
  per-analyst accounts or RBAC, and `reviewer` names are self-declared. Read endpoints are
  unauthenticated, which suits a local dashboard.
- Semantic analysis trusts the caller's description (see 3C). Similarity isn't evidence of
  intent.
- The risk weights and thresholds are provisional engineering choices.
- River's trusted buffer is in memory, so it's lost on restart; the model itself persists.
  In the short demos, few events meet the strict trust policy, so live updates are rare.
  The update logic is covered by unit tests.
- The fail-closed rule holds high-impact actions from brand-new agents with no history.
- The audit log is append-only by API convention, not cryptographically tamper-proof.
- The tool-grant mechanism stops accidental bypass inside the app. It isn't a sandbox
  against hostile code in the same process.
- SQLite with a per-agent lock suits a single-process demo, not a high-throughput
  multi-worker deployment.

## 12. Implemented vs. future work
**Implemented and tested:** everything in sections 2–11 and §14. That covers the registry,
monitor, policy engine, Isolation Forest, River with its trust policy, MiniLM semantic
validation, contextual risk, the weighted score, response rules, escalation, reviews with
revalidation and expiry, idempotency, the gateway state machine, the audit trail, analytics,
scenarios, the WebSocket feed, the CLI runner, **per-agent authentication, signed single-use
permission slips, the separate sandbox tool executor with real side effects, and a real
LangGraph + Claude agent whose only tools are D3COY-protected wrappers**.

**Not implemented / future work:** a frontend (built separately); per-analyst accounts and RBAC
(admin is one shared key); tamper-evident/cryptographic audit; a production database, process
isolation (OS/container sandbox) and multi-worker deployment; replacing the synthetic baseline
with real observed sessions and recalibrating thresholds; and connecting tools other than the
procurement set.

---

## 14. Real-agent integration (D3COY in the execution path)

The earlier sections describe D3COY evaluating actions. This section is about **enforcement on a
real agent**: proving a real tool call is stopped before it happens, not merely scored.

### 14.1 Components

```
Real LLM agent (LangGraph; Gemini or Claude)   agent/procurement_agent.py
  holds ONLY: its bearer token + the D3COY URL
      │ every tool it can call is a D3COY-protected wrapper
      ▼
POST /api/events  (Authorization: Bearer d3a_...)     app/api/events.py
      │ identity authenticated from the token, not the body
      ▼
D3COY pipeline (identity → policy → detectors → risk → decision)
      │ ALLOW/MONITOR → gateway issues a SIGNED, EXPIRING, SINGLE-USE permission slip
      ▼
Enforcement gateway                          app/enforcement/gateway.py
      │ simulated tool → run in-process
      │ sandbox tool   → POST the permission to the executor
      ▼
Sandbox tool executor (SEPARATE service)     sandbox_executor/
  owns the only connection to the dummy database + outbox
  re-verifies the permission, consumes it atomically, then runs the REAL op
```

The agent has **no** database credentials, **no** network route to the executor, and **no**
tool other than the three protected wrappers. A hidden/injected instruction that steers the
model toward deleting customers or exporting data still has to go through a wrapper → D3COY →
policy **DENY**, so it is blocked. The model cannot reach a real operation any other way.

### 14.2 The four problems, and how they're addressed

- **A — detect but fail to stop.** Real tools live behind the gateway in the executor; the agent
  never calls them directly. A blocked action never produces a permission, so the executor is
  never even asked. *Test:* `test_unauthorized_delete_blocked_leaves_db_intact` asserts the
  customer rows are unchanged.
- **B — bypass.** The agent's tool surface is exactly its three allowlisted tools; anything else
  is denied by policy. The executor rejects any request without a valid signed permission, and
  the in-process `ToolRegistry` refuses to run a sandbox tool locally. *Tests:*
  `test_agent_has_no_tool_outside_its_allowlist`, `test_sandbox_tool_cannot_be_invoked_through_local_registry`,
  and the live forged-permission check.
- **C — a simulated tool isn't a real tool.** The executor runs against a real SQLite database
  with dummy rows and a real outbox table. `test_real_delete_actually_removes_row` proves the
  delete is genuinely destructive, so the BLOCK tests are meaningful.
- **D — approval must protect the real action.** A held send stores the pending action; nothing
  is sent until an analyst approves; approval re-runs the policy against the *current* profile;
  a fresh permission bound to the exact arguments is then issued and consumed once. Changing an
  argument after issue invalidates the permission. *Tests:* `test_send_held_then_approved_actually_sends`,
  `test_approval_revalidation_blocks_if_permissions_changed`, `test_argument_change_after_issue_rejected`.

### 14.3 Permission slips

For every authorized execution the gateway issues an HMAC-SHA256-signed permission
(`app/enforcement/permissions.py`) bound to `event_id, agent_id, tool, operation, resource` and
a SHA-256 digest of the arguments (parameters + destination), with an expiry. The executor holds
the same `PERMISSION_SIGNING_KEY`, re-verifies the signature, expiry and exact-action binding,
and inserts the `permission_id` under a unique constraint — so a replayed or concurrent
permission executes **at most once**. If the key is unset the executor fails closed (rejects
everything). If the executor is unreachable, a sandbox action fails closed (never executed).

### 14.4 Per-agent authentication

Agents authenticate with a per-agent bearer token (`d3a_...`), issued by an admin via
`POST /api/agents/{id}/credentials` (returned once; only a SHA-256 hash is stored; revocable).
The pipeline resolves the trusted profile from the token, and rejects a request whose body
`agent_id` differs from the authenticated identity (`identity_mismatch`). An admin key is
accepted as a trusted internal caller (simulator, demos). `AGENT_AUTH_REQUIRED=false` disables
this for local experiments with simulated tools only.

### 14.5 Running it

Two terminals from `d3coy-backend`, **the same `PERMISSION_SIGNING_KEY` and `ADMIN_API_KEY` in
both**:

```powershell
$env:PERMISSION_SIGNING_KEY = "a-shared-secret"
$env:ADMIN_API_KEY = "change-this-local-demo-key"
uvicorn sandbox_executor.main:app --port 8100   # terminal 1 (real dummy-data tools)
uvicorn app.main:app --port 8000                # terminal 2 (D3COY)
```

Then drive the Procurement Agent. The LLM provider is pluggable — set a **Gemini** key
(`GEMINI_API_KEY`, free tier from Google AI Studio) or `ANTHROPIC_API_KEY`; without either, a
deterministic scripted client exercises the same protected wrappers:

```powershell
$env:GEMINI_API_KEY = "..."     # optional; else the scripted client runs
python -m agent.run_procurement_demo
```

Provider is auto-selected (Gemini if a Google key is present, else Claude); force it with
`D3COY_AGENT_PROVIDER=gemini|anthropic` and the model with `D3COY_AGENT_MODEL`. Whichever model
runs, it only ever sees the three D3COY-protected tools, so the enforcement guarantees are
identical.

Observed live (scripted client): the two reads execute via the executor; `delete_customer`
(risk 94) and `export_customer_data` (risk 91) are blocked with the customer table unchanged;
`send_purchase_summary` is held for review and, after admin approval, really lands in the
sandbox outbox. `GET http://127.0.0.1:8100/sandbox/state` shows the dummy data and outbox.

### 14.6 Proof suite (side effects, not just decisions)

`pytest tests/test_integration_real_agent.py tests/test_executor.py tests/test_permissions.py`

| Checklist item | Test |
|---|---|
| Authorized read executes | `test_agent_search_executes_real_tool`, `test_real_delete_actually_removes_row` |
| Unauthorized delete blocked, DB unchanged | `test_unauthorized_delete_blocked_leaves_db_intact` |
| External export blocked, nothing leaves | `test_unauthorized_export_blocked` |
| Send held; sent only after approval + revalidation | `test_send_held_then_approved_actually_sends`, `test_approval_revalidation_blocks_if_permissions_changed` |
| Expired permission rejected | `test_expired_permission_rejected_and_no_effect` |
| Permission reuse rejected | `test_single_use_second_call_rejected`, `test_permission_is_single_use_across_executor` |
| Concurrent reuse → at most one executes | `test_concurrent_identical_permission_executes_at_most_once` |
| Direct tool/bypass prevented | `test_sandbox_tool_cannot_be_invoked_through_local_registry`, `test_agent_has_no_tool_outside_its_allowlist` |
| Arguments changed after approval → new auth needed | `test_argument_change_after_issue_rejected`, `test_changed_arguments_rejected` |
| D3COY/executor failure fails closed | `test_executor_unavailable_fails_closed`, `test_no_signing_key_fails_closed` |

### 14.7 Honest limits of this integration

The wrapper + separate-process executor is an **application-level** boundary, not an OS sandbox:
code with unrestricted access to the host could still reach the database file directly. Real
isolation (separate container/VM, no shared filesystem, network egress rules, per-agent DB
credentials) is the production step. The dummy database is not real data, agent tokens are
bearer tokens (not mTLS/workload identity), and the LLM agent's quality is not evaluated here —
only that D3COY sits in its execution path and enforces decisions.
