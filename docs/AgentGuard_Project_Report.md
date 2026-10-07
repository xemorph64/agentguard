# AgentGuard

### A Multi-Agent AI Guardian for Real-Time Fraud, Mule Account, and Money Laundering Detection in UPI and Net Banking Payments

*Project report: problem statement, abstract, objectives, workflow, technology stack, impact, and roadmap*

---

## 1. Abstract

India's Unified Payments Interface (UPI) now moves more than 800 million payments every day. Its strength is also its weakness: a payment is instant and, once authorised, almost impossible to reverse. Fraudsters exploit this with collect-request scams, account takeovers, and above all **mule accounts**, ordinary bank accounts that receive stolen money and pass it on within minutes. Most deployed defences are rule based, judge each transaction in isolation, and cannot explain themselves clearly to a customer, an analyst, or an auditor.

**AgentGuard** is a real-time, pre-transaction defence system for UPI and net-banking payments. Every payment passes through an **orchestrator** that coordinates **six cooperating AI agents**: Transaction, Behavior, Velocity, Mule, AML (anti-money-laundering), and Explainer. Each scoring agent examines the payment from a different angle, combining machine-learning models (XGBoost, Random Forest, Isolation Forest), graph analysis (NetworkX), and domain rules specific to UPI and net banking. The orchestrator merges their scores, applies hard overrides such as a blacklist, and returns one of four decisions: **Success, Verify, Pause, or Block**. Verify triggers an OTP step-up, Pause sends the payment to a human analyst, and Block stops the payment and raises an alert.

Every decision is explained in three forms (an audit reason, an analyst summary, and a customer-safe message) and written to a **tamper-evident, hash-chained ledger**. The system also includes a labelled scenario simulator, an analyst review queue, a drift monitor, a fraud-ring graph export, and an agents-versus-rules comparison, all served through a FastAPI backend with a live WebSocket feed.

On synthetic test traffic, the agent system flagged about 87% of fraudulent transactions against about 66% for a simple rule baseline, with decisions in roughly 140 ms. These figures come from synthetic data and must be re-measured on real data (Section 15).

**Keywords:** UPI, real-time fraud detection, multi-agent systems, mule accounts, anti-money laundering, graph analysis, explainable AI, pre-transaction defence.

---

## 2. Background and Context

### 2.1 The scale of UPI

UPI has become the backbone of everyday payments in India. According to data released by the National Payments Corporation of India (NPCI), UPI processed about **24.07 billion transactions worth ₹29.37 lakh crore in September 2026**, an average of roughly **802 million transactions per day**, with volume up 23% on the previous year. For the full financial year 2025-26, the government reported about 24,162 crore (over 241 billion) UPI transactions, a rise of almost 13,000 times since 2016-17.

At this scale, even a very small fraud rate produces a very large number of victims. The RBI Deputy Governor has noted that UPI fraud incidents are about 0.68 per one lakh transactions, a low rate, but one that still translates into lakhs of cases a year, and the RBI has publicly argued for a "zero-fraud" objective to protect public trust.

### 2.2 The growth of payment fraud

Figures presented to the Lok Sabha by the Ministry of Finance show how quickly the problem has grown:

| Financial year | Reported UPI fraud cases | Amount involved |
|---|---|---|
| 2021-22 | 4.07 lakh | ₹242 crore |
| 2022-23 | not itemised here | ₹573 crore |
| 2023-24 | 13.42 lakh | ₹1,087 crore |
| 2024-25 | 12.64 lakh | ₹981 crore |
| 2025-26 (till November) | 10.64 lakh | ₹805 crore |

Cases rose more than threefold between 2021-22 and 2023-24 and have stayed above ten lakh a year since. Government and RBI measures such as device binding, PIN-based two-factor authentication, transaction limits, and NPCI's fraud-monitoring solution have helped, but the numbers show the problem is far from solved.

### 2.3 The mule account problem

A fraudster who steals money rarely keeps it in one place. The funds are moved through a chain of **mule accounts**, accounts opened with real or borrowed identities, or ordinary accounts whose owners are recruited or deceived, to break the trail before the money is cashed out. Recognising this, the RBI introduced **MuleHunter.AI**, an AI/ML tool for identifying mule accounts, in December 2024 and piloted it with public-sector banks. Reports also note that such a tool currently works on one bank's data at a time, while real laundering chains cross banks. This shows both the importance of the problem and the room that remains for better detection.

---

## 3. Problem Statement

> **How can a payment system decide, within a fraction of a second and before the money leaves the account, whether a UPI or net-banking payment is legitimate, fraudulent, or part of a money-laundering chain, and explain that decision clearly to the customer, the analyst, and the auditor?**

This single question breaks into six concrete problems.

**Problem 1: Irreversibility.** An authorised UPI payment settles instantly. Detection after the fact can only try to recover funds; it cannot prevent the loss. The decision must be made *before* the payment completes, inside a tight latency budget.

**Problem 2: Single-transaction thinking.** Many rule systems look at one payment at a time. Mule chains, structuring (splitting a large amount into many just-below-threshold payments), and round-tripping (money circling through accounts) are only visible when payments are viewed together as a network over time.

**Problem 3: Mule accounts blend in.** A mule account has no fraud history. Its individual payments look ordinary. The warning signs are behavioural and structural: many unrelated senders, funds forwarded within minutes, almost nothing kept, a very young account, a shared device. A rule on amount or location will not catch these.

**Problem 4: Fixed rules and false alarms.** A rule such as "block anything above ₹50,000 to a new payee" catches obvious cases but also blocks honest customers paying rent or a deposit, while fraudsters simply stay under the threshold. Overly strict rules damage customer trust; overly loose rules let fraud through.

**Problem 5: Opaque decisions.** When a payment is stopped, three different people need three different explanations. The customer needs a clear, polite, safe message. The analyst needs the evidence. The regulator needs a record that cannot be quietly altered. Black-box scores satisfy none of them.

**Problem 6: Constant change.** Fraud patterns shift, and so does honest customer behaviour. A model trained once will slowly degrade unless drift is monitored and human decisions are fed back into training.

AgentGuard is designed as a single system that addresses all six problems together.

---

## 4. Gap Analysis: Why Existing Approaches Fall Short

| Approach | What it does well | Where it falls short |
|---|---|---|
| Fixed rules (amount, time, location) | Simple, fast, easy to explain | Easy to evade, many false alarms, blind to networks |
| One large ML classifier | Learns subtle patterns | One opaque score, hard to explain, weak on rare fraud types, no network view |
| Post-transaction analytics | Good for investigation and recovery | Too late: the money has already moved |
| Bank-by-bank mule detection | Strong inside one bank's data | Cannot follow money across banks (a data-sharing problem for the ecosystem) |

AgentGuard's answer is **specialisation plus coordination**: several focused agents, each good at one kind of evidence, combined by an orchestrator that can also apply hard business rules and produce explanations. This mirrors how a human fraud team works: a payments specialist, a behaviour analyst, a mule investigator, and an AML officer reviewing the same case.

---

## 5. Objectives

### 5.1 Primary objective

Build a real-time, explainable fraud, mule, and money-laundering guard for UPI and net-banking payments that makes a four-way decision (Success, Verify, Pause, Block) before the payment completes.

### 5.2 Specific objectives

1. **Detect fraud across several patterns at once**: unusual amounts, new payees, collect-request scams, account takeover, micro-payment probing, bursts of payments, impossible travel, and shared devices.
2. **Detect and block mule-account payments** using a dedicated machine-learning model combined with graph evidence, checking both the receiver and the sender.
3. **Detect money-laundering typologies** such as structuring, rapid pass-through, fan-in aggregation, and round-tripping, using a dedicated model plus typology rules.
4. **Coordinate the agents through one orchestrator** that merges scores, lets a single strong signal escalate a case, and applies hard rules (blacklist, sanctioned country, corroborated mule, whitelist).
5. **Keep the decision fast**: a target under 200 milliseconds from request to decision.
6. **Explain every decision** in three audiences' terms, without ever revealing AML or mule suspicion to the customer.
7. **Make the audit trail tamper-evident** with a hash-chained ledger and a one-click integrity check.
8. **Support human oversight** through an analyst review queue whose verdicts become labelled feedback for retraining.
9. **Monitor model health** with a drift monitor and measurable metrics (precision, recall, false-positive rate, latency).
10. **Demonstrate and test** the system with a labelled scenario simulator and a side-by-side comparison against a simple rule-based system.

### 5.3 Success measures

| Measure | Target |
|---|---|
| Decision latency | Under 200 ms (mean and 95th percentile) |
| Fraud recall at the "challenge or stop" level | Higher than the rule baseline on the same transactions |
| Hard-stop precision (Pause or Block) | As high as possible; very few honest payments stopped |
| Explanation coverage | 100% of decisions carry audit, analyst, and customer text |
| Ledger integrity | Any edit to a past decision is detected |

---

## 6. Scope

**In scope.** Payments made through **UPI** and **net banking**: account-to-account digital transfers where the sender, receiver, device, and time are known. Fraud types covered include collect-request scams, account takeover, mule-account payments, structuring, round-tripping, device-farm activity, and rapid multi-payee bursts.

**Out of scope for this version.** Real bank or NPCI integration, cross-bank data sharing, a production database, and a finished analyst dashboard (planned; see Section 21). The system runs on public or synthetic data and exposes a standard API that a bank could later connect to.

---

## 7. Proposed Solution Overview

AgentGuard is organised in clear layers:

1. **Entry layer.** Payments from UPI and net banking enter a FastAPI gateway (`POST /analyze`).
2. **Real-time feature layer.** Four shared services prepare evidence once per transaction: an account **history store**, a **velocity store** (sliding time windows), a **live transaction graph**, and a shared **feature builder**.
3. **Decision engine.** The **orchestrator** first checks hard rules, then runs five scoring agents in parallel, combines their scores, and maps the result to a decision.
4. **Action layer.** Each decision triggers an action: settle the payment, send an OTP, queue for an analyst, or block and alert.
5. **Accountability layer.** The **Explainer agent** writes the explanations, the **audit ledger** records the decision, and analyst verdicts return as **feedback labels**.

The design principle throughout is *compute shared evidence once, let specialists judge it, let the orchestrator decide, and explain everything*.

---

## 8. Workflow of the Project

### 8.1 End-to-end flow for one payment

1. **Receive.** A payment request arrives with the sender, receiver, amount, channel (UPI or net banking), time, and optional signals such as device ID, location, IP country, session age, and whether it is a collect request.
2. **Hard-rule check.** The orchestrator checks the blacklist (accounts, VPAs, devices). A match ends the process immediately with a **Block**.
3. **Build shared context.** The feature layer computes the transaction's features once: its amount relative to the sender's history, whether the payee is new, whether the device is new, counts of recent payments, and how much money the sender received recently.
4. **Parallel scoring.** Five agents score the payment from 0 to 100, each returning a reason and optional flags.
5. **Combine.** The orchestrator computes a weighted score. If the single strongest agent is at 50 or above, the final score is pulled halfway toward that agent's score, so one confident specialist cannot be diluted by four quiet ones.
6. **Hard overrides.** Flags such as a corroborated mule or a sanctioned country force a Block. A whitelisted payee can downgrade a Verify or Pause to Success.
7. **Decide.** The score maps to one of four decisions (Section 11).
8. **Explain.** The Explainer agent writes the audit reasons, the analyst summary, and the customer message.
9. **Record.** The decision is appended to the hash-chained ledger and broadcast on the live WebSocket feed.
10. **Act.** The system performs the action: settle, OTP, queue, or block and alert.
11. **Learn.** The transaction is added to the history, velocity, and graph stores, so the next payment benefits. A confirmed bad payee is flagged in the graph, strengthening signals for its neighbours.

### 8.2 What happens after each decision

| Decision | Score | What happens next |
|---|---|---|
| **Success** | 0 to 30 | Payment settles at once; customer is told it succeeded. |
| **Verify** | 31 to 60 | A one-time password is sent (mock SMS). Correct code completes the payment; three wrong or expired attempts escalate it to the analyst queue. |
| **Pause** | 61 to 80 | Payment is held and appears in the analyst review queue. The analyst approves (completes the payment) or rejects, optionally blacklisting the payee. |
| **Block** | 81 to 100 or hard rule | Payment is stopped; SMS and email alerts are logged; a confirmed payee is flagged in the graph. |

### 8.3 The human feedback loop

Every analyst approval or rejection is stored as a labelled example (approved means legitimate, rejected means fraud). These labels are exposed through a feedback endpoint and are the input for periodic retraining, so the system improves from the exact cases it found hard.

### 8.4 Worked examples

**Example B: UPI collect-request scam.** A customer receives a "collect request" from an unknown payee at 2:30 am for ₹48,000, from a new device with a foreign IP address, and approves it within three seconds of opening the session. The Transaction agent sees an amount about 26 times the customer's norm, a first-time payee, an unusual hour, and the collect-request-to-new-payee rule; the Behavior agent sees a new device and a bot-like session. The final score is about 74, so the payment is **Paused**. The customer is told "your payment is on a short hold while we complete a security check, because this is a new payee and the amount is higher than your usual payments."

**Example C: Mule chain.** Ten unrelated people pay a brand-new account within minutes; that account then forwards most of the money within a couple of hours. The AML agent sees rapid pass-through, the Mule agent sees fan-in, a very short holding time, and nothing retained, and the graph shows many senders converging on one account. The forwarding payment is escalated to **Pause or Block**, and the customer-facing message does not mention laundering or mules.

---

## 9. The Six Agents in Detail

### 9.1 Transaction Agent: "Is this payment normal for this customer?"

It judges the payment against the customer's own history. It uses a robust statistical measure (median and median absolute deviation on log amounts) to ask how unusual the amount is *for this person*, falling back to a general baseline for customers with thin history. It also checks whether the payee is new (more suspicious when the amount is large), whether the hour is unusual for this customer, and whether the amount sits just under a channel limit.

It then applies **channel-specific rules**, combined with a "noisy-OR" so independent warning signs reinforce each other:

- **UPI:** a collect request to a new payee; an amount near the per-transaction limit; a very new payee address with a sizeable amount; a night-time payment to a new payee; a foreign IP combined with a new device.
- **Net banking:** a large transfer to a beneficiary added within 24 hours; a large first-time transfer; a credential change followed by a new payee (a classic takeover pattern); a foreign IP combined with a new device.

### 9.2 Behavior Agent: "Is the person and device behaving normally?"

It combines an unsupervised model with device and location intelligence:

- An **Isolation Forest** scores how unusual the behaviour is compared with training traffic. Only the most unusual roughly 10% contribute to risk, so ordinary variation does not trigger alarms. Because it needs no fraud labels, it can flag patterns that have never been seen before.
- **Device intelligence:** a device the customer has never used, and, more strongly, one device shared by four or more accounts, a typical signature of a mule farm.
- **Impossible travel:** a distance covered faster than any plausible travel (for example, Pune to Delhi in ten minutes).
- **Geography:** an IP country that differs from the customer's home country.
- **Session signals:** a large payment seconds after login, which suggests an automated script.

### 9.3 Velocity Agent: "Is the pace of payments abnormal?"

It tracks payments in sliding windows (1 minute, 5 minutes, 1 hour, 24 hours) and looks for:

- bursts of many payments in a minute;
- **micro-payment probing**, several tiny payments to different new IDs followed by a large one;
- many different payees within an hour;
- escalating amounts that look like limit-testing;
- a 24-hour spend several times the customer's own daily average;
- several failed attempts followed by a success (a credential-stuffing pattern).

It is implemented without heavy libraries so it stays fast.

### 9.4 Mule Agent: "Is the sender or receiver a mule account?"

This is the agent that most directly targets the project's core problem. It evaluates **both** the payee and the sender, because a mule can appear on either side of a payment.

- An **XGBoost model** scores the account on 20 behavioural features: the number of incoming and outgoing payments, how many distinct senders and receivers it has, how much it received and sent, the **pass-through ratio** (how much of what came in went out), the **median holding time** before forwarding, the share of money forwarded within six hours, the account's age, its activity density, and the share of round or cross-bank inflows.
- The model's raw output is **calibrated** (isotonic calibration) so that its numbers behave like true probabilities, and two thresholds are chosen on validation data: a "suspected" tier with good recall and a stricter "corroborated" tier with high precision.
- The final risk blends **70% model and 30% graph evidence**. Graph evidence includes membership in a circular money loop, nearby accounts already flagged, and a fan-in pattern combined with rapid pass-through.
- A payment is **hard-blocked only when the model and the graph agree** (flag: *corroborated mule*). A model-only suspicion raises the score but leaves the final call to the orchestrator and other agents. This two-signal rule keeps ordinary busy accounts, such as shops, from being blocked simply for receiving many payments.
- Each decision includes the **top three SHAP-style drivers** (true SHAP contributions from XGBoost), so an analyst can see exactly which features pushed the score up.

### 9.5 AML Agent: "Does this look like money laundering?"

It combines a learned model with explicit laundering typologies:

- A **Random Forest** (calibrated) scores the transaction on features such as amount, time, cross-border indicator, whether the payee is new, how much the sender sent and received in the past 24 hours, and the **forward ratio** (how much of recently received money this payment is moving on).
- **Typology rules** catch the textbook patterns: **structuring** (several payments just under a reporting-style limit), **rapid pass-through**, **fan-in aggregation**, **round-tripping** (detected through loops in the graph), high-value cross-border transfers, velocity spikes, and large first-time payments.
- The score is 65% model plus 35% rules, with a bonus when both agree strongly.
- A configurable list of high-risk jurisdictions can force a Block. The list shipped in the project is an *example* and must be replaced with the current official list in any real deployment.

### 9.6 Explainer Agent: "Why was this decision made, and who needs to know what?"

It runs after the decision and produces three outputs:

1. **Audit reasons**: a structured list of the agents' findings and any rule that applied, stored in the ledger.
2. **Analyst summary**: a short narrative of the decision and its main drivers. This can optionally be polished by a large language model, with a deterministic template as fallback so the system never depends on an external service.
3. **Customer message**: always generated from fixed templates, never free text. This is deliberate. The message gives only safe, general reasons ("this is a new payee", "the amount is higher than usual") and **never discloses AML, mule, blacklist, or sanctions findings**, because telling a suspect they are under suspicion could compromise an investigation and may be legally problematic ("tipping off").

---

## 10. Machine Learning Models and Data

### 10.1 Model summary

| Model | Algorithm | Purpose | Trained on |
|---|---|---|---|
| Mule detector | XGBoost + isotonic calibration | Probability that an account is a mule | Account snapshots at several points in time |
| AML detector | Random Forest + isotonic calibration | Probability that a transaction is laundering | Transactions, chronological split |
| Behavior detector | Isolation Forest | How unusual the behaviour is (no labels needed) | Training-period transactions |
| Fraud-ring graph | NetworkX (graph analysis, no training) | Loops, fan-in, clusters, flagged neighbours | Live transaction stream |

### 10.2 Data

The project is designed around the public **IBM Transactions for Anti-Money-Laundering (AML)** dataset, which contains account-to-account transfers with laundering labels, a natural fit for UPI-style payments. Because real data may not always be available, the project includes a **synthetic data generator** that creates realistic legitimate traffic (customers who repeatedly pay favourite payees, merchants who legitimately receive many payments, payroll runs that legitimately pay many employees) plus injected laundering patterns: mule chains, round-trip cycles, and structuring. Including legitimate "look-alike" cases (busy merchants, payroll) is important, because it forces the models to learn real mule behaviour rather than simply "many incoming payments".

### 10.3 Methods that make the models trustworthy

- **One feature code for training and serving**, which avoids train/serve skew, a common cause of models that look good offline but fail live.
- **Point-in-time training for the mule model.** Snapshots taken at six points in time teach the model to spot mules from partial history, which is when detection is most valuable.
- **Leak-free splits.** Mule snapshots are split by account, and the AML model trains on the past and tests on the future.
- **Imbalance, calibration, and thresholds.** Class weighting handles rare fraud, scores are calibrated into honest probabilities, and thresholds are set from validation data for chosen precision or recall targets.

---

## 11. Decision Engine

### 11.1 Weights and scoring

Five agents contribute to the weighted score: Mule 25%, Transaction 20%, Behavior 20%, AML 20%, Velocity 15%. The weighted average alone would have a flaw: if four agents see nothing unusual and one sees a near-certain mule, the average stays low. The orchestrator therefore adds a **peak-aware boost**: when the strongest agent scores 50 or more, the final score moves halfway from the weighted average toward that agent's score. Weak noise never triggers the boost.

### 11.2 Four decisions

| Score | Decision | Meaning |
|---|---|---|
| 0 to 30 | Success | Looks normal |
| 31 to 60 | Verify | Plausible but uncertain: ask the customer to confirm |
| 61 to 80 | Pause | Suspicious: hold for a human |
| 81 to 100 | Block | Very likely fraud or laundering: stop |

### 11.3 Hard rules

Certain facts should not be averaged away. A blacklisted account or device, a corroborated mule, or a sanctioned jurisdiction forces a Block regardless of the score. Conversely, a payee that an analyst has whitelisted can downgrade a Verify or Pause to Success, which protects regular customers from repeat friction.

---

## 12. Explainability, Auditability, and Compliance

**Explainability.** Every agent returns a readable reason and its top contributing features. The orchestrator also keeps a **debate log**, listing what each agent said and, when agents disagreed by more than 40 points, how the disagreement was resolved (for example, "Mule agent 90, Behavior agent 10, resolved by hard rule").

**Auditability.** Each decision is appended to a **hash-chained ledger**: every entry holds the SHA-256 hash of the previous one, so editing any past decision breaks every later hash. A verification endpoint checks the chain, and a demo button tampers with an old entry to show the break being caught. (A demo-only repair function exists for presentations; a production ledger would not offer one.)

**Customer protection.** Customer messages are template-only and never reveal AML or mule suspicion. Paused payments always reach a human analyst, so no customer is blocked by a model alone without a route to review.

---

## 13. Technology Stack

### 13.1 Built and working

| Layer | Technology | Role |
|---|---|---|
| Language | Python 3.12 | Whole backend |
| API and real-time | FastAPI, Uvicorn, Pydantic, WebSockets | REST endpoints, validation, live decision feed |
| Machine learning | scikit-learn (Random Forest, Isolation Forest, isotonic calibration), XGBoost | The three models |
| Data processing | pandas, NumPy | Feature engineering |
| Graph analysis | NetworkX | Loops, fan-in, clusters, ring export |
| Explainability | XGBoost native SHAP contributions; optional `shap` library | Per-decision feature drivers |
| Optional language model | Anthropic API | Polishing analyst summaries (template fallback) |
| Audit | SHA-256 hash chain (Python `hashlib`) | Tamper-evident ledger |
| Testing | FastAPI TestClient, scripted scenario tests | End-to-end verification |
| Datasets | IBM AML dataset; built-in synthetic generator | Training and testing |

### 13.2 Planned

| Layer | Technology | Role |
|---|---|---|
| Frontend | React with Vite, Tailwind CSS, Recharts, React Flow or vis-network | Live dashboard, review queue, graph and ledger views, metrics |
| Database | MongoDB | Replace in-memory stores with persistent storage |
| Deployment | Docker; AWS or GCP | Containerised cloud deployment |

The stack is deliberately built from widely used, free tools so that the project can be understood, reproduced, and extended by a student team without specialist infrastructure.

---

## 14. Features and API

**Backend capabilities (implemented):**

- `POST /analyze` returns the decision, agent breakdown, debate log, and explanations; `WS /ws/feed` streams decisions live.
- Analyst review queue (approve or reject, optional payee blacklisting) and a feedback export for retraining.
- Mock OTP step-up with hashed codes, expiry, and a three-attempt limit that escalates to an analyst.
- Ledger view, verification, and tamper demonstration; blacklist and whitelist management.
- A simulator with ten scenarios: normal, drifted traffic, mule chain, structuring, round-trip cycle, UPI scam, account takeover, micro-payment probing, device farm, and slow drip.
- Metrics, drift report, fraud-graph export, customer trust profile, and an agents-versus-rules comparison.

**Frontend (planned):** a live dashboard with colour-coded decisions, a transaction-detail view with per-agent scores and the debate log, the analyst queue, the fraud-ring graph with suspicious clusters in red, a ledger viewer, a simulator with comparison, and a metrics and drift page.

---

## 15. Implementation Status and Results

### 15.1 Status

The backend (agents, orchestrator, models, API, simulator, ledger, tests) is implemented and tested end to end. The dashboard, persistent database, and cloud deployment are planned.

### 15.2 Results so far (synthetic data)

All numbers below were produced on **synthetic data** and show that the system works as designed. They are not evidence of real-world accuracy.

- **Agents versus rules.** On the same batch of labelled transactions (roughly 100 fraudulent cases), a simple rule baseline (amount above ₹50,000, or a new payee above ₹20,000, or a night-time payment above ₹10,000) caught about **66%** of fraud with almost no false alarms. The agent system flagged about **87%** at the "challenge or stop" level, at the price of about 13% of honest transactions being challenged. Its hard stops (Pause or Block) had very few false alarms but lower recall.
- **Mule model.** On held-out accounts, the model's precision-recall AUC was about 0.78. The strict tier reached about 98% precision at about 62% recall; the looser tier about 86% precision at about 70% recall.
- **AML model.** Its near-perfect score on synthetic data simply reflects that the injected laundering patterns are cleaner than real ones. Real data is expected to give lower numbers.
- **Speed.** Mean decision time was about 138 to 163 ms, and the 95th percentile about 200 to 230 ms, against a 200 ms target. Roughly 95% of API decisions met the target in the latest run.
- **Scenario behaviour.** The collect-request scam was held or challenged, the account takeover was held or blocked, probing followed by a large payment was blocked, a blacklisted payee was blocked immediately, and an edited ledger entry was detected.

---

## 16. Impact

### 16.1 For customers

- **Fewer losses.** Stopping a payment before it settles is the only protection that matters for irreversible transfers.
- **Less friction.** Step-up verification and analyst review mean most honest customers are confirmed in seconds instead of being blocked outright, and whitelisting removes repeat friction for trusted payees.
- **Clear communication.** Messages explain what happened in plain language without causing alarm.

### 16.2 For banks and payment providers

- **Lower fraud losses and fewer chargeback-style disputes**, plus lower costs of investigation.
- **Analyst productivity.** Analysts receive a ranked queue with evidence, drivers, and a debate log instead of a bare alert.
- **Audit readiness.** A tamper-evident ledger and per-decision reasons simplify regulatory reporting and internal audit.
- **Mule detection earlier in the chain**, reducing the success rate of laundering networks.

### 16.3 For regulators and the ecosystem

- **Support for the zero-fraud goal** that the RBI has publicly articulated, and an example of the AI-based mule detection direction the RBI has begun with MuleHunter.AI.
- **A transparent model for responsible AI in finance**: calibrated probabilities, human-in-the-loop review, explainable outputs, and a clear separation between what customers and investigators are told.
- **Foundation for cross-bank intelligence.** The same graph and mule signals could, with proper governance, be shared between institutions, addressing the single-bank limitation noted earlier.

### 16.4 For society

Confidence in digital payments underpins financial inclusion. Every protected customer, especially first-time digital users who are the most vulnerable to scams, strengthens trust in a system used by hundreds of millions of people each day.

---

## 17. Novelty and Differentiators

1. **Specialised agents with an orchestrator**, each independently explainable and testable, instead of one monolithic model.
2. **Peak-aware aggregation**, so one confident specialist can escalate a case instead of being averaged away.
3. **Two-signal mule blocking**: a hard block needs both a calibrated model and graph evidence, protecting busy legitimate accounts.
4. **Point-in-time mule training** for early detection, and a **four-level graduated response** instead of allow or deny.
5. **Compliance-aware messaging** and a **tamper-evident audit trail** with a built-in integrity check.
6. **Built-in evaluation tools**: simulator, drift monitor, and an agents-versus-rules comparison.

---

## 18. Limitations and Risks

An honest report must state these clearly.

1. **Synthetic data.** All current results come from generated data. Accuracy on real transactions is unknown until the models are retrained and tested on real or realistic datasets.
2. **Cold start.** A brand-new mule account's very first incoming payment has no history, so the Mule agent cannot flag it; the AML and Transaction agents provide partial cover, but this is an inherent limit of any history-based method.
3. **False alarms.** At the "challenge or stop" level, about one in eight honest transactions in the test was challenged. Thresholds and weights need tuning on real traffic and business tolerance.
4. **Latency headroom.** The 95th percentile is close to the 200 ms target, and feature computation with pandas is the main cost. Caching and incremental features are known improvements.
5. **In-memory state.** History, graph, and ledger are held in memory and reset on restart until the database is added.
6. **Single-institution view.** Like most bank systems, AgentGuard sees only payments it processes. Cross-bank laundering chains remain partly invisible.
7. **Adversarial adaptation.** Fraudsters change tactics once defences appear. Drift monitoring, analyst feedback, and retraining are the mitigation, but they need to be run regularly.
8. **Demo and placeholder items.** The drift monitor overstates drift on demo traffic that is not sampled from the training distribution, the optional language-model path is untested against a live service, and the high-risk-country list is illustrative and must be replaced with an official current source.

---

## 19. Ethical, Privacy, and Security Considerations

- **Fairness:** scores should be audited across customer groups; features are behavioural (amounts, timing, devices), not personal attributes.
- **Privacy and security:** a real deployment must follow data-protection law and minimise stored personal data. The OTP service stores only hashes with expiry and attempt limits, and production must never return codes in responses (demo mode does so only for presentation).
- **Responsible disclosure:** detection logic is never revealed to customers, which is why messages stay generic.

---

## 20. Evaluation Plan

1. Retrain on the IBM AML dataset with a time-based split and report precision, recall, and PR-AUC at each decision tier.
2. Run an ablation (remove each agent in turn; compare weighted versus peak-aware aggregation) and compare against a rule-only system and a single gradient-boosted model.
3. Test latency under concurrent load, and test drift and robustness with shifted traffic and slow-drip attacks just below rule thresholds.

---

## 21. Roadmap and Future Work

**Near term**
- Build the React dashboard (live feed, transaction detail, queue, graph, ledger, metrics).
- Reduce latency with feature caching and incremental updates.
- Replace in-memory stores with MongoDB.
- Containerise with Docker and deploy to a cloud platform.

**Medium term**
- Retrain on real datasets and tune thresholds against business cost.
- Retraining loop driven by analyst feedback, with scheduled drift checks.
- Add graph learning (for example, learned embeddings or graph neural networks) for richer ring detection.
- Add behavioural biometrics such as typing and touch patterns.

**Longer term**
- Privacy-preserving cross-bank intelligence sharing (for example, federated learning or shared hashed watchlists) to follow money across institutions.
- Integration with reporting channels for suspicious transactions.
- Customer-facing features such as a "kill switch" and trusted-payee management.

---

## 22. Conclusion

UPI has made payments instant for hundreds of millions of people, and the same instant, irreversible design makes fraud and laundering fast and hard to reverse. Reported UPI fraud has stayed above ten lakh cases a year, and mule accounts are the channel through which stolen money disappears.

AgentGuard answers this with a structure that mirrors a good human fraud team, but works in a fraction of a second: specialist agents examine each payment, an orchestrator weighs their evidence and applies firm rules, graded responses keep honest customers moving, humans handle uncertain cases, and every decision is explained and permanently recorded. The prototype is implemented, tested end to end, and honest about its limits. Its next steps (real data, a dashboard, a database, and cloud deployment) turn a working backend into a deployable, demonstrable product.

---

## Appendix A: Glossary

| Term | Meaning |
|---|---|
| **UPI** | Unified Payments Interface, India's instant account-to-account payment system |
| **Mule account** | An account used to receive and pass on illegally obtained money |
| **Structuring** | Splitting a large amount into smaller payments to avoid thresholds |
| **Round-tripping** | Moving money in a loop through accounts to disguise its origin |
| **Pass-through ratio** | Share of received money that an account sends onward |
| **Collect request** | A UPI request asking the receiver to approve a payment, often abused in scams |
| **Calibration** | Adjusting model outputs so they behave like true probabilities |
| **Tipping off** | Alerting a suspect that they are under suspicion of money laundering |

## Appendix B: Data Sources for Background Figures

- NPCI monthly UPI statistics for August and September 2026, as reported in the financial press.
- Finance Ministry written reply to the Lok Sabha on UPI frauds, FY2021-22 to FY2025-26 (till November).
- Government statement on UPI's ten-year growth (FY2025-26 transaction volume).
- Statements by the RBI Deputy Governor on UPI fraud incidence and the "zero-fraud" objective.
- RBI announcement of MuleHunter.AI (December 2024) as reported in the financial press.

*Figures in Section 2 are drawn from these public sources and should be re-verified against the original NPCI and RBI publications before formal submission.*
