"""Load the three CSVs into typed records. Money arrives as int paisa."""

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from money import parse_rupees, parse_rupees_or_zero


@dataclass(frozen=True)
class Order:
    order_id: str
    customer_id: str
    amount: int
    created_at: datetime
    status: str


@dataclass(frozen=True)
class SettlementRow:
    """One line of the settlement report. `entity_id` is the payment_id column,
    which in a real report holds pay_ / rfnd_ / cb_ / adj_ ids alike."""
    settlement_id: str
    entity_id: str
    order_id: str
    type: str
    gross: int
    fee: int
    gst: int
    net: int
    settled_at: date | None
    utr: str
    status: str


@dataclass(frozen=True)
class BankTxn:
    bank_txn_id: str
    txn_date: date
    narration: str
    credit: int
    debit: int

    @property
    def signed(self):
        """One number, sign-carrying. A payout is positive, a clawback negative."""
        return self.credit - self.debit


@dataclass
class SettlementGroup:
    """All the report lines that share one settlement_id -- i.e. one payout."""
    settlement_id: str
    rows: list = field(default_factory=list)

    @property
    def processed(self):
        # An on-hold line carries a settlement_id but was never paid out.
        # Summing without this filter overshoots the bank credit (EC09).
        return [r for r in self.rows if r.status == "processed"]

    @property
    def payout(self):
        return sum(r.net for r in self.processed)

    @property
    def naive_payout(self):
        """What you get if you ignore status. Logged when it differs, so the
        audit trail shows the trap was live rather than absent."""
        return sum(r.net for r in self.rows)

    @property
    def utr(self):
        for r in self.processed:
            if r.utr:
                return r.utr
        return ""

    @property
    def settled_at(self):
        for r in self.processed:
            if r.settled_at:
                return r.settled_at
        return None

    @property
    def entity_ids(self):
        return [r.entity_id for r in self.processed]


def _dt(text):
    return datetime.fromisoformat(text) if text else None


def load(data_dir: Path):
    data_dir = Path(data_dir)

    with open(data_dir / "orders.csv", newline="", encoding="utf-8") as fh:
        orders = [Order(r["order_id"], r["customer_id"], parse_rupees(r["order_amount"]),
                        _dt(r["created_at"]), r["status"]) for r in csv.DictReader(fh)]

    with open(data_dir / "settlements.csv", newline="", encoding="utf-8") as fh:
        settlements = []
        for r in csv.DictReader(fh):
            settled = _dt(r["settled_at"])
            settlements.append(SettlementRow(
                r["settlement_id"], r["payment_id"], r["order_id"], r["type"],
                parse_rupees(r["gross_amount"]), parse_rupees(r["fee"]),
                parse_rupees(r["gst_on_fee"]), parse_rupees(r["net_amount"]),
                settled.date() if settled else None, r["utr"], r["status"]))

    with open(data_dir / "bank_statement.csv", newline="", encoding="utf-8") as fh:
        bank = [BankTxn(r["bank_txn_id"], date.fromisoformat(r["txn_date"]), r["narration"],
                        parse_rupees_or_zero(r["credit"]), parse_rupees_or_zero(r["debit"]))
                for r in csv.DictReader(fh)]

    groups = {}
    for row in settlements:
        groups.setdefault(row.settlement_id, SettlementGroup(row.settlement_id)).rows.append(row)

    return orders, settlements, bank, groups
