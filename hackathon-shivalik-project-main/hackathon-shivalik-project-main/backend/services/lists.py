"""Analyst-managed blacklist / whitelist (swap sets for MongoDB collections later)."""
class Lists:
    def __init__(self):
        self.black, self.white = set(), set()          # black: accounts/VPAs/devices; white: (src, dst) pairs

    def blacklist(self, x): self.black.add(x)
    def unblacklist(self, x): self.black.discard(x)
    def whitelist(self, src, dst): self.white.add((src, dst))
    def unwhitelist(self, src, dst): self.white.discard((src, dst))

    def is_blacklisted(self, t):
        return bool({t["src"], t["dst"], t.get("device_id")} & self.black)

    def is_whitelisted(self, t):
        return (t["src"], t["dst"]) in self.white
