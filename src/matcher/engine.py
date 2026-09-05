"""Orchestration: run the tiers in order, keep the books, write the trail."""

from datetime import datetime

from matcher.decision import MATCHED, REFUSED, UNRESOLVED, Decision
from matcher.tiers import FINALIZERS, REFERENCE_BACKED, TIERS


class State:
    def __init__(self, bank, groups, audit):
        self.bank = bank
        self.groups = groups
        self.audit = audit
        self.decisions = {t.bank_txn_id: Decision(t.bank_txn_id) for t in bank}
        self.claimed = set()

    def open_txns(self):
        return [t for t in self.bank if self.decisions[t.bank_txn_id].status == UNRESOLVED]

    def free_groups(self):
        return [g for g in self.groups.values() if g.settlement_id not in self.claimed]

    def accept(self, txn, groups, tier, confidence, reason, evidence):
        d = self.decisions[txn.bank_txn_id]
        d.status = MATCHED
        d.tier = tier
        d.settlement_ids = [g.settlement_id for g in groups]
        d.payment_ids = [eid for g in groups for eid in g.entity_ids]
        d.confidence = confidence
        d.reason = reason
        d.evidence = evidence
        for g in groups:
            self.claimed.add(g.settlement_id)
        # Evidence is nested, not splatted: a tier is free to name an evidence
        # key anything without colliding with the trail's own fields.
        self.audit.log(tier, "matched", bank_txn_id=txn.bank_txn_id,
                       settlement_ids=d.settlement_ids, line_count=len(d.payment_ids),
                       confidence=confidence, reason=reason, evidence=evidence)

    def accept_verified(self, txn, rows, tier, confidence, reason, evidence):
        """Record a link that came from a proposal AFTER the verifier passed it.

        Deliberately a separate entry point from `accept`. The tiers own
        `accept`; anything model-originated has to come through here, and the
        only caller is the code path that has just run verifier.verify.
        """
        d = self.decisions[txn.bank_txn_id]
        d.status = MATCHED
        d.tier = tier
        d.settlement_ids = sorted({r.settlement_id for r in rows})
        d.payment_ids = [r.entity_id for r in rows]
        d.confidence = confidence
        d.reason = reason
        d.evidence = evidence
        self.claimed.update(d.settlement_ids)
        self.audit.log(tier, "matched_after_verification", bank_txn_id=d.bank_txn_id,
                       settlement_ids=d.settlement_ids, line_count=len(d.payment_ids),
                       confidence=confidence, reason=reason, evidence=evidence)

    def mark_reason(self, txn, reason, kind=""):
        """Record why a tier declined, without resolving the line."""
        d = self.decisions[txn.bank_txn_id]
        if d.status == UNRESOLVED:
            d.reason = reason
            d.residue_kind = kind or d.residue_kind

    def refuse(self, txn, reason, evidence):
        """Actively declare a line not-a-settlement.

        Distinct from leaving it unresolved: this is a positive claim, and the
        scorer holds it to a higher standard -- refusing something real is its
        own error class.
        """
        d = self.decisions[txn.bank_txn_id]
        d.status = REFUSED
        d.tier = "REFUSE"
        d.reason = reason
        d.evidence = evidence
        self.audit.log("REFUSE", "refused", bank_txn_id=txn.bank_txn_id,
                       reason=reason, evidence=evidence)


def log_decisions(state, bank, audit):
    """One closing record per bank line, so run_log.jsonl alone is sufficient to
    reconstruct every row of results.md without re-running the engine.

    Called after the analyst, not at the end of run(): a trail that recorded
    verdicts the analyst later changed would replay to the wrong report.
    """
    for txn in bank:
        d = state.decisions[txn.bank_txn_id]
        audit.log("decision", d.status, bank_txn_id=d.bank_txn_id, tier=d.tier,
                  settlement_ids=d.settlement_ids, line_count=len(d.payment_ids),
                  confidence=d.confidence, reason=d.reason,
                  evidence_class=("reference+amount" if d.tier in REFERENCE_BACKED
                                  else "amount+date" if d.status == MATCHED else None),
                  residue_kind=d.residue_kind or None)


def run(bank, groups, audit, tiers=None):
    tiers = tiers if tiers is not None else TIERS
    state = State(bank, groups, audit)
    audit.log("engine", "start", bank_lines=len(bank), settlements=len(groups),
              tiers=[name for name, _ in tiers],
              finalizers=[name for name, _ in FINALIZERS])

    for name, tier in tiers:
        before = len(state.claimed)
        tier(state)
        audit.log(name, "pass_complete", newly_matched=len(state.claimed) - before,
                  still_open=len(state.open_txns()))

    # Finalizers make no matches: they either refuse a line outright or explain
    # why it is still open. Kept out of TIERS so the by-tier table stays a
    # table of matching rules.
    for name, step in FINALIZERS:
        step(state)
        audit.log(name, "pass_complete", still_open=len(state.open_txns()))

    residue = [t.bank_txn_id for t in state.open_txns()]
    unclaimed = sorted(g.settlement_id for g in state.free_groups())
    refused = sum(1 for d in state.decisions.values() if d.status == REFUSED)
    audit.log("engine", "done", matched=len(state.claimed), refused=refused,
              residue=len(residue), unclaimed_settlements=len(unclaimed))
    return state, residue, unclaimed
