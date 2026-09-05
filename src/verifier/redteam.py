"""Adversarial proposals, run against the verifier on every execution.

Without an API key the analyst makes no calls, which would leave the verifier
untested in the report — and an untested verifier is the one component whose
failure is invisible, because a verifier that accepts everything looks exactly
like a model that is always right.

So the verifier is measured against a fixed suite of fabricated proposals
shaped like the mistakes language models actually make on this task: netting
against gross instead of net, rounding GST the intuitive way, sweeping in an
on-hold line to force a sum to balance, inventing a plausible settlement id,
and — the one arithmetic cannot catch — a perfectly balanced guess on a line
that is genuinely indeterminate.

Every case states the check that ought to stop it. A case that is rejected for
the *wrong* reason counts as a failure, not a pass.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from fees import breakdown
from matcher.records import BankTxn, SettlementRow
from verifier.verifier import verify

DAY = date(2025, 6, 18)
WINDOW = 3


def _payment(sid, eid, gross, settled=DAY, status="processed"):
    b = breakdown(gross)
    return SettlementRow(sid, eid, f"ord_{eid}", "payment", b["gross"], b["fee"],
                         b["gst_on_fee"], b["net"], settled, "RZPX11110000", status)


# A small ledger with everything the suite needs to attack.
A1 = _payment("stl_A", "pay_A1", 149900)          # fee 29.98, GST 5.40 (539.64 -> 540)
A2 = _payment("stl_A", "pay_A2", 79900)
A3 = _payment("stl_A", "pay_A3", 249900)
AH = _payment("stl_A", "pay_AH", 50000, status="on_hold")
B1 = _payment("stl_B", "pay_B1", 99900)
D1 = _payment("stl_D", "pay_D1", 129900, settled=DAY - timedelta(days=30))

# Booked with GST rounded down instead of half-up: the EC10 drift, one paisa
# short of what the published schedule produces.
C1 = SettlementRow("stl_C", "pay_C1", "ord_C1", "payment", 149900, 2998, 539,
                   149900 - 2998 - 539, DAY, "RZPX11110001", "processed")

LEDGER = {r.entity_id: r for r in (A1, A2, A3, AH, B1, D1, C1)}
A_NET = A1.net + A2.net + A3.net
A_GROSS = A1.gross + A2.gross + A3.gross


def _txn(bid, amount, when=DAY):
    return BankTxn(bid, when, "NEFT-RZPX11110000-RAZORPAY SOFTWARE PVT LTD",
                   max(amount, 0), max(-amount, 0))


@dataclass
class RedTeamCase:
    name: str
    mistake: str
    link: list
    txn: BankTxn
    expect_accept: bool
    expect_check: str = ""
    indeterminate: frozenset = frozenset()
    claimed: frozenset = frozenset()


SUITE = [
    RedTeamCase(
        "correct_proposal",
        "a genuinely right answer; the suite is worthless if nothing passes",
        ["pay_A1", "pay_A2", "pay_A3"], _txn("b1", A_NET), True),
    RedTeamCase(
        "netted_against_gross",
        "summed the sale amounts and forgot the fee and GST were deducted",
        ["pay_A1", "pay_A2", "pay_A3"], _txn("b2", A_GROSS), False, "sum_balances"),
    RedTeamCase(
        "off_by_one_paisa",
        "arithmetic right to the rupee, wrong to the paisa",
        ["pay_A1", "pay_A2", "pay_A3"], _txn("b3", A_NET + 1), False, "sum_balances"),
    RedTeamCase(
        "gst_rounded_the_intuitive_way",
        "GST rounded down rather than half-up on the fee (EC10)",
        ["pay_C1"], _txn("b4", C1.net), False, "line_arithmetic"),
    RedTeamCase(
        "balanced_guess_on_an_indeterminate_line",
        "perfect arithmetic on a line where two candidates fit equally well, "
        "the failure no amount of recomputation can catch",
        ["pay_A1", "pay_A2", "pay_A3"], _txn("b5", A_NET), False, "determinacy",
        indeterminate=frozenset({"b5"})),
    RedTeamCase(
        "invented_settlement_id",
        "a plausible-looking id that is not in the report",
        ["pay_A1", "pay_99999"], _txn("b6", A_NET), False, "existence"),
    RedTeamCase(
        "settlement_already_linked",
        "claimed a settlement another bank line already owns",
        ["pay_B1"], _txn("b7", B1.net), False, "availability",
        claimed=frozenset({"pay_B1"})),
    RedTeamCase(
        "swept_in_an_on_hold_line",
        "added money that never left Razorpay to force the sum to balance",
        ["pay_A1", "pay_A2", "pay_A3", "pay_AH"], _txn("b8", A_NET + AH.net),
        False, "settlement_status"),
    RedTeamCase(
        "settlement_from_last_month",
        "reached outside the date window for a number that happened to fit",
        ["pay_D1"], _txn("b9", D1.net), False, "date_window"),
    RedTeamCase(
        "same_line_proposed_twice",
        "counted one settlement line twice so the total would reach the credit",
        ["pay_B1", "pay_B1"], _txn("b11", B1.net * 2), False, "distinct_lines"),
    RedTeamCase(
        "confident_but_empty",
        "high confidence, no actual link; must not be read as a match",
        [], _txn("b10", A_NET), False, "proposal_present"),
]


def run_suite(tolerance_paisa=0):
    """Returns (results, passed, total). A case adjudicated for the wrong
    reason is a failure: catching the right proposal on the wrong check means
    the check that should have caught it does not work."""
    results = []
    for case in SUITE:
        verdict = verify({"proposed_link": case.link}, case.txn, LEDGER,
                         case.claimed, case.indeterminate, WINDOW, tolerance_paisa)
        right_outcome = verdict.accepted == case.expect_accept
        right_reason = verdict.accepted or verdict.failed_on == case.expect_check
        results.append({
            "name": case.name,
            "mistake": case.mistake,
            "expected": "accept" if case.expect_accept else f"reject at {case.expect_check}",
            "actual": "accept" if verdict.accepted else f"reject at {verdict.failed_on}",
            "ok": right_outcome and right_reason,
            "summary": verdict.summary(),
        })
    passed = sum(1 for r in results if r["ok"])
    return results, passed, len(results)
