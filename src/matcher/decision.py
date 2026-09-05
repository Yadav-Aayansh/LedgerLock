"""What the engine says about one bank line."""

from dataclasses import dataclass, field

MATCHED = "matched"
UNRESOLVED = "unresolved"
REFUSED = "refused"          # actively declared not-a-settlement


@dataclass
class Decision:
    bank_txn_id: str
    status: str = UNRESOLVED
    tier: str = ""
    settlement_ids: list = field(default_factory=list)
    payment_ids: list = field(default_factory=list)
    confidence: float = 0.0
    reason: str = "no rule fired"
    evidence: dict = field(default_factory=dict)
    # Why this line is still in the residue: t3_salvage | ambiguous | capped |
    # no_candidate. Drives the residue breakdown in results.md and, on Day 5,
    # decides which lines are even worth handing to the analyst.
    residue_kind: str = ""
