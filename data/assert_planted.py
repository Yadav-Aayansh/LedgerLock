#!/usr/bin/env python3
"""Prove the eleven planted edge cases are actually in the generated data.

"I planted 11 cases" should be provable, not claimed. This script re-reads the
emitted CSVs -- it does not import the generator's in-memory state -- and
asserts each case from §8 is present and has the shape it is supposed to have.
It also checks the structural invariants that make the dataset legible at all
(running balance, id uniqueness, ground-truth coverage).

Exit code 1 if anything is missing, so `make data` can never silently produce a
dataset that no longer contains the cases the final report claims to handle.
"""

import csv
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from edge_cases import EDGE_CASES                     # noqa: E402
from fees import GST_ON_FEE_RATE, PLATFORM_FEE_RATE   # noqa: E402
from money import format_rupees, parse_rupees, parse_rupees_or_zero  # noqa: E402

DATA = ROOT / "data"
UTR_RE = re.compile(r"RZPX\d{8}")
FULL_UTR_TOKEN = re.compile(r"\bRZPX\d{8}\b")


def read(name):
    with open(DATA / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def d(text):
    return date.fromisoformat(text)


def ts(text):
    return datetime.fromisoformat(text)


class Checks:
    def __init__(self):
        self.results = []

    def check(self, key, ok, detail):
        self.results.append((key, bool(ok), detail))

    def report(self, title):
        print(f"\n{title}")
        print("-" * len(title))
        for key, ok, detail in self.results:
            mark = "PASS" if ok else "FAIL"
            label = EDGE_CASES.get(key, key)
            print(f"  [{mark}] {key:<6} {label}")
            print(f"         {detail}")
        return all(ok for _, ok, _ in self.results)


def main():
    orders = read("orders.csv")
    settlements = read("settlements.csv")
    bank = read("bank_statement.csv")
    truth = read("ground_truth.csv")

    order_by_id = {o["order_id"]: o for o in orders}
    bank_by_id = {b["bank_txn_id"]: b for b in bank}
    truth_by_bank = {t["bank_txn_id"]: t for t in truth if t["bank_txn_id"]}

    by_settlement = defaultdict(list)
    for s in settlements:
        by_settlement[s["settlement_id"]].append(s)

    all_utrs = {s["utr"] for s in settlements if s["utr"]}

    # ------------------------------------------------------------------
    # structural invariants
    # ------------------------------------------------------------------
    st = Checks()

    balance = None
    balance_ok = True
    opening = parse_rupees(bank[0]["closing_balance"]) - (
        parse_rupees_or_zero(bank[0]["credit"]) - parse_rupees_or_zero(bank[0]["debit"]))
    balance = opening
    for row in bank:
        balance += parse_rupees_or_zero(row["credit"]) - parse_rupees_or_zero(row["debit"])
        if balance != parse_rupees(row["closing_balance"]):
            balance_ok = False
            break
    st.check("BAL", balance_ok,
             f"running balance chains across all {len(bank)} rows from "
             f"{format_rupees(opening)}")

    ids = [s["payment_id"] for s in settlements]
    st.check("IDS", len(ids) == len(set(ids)),
             f"{len(ids)} settlement entity ids, {len(set(ids))} distinct")

    dates_sorted = all(d(bank[i]["txn_date"]) <= d(bank[i + 1]["txn_date"])
                       for i in range(len(bank) - 1))
    st.check("ORD", dates_sorted, "bank statement is in date order")

    covered = {t["bank_txn_id"] for t in truth if t["bank_txn_id"]}
    st.check("COV", covered == set(bank_by_id),
             f"ground truth covers {len(covered)}/{len(bank)} bank rows")

    # A payment's net must equal gross - fee - gst on every single row.
    net_ok = all(parse_rupees(s["net_amount"]) ==
                 parse_rupees(s["gross_amount"]) - parse_rupees(s["fee"]) - parse_rupees(s["gst_on_fee"])
                 for s in settlements)
    st.check("NET", net_ok, "net == gross - fee - gst_on_fee on every settlement row")

    # ------------------------------------------------------------------
    # the eleven planted cases
    # ------------------------------------------------------------------
    ec = Checks()

    # EC01 -- one bank credit standing in for 40 payments
    biggest = max(truth, key=lambda t: len(t["order_ids"].split("|")) if t["order_ids"] else 0)
    n_orders = len(biggest["order_ids"].split("|")) if biggest["order_ids"] else 0
    ec.check("EC01", n_orders >= 40,
             f"{biggest['bank_txn_id']} carries {n_orders} orders in one credit of "
             f"Rs {bank_by_id[biggest['bank_txn_id']]['credit']}")

    # EC02 -- fee + GST netting means the credit never equals the sale total
    gaps = []
    for t in truth:
        if t["relation_type"] not in ("batch_settlement", "singleton_settlement"):
            continue
        rows = [s for s in by_settlement[t["settlement_ids"]] if s["status"] == "processed"]
        gross = sum(parse_rupees(s["gross_amount"]) for s in rows)
        credit = parse_rupees_or_zero(bank_by_id[t["bank_txn_id"]]["credit"])
        if credit != gross:
            gaps.append((t["bank_txn_id"], gross - credit))
    ec.check("EC02", len(gaps) == len([t for t in truth if t["relation_type"].endswith("settlement")]),
             f"all {len(gaps)} settlement credits differ from their gross total; "
             f"largest gap Rs {format_rupees(max(g for _, g in gaps))}")

    # EC03 -- a refund hiding inside a later batch
    refund_rows = []
    for t in truth:
        pids = t["payment_ids"].split("|") if t["payment_ids"] else []
        if any(p.startswith("rfnd_") for p in pids) and any(p.startswith("pay_") for p in pids):
            refund_rows.append(t)
    detail = "none found"
    if refund_rows:
        t = refund_rows[0]
        rf = [s for s in by_settlement[t["settlement_ids"]] if s["payment_id"].startswith("rfnd_")][0]
        orig = order_by_id[rf["order_id"]]
        lag = (d(bank_by_id[t["bank_txn_id"]]["txn_date"]) - ts(orig["created_at"]).date()).days
        detail = (f"{len(refund_rows)} batches contain a refund; e.g. {rf['payment_id']} "
                  f"(Rs {rf['net_amount']}) shrinks {t['bank_txn_id']}, "
                  f"{lag} days after the original sale")
    ec.check("EC03", len(refund_rows) >= 1, detail)

    # EC04 -- chargeback debit, three days after the payment settled
    cb = next((s for s in settlements if s["type"] == "chargeback"), None)
    ok, detail = False, "no chargeback row"
    if cb:
        orig = next(s for s in settlements
                    if s["order_id"] == cb["order_id"] and s["type"] == "payment")
        lag = (ts(cb["settled_at"]).date() - ts(orig["settled_at"]).date()).days
        row = next(t for t in truth if t["relation_type"] == "chargeback_debit")
        is_debit = parse_rupees_or_zero(bank_by_id[row["bank_txn_id"]]["debit"]) > 0
        ok = lag == 3 and is_debit
        detail = (f"{cb['payment_id']} debits Rs {bank_by_id[row['bank_txn_id']]['debit']} "
                  f"on {bank_by_id[row['bank_txn_id']]['txn_date']}, {lag} days after "
                  f"{orig['payment_id']} settled")
    ec.check("EC04", ok, detail)

    # EC05 -- and reversed ten days after that
    rev = next((s for s in settlements if s["type"] == "chargeback_reversal"), None)
    ok, detail = False, "no reversal row"
    if rev and cb:
        lag = (ts(rev["settled_at"]).date() - ts(cb["settled_at"]).date()).days
        row = next(t for t in truth if t["relation_type"] == "chargeback_reversal_credit")
        is_credit = parse_rupees_or_zero(bank_by_id[row["bank_txn_id"]]["credit"]) > 0
        ok = lag == 10 and is_credit
        detail = (f"{rev['payment_id']} credits Rs {bank_by_id[row['bank_txn_id']]['credit']} "
                  f"back on {bank_by_id[row['bank_txn_id']]['txn_date']}, {lag} days later")
    ec.check("EC05", ok, detail)

    # EC06 -- a narration whose UTR was cut in half
    truncated = []
    for row in bank:
        if FULL_UTR_TOKEN.search(row["narration"]):
            continue
        for utr in all_utrs:
            for cut in range(9, 5, -1):
                if utr[:cut] in row["narration"]:
                    truncated.append((row, utr, utr[:cut]))
                    break
            else:
                continue
            break
    ec.check("EC06", len(truncated) >= 1,
             (f"{truncated[0][0]['bank_txn_id']} narration {truncated[0][0]['narration']!r} "
              f"holds only {truncated[0][2]!r} of {truncated[0][1]!r}")
             if truncated else "no truncated UTR found")

    # EC07 -- two identical amounts, same day, no UTR to tell them apart
    seen = defaultdict(list)
    for row in bank:
        if parse_rupees_or_zero(row["credit"]):
            seen[(row["txn_date"], row["credit"])].append(row)
    collisions = [rows for rows in seen.values()
                  if len(rows) > 1 and not any(FULL_UTR_TOKEN.search(r["narration"]) for r in rows)]
    ok = len(collisions) >= 1
    detail = "no ambiguous pair found"
    if ok:
        pair = collisions[0]
        detail = (f"{', '.join(r['bank_txn_id'] for r in pair)} are both Rs {pair[0]['credit']} "
                  f"on {pair[0]['txn_date']} with narration {pair[0]['narration']!r} -- "
                  f"amount and date cannot separate them")
    ec.check("EC07", ok, detail)

    # EC08 -- T+2 stretched by a long weekend
    stretched = []
    for sid, rows in by_settlement.items():
        settled = [r for r in rows if r["settled_at"] and r["type"] == "payment"]
        if not settled:
            continue
        settle_date = ts(settled[0]["settled_at"]).date()
        captures = [ts(order_by_id[r["order_id"]]["created_at"]).date()
                    for r in settled if r["order_id"] in order_by_id]
        if captures and (settle_date - max(captures)).days >= 5:
            stretched.append((sid, max(captures), settle_date))
    ec.check("EC08", len(stretched) >= 1,
             (f"{stretched[0][0]} captured {stretched[0][1]} settles {stretched[0][2]} "
              f"-- {(stretched[0][2] - stretched[0][1]).days} calendar days for a T+2 rule")
             if stretched else "no stretched settlement found")

    # EC09 -- on-hold entry sharing a settlement_id but absent from the credit
    held = [s for s in settlements if s["status"] == "on_hold"]
    ok, detail = False, "no on-hold settlement row"
    if held:
        h = held[0]
        siblings = [s for s in by_settlement[h["settlement_id"]] if s["status"] == "processed"]
        t = truth_by_bank.get(next((tt["bank_txn_id"] for tt in truth
                                    if tt["settlement_ids"] == h["settlement_id"]
                                    and tt["bank_txn_id"]), ""))
        credit = parse_rupees_or_zero(bank_by_id[t["bank_txn_id"]]["credit"]) if t else 0
        naive = sum(parse_rupees(s["net_amount"]) for s in by_settlement[h["settlement_id"]])
        ok = bool(siblings) and h["payment_id"] not in (t["payment_ids"] if t else "") and naive != credit
        detail = (f"{h['payment_id']} (Rs {h['net_amount']}) carries {h['settlement_id']} but never "
                  f"paid out: summing the whole settlement gives Rs {format_rupees(naive)} "
                  f"against a credit of Rs {format_rupees(credit)}")
    ec.check("EC09", ok, detail)

    # EC10 -- paisa rounding drift on GST
    drifted = []
    for s in settlements:
        fee = parse_rupees(s["fee"])
        if fee == 0:
            continue
        exact = Decimal(fee) * GST_ON_FEE_RATE
        if exact != exact.to_integral_value():
            drifted.append((s, exact, parse_rupees(s["gst_on_fee"])))
    ok = len(drifted) >= 1
    detail = "no rounding drift found"
    if ok:
        s, exact, actual = drifted[0]
        detail = (f"{len(drifted)} rows round; e.g. {s['payment_id']} fee {s['fee']} x 0.18 "
                  f"= {exact} paisa, booked as {actual} "
                  f"(drift {actual - exact:+} paisa)")
    ec.check("EC10", ok, detail)

    # EC11 -- a bank credit that only looks like a settlement
    foreign = [t for t in truth if t["relation_type"] == "foreign" and t["bank_txn_id"]]
    decoys = [t for t in foreign
              if UTR_RE.search(bank_by_id[t["bank_txn_id"]]["narration"])
              and "RAZORPAY" in bank_by_id[t["bank_txn_id"]]["narration"].upper()
              and parse_rupees_or_zero(bank_by_id[t["bank_txn_id"]]["credit"]) > 0]
    ec.check("EC11", len(decoys) >= 1,
             (f"{decoys[0]['bank_txn_id']} {bank_by_id[decoys[0]['bank_txn_id']]['narration']!r} "
              f"credits Rs {bank_by_id[decoys[0]['bank_txn_id']]['credit']} and links to nothing "
              f"-- must be refused, not matched")
             if decoys else "no non-settlement decoy found")

    a = st.report("Structural invariants")
    b = ec.report("Planted edge cases (project-statement.md §8)")

    total = len(ec.results)
    passed = sum(1 for _, ok, _ in ec.results if ok)
    print(f"\n{passed}/{total} edge cases present, "
          f"{sum(1 for _, ok, _ in st.results if ok)}/{len(st.results)} invariants hold")
    return 0 if (a and b) else 1


if __name__ == "__main__":
    sys.exit(main())
