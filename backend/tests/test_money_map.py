import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from main import app  # noqa: E402

client = TestClient(app)


def test_v1_accounts_endpoint():
    response = client.get("/v1/accounts")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert {"id", "display_name", "role"} <= set(data[0])


def test_v1_graph_account_empty_history():
    response = client.get("/v1/graph/account/new_user_empty_test?hops=1")
    assert response.status_code == 200
    data = response.json()
    assert data["account"]["id"] == "new_user_empty_test"
    assert data["account"]["risk_level"] == "Safe"
    assert data["account"]["risk_score"] == 0.0
    assert data["received"]["senders"] == [] and data["received"]["total_amount"] == 0.0
    assert data["sent"]["payees"] == [] and data["sent"]["total_amount"] == 0.0
    assert "Received ₹0" in data["summary_sentence"]


def test_v1_graph_account_mule_fanin():
    client.post("/reset")
    sim = client.post("/simulate/mule_fanin").json()
    mule = next(a for a, r in sim["ground_truth"].items() if r == "mule")
    res = client.get(f"/v1/graph/account/{mule}?hops=1").json()
    # risk comes from evidence (decisions + network), not from the planted persona
    assert res["account"]["risk_level"] == "High risk"
    assert res["account"]["risk_score"] >= 80
    assert len(res["received"]["senders"]) >= 8
    assert res["received"]["total_amount"] > 0
    assert len(res["account"]["verdict_reasons"]) >= 2


def test_v1_graph_account_normal_customer():
    client.post("/simulate/normal")
    res = client.get("/v1/graph/account/acc_1?hops=1").json()
    assert res["account"]["id"] == "acc_1"
    assert res["account"]["risk_level"] in ("Safe", "Watch")
