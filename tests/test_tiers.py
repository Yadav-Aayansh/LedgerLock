#!/usr/bin/env python3
"""Tests for the safety-critical rules in the matching tiers.

T2b resolved nothing on the seed-42 dataset, because every bank line there
corresponds to exactly one settlement_id and T2 gets them all. That makes it
unproven code, and unproven code in the tier that is *most* capable of
inventing a match is not acceptable. These tests exercise it directly.

Standalone on purpose -- no pytest, so `make test` needs nothing installed.
"""

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from matcher import tiers                                          # noqa: E402
from matcher.decision import MATCHED, REFUSED, UNRESOLVED          # noqa: E402
from matcher.engine import State                                   # noqa: E402
from matcher.records import BankTxn, SettlementGroup, SettlementRow  # noqa: E402

DAY = date(2025, 6, 10)


class NullAudit:
    def __init__(self):
        self.events = []

    def log(self, stage, event, **fields):
        self.events.append((stage, event, fields))


def group(sid, *nets, settled=DAY, held=(), utr=""):
    g = SettlementGroup(sid)
    for i, n in enumerate(nets):
        g.rows.append(SettlementRow(sid, f"{sid}_e{i}", f"ord_{sid}_{i}", "payment",
                                    n, 0, 0, n, settled, utr, "processed"))
    for i, n in enumerate(held):
        g.rows.append(SettlementRow(sid, f"{sid}_h{i}", f"ord_{sid}_h{i}", "payment",
                                    n, 0, 0, n, None, "", "on_hold"))
    return g


def txn(bid, amount, when=DAY, narration="UPI-RAZORPAYSOFTWARE-SETTLEMENT-CR"):
    return BankTxn(bid, when, narration, max(amount, 0), max(-amount, 0))


def make_state(bank, groups):
    return State(bank, {g.settlement_id: g for g in groups}, NullAudit())


CASES = []


def case(fn):
    CASES.append(fn)
    return fn


# --------------------------------------------------------------------------

@case
def t2b_finds_a_unique_combination():
    b = txn("bank_1", 30000)
    gs = [group("s1", 10000), group("s2", 20000), group("s3", 70000)]
    st = make_state([b], gs)
    tiers.tier2b_subset_sum(st)
    d = st.decisions["bank_1"]
    assert d.status == MATCHED, d.status
    assert d.tier == "T2b"
    assert sorted(d.settlement_ids) == ["s1", "s2"], d.settlement_ids


@case
def t2b_declines_when_two_combinations_fit():
    # 100+200 and 120+180 both reach 300. The evidence does not pick one, so
    # neither may be written. This is the false-match guard.
    b = txn("bank_1", 30000)
    gs = [group("s1", 10000), group("s2", 20000), group("s3", 12000), group("s4", 18000)]
    st = make_state([b], gs)
    tiers.tier2b_subset_sum(st)
    d = st.decisions["bank_1"]
    assert d.status == UNRESOLVED, d.status
    assert d.residue_kind == "ambiguous", d.residue_kind
    assert any(e[1] == "multiple_solutions" for e in st.audit.events)


@case
def t2b_will_not_use_more_than_the_size_cap():
    # Five settlements are needed; the cap is four. Correct answer: no match.
    target = 5 * 10000
    gs = [group(f"s{i}", 10000) for i in range(5)]
    st = make_state([txn("bank_1", target)], gs)
    tiers.tier2b_subset_sum(st)
    assert st.decisions["bank_1"].status == UNRESOLVED


@case
def t2b_refuses_to_search_an_oversized_pool():
    gs = [group(f"s{i}", 1000 + i) for i in range(tiers.MAX_POOL_SIZE + 1)]
    st = make_state([txn("bank_1", 2001)], gs)
    tiers.tier2b_subset_sum(st)
    d = st.decisions["bank_1"]
    assert d.status == UNRESOLVED
    assert d.residue_kind == "capped", d.residue_kind
    assert any(e[1] == "pool_cap_exceeded" for e in st.audit.events)


@case
def subset_search_prunes_an_unreachable_target_immediately():
    # Suffix-sum pruning: if everything left cannot reach the target, stop at
    # once rather than enumerate. An out-of-range credit costs one node.
    payouts = [100 + i for i in range(20)]
    sols, nodes, aborted = tiers._subset_solutions(payouts, 10_000, 0, 4, max_nodes=50)
    assert sols == [] and aborted is False
    assert nodes == 1, nodes


@case
def subset_search_aborts_on_the_node_cap():
    # The cap is a mechanism, not a magic number: drive it with a small limit.
    # Amounts are 1000 + 7k and the target is 1 above a reachable sum, so no
    # subset can ever hit it -- the search cannot short-circuit on a solution
    # and must be stopped by the node cap instead.
    payouts = [1000 + 7 * i for i in range(20)]
    sols, nodes, aborted = tiers._subset_solutions(payouts, 4001, 0, 4, max_nodes=50)
    assert aborted is True, f"expected the cap to bite, visited {nodes}"
    assert nodes <= 51, nodes
    assert sols == []


@case
def subset_search_ignores_single_settlements():
    # A lone settlement equal to the credit is T2's job, not T2b's.
    sols, _, _ = tiers._subset_solutions([30000, 10000], 30000, 0, 4, 10_000)
    assert sols == []


# --------------------------------------------------------------------------

@case
def mutual_uniqueness_declines_the_ec07_shape():
    # Two credits, one date, identical amounts, two settlements that fit
    # either. Iteration order must not be allowed to decide.
    bank = [txn("bank_1", 146362), txn("bank_2", 146362)]
    gs = [group("s1", 146362), group("s2", 146362)]
    st = make_state(bank, gs)
    tiers.tier1_amount_date(st)
    for b in bank:
        assert st.decisions[b.bank_txn_id].status == UNRESOLVED
        assert st.decisions[b.bank_txn_id].residue_kind == "ambiguous"


@case
def contested_sole_candidate_is_declined():
    # bank_1's only candidate is also bank_2's only candidate.
    bank = [txn("bank_1", 50000), txn("bank_2", 50000)]
    gs = [group("s1", 50000)]
    st = make_state(bank, gs)
    tiers.tier1_amount_date(st)
    assert all(st.decisions[b.bank_txn_id].status == UNRESOLVED for b in bank)
    assert any(e[1] == "contested_candidate" for e in st.audit.events)


@case
def t2_sums_only_processed_lines():
    # The EC09 trap: an on-hold line shares the settlement_id but was never
    # paid out. Summing every row overshoots and the batch looks unreconciled.
    g = group("s1", 40000, 30000, held=(25000,))
    assert g.naive_payout == 95000 and g.payout == 70000
    st = make_state([txn("bank_1", 70000)], [g])
    tiers.tier2_batch_decomposition(st)
    d = st.decisions["bank_1"]
    assert d.status == MATCHED and d.tier == "T2"
    assert d.payment_ids == ["s1_e0", "s1_e1"], d.payment_ids


@case
def t2_leaves_singletons_to_t1():
    st = make_state([txn("bank_1", 50000)], [group("s1", 50000)])
    tiers.tier2_batch_decomposition(st)
    assert st.decisions["bank_1"].status == UNRESOLVED


@case
def tolerance_cannot_swallow_a_fee():
    # 2% of the smallest realistic sale, against the tolerance. If this ever
    # fails, the tolerance has grown into a false-match generator.
    smallest_sale = 49900
    assert tiers.TOLERANCE_PAISA * 100 < smallest_sale * 0.02


# --------------------------------------------------------------------------

@case
def refusal_requires_proof_of_impossibility():
    st = make_state([txn("bank_1", 25_000_000)], [group("s1", 146362), group("s2", 146362)])
    tiers.refuse_impossible(st)
    d = st.decisions["bank_1"]
    assert d.status == REFUSED, d.status
    assert d.evidence["max_reachable"] == 292724


@case
def refusal_never_fires_when_the_amount_is_reachable():
    # Reachable in principle, even though nothing matches exactly. Staying
    # unresolved is correct; refusing here would be a false refusal.
    st = make_state([txn("bank_1", 200000)], [group("s1", 150000), group("s2", 90000)])
    tiers.refuse_impossible(st)
    assert st.decisions["bank_1"].status == UNRESOLVED


@case
def refusal_respects_sign():
    # A debit cannot be explained by credits, however large they are.
    st = make_state([txn("bank_1", -3_500_000)], [group("s1", 9_000_000)])
    tiers.refuse_impossible(st)
    assert st.decisions["bank_1"].status == REFUSED


# --------------------------------------------------------------------------
# T3: salvage is only ever half of the evidence.

@case
def t3_salvages_a_whitespace_injected_reference():
    g = group("s1", 50000, utr="RZPX12345678")
    st = make_state([txn("b1", 50000, narration="NEFT-RZPX 1234 5678-RAZORPAY SOFT")], [g])
    tiers.tier3_narration_salvage(st)
    d = st.decisions["b1"]
    assert d.status == MATCHED and d.tier == "T3", (d.status, d.tier)
    assert d.evidence["salvage"] == "normalised", d.evidence


@case
def t3_salvages_a_truncated_reference():
    g = group("s1", 50000, utr="RZPX12345678")
    st = make_state([txn("b1", 50000, narration="NEFT-RZPX1234-RAZORPAY SOFTWA")], [g])
    tiers.tier3_narration_salvage(st)
    assert st.decisions["b1"].evidence["salvage"] == "truncated"


@case
def t3_salvages_a_dropped_digit():
    g = group("s1", 50000, utr="RZPX12345678")
    st = make_state([txn("b1", 50000, narration="NEFT-RZPX1234568-RAZORPAY")], [g])
    tiers.tier3_narration_salvage(st)
    assert st.decisions["b1"].evidence["salvage"] == "edit-distance 1"


@case
def t3_will_not_match_a_reference_the_amount_contradicts():
    # THE test for this tier. The reference salvages cleanly, but the payout is
    # wrong. A fuzzy string match written into a ledger is indistinguishable
    # from a correct one afterwards, so this must stay unresolved.
    g = group("s1", 50000, utr="RZPX12345678")
    st = make_state([txn("b1", 99999, narration="NEFT-RZPX 1234 5678-RAZORPAY SOFT")], [g])
    tiers.tier3_narration_salvage(st)
    assert st.decisions["b1"].status == UNRESOLVED
    assert any(e[1] == "salvage_without_corroboration" for e in st.audit.events)


@case
def t3_declines_when_two_settlements_fit_the_reference_and_amount():
    gs = [group("s1", 50000, utr="RZPX12345678"), group("s2", 50000, utr="RZPX12345679")]
    st = make_state([txn("b1", 50000, narration="NEFT-RZPX1234567-RAZORPAY")], gs)
    tiers.tier3_narration_salvage(st)
    d = st.decisions["b1"]
    assert d.status == UNRESOLVED and d.residue_kind == "ambiguous", (d.status, d.residue_kind)


@case
def t3_ignores_a_reference_beyond_the_edit_cap():
    g = group("s1", 50000, utr="RZPX12345678")
    st = make_state([txn("b1", 50000, narration="NEFT-RZPX99999999-RAZORPAY")], [g])
    tiers.tier3_narration_salvage(st)
    assert st.decisions["b1"].status == UNRESOLVED


def main():
    failed = 0
    for fn in CASES:
        try:
            fn()
            print(f"  PASS  {fn.__name__.replace('_', ' ')}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {fn.__name__.replace('_', ' ')}: {exc}")
    print(f"\n{len(CASES) - failed}/{len(CASES)} tier tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
