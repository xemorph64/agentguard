"""Every scenario must tell a coherent story *as decided by the engine*, and the engine's
investigation focus must match the role the script planted."""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from main import app  # noqa: E402

client = TestClient(app)


def run(name):
    client.post("/reset")
    r = client.post(f"/simulate/{name}")
    assert r.status_code == 200, r.text
    return r.json()


def planted(res, role):
    return [a for a, r in res["ground_truth"].items() if r == role]


def outcomes(res):
    return [d["outcome"] for d in res["decisions"]]


def test_payload_contract():
    res = run("mule_fanin")
    for key in ("scenario", "transactions", "decisions", "graph", "focus", "key_transaction", "key_accounts",
                "explanation", "metrics", "timeline", "investigation"):
        assert key in res
    g = res["graph"]
    ids = {n["id"] for n in g["nodes"]}
    for e in g["edges"]:
        assert e["source"] in ids and e["target"] in ids
    pay = [e for e in g["edges"] if e["type"] == "payment"]
    assert all(e["transaction_ids"] and e["amount"] > 0 and e["state"] for e in pay)


def test_normal_is_clean():
    res = run("normal")
    assert set(outcomes(res)) <= {"ALLOW", "ALLOW_NUDGE"}
    assert res["focus"] is None
    assert all(d["request"]["payer"] != d["request"]["payee"] for d in res["decisions"])
    assert all(d["dominant"] is None for d in res["decisions"])


def test_mule_fanin_topology_and_detection():
    res = run("mule_fanin")
    mule = planted(res, "mule")[0]
    assert res["focus"] == mule
    m = res["metrics"]
    assert 8 <= m["inbound_sources"] <= 12
    assert m["outbound_sinks"] == 1
    assert m["inbound"] >= 100000
    assert m["holding_minutes"] is not None and m["holding_minutes"] < 10
    assert m["pass_through"] >= 0.8
    cashouts = [d for d in res["decisions"] if d["request"]["payer"] == mule]
    assert cashouts and all(d["outcome"] == "BLOCK" for d in cashouts)
    assert res["explanation"]["headline"] == "RAPID PASS-THROUGH DETECTED"
    assert res["key_transaction"]["payer"] == mule


def test_account_takeover():
    res = run("account_takeover")
    assert res["focus"] == planted(res, "attacker_beneficiary")[0]
    attack = [d for d in res["decisions"] if d["dominant"] == "T3"]
    assert [d["outcome"] for d in attack] == ["STEP_UP", "BLOCK"]
    beh = next(a for a in attack[0]["agents"] if a["agent"] == "behavior")
    feats = {s["feature"] for s in beh["signals"]}
    assert {"new_device", "sim_change_72h", "pin_reset_24h"} <= feats
    assert any(e["kind"] == "event" for e in res["timeline"])


def test_digital_arrest():
    res = run("digital_arrest")
    assert res["focus"] == planted(res, "fraudster_beneficiary")[0]
    assert outcomes(res)[1:] == ["COOLING_ROOM", "HOLD_CREDIT"]
    assert all(d["dominant"] == "T1" for d in res["decisions"][1:])


def test_structuring_is_repeated_sub_threshold():
    res = run("structuring")
    amounts = [d["request"]["amount"] for d in res["decisions"]]
    assert all(45000 <= a < 50000 for a in amounts)
    payers = [d["request"]["payer"] for d in res["decisions"]]
    assert all(payers.count(p) >= 3 for p in set(payers))      # genuinely repeated, not one-shot smurfs
    assert res["focus"] == planted(res, "collector")[0]
    assert res["explanation"]["typology"] == "T11"
    assert "PAUSE" in outcomes(res)


def test_probing_targets_related_route():
    res = run("probing")
    target = planted(res, "target")[0]
    probes = [d for d in res["decisions"] if d["request"]["amount"] <= 50]
    big = [d for d in res["decisions"] if d["request"]["amount"] > 1000]
    assert len(probes) >= 4 and len(big) == 1
    assert big[0]["request"]["payee"] == target
    assert big[0]["outcome"] == "BLOCK" and big[0]["dominant"] == "T7"
    assert res["focus"] == target


def test_device_farm_shares_device():
    res = run("device_farm")
    farm = planted(res, "farm_device")[0]
    dev = next(n for n in res["graph"]["nodes"] if n["id"] == farm)
    assert dev["status"] == "shared" and dev["metadata"]["account_count"] >= 8
    assert res["focus"] == planted(res, "cashout")[0]
    assert "HOLD_CREDIT" in outcomes(res)


@pytest.mark.parametrize("name", ["normal", "account_takeover", "digital_arrest", "mule_fanin", "structuring",
                                  "probing", "device_farm"])
def test_deterministic(name):
    a = run(name)
    b = run(name)
    assert [(d["request"]["amount"], d["outcome"], d["score"]) for d in a["decisions"]] == \
           [(d["request"]["amount"], d["outcome"], d["score"]) for d in b["decisions"]]


def test_evidence_links_signals_to_graph():
    res = run("mule_fanin")
    ev = res["key_transaction"]["evidence"]
    edge_ids = {e["id"] for e in res["graph"]["edges"]}
    node_ids = {n["id"] for n in res["graph"]["nodes"]}
    assert ev
    for item in ev.values():
        assert set(item["edges"]) <= edge_ids
        assert set(item["nodes"]) <= node_ids


def test_preview_is_side_effect_free_and_counterfactual():
    client.post("/reset")
    before = client.get("/overview").json()
    base = client.post("/analyze/preview", json={"amount": 2500}).json()
    risky = client.post("/analyze/preview", json={"amount": 60000, "new_beneficiary": True, "new_device": True,
                                                  "sim_changed": True, "pin_reset": True}).json()
    assert risky["score"] > base["score"]
    cf = {c["factor"]: c for c in risky["counterfactuals"]}
    assert "sim_changed" in cf and cf["sim_changed"]["score_if"] <= risky["score"]
    after = client.get("/overview").json()
    assert after["monitored"] == before["monitored"] and after["ledger_records"] == before["ledger_records"]


def test_graph_investigation_scopes():
    res = run("mule_fanin")
    mule = res["focus"]
    one = client.get(f"/graph/investigation?account={mule}&hops=1").json()
    full = client.get("/graph/investigation").json()
    assert one["focus"] == mule
    assert len(one["graph"]["nodes"]) <= len(full["graph"]["nodes"])
    acc = client.get(f"/accounts/{mule}").json()
    assert acc["status"] in ("flagged", "suspicious") and acc["transaction_count"] == 12
    assert client.get("/graph/investigation?run_id=NOPE").status_code == 404
