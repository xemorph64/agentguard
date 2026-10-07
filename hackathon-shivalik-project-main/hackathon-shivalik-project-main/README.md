# AgentGuard - AML + Mule agents (v1)
Run from this folder:
    pip install -r requirements.txt
    python -m backend.models.train_mule      # add path to IBM AML HI-Small_Trans.csv to use real data
    python -m backend.models.train_aml
    python -m backend.tests.smoke_test       # replays traffic through history + graph + both agents
Wire into the orchestrator:
    hist, graph = AccountHistory(), LiveGraph()
    agents = [..., MuleAgent(hist, graph), AMLAgent(hist, graph)]
    after each decision: hist.record(txn); graph.add_txn(src, dst, amount); on BLOCK -> graph.flag(account)
Txn dict: txn_id, ts, src, dst, amount, channel(UPI|NET_BANKING), src_bank, dst_bank, cross_border

## All 6 agents (v2)
    python -m backend.models.train_behavior
    python -m backend.tests.e2e_test         # scripted scenarios + replay through the full pipeline
Use it:  from backend.pipeline import AgentGuard;  ag = AgentGuard();  out = ag.process(txn)
Optional txn fields: device_id, lat, lon, ip_country, home_country,
request_type (COLLECT), beneficiary_age_hours, failed_attempts_1h, session_age_s, recent_credential_change
Set ANTHROPIC_API_KEY and AgentGuard(use_llm=True) for LLM analyst narratives (template fallback otherwise).

## API (v3)
    pip install -r requirements.txt fastapi uvicorn httpx websockets
    python -m backend.models.train_mule && python -m backend.models.train_aml && python -m backend.models.train_behavior
    uvicorn backend.main:app --reload        # interactive docs: http://localhost:8000/docs
    python -m backend.tests.api_test         # exercises every endpoint
Endpoints: POST /analyze | WS /ws/feed | GET /transactions[/{id}] | GET /review-queue, POST /review/{id} | POST /otp/send, /otp/verify
GET /ledger, /ledger/verify, POST /ledger/tamper, /ledger/demo-repair | /lists (blacklist, whitelist) | POST /simulate/{scenario}
GET /metrics, /drift, /graph, /customers/{acct} | POST /compare, /drift/reset | GET /feedback
Known: demo drift baseline needs traffic sampled from the training distribution; latency p95 ~260 ms (target 200).

## Scope
UPI and net-banking (digital account-to-account payments) only. Credit/debit card logic has been removed.
IBM AML note: 'Credit Card' rows are dropped when loading the real dataset.
