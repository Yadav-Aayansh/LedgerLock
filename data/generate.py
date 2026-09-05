#!/usr/bin/env python3
"""LedgerLock — synthetic data generator (seeded, reproducible).

Emits four CSVs into data/:

    orders.csv          what the merchant sold
    settlements.csv     what Razorpay says it sent
    bank_statement.csv  what actually landed
    ground_truth.csv    the hidden link table -- NEVER shown to the matcher

Two deliberate choices worth stating up front:

1. This file was written without reference to how the matcher will solve it.
   If the mess is shaped around the tiers, a high match rate measures nothing
   but the author's own assumptions.

2. Ground truth is emitted *by construction* -- the generator knows every link
   because it created them. It is never reconstructed afterwards by inference,
   which would make the false-match rate circular and worthless.

Schema notes (small, deliberate deviations from project-statement.md §7):

  * bank_statement.csv carries a `bank_txn_id` first column. Banks do not
    supply one; this is a row handle assigned at ingestion, and ground truth
    needs something to point at.
  * ground_truth.csv carries `payment_ids` and `edge_case_ids` in addition to
    the fields in §7. `payment_ids` is what the scorer actually compares
    against (settlement_id is shared across every row of a batch, so it cannot
    identify a single settlement line); `edge_case_ids` is what lets the final
    report give a per-case handled/partial/failed verdict.
  * settlements.csv reuses the `payment_id` column for refund / chargeback /
    adjustment entity ids (rfnd_, cb_, cbrev_, adj_), exactly as a real
    Razorpay settlement report reuses its entity_id column.
"""

import argparse
import csv
import json
import random
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from edge_cases import EDGE_CASES          # noqa: E402
from fees import SCHEDULE, breakdown       # noqa: E402
from money import format_rupees            # noqa: E402

SEED = 42
START = date(2025, 6, 1)
DAYS = 30
IST = "+05:30"

# A single mid-June holiday turns Sat 14 / Sun 15 / Mon 16 into a long weekend,
# so Friday the 13th's capture settles on Wednesday the 18th. That is EC08 --
# a five-calendar-day gap produced by an ordinary T+2 rule, not by a fudge.
HOLIDAYS = {date(2025, 6, 16)}

SPIKE_DAY = date(2025, 6, 10)      # EC01: 40 paid orders in one day, one credit
AMBIG_DAY = date(2025, 6, 20)      # EC07: two identical amounts, split batches
AMBIG_AMOUNT = 149900              # Rs 1,499.00
OPENING_BALANCE = 50000000         # Rs 5,00,000.00

PRICE_POINTS = [49900, 79900, 99900, 129900, 149900,
                199900, 249900, 349900, 499900, 799900]

NARRATION_TEMPLATES = [
    "NEFT-{utr}-RAZORPAY SOFTWARE PVT LTD",
    "IMPS/{utr}/RAZORPAYSOFTWAREP",
    "NEFT/{utr}/RAZORPAY SOFTWARE PRIVATE LIMI",
    "RTGS-{utr}-RAZORPAY SOFTWARE PVT LTD-COLLECTIONS",
    "NEFT-{utr}-RAZORPAY SOFT",
]

# Narration quality is not uniform in the real world -- it varies by channel,
# by bank, and by how much of the reference string survived truncation. If
# every credit carried a clean UTR, exact-id matching would resolve almost
# everything and the rest of the engine would be decoration. This distribution
# is set from how messy bank statements actually are, not from what the
# matching tiers would like to be handed.
NARRATION_STYLES = ["full", "no_utr", "truncated", "garbled"]
NARRATION_WEIGHTS = [0.45, 0.25, 0.15, 0.15]

NO_UTR_TEMPLATES = [
    "UPI-RAZORPAYSOFTWARE-SETTLEMENT-CR",
    "NEFT-RAZORPAY SOFTWARE PVT LTD-SETTLEMENT",
    "IMPS-RZP-SETTLEMENT-JUN-CR",
    "NEFT CR RAZORPAY SOFTWARE PRIVATE LIMITED",
]


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------

@dataclass
class Order:
    order_id: str
    customer_id: str
    amount: int
    created_at: datetime
    status: str            # paid | failed | pending


@dataclass
class Entry:
    """One row of settlements.csv."""
    entity_id: str         # pay_ / rfnd_ / cb_ / cbrev_ / adj_
    order_id: str
    type: str
    gross: int
    fee: int
    gst: int
    net: int
    status: str            # processed | on_hold


@dataclass
class Batch:
    """One settlement = one payout = (eventually) one bank line."""
    settlement_id: str
    utr: str
    settled_at: date
    entries: list = field(default_factory=list)
    narration_style: str = "full"
    credit_lag: int = 0          # business days between payout and bank credit
    edge_cases: list = field(default_factory=list)

    def processed(self):
        return [e for e in self.entries if e.status == "processed"]

    def payout(self):
        return sum(e.net for e in self.processed())


@dataclass
class BankRow:
    txn_date: date
    narration: str
    credit: int
    debit: int
    seq: int
    # ground truth only -- never written to bank_statement.csv
    settlement_ids: list = field(default_factory=list)
    payment_ids: list = field(default_factory=list)
    order_ids: list = field(default_factory=list)
    relation: str = "foreign"
    edge_cases: list = field(default_factory=list)
    bank_txn_id: str = ""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def is_business_day(d):
    return d.weekday() < 5 and d not in HOLIDAYS


def business_days_after(d, n):
    cur, left = d, n
    while left > 0:
        cur += timedelta(days=1)
        if is_business_day(cur):
            left -= 1
    return cur


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + IST


class UtrFactory:
    """RZPX + 8 digits, unique across the run."""

    def __init__(self, rng):
        self.rng = rng
        self.seen = set()

    def __call__(self):
        while True:
            utr = f"RZPX{self.rng.randrange(10_000_000, 100_000_000)}"
            if utr not in self.seen:
                self.seen.add(utr)
                return utr


# --------------------------------------------------------------------------
# stage 1 -- orders
# --------------------------------------------------------------------------

def build_orders(rng):
    orders = []
    n = 0
    for offset in range(DAYS):
        day = START + timedelta(days=offset)

        if day == SPIKE_DAY:
            count = 40
        elif day == AMBIG_DAY:
            count = 2
        else:
            count = rng.choice([2, 3, 3, 4, 4, 5])

        for _ in range(count):
            n += 1
            if day == AMBIG_DAY:
                amount = AMBIG_AMOUNT
                status = "paid"
            else:
                # A mix of clean price points and genuinely messy amounts.
                # The messy ones are what make GST rounding drift show up.
                amount = (rng.choice(PRICE_POINTS) if rng.random() < 0.7
                          else rng.randrange(30_000, 900_000))
                roll = rng.random()
                status = "paid" if roll < 0.88 else ("failed" if roll < 0.95 else "pending")
                if day == SPIKE_DAY:
                    status = "paid"

            created = datetime.combine(
                day, time(rng.randrange(6, 23), rng.randrange(0, 60), rng.randrange(0, 60))
            )
            orders.append(Order(
                order_id=f"ord_{n:05d}",
                customer_id=f"cust_{rng.randrange(1, 400):04d}",
                amount=amount,
                created_at=created,
                status=status,
            ))
    return orders


# --------------------------------------------------------------------------
# stage 2 -- settlements
# --------------------------------------------------------------------------

def build_batches(orders, rng, utrs):
    paid_by_day = {}
    for o in orders:
        if o.status == "paid":
            paid_by_day.setdefault(o.created_at.date(), []).append(o)

    batches, sid, pay_n = [], 0, 0
    for day in sorted(paid_by_day):
        same_day = paid_by_day[day]
        # Merchants can receive more than one payout a day. On the ambiguity
        # day we split deliberately so the two Rs 1,499 sales land as two
        # separate credits of identical amount on identical dates.
        groups = [[o] for o in same_day] if day == AMBIG_DAY else [same_day]
        settled_at = business_days_after(day, 2)

        for group in groups:
            sid += 1
            batch = Batch(
                settlement_id=f"stl_{sid:05d}", utr=utrs(), settled_at=settled_at,
                narration_style=rng.choices(NARRATION_STYLES, NARRATION_WEIGHTS)[0],
                credit_lag=0 if rng.random() < 0.75 else 1,
            )
            for o in group:
                pay_n += 1
                b = breakdown(o.amount)
                batch.entries.append(Entry(
                    entity_id=f"pay_{pay_n:05d}", order_id=o.order_id, type="payment",
                    gross=b["gross"], fee=b["fee"], gst=b["gst_on_fee"], net=b["net"],
                    status="processed",
                ))
            if day == SPIKE_DAY:
                batch.edge_cases.append("EC01")
                batch.narration_style = "no_utr"
            if day == AMBIG_DAY:
                batch.edge_cases.append("EC07")
                batch.credit_lag = 0     # the pair must collide on one date
                # No usable UTR in the narration: amount + date is the only
                # lever, and it is genuinely insufficient. Guessing here is a
                # coin flip that lands in the false-match rate.
                batch.narration_style = "no_utr"
            if (settled_at - day).days >= 5:
                batch.edge_cases.append("EC08")
            batches.append(batch)

    return batches


def inject_refunds(batches, rng):
    """EC03 -- a refund booked into a *later* batch, shrinking it for no
    visible reason. The platform fee on the original payment is not returned,
    so the refund nets at full face value."""
    ordered = sorted(batches, key=lambda b: (b.settled_at, b.settlement_id))
    early = [b for b in ordered if b.settled_at <= START + timedelta(days=16)]
    # The ambiguity pair (EC07) must keep its two payouts exactly equal, so
    # nothing may be netted into either of them.
    late = [b for b in ordered
            if b.settled_at >= START + timedelta(days=20) and "EC07" not in b.edge_cases]

    sources = []
    for b in early:
        sources.extend([(b, e) for e in b.entries if e.type == "payment"])
    picked = rng.sample(sources, 4)
    targets = rng.sample(late, 4)

    for n, ((_, entry), target) in enumerate(zip(picked, targets), start=1):
        # Three full refunds and one partial, because both happen.
        amount = entry.gross if n != 4 else entry.gross // 2
        target.entries.append(Entry(
            entity_id=f"rfnd_{n:04d}", order_id=entry.order_id, type="refund",
            gross=-amount, fee=0, gst=0, net=-amount, status="processed",
        ))
        if "EC03" not in target.edge_cases:
            target.edge_cases.append("EC03")


def inject_adjustment(batches, rng):
    """Not one of the eleven, but `adjustment` is in the §7 type enum and a
    batch that fails to balance for an unexplained reason is realistic."""
    target = rng.choice([b for b in batches
                         if len(b.entries) >= 3 and "EC07" not in b.edge_cases])
    target.entries.append(Entry(
        entity_id="adj_0001", order_id="", type="adjustment",
        gross=-7500, fee=0, gst=0, net=-7500, status="processed",
    ))


def inject_on_hold(batches, rng):
    """EC09 -- an entry that carries a settlement_id but was never paid out.
    Anyone who groups by settlement_id and sums without checking status will
    overshoot the bank credit and conclude the batch does not match."""
    candidates = [b for b in batches
                  if len(b.entries) >= 4 and not b.edge_cases and b.narration_style == "full"]
    target = rng.choice(candidates)
    held = target.entries[1]
    held.status = "on_hold"
    target.edge_cases.append("EC09")
    return target, held


def pick_truncated(batches, rng):
    """EC06 -- the bank cuts the narration off mid-UTR."""
    candidates = [b for b in batches if b.narration_style == "full" and not b.edge_cases]
    target = rng.choice(candidates)
    target.narration_style = "truncated"
    target.edge_cases.append("EC06")
    return target


def build_chargeback(batches, rng, utrs, next_sid):
    """EC04 / EC05 -- a clawback landing three days after the payment settled,
    then reversed ten days after that when the merchant wins the dispute.
    Both arrive as their own bank lines, not netted into a batch."""
    pool = [b for b in batches
            if not b.edge_cases
            and START + timedelta(days=6) <= b.settled_at <= START + timedelta(days=14)]
    source = rng.choice(pool)
    entry = source.processed()[0]

    cb_date = source.settled_at + timedelta(days=3)
    rev_date = cb_date + timedelta(days=10)

    cb = Batch(settlement_id=f"stl_{next_sid:05d}", utr=utrs(), settled_at=cb_date,
               edge_cases=["EC04"])
    cb.entries.append(Entry(
        entity_id="cb_0001", order_id=entry.order_id, type="chargeback",
        gross=-entry.gross, fee=0, gst=0, net=-entry.gross, status="processed",
    ))

    rev = Batch(settlement_id=f"stl_{next_sid + 1:05d}", utr=utrs(), settled_at=rev_date,
                edge_cases=["EC05"])
    rev.entries.append(Entry(
        entity_id="cbrev_0001", order_id=entry.order_id, type="chargeback_reversal",
        gross=entry.gross, fee=0, gst=0, net=entry.gross, status="processed",
    ))
    return cb, rev


# --------------------------------------------------------------------------
# stage 3 -- bank statement
# --------------------------------------------------------------------------

def narration_for(batch, rng):
    if batch.narration_style == "truncated":
        # The bank cut the reference string off mid-UTR.
        return f"NEFT-{batch.utr[:8]}-RAZORPAY SOFTWA"
    if batch.narration_style == "no_utr":
        # EC07's pair must collide exactly, so it always gets the same shell.
        if "EC07" in batch.edge_cases:
            return NO_UTR_TEMPLATES[0]
        return rng.choice(NO_UTR_TEMPLATES)
    if batch.narration_style == "garbled":
        # The UTR is all there but no longer greppable: a digit dropped, or
        # whitespace injected mid-token. This is T3 fuzzy-salvage territory.
        u = batch.utr
        if rng.random() < 0.5:
            cut = rng.randrange(5, len(u) - 1)
            mangled = u[:cut] + u[cut + 1:]
        else:
            mangled = f"{u[:4]} {u[4:8]} {u[8:]}"
        return rng.choice(NARRATION_TEMPLATES).format(utr=mangled)
    return rng.choice(NARRATION_TEMPLATES).format(utr=batch.utr)


def foreign_rows(seq):
    """Ordinary business traffic, plus the one row that matters.

    The loan disbursal is Razorpay-branded, carries a UTR-shaped reference and
    sits in a plausible amount range. Everything a naive matcher keys on says
    'settlement'. The correct behaviour is to refuse it (EC11)."""
    rows = [
        BankRow(date(2025, 6, 5), "UPI/DR/512334123/PROPCO REALTY/OFFICE RENT JUN",
                0, 3500000, seq + 1),
        BankRow(date(2025, 6, 12), "ACH-DR-VENDOR PAYMENT-SUPPLYCO PACKAGING",
                0, 1245000, seq + 2),
        BankRow(date(2025, 6, 17), "NEFT-CR-HDFC0001234-DIRECT CUSTOMER TRANSFER",
                890000, 0, seq + 3),
        BankRow(date(2025, 6, 24), "NEFT-RZPX00088421-RAZORPAY CAPITAL LOAN DISB",
                25000000, 0, seq + 4, relation="foreign", edge_cases=["EC11"]),
        BankRow(date(2025, 6, 30), "SALARY JUN 2025 PAYROLL BATCH",
                0, 18500000, seq + 5),
    ]
    return rows


def build_bank_rows(batches, rng):
    rows, seq = [], 0
    for batch in sorted(batches, key=lambda b: (b.settled_at, b.settlement_id)):
        processed = batch.processed()
        payout = batch.payout()
        seq += 1

        if payout >= 0:
            credit, debit = payout, 0
        else:
            credit, debit = 0, -payout

        if len(processed) == 1 and processed[0].type == "chargeback":
            relation = "chargeback_debit"
        elif len(processed) == 1 and processed[0].type == "chargeback_reversal":
            relation = "chargeback_reversal_credit"
        elif len(processed) == 1:
            relation = "singleton_settlement"
        else:
            relation = "batch_settlement"

        rows.append(BankRow(
            txn_date=(business_days_after(batch.settled_at, batch.credit_lag)
                      if batch.credit_lag else batch.settled_at),
            narration=narration_for(batch, rng),
            credit=credit,
            debit=debit,
            seq=seq,
            settlement_ids=[batch.settlement_id],
            payment_ids=[e.entity_id for e in processed],
            order_ids=[e.order_id for e in processed if e.order_id],
            relation=relation,
            edge_cases=list(batch.edge_cases),
        ))

    rows.extend(foreign_rows(seq))
    rows.sort(key=lambda r: (r.txn_date, r.seq))
    for i, row in enumerate(rows, start=1):
        row.bank_txn_id = f"bank_{i:04d}"
    return rows


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------

def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def write_orders(out, orders):
    write_csv(out / "orders.csv",
              ["order_id", "customer_id", "order_amount", "currency", "created_at", "status"],
              [[o.order_id, o.customer_id, format_rupees(o.amount), "INR",
                iso(o.created_at), o.status] for o in orders])


def write_settlements(out, batches):
    flat = []
    for b in sorted(batches, key=lambda b: (b.settled_at, b.settlement_id)):
        for e in b.entries:
            held = e.status == "on_hold"
            flat.append([
                b.settlement_id, e.entity_id, e.order_id, e.type,
                format_rupees(e.gross), format_rupees(e.fee), format_rupees(e.gst),
                format_rupees(e.net),
                "" if held else iso(datetime.combine(b.settled_at, time(14, 0))),
                "" if held else b.utr,
                e.status,
            ])
    write_csv(out / "settlements.csv",
              ["settlement_id", "payment_id", "order_id", "type", "gross_amount",
               "fee", "gst_on_fee", "net_amount", "settled_at", "utr", "status"],
              flat)


def write_bank(out, rows):
    body, balance = [], OPENING_BALANCE
    for r in rows:
        balance += r.credit - r.debit
        body.append([
            r.bank_txn_id, r.txn_date.isoformat(), r.narration,
            format_rupees(r.credit) if r.credit else "",
            format_rupees(r.debit) if r.debit else "",
            format_rupees(balance),
        ])
    write_csv(out / "bank_statement.csv",
              ["bank_txn_id", "txn_date", "narration", "credit", "debit", "closing_balance"],
              body)


def write_ground_truth(out, rows, held):
    body = [[r.bank_txn_id, "|".join(r.settlement_ids), "|".join(r.payment_ids),
             "|".join(r.order_ids), r.relation, "|".join(r.edge_cases)] for r in rows]
    held_batch, held_entry = held
    # A settlement line with no bank counterpart at all: the on-hold entry.
    # It is an exception in the other direction, and the scorer needs to know
    # that leaving it unmatched is correct.
    body.append(["", held_batch.settlement_id, held_entry.entity_id,
                 held_entry.order_id, "unsettled_hold", "EC09"])
    write_csv(out / "ground_truth.csv",
              ["bank_txn_id", "settlement_ids", "payment_ids", "order_ids",
               "relation_type", "edge_case_ids"],
              body)


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Generate LedgerLock synthetic data.")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--out", type=Path, default=ROOT / "data")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    utrs = UtrFactory(rng)

    orders = build_orders(rng)
    batches = build_batches(orders, rng, utrs)
    inject_refunds(batches, rng)
    inject_adjustment(batches, rng)
    held = inject_on_hold(batches, rng)
    pick_truncated(batches, rng)
    cb, rev = build_chargeback(batches, rng, utrs, len(batches) + 1)
    batches.extend([cb, rev])

    rows = build_bank_rows(batches, rng)

    args.out.mkdir(parents=True, exist_ok=True)
    write_orders(args.out, orders)
    write_settlements(args.out, batches)
    write_bank(args.out, rows)
    write_ground_truth(args.out, rows, held)
    (args.out / "fee_schedule.json").write_text(json.dumps(SCHEDULE, indent=2) + "\n")

    paid = [o for o in orders if o.status == "paid"]
    entries = [e for b in batches for e in b.entries]
    planted = sorted({ec for r in rows for ec in r.edge_cases} | {"EC09"})

    print(f"seed {args.seed}  ->  {args.out}")
    print(f"  orders.csv          {len(orders):>4} rows ({len(paid)} paid)")
    print(f"  settlements.csv     {len(entries):>4} rows in {len(batches)} settlements")
    print(f"  bank_statement.csv  {len(rows):>4} rows")
    print(f"  ground_truth.csv    {len(rows) + 1:>4} rows")
    styles = {}
    for b in batches:
        styles[b.narration_style] = styles.get(b.narration_style, 0) + 1
    print("  narration quality   " + ", ".join(
        f"{k} {v}" for k, v in sorted(styles.items(), key=lambda kv: -kv[1])))
    print(f"  planted edge cases  {len(planted)}/{len(EDGE_CASES)}: {', '.join(planted)}")
    missing = sorted(set(EDGE_CASES) - set(planted))
    if missing:
        print(f"  NOT TAGGED (implicit, checked by assert_planted.py): {', '.join(missing)}")


if __name__ == "__main__":
    main()
