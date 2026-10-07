"""Explainer Agent (6th): turns the decision into (1) audit reasons, (2) analyst summary, (3) customer message.
- Customer text is TEMPLATE-based on purpose: deterministic, compliant, and never tips off a suspect.
  AML/mule/blacklist/sanctions findings are never disclosed to the customer ("tipping off" risk).
- Analyst narrative is optional LLM polish (Anthropic API) with a strict template fallback."""
import os

SECRET_PREFIXES = ("AML_", "MULE_", "KNOWN_MULE", "BLACKLISTED", "SANCTIONED")
SAFE_REASONS = [   # (test(agent_results) -> bool, customer-safe phrase)
    (lambda d: d.get("transaction", {}).get("new_payee"), "this is a new payee"),
    (lambda d: d.get("transaction", {}).get("amount_z", 0) > 1.5, "the amount is higher than your usual payments"),
    (lambda d: d.get("behavior", {}).get("new_device"), "the payment came from a new device"),
    (lambda d: any(f in d.get("_flags", ()) for f in ("BEH_IMPOSSIBLE_TRAVEL",)), "the location looks unusual"),
    (lambda d: any(f.startswith("VEL_") for f in d.get("_flags", ())), "there were many payments in a short time"),
]


class ExplainerAgent:
    name = "explainer"

    def __init__(self, use_llm=False):
        self.use_llm = use_llm and bool(os.getenv("ANTHROPIC_API_KEY"))
        self.model = os.getenv("AG_LLM_MODEL", "claude-sonnet-5-5")

    # -------------------------------------------------------------- public
    def explain(self, txn, out):
        agents = sorted(out["agents"], key=lambda a: -a["score"])
        top = [{"agent": a["agent"], "score": a["score"], "reason": a["reason"]} for a in agents if a["score"] >= 30]
        audit = [f"{t['agent']}({t['score']:.0f}): {t['reason']}" for t in top]
        if out.get("rule_note"): audit.append(f"rule: {out['rule_note']}")
        if not audit: audit = ["no agent raised material risk"]
        analyst, src = self._analyst(txn, out, top)
        return {"customer_message": self._customer(txn, out), "analyst_summary": analyst,
                "audit_reasons": audit, "top_factors": top, "narrative_source": src}

    # -------------------------------------------------------------- customer (never discloses AML/mule logic)
    def _customer(self, txn, out):
        amt, d = f"₹{float(txn['amount']):,.0f}", out["decision"]
        if d == "SUCCESS":
            return f"Your payment of {amt} was successful."
        secret = any(f.startswith(SECRET_PREFIXES) for f in out["flags"])
        detail = {a["agent"]: a.get("details", {}) for a in out["agents"]}
        detail["_flags"] = out["flags"]
        reasons = [] if secret else [p for t, p in SAFE_REASONS if t(detail)][:2]
        because = (" We asked because " + " and ".join(reasons) + ".") if reasons else ""
        if d == "VERIFY":
            return f"To keep your money safe, please confirm this {amt} payment with the OTP sent to your registered mobile number.{because}"
        if d == "PAUSE":
            return f"Your {amt} payment is on a short hold while we complete a security check.{because} We'll update you shortly."
        return f"We couldn't complete this {amt} payment. For your security, please contact your bank's support line."

    # -------------------------------------------------------------- analyst narrative
    def _analyst(self, txn, out, top):
        facts = (f"{out['decision']} (risk {out['score']}/100) for {txn['channel']} payment of ₹{float(txn['amount']):,.0f} "
                 f"{txn['src']}→{txn['dst']}. Drivers: " + ("; ".join(f"{t['agent']} {t['score']:.0f}: {t['reason']}" for t in top[:3]) or "none")
                 + (f". Flags: {', '.join(out['flags'])}" if out["flags"] else "")
                 + (f". Rule: {out['rule_note']}" if out.get("rule_note") else ""))
        if self.use_llm:
            try:
                import anthropic
                msg = anthropic.Anthropic(timeout=3.0).messages.create(
                    model=self.model, max_tokens=200,
                    messages=[{"role": "user", "content":
                               "Write a 2-sentence fraud-analyst summary of this decision. Use ONLY these facts, invent nothing:\n" + facts}])
                return msg.content[0].text.strip(), "llm"
            except Exception:
                pass
        return facts, "template"
