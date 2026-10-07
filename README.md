# AgentGuard

Multi-agent, pre-transaction fraud / mule / AML guard for UPI and net-banking payments.
Six agents (Transaction, Behavior, Velocity, Mule, AML, Counsel) score every payment in parallel;
an orchestrator fuses the scores (peak-aware), classifies the fraud typology and routes to
Allow / Nudge / Step-up / Cooling room / Hold / Pause / Block. Every decision is written to a
SHA-256 hash-chained ledger. Full write-up: [docs/AgentGuard_Project_Report.md](docs/AgentGuard_Project_Report.md).

## Run

```bash
./run.sh        # Linux / macOS
run.bat         # Windows (or just double-click it)
```

First run creates `.venv` and installs `backend/requirements.txt`. Then open:

- Dashboard: http://localhost:8000
- API docs: http://localhost:8000/docs

## Dashboard

- **Run a scenario**: normal traffic, digital arrest, account takeover, mule fan-in, structuring, micro probing, device farm
- **Send a payment**: custom payment with risk signals (on call, screen share, SIM swap, PIN reset, device, location)
- **Live decisions**: click any row to see per-agent scores, typologies, customer message, counterfactual, debate log
- **Money-flow graph**: accounts, payments and shared devices
- **Audit ledger**: verify the hash chain, run the tamper demo, restore

## API

`POST /analyze` · `GET /transactions` · `GET /stats` · `GET /graph` · `GET /ledger` · `GET /ledger/verify` ·
`POST /ledger/tamper` · `POST /ledger/restore` · `GET /scenarios` · `POST /simulate/{name}` · `POST /reset` · `GET /health`

## Layout

- `backend/`: FastAPI app (`main.py`), agents, orchestrator, policy (`app/policy/default_policy.yaml`), ledger, graph, saved models
- `backend/static/index.html`: dashboard (single file, no build step)
- `hackathon-shivalik-project-main/`: original v1–v3 prototype (ML training scripts, tests)
- `frontend/`: unused Next.js starter

State is in memory and resets on restart. All results are on synthetic data.
