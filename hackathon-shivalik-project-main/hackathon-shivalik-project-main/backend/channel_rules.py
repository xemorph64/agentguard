"""Channel-specific fraud rules for UPI and net banking. Each rule = (name, weight 0..1). Combined with noisy-OR."""
LIMITS = {"UPI": 100_000, "NET_BANKING": 500_000}


def noisy_or(ws):
    p = 1.0
    for w in ws:
        p *= (1 - w)
    return 1 - p


def evaluate(txn, new_payee, new_device):
    """Returns (risk 0..1, [(rule, weight)])."""
    ch, amt = txn.get("channel", "UPI"), float(txn["amount"])
    hr = int(getattr(txn["ts"], "hour", 12))
    night = hr < 6
    foreign = bool(txn.get("cross_border")) or bool(txn.get("ip_country") and txn.get("home_country")
                                                    and txn["ip_country"] != txn["home_country"])
    lim, hits = LIMITS.get(ch, 100_000), []
    if ch == "UPI":
        if txn.get("request_type") == "COLLECT" and new_payee:
            hits.append(("UPI_COLLECT_REQUEST_NEW_PAYEE", .55))        # classic 'enter PIN to receive money' scam
        if amt >= .9 * lim:
            hits.append(("UPI_NEAR_TXN_LIMIT", .30))
        if txn.get("beneficiary_age_hours", 9999) < 24 and amt >= 10_000:
            hits.append(("NEW_VPA_LARGE_AMOUNT", .40))
        if night and new_payee and amt >= 20_000:
            hits.append(("NIGHT_NEW_PAYEE_LARGE", .30))
        if foreign and new_device and amt >= 10_000:
            hits.append(("FOREIGN_IP_NEW_DEVICE", .35))
    elif ch == "NET_BANKING":
        if txn.get("beneficiary_age_hours", 9999) < 24 and amt >= 50_000:
            hits.append(("NEW_BENEFICIARY_LARGE_TRANSFER", .60))
        if new_payee and amt >= .5 * lim:
            hits.append(("LARGE_FIRST_TIME_TRANSFER", .35))
        if txn.get("recent_credential_change") and new_payee:
            hits.append(("CREDENTIAL_CHANGE_THEN_NEW_PAYEE", .45))     # account-takeover pattern
        if foreign and new_device and amt >= 25_000:
            hits.append(("FOREIGN_IP_NEW_DEVICE", .35))
    return noisy_or([w for _, w in hits]), hits
