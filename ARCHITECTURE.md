# Architecture

## The problem in one picture

A merchant has three lists that never agree.

```
ORDERS                SETTLEMENT REPORT                BANK STATEMENT
(what I sold)         (what Razorpay says it sent)     (what landed)

order_id  ──1:1──▶    payment_id
                      ├─ gross, fee, gst_on_fee, net
                      └─ settlement_id ──N:1──▶        one credit (UTR, amount, date)
```

**Join A** (order → payment) is a clean `order_id`. Easy.

**Join B** (many payments to one bank credit) is the whole problem. You have to
sum a group, subtract a fee and 18% GST *on that fee*, allow a date window that
weekends and holidays stretch, and read a narration the bank may have cut in
half.

Everything interesting lives in Join B.

## Pipeline

```
┌─────────────────────────────────────────────────────────┐
│ 1. SYNTHETIC DATA GENERATOR          data/generate.py   │
│    3 CSVs + a hidden ground-truth link table            │
│    Seeded. The answer key is written AS THE DATA IS MADE.│
└────────────────────────┬────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────┐
│ 2. DETERMINISTIC MATCHER             src/matcher/       │
│    T0 exact UTR → T3 narration salvage → T1 singleton   │
│    → T2 batch decomposition → T2b bounded subset-sum    │
│    Then: refusal by exhaustion, residue annotation.     │
└──────────────┬──────────────────────────────────────────┘
        matched │ residue
                ▼
┌─────────────────────────────────────────────────────────┐
│ 3. LLM EXCEPTION ANALYST             src/analyst/       │
│    Sees ONLY one residue line, its plausible            │
│    neighbours, the fee schedule, and the rules that     │
│    already failed. Returns strict JSON. Proposes only.  │
└────────────────────────┬────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────┐
│ 4. VERIFIER                          src/verifier/      │
│    Recompute to the paisa. Balances → accept.           │
│    Does not balance, reject. The line stays unresolved. │
└────────────────────────┬────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────┐
│ 5. REPORT + AUDIT TRAIL                                 │
│    results.md · exceptions.md · runs/run_log.jsonl      │
└─────────────────────────────────────────────────────────┘
```

## The central decision

> The LLM does not do the matching. A deterministic engine does. The LLM only
> explains what the engine could not match, and a verifier recomputes its
> arithmetic before anything is accepted.

This is enforced by control flow, not by prompt discipline. `src/analyst/`
never calls `State.accept`. The only route from a proposal to a link is
`State.accept_verified`, and the only thing that calls it is the code right
after `verifier.verify` returns an acceptance. A model cannot write to the
ledger because there is no function it can reach that does.

## Money

Integer paisa everywhere. Rupee strings exist only at the CSV boundary.
`src/money.py` refuses sub-paisa input instead of rounding it away quietly, and
holds the one rounding rule (`apply_rate`, half-up) that the generator applies
forward and the verifier applies backward.

Floats would make the verifier's own arithmetic a source of drift, and then
there is no way to tell a real 3-paisa GST gap from IEEE-754 noise.

## The tiers

Each tier is a **global pass**, not a per-row cascade. T0 claims everything it
is sure about across all bank lines before the next tier sees what is left. In
a per-row cascade a weak rule can claim a settlement that a strong rule was
about to claim correctly. That is a false match created by nothing but loop
order.

| Tier | Rule | Evidence |
|---|---|---|
| **T0** | Full UTR in the narration **and** an exact payout | reference + amount |
| **T3** | Salvage a mangled UTR (whitespace, truncation, edit distance ≤ 2), **corroborated by an exact payout** | reference + amount |
| **T1** | Unique singleton settlement by amount and date | amount + date |
| **T2** | Group by `settlement_id`, sum the processed nets, match the total | amount + date |
| **T2b** | Bounded subset-sum over leftovers, unique solution only | amount + date |
| → | Everything else is **residue** and goes to the analyst | |

### Why T3 runs second, not last

§9 of the project statement lists T3 last, as a fuzzy last resort. That is the
wrong place for it. The difference is measured, not argued: every run executes
both orders and prints the comparison in results.md.

T3 needs the payout to match exactly *as well as* the salvaged reference. That
makes it **stronger** evidence than T1 and T2's amount-and-date agreement, not
weaker. Put it last and T2 claims those lines first on arithmetic alone, and
the recovered reference never gets looked at. The match count is the same
either way. What changes is that 10 links now rest on a recovered identifier
instead of on the absence of a coincidence.

### The rule that prevents false matches

`_match_unique` is written once and used by every amount-based tier:

> A bank line is matched only when it has exactly one candidate, **and** that
> candidate is the sole candidate of exactly one bank line.

Without the second half, two identical payouts on the same day get resolved by
whichever one the loop reached first. A coin flip, recorded as a fact. That is
edge case EC07, and declining it is the correct outcome.

### Refusal by exhaustion

Not a heuristic, and not keyword matching. If every unclaimed settlement inside
the date window, added together, cannot reach the amount of a bank line, then
no combination of them can either. So the line is provably not a settlement:

> `bank_0028`: every unclaimed settlement within 3 days of 2025-06-24 adds up
> to ₹2,927.24, which cannot reach ₹2,50,000.00.

That is what turns the planted loan disbursal (EC11) from "unresolved" into a
refusal with evidence behind it.

## The verifier

Eight checks on any real proposal, run in order. Once one fails the rest are
skipped, because they would be answering a question the failed check already
settled. `proposal_present` sits in front of all of them as an early exit for
an empty link, which is a valid answer rather than a failure.

| Check | Catches |
|---|---|
| `proposal_present` | high confidence with no actual link |
| `distinct_lines` | the same settlement line counted twice to force a balance |
| **`determinacy`** | a balanced guess on a line proved indeterminate |
| `existence` | invented settlement ids |
| `availability` | a settlement another bank line already owns |
| `settlement_status` | an on-hold line swept in to force a sum to balance |
| `date_window` | reaching outside the window for a number that happens to fit |
| `line_arithmetic` | fee and GST recomputed from the schedule, to the paisa |
| `sum_balances` | nets that do not add to the bank amount, at zero tolerance |

**`determinacy` is the one that matters most.** On EC07 the two candidate
settlements have identical payouts, so a coin flip between them balances
perfectly. An arithmetic-only verifier accepts it and writes down a 50-50 guess
as a verified fact.

So recomputing the maths is necessary but not enough. The verifier also refuses
any proposal for a line the engine already proved indeterminate. A claim that
no evidence can disprove is not a finding.

The verifier runs at **zero tolerance**, unlike the tiers at plus or minus 5
paisa. The tiers need slack because they compare against reconstructed figures.
The verifier recomputes every component itself, so it asks for the paisa.

## Scoring

Six buckets, not three. Three cannot express this problem.

| | truth has a link | truth says foreign |
|---|---|---|
| **matched, set right** | `correct_match` | `false_match` |
| **matched, set wrong** | `false_match` | `false_match` |
| **refused** | `false_refusal` | `correct_refusal` |
| **unresolved** | `honest_miss` | `foreign_unresolved` |

- **`correct_refusal`** is a win. A scorer that files EC11 under "unresolved"
  punishes exactly the behaviour this project exists to show.
- **`false_refusal`** is its own error class, separate from an honest miss. One
  is over-caution, the other is a coverage gap. They get fixed by opposite
  changes, so they cannot share a bucket.
- **`foreign_unresolved`** is kept apart from `correct_refusal` on purpose. "I
  could not match this" and "this is not a settlement and I decline to match
  it" are different claims. Only the second one is worth showing.

## Data model

`orders.csv`: `order_id, customer_id, order_amount, currency, created_at, status`

`settlements.csv`: `settlement_id, payment_id, order_id, type, gross_amount,
fee, gst_on_fee, net_amount, settled_at, utr, status`
where `type ∈ {payment, refund, chargeback, chargeback_reversal, adjustment}`

`bank_statement.csv`: `bank_txn_id, txn_date, narration, credit, debit, closing_balance`

`ground_truth.csv`: `bank_txn_id, settlement_ids, payment_ids, order_ids,
relation_type, edge_case_ids`. **Never shown to the matcher.**

### Where this differs from the stated schema, and why

- `bank_statement.csv` carries a `bank_txn_id`. Banks do not give you one. It
  is a row handle assigned at ingestion, because the answer key needs something
  to point at.
- `ground_truth.csv` adds `payment_ids` and `edge_case_ids`. Every row of a
  batch shares one `settlement_id`, so it cannot identify a single settlement
  line, and the scorer needs row-level identity. `edge_case_ids` is what lets
  the report give a verdict per case instead of one aggregate number.
- `settlements.csv` reuses the `payment_id` column for `rfnd_`, `cb_`,
  `cbrev_` and `adj_` entity ids. A real Razorpay settlement report reuses its
  entity id column the same way.

## Audit trail

`runs/run_log.jsonl`. Every tier records the rule that fired, its inputs and
its confidence. Near misses go in too. A rule that *almost* fired is the most
useful thing in the file when a number looks wrong.

Each bank line gets a closing `decision` record, and `run.py` replays the log
after every run to check it reproduces every verdict on its own. A trail you
cannot replay is not an audit trail, so the run fails if it drifts.
