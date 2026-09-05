"""Engine objects -> plain JSON.

Presentation only. Nothing here decides anything, recomputes anything, or
rounds anything: money arrives as int paisa and leaves as a rupee string
through the same `format_rupees` the reports use. If a number appears on the
screen that is not in `results.md`, this file is where the bug is.
"""

from edge_cases import EDGE_CASES
from money import format_rupees

# The whole dataset is ~150 rows per ledger. Capped anyway so an uploaded file
# of unknown size cannot wedge the browser.
MAX_LEDGER_ROWS = 2000

STATUS_LABEL = {
    "matched": "Matched",
    "refused": "Refused — proved not a settlement",
    "unresolved": "Unresolved — declined to guess",
}

RESIDUE_LABEL = {
    "ambiguous": "Two or more candidates are indistinguishable",
    "t3_salvage": "A reference was salvaged but the amount does not corroborate it",
    "capped": "The search hit its bounded limit",
    "no_candidate": "Nothing in the window could account for it",
}


# -- the three ledgers ------------------------------------------------------

def ledgers(session):
    """Screen 1: the raw lists, exactly as they arrived."""
    return {
        "orders": session.rows("orders.csv", MAX_LEDGER_ROWS),
        "settlements": session.rows("settlements.csv", MAX_LEDGER_ROWS),
        "bank": session.rows("bank_statement.csv", MAX_LEDGER_ROWS),
    }


def disagreement(orders, settlements, bank, groups):
    """Screen 1's point: three totals for what is supposedly the same money.

    They do not form an equation and are not presented as one. Sales exceed
    payouts by the fees. Deposits exceed payouts again, because not everything
    that lands in the account came from Razorpay -- which is the whole reason
    a matcher has to be allowed to say "this one is not mine".
    """
    sold = sum(o.amount for o in orders if o.status == "paid")
    settled = sum(r.net for r in settlements if r.status == "processed")
    banked = sum(t.signed for t in bank)

    order_amounts = {o.amount for o in orders}
    credits = [t.credit for t in bank if t.credit]
    coincidences = sum(1 for c in credits if c in order_amounts)

    return {
        "sold": format_rupees(sold),
        "settled": format_rupees(settled),
        "banked": format_rupees(banked),
        "fee_gap": format_rupees(sold - settled),
        "bank_gap": format_rupees(banked - settled),
        "bank_lines": len(bank),
        "settlement_rows": len(settlements),
        "settlement_batches": len(groups),
        "orders": len(orders),
        "credits": len(credits),
        "credits_matching_a_sale_amount": coincidences,
    }


def batches(groups):
    """142 report rows collapse into 33 payouts. `naive` is the trap: what you
    get if you sum a batch without excluding lines that never paid out."""
    out = []
    for g in groups.values():
        out.append({
            "settlement_id": g.settlement_id,
            "lines": len(g.rows),
            "processed_lines": len(g.processed),
            "payout": format_rupees(g.payout),
            "naive_payout": format_rupees(g.naive_payout),
            "has_hold": g.payout != g.naive_payout,
            "utr": g.utr or None,
            "settled_at": g.settled_at.isoformat() if g.settled_at else None,
            "types": sorted({r.type for r in g.rows}),
        })
    return sorted(out, key=lambda b: b["settlement_id"])


# -- the run ----------------------------------------------------------------

def _settlement_line(row):
    return {
        "entity_id": row.entity_id,
        "order_id": row.order_id or None,
        "type": row.type,
        "status": row.status,
        "gross": format_rupees(row.gross),
        "fee": format_rupees(row.fee),
        "gst_on_fee": format_rupees(row.gst),
        "net": format_rupees(row.net),
    }


def _linked(result, settlement_ids):
    """The settlements a decision points at, expanded to their lines."""
    out = []
    for sid in settlement_ids:
        g = result.groups.get(sid)
        if g is None:
            continue
        out.append({
            "settlement_id": sid,
            "payout": format_rupees(g.payout),
            "utr": g.utr or None,
            "settled_at": g.settled_at.isoformat() if g.settled_at else None,
            "lines": [_settlement_line(r) for r in g.rows],
        })
    return out


def decision(result, txn):
    """One bank line and everything the engine concluded about it."""
    d = result.state.decisions[txn.bank_txn_id]
    return {
        "bank_txn_id": txn.bank_txn_id,
        "date": txn.txn_date.isoformat(),
        "narration": txn.narration,
        "amount": format_rupees(txn.signed),
        "direction": "credit" if txn.signed >= 0 else "debit",
        "status": d.status,
        "status_label": STATUS_LABEL.get(d.status, d.status),
        "tier": d.tier or None,
        "confidence": d.confidence or None,
        "reason": d.reason,
        "residue_kind": d.residue_kind or None,
        "residue_label": RESIDUE_LABEL.get(d.residue_kind),
        "settlement_ids": d.settlement_ids,
        "line_count": len(d.payment_ids),
        "evidence": d.evidence,
        "linked": _linked(result, d.settlement_ids),
        "trail": result.audit.recent(txn.bank_txn_id, limit=20),
    }


def run(result):
    """Screen 2: every bank line, with the tier tally underneath."""
    decisions = [decision(result, t) for t in result.bank]
    counts = {"matched": 0, "refused": 0, "unresolved": 0}
    for d in decisions:
        counts[d["status"]] = counts.get(d["status"], 0) + 1

    split, per_tier = result.meta["evidence"]
    alt_tiers, (alt_split, _) = result.meta["alt_order"]

    return {
        "run_id": result.run_id,
        "runtime_ms": round(result.runtime * 1000, 1),
        "tiers": result.meta["tiers"],
        "counts": counts,
        "per_tier": per_tier,
        "evidence": split,
        "audit_events": result.meta["audit_events"],
        "unclaimed_settlements": result.unclaimed,
        "window_days": result.meta["window_days"],
        "tolerance_paisa": result.meta["tolerance_paisa"],
        "tier_order_comparison": {
            "used": {"order": result.meta["tiers"], "evidence": split},
            "spec_literal": {"order": alt_tiers, "evidence": alt_split},
        },
        "decisions": decisions,
    }


# -- the analyst ------------------------------------------------------------

def analyst(result):
    """Screen 3: what the model proposed and what the verifier did about it."""
    a = result.analyst
    by_line = {t.bank_txn_id: t for t in result.bank}
    outcomes = []
    for o in a.outcomes:
        txn = by_line.get(o.bank_txn_id)
        outcomes.append({
            "bank_txn_id": o.bank_txn_id,
            "amount": format_rupees(txn.signed) if txn else None,
            "narration": txn.narration if txn else None,
            "status": o.status,
            "detail": o.detail,
            "hypothesis": o.hypothesis,
            "confidence": o.confidence,
            "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail}
                       for c in o.checks],
        })
    return {
        "mode": a.mode,
        "provider": a.provider.name if a.provider else None,
        "protocol": a.provider.protocol if a.provider else None,
        "base_url": a.provider.base_url if a.provider else None,
        "model": a.model or None,
        "key_source": a.provider.key_source if a.provider else "none",
        "unavailable_reason": a.unavailable_reason or None,
        "proposals": a.proposals,
        "accepted": a.accepted,
        "rejected": a.rejected,
        "declined": a.declined,
        "not_run": a.not_run,
        "rejection_rate": a.rejection_rate,
        "usage": a.usage,
        "cost_inr": a.cost_inr,
        "outcomes": outcomes,
    }


def redteam(result):
    """The verifier's own exam: ten proposals that must not get through."""
    cases, passed, total = result.redteam
    return {"passed": passed, "total": total, "cases": cases}


# -- the answer key ---------------------------------------------------------

def score(result):
    """Screen 4, and only after the user asks: the accuracy table.

    Held back until requested because that is the honest ordering — the engine
    never sees ground truth, and neither should the screen until the run is
    already on it.
    """
    if not result.scored:
        return {"available": False,
                "why": "this dataset has no ground_truth.csv, so accuracy "
                       "cannot be measured. Only the run itself is shown."}

    s, m = result.score, result.metrics
    return {
        "available": True,
        "headline": {
            "false_matches": m["false_matches"],
            "auto_matched": m["auto_matched"],
            "linkable": m["linkable"],
            "auto_match_rate": m["auto_match_rate"],
        },
        "metrics": m,
        "buckets": [
            {"bucket": o.bucket, "bank_txn_id": o.bank_txn_id, "relation": o.relation,
             "tier": o.tier or None, "detail": o.detail, "edge_cases": o.edge_cases,
             "residue_kind": o.residue_kind or None, "expected_lines": o.expected_lines}
            for o in s.outcomes],
        "counts": s.counts,
        "hold_excluded_correctly": s.hold_excluded_correctly,
        "unclaimed_settlements": s.unclaimed_settlements,
        "edge_cases": [
            {"id": ec, "label": EDGE_CASES[ec],
             "verdict": s.edge_verdicts.get(ec, ("unknown", ""))[0],
             "detail": s.edge_verdicts.get(ec, ("unknown", ""))[1]}
            for ec in EDGE_CASES],
    }
