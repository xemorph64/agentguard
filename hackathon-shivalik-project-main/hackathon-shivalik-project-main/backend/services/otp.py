"""Mock OTP step-up (Verify decision). Stores only a hash, expires in 5 minutes, max 3 tries."""
import hashlib, secrets, time


class OTPService:
    def __init__(self):
        self.ch = {}

    @staticmethod
    def _h(code, txn_id):
        return hashlib.sha256(f"{code}:{txn_id}".encode()).hexdigest()

    def send(self, txn_id):
        code = f"{secrets.randbelow(10**6):06d}"
        self.ch[txn_id] = {"hash": self._h(code, txn_id), "exp": time.time() + 300, "tries": 0}
        return code

    def verify(self, txn_id, code):
        c = self.ch.get(txn_id)
        if not c: return {"ok": False, "reason": "no_challenge", "tries_left": 0}
        if time.time() > c["exp"]: return {"ok": False, "reason": "expired", "tries_left": 0}
        if c["tries"] >= 3: return {"ok": False, "reason": "too_many_attempts", "tries_left": 0}
        c["tries"] += 1
        if secrets.compare_digest(c["hash"], self._h(code, txn_id)):
            del self.ch[txn_id]; return {"ok": True, "reason": "verified", "tries_left": 0}
        return {"ok": False, "reason": "wrong_code", "tries_left": 3 - c["tries"]}
