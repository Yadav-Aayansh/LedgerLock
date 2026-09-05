#!/usr/bin/env python3
"""Tests for the propose -> verify -> accept path, without an API key.

The analyst's transport has a replay mode precisely so the pipeline is
testable and the demo is reproducible. These tests use it: a response is
planted in the cache under the exact key the real prompt would produce, then
the real `analyse()` runs over it. Nothing is stubbed out except the network.

What matters here is not that the model is clever. It is that:
  * a model that declines is recorded as having answered correctly, not as a
    failure (§10);
  * a model that guesses on an indeterminate line is stopped by the verifier
    even though its arithmetic is perfect;
  * and a correct proposal only becomes a link after every check passes.
"""

import json
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyst import packet as packet_builder            # noqa: E402
from analyst.analyst import analyse                     # noqa: E402
from analyst.client import AnalystClient                # noqa: E402
from analyst.schema import PROPOSAL_SCHEMA, SYSTEM      # noqa: E402
from fees import breakdown                              # noqa: E402
from matcher.audit import Audit                         # noqa: E402
from matcher.decision import MATCHED, UNRESOLVED        # noqa: E402
from matcher.engine import State                        # noqa: E402
from matcher.records import BankTxn, SettlementGroup, SettlementRow  # noqa: E402

DAY = date(2025, 6, 18)
CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def payment(sid, eid, gross):
    b = breakdown(gross)
    return SettlementRow(sid, eid, f"ord_{eid}", "payment", b["gross"], b["fee"],
                         b["gst_on_fee"], b["net"], DAY, "RZPX20000001", "processed")


def fixture():
    g = SettlementGroup("stl_X")
    g.rows.extend([payment("stl_X", "pay_X1", 149900), payment("stl_X", "pay_X2", 79900)])
    txn = BankTxn("bank_X", DAY, "UPI-RAZORPAYSOFTWARE-SETTLEMENT-CR", g.payout, 0)
    audit = Audit(None, "test")
    state = State([txn], {"stl_X": g}, audit)
    rows_by_id = {r.entity_id: r for r in g.rows}
    return g, txn, audit, state, rows_by_id


def run_with(proposal, indeterminate=frozenset()):
    """Plant `proposal` in the cache under the key the real prompt yields, then
    run the real analyst over it."""
    g, txn, audit, state, rows_by_id = fixture()
    groups = {"stl_X": g}

    pkt = packet_builder.build(txn, state.decisions[txn.bank_txn_id], groups,
                               state.claimed, audit.recent(txn.bank_txn_id))
    client = AnalystClient("off", Path("/nonexistent"), SYSTEM, PROPOSAL_SCHEMA)

    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp) / "analyst_cache.jsonl"
        cache.write_text(json.dumps({
            "key": client.key(pkt), "model": "claude-opus-5", "proposal": proposal,
            "usage": {"input_tokens": 1200, "output_tokens": 300},
        }) + "\n")
        report = analyse(state, groups, rows_by_id, audit, cache, "replay",
                         window_days=3, indeterminate=indeterminate)
    return report, state, txn


@case
def declining_is_recorded_as_a_correct_answer():
    report, state, txn = run_with({
        "hypothesis": "Two settlements of identical value fall on this date.",
        "arithmetic": "1463.62 == 1463.62 for both candidates",
        "proposed_link": [],
        "confidence": 0.0,
        "unresolvable_reason": "Nothing in the data separates the candidates.",
    })
    assert report.declined == 1, report
    assert report.proposals == 0 and report.rejected == 0
    assert state.decisions[txn.bank_txn_id].status == UNRESOLVED


@case
def a_correct_proposal_becomes_a_link_only_after_verification():
    report, state, txn = run_with({
        "hypothesis": "Settlement stl_X nets to this credit.",
        "arithmetic": "1463.62 + 780.14 = 2243.76",
        "proposed_link": ["pay_X1", "pay_X2"],
        "confidence": 0.88,
        "unresolvable_reason": None,
    })
    assert report.accepted == 1 and report.rejected == 0, report
    d = state.decisions[txn.bank_txn_id]
    assert d.status == MATCHED and d.tier == "LLM", (d.status, d.tier)
    assert d.payment_ids == ["pay_X1", "pay_X2"]


@case
def a_balanced_guess_on_an_indeterminate_line_is_rejected():
    # Identical to the accepted case above, except the engine had already
    # proved this line indeterminate. The arithmetic still balances perfectly;
    # the verifier must refuse it anyway.
    report, state, txn = run_with({
        "hypothesis": "Settlement stl_X nets to this credit.",
        "arithmetic": "1463.62 + 780.14 = 2243.76",
        "proposed_link": ["pay_X1", "pay_X2"],
        "confidence": 0.95,
        "unresolvable_reason": None,
    }, indeterminate=frozenset({"bank_X"}))
    assert report.rejected == 1 and report.accepted == 0, report
    assert state.decisions[txn.bank_txn_id].status == UNRESOLVED
    assert "determinacy" in report.outcomes[0].detail, report.outcomes[0].detail


@case
def an_unbalanced_proposal_is_rejected():
    report, state, txn = run_with({
        "hypothesis": "These sales add up to the credit.",
        "arithmetic": "1499.00 + 799.00 = 2298.00",
        "proposed_link": ["pay_X1"],
        "confidence": 0.9,
        "unresolvable_reason": None,
    })
    assert report.rejected == 1 and report.accepted == 0
    assert "sum_balances" in report.outcomes[0].detail


@case
def cost_is_accounted_from_real_token_counts():
    report, _, _ = run_with({
        "hypothesis": "x", "arithmetic": "y", "proposed_link": [],
        "confidence": 0.0, "unresolvable_reason": "z",
    })
    assert report.usage == {"input_tokens": 1200, "output_tokens": 300}
    # 1200 in @ $5/M + 300 out @ $25/M = $0.0135
    assert abs(report.cost_usd - 0.0135) < 1e-9, report.cost_usd


@case
def a_missing_cache_entry_is_reported_not_faked():
    g, txn, audit, state, rows_by_id = fixture()
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp) / "empty.jsonl"
        cache.write_text("")
        report = analyse(state, {"stl_X": g}, rows_by_id, audit, cache, "replay",
                         window_days=3, indeterminate=frozenset())
    assert report.not_run == 1 and report.accepted == 0
    assert state.decisions[txn.bank_txn_id].status == UNRESOLVED


def main():
    failed = 0
    for fn in CASES:
        try:
            fn()
            print(f"  PASS  {fn.__name__.replace('_', ' ')}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {fn.__name__.replace('_', ' ')}: {exc}")
    print(f"\n{len(CASES) - failed}/{len(CASES)} analyst tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
