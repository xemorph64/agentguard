"""API smoke test using FastAPI TestClient. Run: python -m backend.tests.api_test"""
import collections
from fastapi.testclient import TestClient
from ..main import app, S

dist = lambda o: dict(collections.Counter(x["decision"] for x in o))
with TestClient(app) as c:
    with c.websocket_connect("/ws/feed") as ws:
        a = S.sim.active[5]
        r = c.post("/analyze", json={"src": a, "dst": S.sim.fav[a], "amount": 1500, "device_id": f"D-{a}"}).json()
        print(f"/analyze normal -> {r['decision']} {r['score']} | websocket event received: {ws.receive_json()['data']['txn_id']==r['txn_id']}")

    c.post("/drift/reset"); [c.post("/simulate/normal?count=20") for _ in range(3)]
    d = c.get("/drift").json(); print("drift on 60 normal txns :", d["status"], d["max_psi"])
    r = c.post("/analyze", json={"src": a, "dst": "N99999", "amount": 48000, "request_type": "COLLECT", "device_id": "D-NEW1",
                                 "ip_country": "RU", "home_country": "IN", "session_age_s": 3, "beneficiary_age_hours": 2}).json()
    print(f"\n/analyze UPI scam -> {r['decision']} score {r['score']}\n   customer: {r['explanation']['customer_message']}")
    if r["decision"] == "VERIFY":
        print("   otp wrong:", c.post("/otp/verify", json={"txn_id": r["txn_id"], "otp": "000000"}).json()["reason"],
              "| otp right:", c.post("/otp/verify", json={"txn_id": r["txn_id"], "otp": r["dev_otp"]}).json()["status"])

    print("\n-- scenarios (all fraud unless 'normal')")
    for sc, n in (("mule", 1), ("structuring", 1), ("cycle", 1), ("upi_scam", 3), ("account_takeover", 2), ("micro_probing", 2), ("device_farm", 1), ("slow_drip", 2)):
        print(f"{sc:12s}", dist(c.post(f"/simulate/{sc}?count={n}").json()))

    q = c.get("/review-queue").json(); print("\nreview queue:", len(q))
    if q:
        t = q[0]; print("analyst rejects + blacklists payee ->", c.post(f"/review/{t['txn_id']}", json={"action": "reject", "blacklist_payee": True}).json()["status"])
        print("same payee again ->", c.post("/analyze", json={"src": t["src"], "dst": t["dst"], "amount": 500}).json()["decision"])

    print("\nledger:", c.get("/ledger/verify").json(), "| tamper:", c.post("/ledger/tamper", json={"index": 3}).json(),
          "| repair:", c.post("/ledger/demo-repair").json())
    g = c.get("/graph").json(); print(f"graph: {len(g['nodes'])} nodes, {sum(n['suspicious'] for n in g['nodes'])} red, {sum(n['in_cycle'] for n in g['nodes'])} in loops")
    print("customer trust score:", c.get(f"/customers/{a}").json()["trust_score"])
    c.post("/drift/reset"); [c.post("/simulate/normal_shifted?count=20") for _ in range(3)]; d = c.get("/drift").json()
    print("drift after shifted traffic:", d["status"], d["max_psi"], {k: v["psi"] for k, v in d["features"].items()})
    print("\n/compare:"); [print("  ", k, v) for k, v in c.post("/compare?n_normal=80").json().items()]
    m = c.get("/metrics").json(); print("\nlatency:", m["latency"]); print("decisions:", m["decisions"])
