"""Deterministic multi-label typology classifier — maps signal patterns to T1-T14.

The PRD's production head is a multi-label model; the demo uses transparent
rules over the same features so every label is auditable.
"""
from __future__ import annotations
from ..agents.base import Ctx, clip


def classify(ctx: Ctx, feats: dict, agent_scores: dict) -> list[dict]:
    """Returns [{typology, confidence, evidence}] sorted by confidence desc."""
    t, p, payee = ctx.txn, ctx.payer, ctx.payee
    tx = feats.get("transaction", {})
    be = feats.get("behavior", {})
    ve = feats.get("velocity", {})
    mu = feats.get("mule", {})
    am = feats.get("aml", {})
    out: list[dict] = []

    def add(code: str, conf: float, evidence: list[str]):
        if conf >= 0.15:
            out.append({"typology": code, "confidence": round(clip(conf), 3), "evidence": evidence})

    # T1 digital arrest / coercion
    t1 = []
    if be.get("on_call"):
        t1.append(("active call during payment", 0.30))
    if be.get("call_minutes", 0) and be["call_minutes"] >= 20:
        t1.append((f"call running {int(be['call_minutes'])} min", 0.10))
    if tx.get("payee_novel"):
        t1.append(("first-time payee", 0.15))
    if tx.get("drain", 0) >= 0.5:
        t1.append((f"drains {int(tx['drain'] * 100)}% of balance", 0.15))
    if tx.get("amount_z", 0) >= 3:
        t1.append(("amount far above baseline", 0.15))
    if 0 < tx.get("payee_vpa_age_days", 9999) < 30:
        t1.append(("payee account < 30 days old", 0.10))
    if p.persona == "elderly":
        t1.append(("elderly payer profile", 0.05))
    # coercion is what makes it T1: an unusual payment without a call/screen-share is not digital arrest
    if be.get("on_call") or be.get("screen_share"):
        add("T1", sum(c for _, c in t1), [e for e, _ in t1])

    # T3 account takeover via SIM swap
    t3 = []
    if be.get("new_device"):
        t3.append(("unrecognized device", 0.35))
    if be.get("sim_change_72h"):
        t3.append(("SIM changed recently", 0.30))
    if be.get("pin_reset_24h"):
        t3.append(("UPI PIN reset in last 24h", 0.20))
    if be.get("impossible_travel_kmh", 0) >= 500:
        t3.append((f"impossible travel ({int(be['impossible_travel_kmh'])} km/h)", 0.20))
    if tx.get("payee_novel"):
        t3.append(("new payee", 0.10))
    # an unfamiliar device alone is just a new phone; takeover needs a credential change or travel
    if be.get("sim_change_72h") or be.get("pin_reset_24h") or be.get("impossible_travel_kmh", 0) >= 500:
        add("T3", sum(c for _, c in t3), [e for e, _ in t3])

    # T4 remote access / screen share
    t4 = []
    if be.get("screen_share"):
        t4.append(("screen-share/accessibility flag", 0.60))
        if be.get("new_device"):
            t4.append(("new device fingerprint", 0.20))
    add("T4", sum(c for _, c in t4), [e for e, _ in t4])

    # T7 probing
    t7 = []
    if ve.get("micro_burst_10m", 0) >= 4:
        t7.append((f"{ve['micro_burst_10m']} micro-payments in 10 min", 0.60))
    if ve.get("distinct_payees_1h", 0) >= 6:
        t7.append((f"{ve['distinct_payees_1h']} distinct payees in 1h", 0.35))
    linked = _probe_linked_payee(ctx)
    if linked and t.amount > 1000:
        t7.append((f"payee shares a device with {linked} earlier probe recipient(s)", 0.30))
    add("T7", sum(c for _, c in t7), [e for e, _ in t7])

    # T8 fan-in mule collector
    t8 = []
    n_src = mu.get("distinct_sources_24h", 0)
    if n_src >= 3:
        t8.append((f"{n_src} inbound sources in 24h", 0.35 if n_src >= 8 else 0.25 if n_src >= 5 else 0.15))
    if n_src >= 3 and (mu.get("unlinked_sources") or 0) >= 0.7:
        t8.append(("senders are mutually unrelated", 0.10))
    if mu.get("pass_through_24h", 0) >= 0.85:
        t8.append((f"pass-through ratio {mu['pass_through_24h']}", 0.25))
    if mu.get("median_hold_min") is not None and mu["median_hold_min"] < 30:
        t8.append((f"median hold {mu['median_hold_min']} min", 0.20))
    if 0 < mu.get("account_age_days", 9999) < 30:
        t8.append(("payee account < 30 days old", 0.10))
    if mu.get("shared_device_accounts", 0) >= 4:
        t8.append((f"device shared by {mu['shared_device_accounts']} accounts", 0.10))
    add("T8", sum(c for _, c in t8), [e for e, _ in t8])

    # T9 layering chain
    t9 = []
    if mu.get("pass_through_24h", 0) >= 0.8 and mu.get("median_hold_min") is not None \
            and mu["median_hold_min"] < 60:
        t9.append(("rapid onward forwarding", 0.35))
    if am.get("distance_to_flagged_hops") in (1, 2):
        t9.append((f"{am['distance_to_flagged_hops']} hops from a flagged account", 0.30))
    if agent_scores.get("aml", 0) >= 45:
        t9.append(("graph network risk elevated", 0.15))
    add("T9", sum(c for _, c in t9), [e for e, _ in t9])

    # T10 round-tripping
    if am.get("cycle_member"):
        add("T10", 0.7, ["value cycle back to origin detected (async graph job)"])

    # T11 structuring / smurfing
    t11 = []
    if am.get("structuring_aggregation", 0) >= 0.3:
        t11.append(("many just-below-threshold senders aggregating on payee", 0.45))
    if (am.get("unlinked_fraction") or 0) >= 0.6:
        t11.append(("senders mutually unlinked", 0.25))
    if ve.get("sub_threshold_count_24h", 0) >= 2:
        t11.append((f"{ve['sub_threshold_count_24h']} sub-threshold payments by payer", 0.20))
    add("T11", sum(c for _, c in t11), [e for e, _ in t11])

    # T12 mule farm (shared device infrastructure)
    t12 = []
    if mu.get("shared_device_accounts", 0) >= 5:
        t12.append((f"device shared by {mu['shared_device_accounts'] + 1} accounts", 0.50))
    elif mu.get("shared_device_accounts", 0) >= 2:
        t12.append((f"device shared by {mu['shared_device_accounts'] + 1} accounts", 0.25))
    if t12:   # a mule *farm* needs shared device infrastructure; the rest only corroborates it
        if (mu.get("unlinked_sources", 0) or 0) >= 0.7:
            t12.append(("inbound sources mutually unlinked", 0.30))
        if 0 < mu.get("account_age_days", 9999) < 30:
            t12.append(("young account", 0.15))
        add("T12", sum(c for _, c in t12), [e for e, _ in t12])

    # T13 dormant awakening
    t13 = []
    if mu.get("dormant_awakening"):
        t13.append(("dormant account suddenly active with volume", 0.60))
    if mu.get("distinct_sources_24h", 0) >= 5:
        t13.append(("multi-source inflow", 0.20))
    add("T13", sum(c for _, c in t13), [e for e, _ in t13])

    out.sort(key=lambda d: -d["confidence"])
    return out


def _probe_linked_payee(ctx: Ctx) -> int:
    """How many of the payer's micro-payment recipients (last hour) are this payee
    or share a device with it — i.e. the probes were testing this payee's route."""
    t = ctx.txn
    probed = {m[3] for m in ctx.store.ledger_moves.get(t.payer, ())
              if m[1] == "out" and m[2] <= 50 and m[0] >= t.ts - 3600}
    if not probed:
        return 0
    g = ctx.graph
    payee_devices = {g.edges[i]["dst"] for i in g._out.get(t.payee, ()) if g.edges[i]["type"] == "USED_DEVICE"}
    linked = 0
    for acc in probed:
        if acc == t.payee:
            linked += 1
            continue
        devs = {g.edges[i]["dst"] for i in g._out.get(acc, ()) if g.edges[i]["type"] == "USED_DEVICE"}
        if devs & payee_devices:
            linked += 1
    return linked
