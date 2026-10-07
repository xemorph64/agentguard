"""Explainer — customer/analyst messages and the structured debate log.

Victim-side messages are explicit and protective; beneficiary-side messages are
generic and non-tipping (RBI KYC Master Direction / PMLA tipping-off rule).
Free-form LLM text is never shown to customers — narratives are templates.
"""
from __future__ import annotations

from ..core.types import AgentResult, Decision, TYPOLOGIES

# message keys -> text; the customer app resolves these against its language pack
CUSTOMER_MESSAGES = {
    "BLOCK": {
        "title": "payment_declined",
        "body": "This payment was declined for your safety. No police, CBI, RBI or court "
                "ever asks for money over UPI or a video call.",
    },
    "COOLING_ROOM": {
        "title": "cooling_room",
        "body": "This payment looks unusual for you. Let's pause for a moment and check "
                "a few things together.",
    },
    "HOLD_CREDIT": {
        "title": "payment_pending",
        "body": "Payment sent. The receiver's account is being verified — usually a few "
                "minutes. Your money stays protected until then.",
    },
    "STEP_UP": {
        "title": "verify_it_is_you",
        "body": "Before we send this, confirm it's really you with your phone's biometrics.",
    },
    "PAUSE": {
        "title": "under_review",
        "body": "This payment is under a quick review — usually under 10 minutes.",
    },
    "ALLOW_NUDGE": {
        "title": "heads_up",
        "body": "Payment sent. A quick note: this is your first payment to this payee.",
    },
    "ALLOW": {"title": "payment_sent", "body": "Payment sent."},
}

BENEFICIARY_GENERIC = "Credit is being processed. It may take slightly longer than usual."


def customer_message(outcome: str, hold_minutes: int | None = None) -> str:
    m = CUSTOMER_MESSAGES.get(outcome, CUSTOMER_MESSAGES["ALLOW"])
    parts = [f"{m['title']}: {m['body']}"]
    if outcome == "HOLD_CREDIT" and hold_minutes:
        parts.append(f"Expected release in about {hold_minutes} minutes.")
    return " ".join(parts)


def beneficiary_message(_decision: object = None) -> str:
    return BENEFICIARY_GENERIC  # never tips off, whatever the risk


def debate_log(agents: list[AgentResult], fused: float, typologies: list,
               routing_reason: str, counterfactual: dict | None = None) -> list[dict]:
    """The debate log is a structured transcript, not LLM chatter (PRD §6)."""
    lines = []
    for a in agents:
        if a.agent == "counsel":
            if a.score > 20:
                lines.append({"actor": "Counsel", "impact": -1, "value": a.score,
                              "text": f"legitimacy {a.score:.0f}/100 → bounded discount applied"})
            continue
        top = a.signals[0]["feature"] if a.signals else "no signals"
        val = a.signals[0]["value"] if a.signals else ""
        lines.append({
            "actor": a.agent.capitalize(), "impact": 1 if a.score >= 0 else 0,
            "value": a.score, "text": f"score {a.score:.0f} · top signal {top}={val}",
        })
    typ = " + ".join(f"{t['typology']} ({t['confidence']:.2f})" for t in typologies[:2]) or "none"
    lines.append({"actor": "Policy", "impact": 0, "value": fused,
                  "text": f"score {fused:.0f} · typology {typ} · {routing_reason}"})
    if counterfactual:
        lines.append({"actor": "Counterfactual", "impact": 0, "value": counterfactual.get("score_if"),
                      "text": counterfactual.get("text", "")})
    return lines


def analyst_narrative(decision: Decision, graph_facts: list[str]) -> str:
    typ = ", ".join(f"{t['typology']} {TYPOLOGIES.get(t['typology'], '')}"
                    for t in decision.typologies[:2]) or "no specific typology"
    top_agents = sorted((a for a in decision.agent_results if a.agent != "counsel"),
                        key=lambda a: -a.score)[:3]
    drivers = "; ".join(f"{a.agent} {a.score:.0f} ({a.signals[0]['feature'] if a.signals else '—'})"
                        for a in top_agents)
    narrative = (
        f"Deterministic template — not LLM. Txn {decision.txn_id} scored {decision.score:.0f} → "
        f"{decision.outcome}. Dominant pattern: {typ}. Main drivers: {drivers}. "
    )
    if graph_facts:
        narrative += "Graph evidence: " + "; ".join(graph_facts) + ". "
    narrative += f"Routing: {decision.routing_reason}. Hard rule: {decision.hard_rule or 'none'}."
    return narrative
