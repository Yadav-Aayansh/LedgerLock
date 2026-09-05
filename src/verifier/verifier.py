"""Recompute the maths. Balances -> accept. Doesn't -> reject (§6, stage 4).

The rule from §5, enforced in code: the model returns a *proposal*. It never
writes a match. This module is the only thing that can turn a proposal into a
link, and it does so only when every check below passes.

One check here is not arithmetic, and it is the most important one.
Arithmetic verification is necessary but NOT sufficient: on the ambiguity trap
(EC07) two candidate settlements have identical payouts, so a coin flip
between them balances perfectly to the paisa. An arithmetic-only verifier
would accept it and record a 50%-likely false match as a verified fact. So the
verifier also refuses any proposal for a bank line the deterministic engine
already proved indeterminate. A proposal that cannot be falsified by evidence
is not a finding.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from fees import GST_ON_FEE_RATE, PLATFORM_FEE_RATE
from money import apply_rate, format_rupees

NOT_RUN = "not run"


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


@dataclass
class Verdict:
    accepted: bool
    checks: list = field(default_factory=list)

    @property
    def failed(self):
        return [c for c in self.checks if not c.passed]

    @property
    def failed_on(self):
        return self.failed[0].name if self.failed else None

    def summary(self):
        if self.accepted:
            return f"accepted: {len(self.checks)} checks passed"
        c = self.failed[0]
        return f"rejected at `{c.name}`: {c.detail}"


class _Run:
    """Sequential checks. Once one fails the rest are not run, because they
    would be answering a question the failed check already invalidated."""

    def __init__(self):
        self.checks = []
        self.ok = True

    def check(self, name, passed, detail):
        if not self.ok:
            self.checks.append(Check(name, False, NOT_RUN))
            return False
        self.checks.append(Check(name, bool(passed), detail))
        if not passed:
            self.ok = False
        return bool(passed)


def _expected_fees(gross):
    fee = apply_rate(gross, PLATFORM_FEE_RATE)
    return fee, apply_rate(fee, GST_ON_FEE_RATE)


def verify(proposal, txn, rows_by_id, claimed_entity_ids, indeterminate, window_days,
           tolerance_paisa):
    """Adjudicate one analyst proposal.

    proposal          the model's JSON, already schema-validated
    txn               the BankTxn it is about
    rows_by_id        {entity_id: SettlementRow} for the whole report
    claimed_entity_ids  entity ids already linked by the deterministic tiers
    indeterminate     bank_txn_ids the engine proved it could not decide
    """
    r = _Run()
    link = list(proposal.get("proposed_link") or [])

    if not link:
        # Not a failure. "I cannot resolve this" is a first-class answer (§10)
        # and is handled by the caller, not rejected here.
        r.check("proposal_present", False, "no link proposed, treated as unresolvable")
        return Verdict(False, r.checks)

    # 0. No id twice: a repeat is counted twice by the sum below.
    seen, repeated = set(), set()
    for entity_id in link:
        if entity_id in seen:
            repeated.add(entity_id)
        seen.add(entity_id)
    repeated = sorted(repeated)
    r.check("distinct_lines", not repeated,
            f"the same settlement line is proposed more than once: {', '.join(repeated)}"
            if repeated else f"all {len(link)} proposed lines are distinct")

    # 1. Determinacy. Runs first: if the engine proved the evidence cannot
    #    single out an answer, no amount of correct arithmetic makes a guess
    #    correct, and every later check would pass on a coin flip.
    r.check("determinacy",
            txn.bank_txn_id not in indeterminate,
            f"the engine proved {txn.bank_txn_id} indeterminate: more than one "
            f"candidate fits equally well, so any proposal here is a guess that "
            f"arithmetic cannot falsify"
            if txn.bank_txn_id in indeterminate else
            "the engine did not rule this line indeterminate")

    # 2. Every proposed id must exist. Models invent plausible-looking ids.
    missing = [e for e in link if e not in rows_by_id]
    r.check("existence", not missing,
            f"no such settlement line: {', '.join(missing)}" if missing
            else f"all {len(link)} proposed lines exist in the report")

    # 3. Nothing already spoken for.
    taken = [e for e in link if e in claimed_entity_ids]
    r.check("availability", not taken,
            f"already linked to another bank line: {', '.join(taken)}" if taken
            else "no proposed line is already claimed")

    rows = [rows_by_id[e] for e in link if e in rows_by_id]

    # 4. On-hold money never left Razorpay, so it cannot appear in a credit.
    #    Sweeping a held line in is an easy way to make a sum balance.
    held = [row.entity_id for row in rows if row.status != "processed"]
    r.check("settlement_status", not held,
            f"on hold, never paid out: {', '.join(held)}" if held
            else "every proposed line is processed")

    # 5. Dates.
    outside = [row.entity_id for row in rows
               if row.settled_at is None
               or not 0 <= (txn.txn_date - row.settled_at).days <= window_days]
    r.check("date_window", not outside,
            f"settled outside the {window_days}-day window: {', '.join(outside)}"
            if outside else f"all lines settle within {window_days} days")

    # 6. Per-line arithmetic, recomputed from the fee schedule. This is where
    #    paisa rounding on GST (EC10) is caught: the GST is rounded half-up on
    #    the *fee*, not on the gross, and a proposal built on any other order
    #    of operations lands a paisa or two out.
    bad = []
    for row in rows:
        if row.type == "payment":
            fee, gst = _expected_fees(row.gross)
            if (row.fee, row.gst) != (fee, gst):
                bad.append(f"{row.entity_id}: booked fee {format_rupees(row.fee)}/"
                           f"GST {format_rupees(row.gst)}, schedule gives "
                           f"{format_rupees(fee)}/{format_rupees(gst)}")
                continue
        elif (row.fee, row.gst) != (0, 0):
            bad.append(f"{row.entity_id}: a {row.type} carries a fee")
            continue
        if row.net != row.gross - row.fee - row.gst:
            bad.append(f"{row.entity_id}: net {format_rupees(row.net)} != gross - fee - GST")
    r.check("line_arithmetic", not bad,
            "; ".join(bad) if bad else
            f"fee and GST recomputed to the paisa on all {len(rows)} lines")

    # 7. The sum. Tolerance is the same paisa-level figure the tiers use; it
    #    cannot absorb a fee, which is the error this check exists to catch.
    total = sum(row.net for row in rows)
    delta = txn.signed - total
    r.check("sum_balances", abs(delta) <= tolerance_paisa,
            f"proposed lines net {format_rupees(total)} against a bank amount of "
            f"{format_rupees(txn.signed)}, off by {format_rupees(delta)}"
            if abs(delta) > tolerance_paisa else
            f"{len(rows)} lines net exactly {format_rupees(total)}")

    return Verdict(r.ok, r.checks)
