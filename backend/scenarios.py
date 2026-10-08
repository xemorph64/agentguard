"""Scripted fraud scenarios — deterministic, timestamped stories fed through the real engine.

A script only describes *what happens* (accounts, their history, the payments and non-payment
events in order). Every decision, score, typology, focus account and explanation is produced
afterwards by the agents/orchestrator and the investigation builder — never by the script.

`roles` is the planted ground truth, exposed separately so tests (and sceptical judges) can
check that what the engine *found* matches what was *planted*.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

DAY = 86400.0


@dataclass
class AccountSpec:
    id: str
    name: str
    persona: str = "salaried"
    balance: float = 50000.0
    age_days: int = 900
    avg_amt_90d: float = 1500.0
    std_amt_90d: float = 800.0
    cluster: str = ""
    devices: list = field(default_factory=list)       # registered devices (login/registration data)
    known_payees: dict = field(default_factory=dict)   # payee id -> number of past payments (spread over 60 days)
    verified_merchant: bool = False
    city: str = "PUNE-W"


@dataclass
class Step:
    """One payment attempt. `at` is seconds from scenario start."""
    at: float
    payer: str
    payee: str
    amount: float
    caption: str
    channel: str = "p2p"
    device_fp: str = ""
    geo: str = ""
    on_call: bool = False
    call_minutes: float = 0.0
    screen_share: bool = False
    sim_changed_hrs_ago: Optional[float] = None
    pin_reset_hrs_ago: Optional[float] = None
    cooling_override: bool = False
    stepup_failed: bool = False


@dataclass
class Event:
    """A non-payment fact in the timeline (SIM swap, call started, step-up failed...)."""
    at: float
    text: str
    account: str = ""
    kind: str = "event"


@dataclass
class Script:
    name: str
    title: str
    summary: str
    accounts: list
    steps: list
    events: list = field(default_factory=list)
    roles: dict = field(default_factory=dict)          # account id -> planted role (ground truth)

    @property
    def span(self) -> float:
        return max([s.at for s in self.steps] + [e.at for e in self.events] + [0.0])


SCENARIOS = {
    "normal": ("Legitimate payments", "Everyday payments between people who know each other"),
    "account_takeover": ("Account takeover", "SIM swap, PIN reset and a new phone in another city"),
    "digital_arrest": ("Digital arrest", "Elderly victim coerced on a video call by a fake officer"),
    "mule_fanin": ("Mule fan-in", "10 unrelated victims pay one young account that forwards it on"),
    "structuring": ("Structuring", "₹4.4 lakh split into repeated payments just under ₹50,000"),
    "probing": ("Micro probing", "₹1 test payments, then a large transfer down the same route"),
    "device_farm": ("Device farm", "One phone operating many rented accounts"),
}


def build(name: str, run: int) -> Script:
    fn = _BUILDERS.get(name)
    if fn is None:
        raise KeyError(name)
    title, summary = SCENARIOS[name]
    p = f"R{run}-"
    s = fn(p)
    s.name, s.title, s.summary = name, title, summary
    return s


# ---------------------------------------------------------------------------------------------
def _normal(p: str) -> Script:
    A = AccountSpec
    asha, vikram, neha = p + "ASHA", p + "VIKRAM", p + "NEHA"
    shop, landlord, mother = p + "KIRANA", p + "LANDLORD", p + "MOTHER"
    accounts = [
        A(asha, "Asha Patil", balance=86000, avg_amt_90d=3200, std_amt_90d=5200, cluster=p + "patil",
          devices=[p + "PH-ASHA"], known_payees={shop: 14, landlord: 3, mother: 6, vikram: 2}),
        A(vikram, "Vikram Shah", balance=54000, avg_amt_90d=1400, std_amt_90d=900, cluster=p + "shah",
          devices=[p + "PH-VIKRAM"], known_payees={shop: 9, asha: 3, neha: 5}),
        A(neha, "Neha Shah", persona="student", balance=9000, avg_amt_90d=450, std_amt_90d=300,
          cluster=p + "shah", devices=[p + "PH-NEHA"], known_payees={vikram: 7, shop: 4}),
        A(shop, "Sai Kirana Store", persona="merchant", balance=120000, verified_merchant=True,
          avg_amt_90d=6000, std_amt_90d=4000, devices=[p + "POS-KIRANA"], known_payees={}),
        A(landlord, "R. Kulkarni", balance=210000, avg_amt_90d=5000, std_amt_90d=4000,
          devices=[p + "PH-KULKARNI"]),
        A(mother, "Sunita Patil", persona="elderly", balance=64000, avg_amt_90d=900, std_amt_90d=500,
          cluster=p + "patil", devices=[p + "PH-SUNITA"], known_payees={asha: 4}),
    ]
    S = Step
    steps = [
        S(0, asha, shop, 1240, "Asha buys groceries at her usual store", channel="p2m", device_fp=p + "PH-ASHA", geo="PUNE-W"),
        S(900, vikram, shop, 860, "Vikram pays the same store", channel="p2m", device_fp=p + "PH-VIKRAM", geo="PUNE-W"),
        S(2400, asha, mother, 3000, "Asha sends her mother money for medicines", device_fp=p + "PH-ASHA", geo="PUNE-W"),
        S(3600, neha, vikram, 450, "Neha pays her brother back", device_fp=p + "PH-NEHA", geo="PUNE-E"),
        S(5400, vikram, asha, 1150, "Vikram splits a dinner bill with Asha", device_fp=p + "PH-VIKRAM", geo="PUNE-W"),
        S(7200, asha, landlord, 18000, "Asha pays monthly rent to her landlord", device_fp=p + "PH-ASHA", geo="PUNE-W"),
        S(8400, mother, asha, 500, "Sunita sends Asha a little back", device_fp=p + "PH-SUNITA", geo="PUNE-W"),
        S(9000, neha, shop, 320, "Neha buys snacks at the store", channel="p2m", device_fp=p + "PH-NEHA", geo="PUNE-E"),
    ]
    return Script("", "", "", accounts, steps,
                  roles={asha: "customer", vikram: "customer", neha: "customer", shop: "merchant",
                         landlord: "customer", mother: "customer"})


def _account_takeover(p: str) -> Script:
    A = AccountSpec
    priya, cafe, shop, thief = p + "PRIYA", p + "CAFE", p + "KIRANA", p + "BENEF"
    home, attacker_dev = p + "PH-PRIYA", p + "PH-UNKNOWN"
    accounts = [
        A(priya, "Priya Nair", balance=92000, avg_amt_90d=1800, std_amt_90d=1100, cluster=p + "nair",
          devices=[home], known_payees={cafe: 11, shop: 20}),
        A(cafe, "Blue Tokai Café", persona="merchant", balance=80000, verified_merchant=True, devices=[p + "POS-CAFE"]),
        A(shop, "Sai Kirana Store", persona="merchant", balance=120000, verified_merchant=True, devices=[p + "POS-KIRANA"]),
        A(thief, "New beneficiary", persona="mule", balance=500, age_days=2, avg_amt_90d=800, cluster=p + "x"),
    ]
    S = Step
    t_attack = 14400
    steps = [
        S(0, priya, cafe, 420, "Priya buys coffee — home phone, Pune", channel="p2m", device_fp=home, geo="PUNE-W"),
        S(t_attack - 3300, priya, shop, 1180, "Priya pays for groceries — home phone, Pune", channel="p2m",
          device_fp=home, geo="PUNE-W"),
        S(t_attack, priya, thief, 49999, "₹49,999 to a 2-day-old payee from an unknown phone in Patna",
          device_fp=attacker_dev, geo="PATNA", sim_changed_hrs_ago=0.7, pin_reset_hrs_ago=0.3),
        S(t_attack + 150, priya, thief, 49999, "Same payment retried after the biometric check failed",
          device_fp=attacker_dev, geo="PATNA", sim_changed_hrs_ago=0.75, pin_reset_hrs_ago=0.35, stepup_failed=True),
    ]
    events = [
        Event(t_attack - 2520, "SIM re-issued on Priya's number (SIM swap)", priya, "sim_swap"),
        Event(t_attack - 1080, "UPI PIN reset from an unregistered phone", priya, "pin_reset"),
        Event(t_attack + 60, "Step-up sent to Priya's registered phone biometrics — not completed", priya, "stepup"),
    ]
    return Script("", "", "", accounts, steps, events,
                  roles={priya: "victim", thief: "attacker_beneficiary", attacker_dev: "attacker_device"})


def _digital_arrest(p: str) -> Script:
    A = AccountSpec
    rao, pharmacy, grandson, fake = p + "RAO", p + "PHARMACY", p + "GRANDSON", p + "OFFICER"
    phone = p + "PH-RAO"
    accounts = [
        A(rao, "S. Rao (72)", persona="elderly", balance=420000, avg_amt_90d=1600, std_amt_90d=900,
          cluster=p + "rao", devices=[phone], known_payees={pharmacy: 12, grandson: 5}),
        A(pharmacy, "Apollo Pharmacy", persona="merchant", verified_merchant=True, balance=90000, devices=[p + "POS-APOLLO"]),
        A(grandson, "Karthik Rao", balance=30000, cluster=p + "rao", devices=[p + "PH-KARTHIK"]),
        A(fake, "‘CBI officer’", persona="mule", balance=1200, age_days=4, avg_amt_90d=900, cluster=p + "y"),
    ]
    S = Step
    t = 10800
    steps = [
        S(0, rao, pharmacy, 640, "Mr Rao pays his pharmacy as usual", channel="p2m", device_fp=phone, geo="PUNE-W"),
        S(t, rao, fake, 180000, "₹1,80,000 to a 4-day-old account while on a 55-minute video call, screen shared",
          device_fp=phone, geo="PUNE-W", on_call=True, call_minutes=55, screen_share=True),
        S(t + 420, rao, fake, 180000, "Caller insists — Mr Rao overrides the Cooling Room and pays again",
          device_fp=phone, geo="PUNE-W", on_call=True, call_minutes=62, screen_share=True, cooling_override=True),
    ]
    events = [
        Event(t - 3300, "Incoming call: caller claims to be from the CBI, says Mr Rao is under 'digital arrest'", rao, "call"),
        Event(t - 1500, "Caller moves to a video call and asks Mr Rao to share his screen", rao, "screen_share"),
        Event(t + 200, "Cooling Room shown: 'No police or CBI ever asks for money over UPI'", rao, "cooling"),
    ]
    return Script("", "", "", accounts, steps, events,
                  roles={rao: "victim", fake: "fraudster_beneficiary"})


def _mule_fanin(p: str) -> Script:
    A = AccountSpec
    mule, cash = p + "MULE", p + "CASHOUT"
    amounts = [18400, 12650, 21300, 8750, 15900, 24100, 11200, 19750, 13800, 16450]
    personas = ["salaried", "student", "salaried", "gig", "elderly", "salaried", "student", "gig", "salaried", "salaried"]
    victims = [p + f"VICTIM{i + 1:02d}" for i in range(len(amounts))]
    accounts = [
        A(v, f"Victim {i + 1:02d}", persona=personas[i], balance=amounts[i] * 3.2, avg_amt_90d=1500,
          std_amt_90d=900, cluster=p + f"c{i}", devices=[p + f"PH-V{i + 1:02d}"], age_days=400 + 90 * i)
        for i, v in enumerate(victims)
    ] + [
        A(mule, "Mule collector", persona="mule", balance=2000, age_days=3, avg_amt_90d=900, std_amt_90d=500,
          cluster=p + "m", devices=[p + "PH-MULE"]),
        A(cash, "Cash-out account", persona="ring", balance=5000, age_days=5, avg_amt_90d=2000, cluster=p + "r"),
    ]
    S = Step
    steps = []
    at = 0
    for i in range(5):
        steps.append(S(at, victims[i], mule, amounts[i], f"Victim {i + 1:02d} pays a ‘task fee’ to the collector",
                       device_fp=p + f"PH-V{i + 1:02d}", geo="PUNE-W"))
        at += 70
    first_in = sum(amounts[:5])
    first_fwd = (first_in * 0.92) // 1000 * 1000
    steps.append(S(at + 170, mule, cash, first_fwd, "Collector forwards most of it to a cash-out account",
                   device_fp=p + "PH-MULE", geo="MUMBAI"))
    at += 260
    for i in range(5, 10):
        steps.append(S(at, victims[i], mule, amounts[i], f"Victim {i + 1:02d} pays a ‘task fee’ to the collector",
                       device_fp=p + f"PH-V{i + 1:02d}", geo="PUNE-W"))
        at += 70
    second_fwd = (sum(amounts[5:]) * 0.95) // 1000 * 1000
    steps.append(S(at + 170, mule, cash, second_fwd, "Collector tries to forward the next batch within minutes",
                   device_fp=p + "PH-MULE", geo="MUMBAI"))
    roles = {v: "victim" for v in victims}
    roles.update({mule: "mule", cash: "cashout"})
    return Script("", "", "", accounts, steps, roles=roles)


def _structuring(p: str) -> Script:
    A = AccountSpec
    coll = p + "COLLECTOR"
    smurfs = [p + "SMURF-A", p + "SMURF-B", p + "SMURF-C"]
    accounts = [
        A(s, f"Smurf {s[-1]}", balance=160000, avg_amt_90d=2500, std_amt_90d=1500, cluster=p + f"s{i}",
          age_days=60 + 70 * i, devices=[p + f"PH-S{s[-1]}"])
        for i, s in enumerate(smurfs)
    ] + [A(coll, "Collector account", persona="ring", balance=8000, age_days=20, avg_amt_90d=3000, cluster=p + "k")]
    amounts = [49500, 49800, 49200, 49900, 49400, 49700, 49600, 49300, 49950]
    S = Step
    steps = [
        S(300 * i, smurfs[i % 3], coll, amounts[i],
          f"Smurf {smurfs[i % 3][-1]} sends ₹{amounts[i]:,} — just under the ₹50,000 reporting line",
          device_fp=p + f"PH-S{smurfs[i % 3][-1]}", geo="DELHI")
        for i in range(9)
    ]
    roles = {s: "smurf" for s in smurfs}
    roles[coll] = "collector"
    return Script("", "", "", accounts, steps, roles=roles)


def _probing(p: str) -> Script:
    A = AccountSpec
    anil, target = p + "ANIL", p + "TARGET"
    probes = [p + f"PROBE{i}" for i in range(1, 5)]
    ring_phone = p + "PH-RING"
    accounts = [
        A(anil, "Anil Joshi", balance=125000, avg_amt_90d=2100, std_amt_90d=1300, cluster=p + "j",
          devices=[p + "PH-ANIL"]),
        A(target, "Target beneficiary", persona="mule", balance=300, age_days=1, avg_amt_90d=500,
          cluster=p + "t", devices=[ring_phone]),
    ] + [A(x, f"Probe account {i + 1}", persona="mule", balance=100, age_days=1 + i, cluster=p + "t",
           devices=[ring_phone]) for i, x in enumerate(probes)]
    S = Step
    steps = [S(35 * i, anil, dst, 1, f"₹1 test payment to {'the target' if dst == target else f'probe account {i + 1}'}",
               device_fp=p + "PH-ANIL", geo="PUNE-W")
             for i, dst in enumerate(probes + [target])]
    steps.append(S(35 * 5 + 60, anil, target, 60000, "₹60,000 down the route the probes just tested",
                   device_fp=p + "PH-ANIL", geo="PUNE-W"))
    roles = {x: "probe_account" for x in probes}
    roles.update({anil: "victim", target: "target", ring_phone: "ring_device"})
    return Script("", "", "", accounts, steps, roles=roles)


def _device_farm(p: str) -> Script:
    A = AccountSpec
    farm, cash = p + "PH-FARM", p + "CASHOUT"
    rented = [p + f"RENTED{i}" for i in range(1, 9)]
    amounts = [8400, 11200, 6900, 13500, 9600, 12100, 7400, 10800]
    accounts = [
        A(r, f"Rented account {i + 1}", persona="mule", balance=amounts[i] + 1500, age_days=8 + 2 * i,
          avg_amt_90d=1200, std_amt_90d=700, cluster=p + f"f{i}")
        for i, r in enumerate(rented)
    ] + [A(cash, "Cash-out account", persona="ring", balance=3000, age_days=6, avg_amt_90d=1500,
           cluster=p + "z", devices=[farm])]
    S = Step
    steps = [S(80 * i, r, cash, amounts[i], f"Rented account {i + 1} pays out — from the same phone",
               device_fp=farm, geo="KOLKATA") for i, r in enumerate(rented)]
    roles = {r: "rented_account" for r in rented}
    roles.update({cash: "cashout", farm: "farm_device"})
    return Script("", "", "", accounts, steps, roles=roles)


_BUILDERS = {
    "normal": _normal, "account_takeover": _account_takeover, "digital_arrest": _digital_arrest,
    "mule_fanin": _mule_fanin, "structuring": _structuring, "probing": _probing,
    "device_farm": _device_farm,
}
