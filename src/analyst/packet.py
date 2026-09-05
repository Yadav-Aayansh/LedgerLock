"""Build exactly what the analyst is allowed to see (§10).

One unmatched record, its plausible neighbours, the fee schedule, and the tier
rules that already failed. Nothing else — in particular, never ground truth,
and never the rest of the ledger.
"""

from fees import SCHEDULE
from money import format_rupees

MAX_CANDIDATES = 8
CANDIDATE_WINDOW_DAYS = 5


def _line(row):
    d = {"entity_id": row.entity_id, "type": row.type, "status": row.status,
         "gross": format_rupees(row.gross), "fee": format_rupees(row.fee),
         "gst_on_fee": format_rupees(row.gst), "net": format_rupees(row.net)}
    if row.order_id:
        d["order_id"] = row.order_id
    return d


def build(txn, decision, groups, claimed, audit_events):
    """audit_events: the trail entries for this bank line, so the model is told
    which rules already fired and failed rather than re-deriving it."""
    candidates = []
    for g in groups.values():
        if g.settlement_id in claimed or not g.settled_at:
            continue
        lag = (txn.txn_date - g.settled_at).days
        if not -1 <= lag <= CANDIDATE_WINDOW_DAYS:
            continue
        candidates.append((abs(g.payout - txn.signed), g, lag))
    candidates.sort(key=lambda c: (c[0], c[1].settlement_id))

    return {
        "bank_line": {
            "bank_txn_id": txn.bank_txn_id,
            "date": txn.txn_date.isoformat(),
            "narration": txn.narration,
            "amount": format_rupees(txn.signed),
            "direction": "credit" if txn.signed > 0 else "debit",
        },
        "why_the_engine_could_not_resolve_it": decision.reason,
        "rules_that_already_ran": [
            {"stage": e.get("stage"), "outcome": e.get("event"),
             "detail": {k: v for k, v in e.items()
                        if k not in ("run_id", "seq", "ts", "stage", "event", "bank_txn_id")}}
            for e in audit_events],
        "candidate_settlements": [
            {"settlement_id": g.settlement_id,
             "settled_at": g.settled_at.isoformat(),
             "days_before_bank_date": lag,
             "utr": g.utr or None,
             "payout_net_total": format_rupees(g.payout),
             "lines": [_line(r) for r in g.rows]}
            for _, g, lag in candidates[:MAX_CANDIDATES]],
        "fee_schedule": SCHEDULE,
        "note": ("Amounts are rupees. payout_net_total already has fee and GST "
                 "deducted and excludes any line whose status is not 'processed'."),
    }
