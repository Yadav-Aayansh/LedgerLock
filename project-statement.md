# Project Statement — LedgerLock

**Track 04 · AI Finance Controller · Razorpay AI Buildathon**

---

## 1. One line

> A reconciliation agent that matches a merchant's orders, their Razorpay settlements, and their bank statement — and tells you honestly which ones it couldn't match, and why.

## 2. Explain it to anyone (no jargon)

A shop sells 50 things online. The money does **not** arrive as 50 separate deposits in their bank account. It arrives as a handful of lumpy deposits, days later, with fees quietly cut out.

So the shop owner has three lists that never agree:

1. **What I sold** (their own order system)
2. **What the payment company says it sent me** (Razorpay settlement report)
3. **What actually landed in my bank** (bank statement)

Somebody has to sit with a spreadsheet and make the three lists agree. Every single month. That's reconciliation. It's manual, it's boring, and it's where money silently goes missing.

**LedgerLock does that matching automatically — and, more importantly, admits what it couldn't figure out instead of pretending.**

## 3. Why the three lists never agree

This is the actual difficulty. Not "AI is hard" — this.

| Reason | What it looks like |
|---|---|
| **Batching** | 40 payments get combined into 1 bank credit. No one-to-one line exists. |
| **Fee netting** | Razorpay deducts a platform fee, plus 18% GST *on that fee*, before paying out. So the bank amount never equals the sale amount. |
| **Timing (T+2)** | A Friday sale lands Tuesday. Weekends and holidays stretch the gap unpredictably. |
| **Refunds** | A refund is subtracted from a *later* settlement batch, shrinking it for no visible reason. |
| **Chargebacks** | A disputed payment is clawed back days after it settled — and may be reversed again if the merchant wins. |
| **Mangled bank narration** | The bank truncates the reference string. The UTR is half-there or missing. |
| **Ambiguity** | Two customers pay ₹1,499 on the same day. Amount-matching alone cannot tell them apart. |
| **Rounding** | Paisa-level rounding on GST means totals are off by ₹0.01–0.05. |

## 4. The core structure — "three ledgers, two joins"

This is the whole mental model. Memorise it; it's how you'll explain the project in the video.

```
ORDERS            SETTLEMENT REPORT              BANK STATEMENT
(what I sold)     (what RZP says it sent)        (what landed)

order_id  ──1:1──▶ payment_id                          
                   ├─ gross, fee, tax, net
                   └─ settlement_id ──N:1──▶ one credit (UTR, amount, date)
```

- **Join A (easy-ish):** order → payment. Usually a clean `order_id`.
- **Join B (the hard one):** many payments → one bank credit. Requires summing a group, subtracting fees, allowing a date window, and parsing a messy narration.

Everything interesting lives in Join B.

## 5. The central design decision

> **The LLM does not do the matching. A deterministic engine does the matching. The LLM only explains what the engine could not match — and a verifier checks its arithmetic before anything is accepted.**

Why this matters, and why it should be the first thing you say in the pitch:

Most submissions will dump the whole batch into a model and ask it to reconcile. It will return ~75% and a pile of confidently invented matches. Invented matches in a finance system are worse than no match — a wrong "matched" is a silent error nobody catches, whereas an honest "unresolved" gets a human's attention.

The track brief itself says the bottleneck is **verification capacity, not generation speed**. This architecture *is* that sentence, implemented.

**Rule, enforced in code:** the LLM returns a proposal. A deterministic verifier recomputes the arithmetic to the paisa. If it doesn't balance, the proposal is rejected and the record stays unresolved. The model can never write a match directly.

## 6. Architecture

```
┌─────────────────────────────────────────────────┐
│ 1. SYNTHETIC DATA GENERATOR (seeded)            │
│    3 CSVs + a hidden ground-truth link table    │
└──────────────────┬──────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────┐
│ 2. DETERMINISTIC MATCHER (tiered)               │
│    T0 exact ID → T1 amount+date → T2 batch      │
│    decomposition → T3 fuzzy narration           │
└──────────────────┬──────────────────────────────┘
              matched │ residue
                   ▼
┌─────────────────────────────────────────────────┐
│ 3. LLM EXCEPTION ANALYST                        │
│    Sees ONLY the residue + candidates + fee      │
│    schedule. Returns structured JSON hypothesis. │
└──────────────────┬──────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────┐
│ 4. VERIFIER (deterministic)                     │
│    Recompute the maths. Balances → accept.      │
│    Doesn't → reject, stays unresolved.          │
└──────────────────┬──────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────┐
│ 5. REPORT + AUDIT TRAIL                         │
│    results.md · exceptions.md · run_log.jsonl   │
└─────────────────────────────────────────────────┘
```

## 7. Data model

**`orders.csv`** — the merchant's own system
`order_id, customer_id, order_amount, currency, created_at, status`

**`settlements.csv`** — the Razorpay-style report
`settlement_id, payment_id, order_id, type, gross_amount, fee, gst_on_fee, net_amount, settled_at, utr, status`
`type ∈ {payment, refund, chargeback, chargeback_reversal, adjustment}`

**`bank_statement.csv`** — the bank
`txn_date, narration, credit, debit, closing_balance`
(narration is deliberately messy: `NEFT-RZPX00019283-RAZORPAY SOFT...`)

**`ground_truth.csv`** — generated, **never shown to the matcher**
`bank_txn_id, settlement_id[], order_id[], relation_type`

Ground truth is the whole reason to use synthetic data: it lets you compute **false-match rate**, which is the metric almost nobody else will report.

## 8. Deliberately injected edge cases

Your generator plants these on purpose. Each one is a line item in your final report — "handled / partially handled / failed".

1. A 40-payment batch collapsing into one credit
2. Fee + 18% GST netting so no amount matches directly
3. A refund buried inside a settlement batch
4. A chargeback debit landing 3 days after the payment settled
5. A chargeback **reversal** 10 days after that
6. A truncated narration with the UTR cut in half
7. Two identical-amount payments on the same day (ambiguity trap)
8. A T+2 settlement crossing a long weekend
9. An on-hold / partial settlement
10. Paisa rounding drift on GST
11. **A bank credit that is not a settlement at all** — a loan disbursal that superficially resembles one

Case 11 is the sharpest one. The correct behaviour is to **refuse to match it** and flag it as foreign. A system that correctly declines is a stronger result than one that matches everything.

## 9. The matching tiers (Join B)

| Tier | Method | Notes |
|---|---|---|
| **T0** | Exact UTR extracted from narration + exact net amount | Cleanest path |
| **T1** | Amount within tolerance + date inside T+N window | Tolerance must cover rounding, not fees |
| **T2** | **Batch decomposition** — group settlements by `settlement_id`, sum nets, match to credit | The real workhorse |
| **T2b** | Bounded subset-sum on leftovers only | Hard cap on set size and runtime — this is where naive implementations hang |
| **T3** | Fuzzy narration parse (regex UTR salvage + string distance) | Confidence-scored, not binary |
| **→** | Everything else = **residue**, goes to the LLM | |

Every tier writes to the audit trail: which rule fired, what inputs, what confidence.

## 10. The LLM layer — precise scope

**Gets to see:** one unmatched record, its plausible neighbours, the fee schedule, the tier rules that already failed.

**Must return** strict JSON:

```json
{
  "hypothesis": "This ₹4,312 gap equals 2% platform fee + 18% GST on that fee",
  "arithmetic": "48000 * 0.02 = 960; 960 * 0.18 = 172.80; ...",
  "proposed_link": ["stl_00412", "stl_00413"],
  "confidence": 0.82,
  "unresolvable_reason": null
}
```

**Is allowed to:** propose a link, explain a discrepancy, classify an exception, say `"I cannot resolve this"`.

**Is forbidden from:** writing to the ledger, computing final amounts, being trusted without the verifier.

`"I cannot resolve this"` must be a first-class, rewarded output — not a failure. Prompt for it explicitly.

## 11. Metrics you report

Non-negotiable. These go in `results.md` as a plain table.

| Metric | Why it's there |
|---|---|
| Auto-match rate (T0–T3) | Baseline throughput |
| LLM-assisted resolution rate | Did the model actually add value? |
| **False-match rate** | Matched but wrong. **The number nobody else will publish.** |
| Unresolved count | Honest remainder |
| Verifier rejection rate | How often the model was wrong and got caught |
| Cost per 50 records | Tokens ≈ ₹ |
| Wall-clock runtime | Throughput |

Target framing for the video: *"92% auto-matched, 6% resolved with AI assistance, 2% unresolved, **0 false matches**."* Zero false matches is the headline. Not the 92%.

## 12. Deliverables

```
ledgerlock/
├── README.md              ← run in one command, results in 60s
├── results.md             ← the metrics table. Root level. Non-negotiable.
├── exceptions.md          ← every unresolved record, individually, with your honest guess at why
├── ARCHITECTURE.md        ← the diagram from §6
├── data/generate.py       ← seeded, reproducible
├── src/matcher/           ← deterministic tiers
├── src/analyst/           ← LLM layer
├── src/verifier/          ← arithmetic checker
└── runs/run_log.jsonl     ← full audit trail
```

**Reproducibility clause in the README:** *"Run `make demo`. You will get exactly the numbers in results.md, seed 42."* Judges who can rerun you trust you.

## 13. Build plan

**7-day version**

| Day | Work |
|---|---|
| 1 | Data generator + ground truth. Nothing else. Get the mess right first. |
| 2 | T0 + T1. Measure. Expect a bad number — that's the baseline. |
| 3 | T2 batch decomposition. This is where the match rate jumps. |
| 4 | T3 fuzzy + the audit trail |
| 5 | LLM analyst + verifier |
| 6 | Metrics harness, `results.md`, `exceptions.md`. **Do not skip.** |
| 7 | README, architecture doc, record the video |

**3-day compressed version:** Day 1 = generator + T0/T1/T2. Day 2 = LLM + verifier + metrics. Day 3 = docs + video. Cut T3 and cut edge cases 8–10. Never cut the metrics harness or the exception list.

## 14. Five-minute video outline

| Time | Content |
|---|---|
| 0:00–0:30 | The problem in one sentence + the three disagreeing lists on screen |
| 0:30–1:15 | Why they disagree — batching, fees, timing. Show one real mismatch. |
| 1:15–2:15 | Architecture. Land the line: *"the LLM never writes a match. It proposes; the verifier decides."* |
| 2:15–3:15 | Live run on 50 records |
| 3:15–4:15 | **The exception list.** Walk through what failed and why. Show the loan disbursal it correctly refused. |
| 4:15–5:00 | Metrics table. Lead with zero false matches. State what you'd build next. |

Section 3:15–4:15 is the one that gets you shortlisted. Most people will use those 60 seconds on more features. Use them on failures.

## 15. Known weaknesses (state these openly)

Volunteering these is a strength signal, not a liability.

- Synthetic data is generated from assumed rules — it validates the engine, not real-world messiness.
- Subset-sum is bounded; a pathological batch could exceed the cap and land in residue.
- Single fee schedule; real merchants have per-method rates.
- No TDS (194-O), no international/multi-currency, no instant-settlement pricing.
- LLM costs scale with residue size, not batch size — untested at 10,000 records.

## 16. Explicitly out of scope

A dashboard. Auth. Multi-tenancy. A database. Real Razorpay API keys. Anything that makes it look like a product instead of an engine.

The brief asks for throughput, accuracy, and an honest exception list. Building a login page instead of a metrics harness is how you lose this.
