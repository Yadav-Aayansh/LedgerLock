"""The audit trail: one JSONL line per decision the engine makes.

§9 requires every tier to record which rule fired, on what inputs, at what
confidence. Near-misses are logged too -- a rule that *almost* fired is the
most useful thing in the file when a number looks wrong.
"""

import json
import time
from pathlib import Path


class Audit:
    def __init__(self, path, run_id: str):
        """path=None discards. Used for the tier-ordering comparison run, which
        must not overwrite the trail of the run actually being reported."""
        self.path = Path(path) if path else None
        self.run_id = run_id
        self.n = 0
        # Kept in memory as well as on disk: the analyst packet has to tell the
        # model which rules already fired on this line (§10).
        self.by_line = {}
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.fh = self.path.open("w", encoding="utf-8")
        else:
            self.fh = None

    def log(self, stage, event, **fields):
        self.n += 1
        record = {"run_id": self.run_id, "seq": self.n, "ts": round(time.time(), 3),
                  "stage": stage, "event": event}
        record.update(fields)
        # Serialise once. The in-memory copy is the round-tripped one so that
        # dates and other objects are already JSON-safe by the time the analyst
        # packet embeds them -- the packet is hashed and sent over the wire.
        line = json.dumps(record, default=str)
        if fields.get("bank_txn_id"):
            self.by_line.setdefault(fields["bank_txn_id"], []).append(json.loads(line))
        if self.fh:
            self.fh.write(line + "\n")

    def recent(self, bank_txn_id, limit=8):
        return self.by_line.get(bank_txn_id, [])[-limit:]

    def close(self):
        if self.fh:
            self.fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
