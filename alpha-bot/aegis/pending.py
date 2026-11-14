"""Reference-count queued and in-flight checks for the same Discord message."""

from collections import Counter


class PendingMessages:
    def __init__(self):
        self.counts = Counter()

    def add(self, key):
        self.counts[key] += 1

    def discard(self, key):
        if self.counts[key] <= 1:
            self.counts.pop(key, None)
        else:
            self.counts[key] -= 1

    def __contains__(self, key):
        return key in self.counts

    def clear_guild(self, guild):
        for key in list(self.counts):
            if key[0] == guild:
                del self.counts[key]
