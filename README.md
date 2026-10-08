# AgentGuard

Multi-agent, pre-transaction fraud / mule / AML guard for UPI and net-banking payments.
Six agents (Transaction, Behavior, Velocity, Mule, AML, Counsel) score every payment in parallel;
an orchestrator fuses the scores (peak-aware + corroboration), classifies the fraud typology and routes to
Allow / Nudge / Step-up / Cooling room / Hold / Pause / Block. Every decision is written to a
SHA-256 hash-chained ledger. Full write-up: [docs/AgentGuard_Project_Report.md](docs/AgentGuard_Project_Report.md).

## Run

```bash
./run.sh        # Linux / macOS
run.bat         # Windows (or just double-click it)
```

First run creates `.venv` and installs `backend/requirements.txt` (needs internet, a few minutes). Then open
http://localhost:8000 (API docs at `/docs`; `run.bat` opens the browser for you). Everything — fonts, Three.js —
is served locally; no internet needed at the venue.

**Windows:** install Python 3.12 or 3.13 from python.org and tick *Add python.exe to PATH*. Different port:
`set PORT=8001` then `run.bat` in the same Command Prompt. Tests: `.venv\Scripts\python -m pytest backend\tests`.

**For judges:** press **Start demo** (top right). It resets the workspace and walks through a legitimate
payment → account takeover → mule network → follow the money → agent detection → block → audit, ending on
“Why AgentGuard stopped this”. Pause / skip / replay from the bar at the bottom; Esc exits.

## The console

| Section | What it does |
|---|---|
| **Overview** | What AgentGuard does, live KPIs (monitored, suspicious, money protected, active investigations, top network risk), scenario launcher |
| **Investigate** | Run a scenario and **play** it back in real transaction order: money moves as particles, the network emerges, the suspicious account lights up as its risk rises, agents score each payment live, then the case summary, evidence and decision. Click any account or payment for the account panel / transaction inspector; **follow the money** in/out; expand 1 hop / 2 hops / full; click a “why” signal to highlight its graph evidence |
| **Simulate** | “What changes the decision?” — every control calls the real engine (`POST /analyze/preview`, never recorded): risk trail, decision ladder, what changed, and engine-computed counterfactuals |
| **Network** | Every investigation as one money network. Filters (risk, outcome, phones), search. Optional **3D explorer** where elevation = risk |
| **Audit** | The hash chain: verify, tamper demo (sandbox copy), restore, and each record's evidence |

Edge colours are what happened to the money: **settled** (teal), **held** in lien (amber), **paused / challenged**
(violet, pause mark mid-edge), **blocked** (red, cut mark — particles stop at the cut). Red is used for danger only.

## Scenarios

Deterministic, timestamped scripts in `backend/scenarios.py`. A script only says *what happens*; every score,
decision, typology, focus account and explanation comes from the engine. The planted roles are returned
separately as `ground_truth`, and `backend/tests/test_scenarios.py` checks the engine's findings against them.

| Scenario | Story | Engine result |
|---|---|---|
| Normal | 8 payments between people with history | all ALLOW, no typology, no focus |
| Account takeover | SIM swap, PIN reset, unknown phone in Patna → new 2-day-old payee | STEP_UP → retry after failed biometrics → BLOCK (T3) |
| Digital arrest | 72-year-old on a 55-min video call, screen shared → ₹1.8L | COOLING_ROOM → victim overrides → HOLD_CREDIT (T1) |
| Mule fan-in | 10 unrelated victims → 3-day-old collector → cash-out | first cash-out BLOCKED, mule flagged, later credits held, retry blocked (T8 · rapid pass-through, 4 min hold) |
| Structuring | 3 smurfs × 3 payments of ₹49,xxx → collector | silent monitoring → PAUSE once repetition is unambiguous (T11) |
| Micro probing | 5 × ₹1 to accounts on one phone → ₹60,000 to that phone's main account | BLOCK (T7) |
| Device farm | 8 rented accounts paying out from one phone | credits HELD as the shared phone emerges (T12) |

## API

Investigation: `POST /simulate/{name}` · `GET /investigations` · `GET /investigations/{run_id}` ·
`GET /graph/investigation?run_id=|account=&hops=` · `GET /accounts/{id}` · `GET /transactions/{txn_id}` ·
`GET /overview` · `GET /version`

Scoring: `POST /analyze` (recorded) · `POST /analyze/preview` (dry run + counterfactuals)

Ledger: `GET /ledger` · `GET /ledger/verify` · `GET /ledger/record/{txn_id}` · `POST /ledger/tamper` · `POST /ledger/restore`

Other: `GET /meta` · `GET /scenarios` · `GET /stats` · `GET /graph` · `GET /v1/accounts` · `GET /v1/graph/account/{id}` · `POST /reset` · `GET /health`

Every investigation payload carries `scenario, transactions, decisions, graph{nodes,edges}, focus,
key_transaction (agents, drivers, signal→evidence map, ledger record), key_accounts, explanation, metrics,
timeline, ground_truth`. The frontend never infers relationships or the focus account itself.

## Layout

- `backend/main.py` — FastAPI app, workspace state, scenario runner, simulator
- `backend/investigation.py` — authoritative graph, focus, metrics, explanation, signal→evidence
- `backend/scenarios.py` — scenario scripts
- `backend/app/` — agents, orchestrator, typology, policy (`app/policy/default_policy.yaml`), graph, ledger, holds
- `backend/static/` — the console: vanilla ES modules, no build step (`js/graph/` canvas 2D + Three.js 3D)
- `hackathon-shivalik-project-main/`, `agentguard-main/`, `agentguard-suite/`, `agentguard_full_final_stimulation/`: earlier prototypes, not used by `run.sh`
- `frontend/`: unused Next.js starter

Tests: `make test` (or `.venv/bin/python -m pytest backend/tests`).
State is in memory and resets on restart. All results are on synthetic data.
