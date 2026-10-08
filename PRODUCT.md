# AgentGuard — product context

_Inferred from the hackathon brief (2026-10-08); assumptions marked._

## What it is
A pre-transaction fraud guard for UPI / net-banking. Six agents (Transaction, Behavior, Velocity,
Mule, AML, Counsel) score every payment before it settles; a policy engine routes it to
Allow / Nudge / Step-up / Cooling room / Hold / Pause / Block; every decision lands in a
hash-chained audit ledger.

## Who uses this surface
- **Primary (demo):** hackathon judges, unfamiliar with the code, watching on a projector or a
  laptop. Must understand within 30 seconds: what AgentGuard does, where money moves, which account
  is suspicious, why, which agents detected it, what was decided, and what evidence supports it.
- **Secondary (product truth):** a bank fraud analyst investigating a case. Dense, precise, keyboard
  friendly; trusts numbers only when it can trace them to evidence.

## Register
Operate mode. A financial-intelligence investigation tool — Bloomberg-terminal clarity, modern
fintech polish, cyber-investigation atmosphere. Not a generic AI SaaS dashboard, not neon cyberpunk.

## Non-negotiables
- Every number on screen comes from the backend; nothing is faked for animation.
- Red means danger (blocked / high risk) and nothing else.
- Motion explains a state change (money moving, risk rising, a decision landing) or it is removed.
- Works offline at the venue: no CDN dependencies at runtime (assumption: venue Wi-Fi is unreliable).
- Default view is 2D; 3D is an optional explorer for large networks.
