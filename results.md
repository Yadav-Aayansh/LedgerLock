# results.md

Seed 42. Reproduce with `make demo`: every figure below is byte-identical on a rerun except wall-clock runtime, which is a measurement of this machine rather than a result.

**Tiers active: T0, T3, T1, T2, T2b.** All five stages of §6 are built. Where a figure below is zero, the section beneath it says whether that is because nothing ran or because nothing was wrong.

## Headline

> 31/33 settlement lines auto-matched (93.9%), **0 false matches**, 2 honestly unresolved.

## Dataset

| | |
|---|---|
| Bank lines | 38 |
| of which genuine settlement lines | 33 |
| of which not settlements at all | 5 |
| Settlement report lines | 142 in 33 settlements |
| Orders | 146 |

## Metrics (§11)

| Metric | Value | Denominator |
|---|---|---|
| Auto-match rate (T0–T3) | 93.9% (31) | genuine settlement lines (33) |
| LLM-assisted resolution rate | 0.0% (0) | genuine settlement lines (33) |
| **False-match rate** | **0.0%** (0) | links the engine asserted (31) |
| False refusals | 0 | — |
| Unresolved count | 2 | genuine settlement lines |
| Correct refusals | 5 of 5 foreign lines | — |
| Verifier rejection rate | — | LLM proposals (0) |
| Cost per 50 records | 71,650 tokens per 50 lines | gemini-2.5-pro, 2,866 tokens over 2 line(s); no published rate asserted for this provider |
| Wall-clock runtime | 0.005s | full pipeline |

## Outcome buckets

Three buckets cannot describe this problem. Refusing something that is not
a settlement is a win. Refusing a real one is a different mistake from
simply failing to find it.

| Outcome | Count | Meaning |
|---|---|---|
| Correct match | 31 | linked, and the link agrees with ground truth |
| **False match** | 0 | linked, and the link is wrong -- the number that matters |
| Correct refusal | 5 | actively declared not-a-settlement, and it wasn't one |
| False refusal | 0 | declined something that was real |
| Honest miss | 2 | left unresolved, and there was a link to find |
| Foreign, unresolved | 0 | not a settlement, but only passively skipped |

## By tier

| Tier | Correct matches |
|---|---|
| T0 | 15 |
| T3 | 10 |
| T1 | 0 |
| T2 | 6 |
| T2b | 0 |

_T1: contributed nothing here. Stated rather than hidden: every singleton settlement either carried a clean UTR (claimed by T0) or was one of the ambiguous pair it correctly declined._

_T2b: contributed nothing here, and that is the expected result. Every bank line in this dataset corresponds to exactly one settlement_id, so T2 resolves them by grouping alone and no leftover needs combining. T2b exists for reports where the grouping is missing or a settlement was split across credits; it is exercised directly in `tests/test_tiers.py` rather than left as unproven code._

## What is left (the residue)

The 2 unresolved line(s), by what it would take to resolve them:

| Why it is open | Count | What would close it |
|---|---|---|
| Genuinely ambiguous: more than one settlement fits equally well | 2 | a reference the data does not contain; guessing is a coin flip |

## Assumptions this run rests on

- **Date window.** A payout is assumed to credit within 3 calendar days of its settlement date. Every amount-based tier and the refusal rule depend on it; a genuine settlement landing outside it would be missed, and could be refused.
- **Tolerance.** ±5 paisa, sized to absorb reconstruction rounding and nothing else. It is three orders of magnitude below the smallest fee in the data, so it cannot silently swallow one. Asserted in `tests/test_tiers.py`.
- **Refusals chain off earlier tiers.** A line is refused when the unclaimed settlements in its window cannot reach its amount. That proof holds only if no earlier tier claimed a settlement wrongly. So a false match could turn into a false refusal further down. With 0 false matches this run, no such chain exists.

## What the matches rest on

A link backed by a recovered reference is not the same as a link backed by
two numbers agreeing. Amount-and-date matching is only safe while no two
amounts collide, and collisions get likelier as a merchant gets busier.
So this split matters more than the headline rate:

| Basis | Links | Share |
|---|---|---|
| Reference (UTR) **and** exact amount | 25 | 80.6% |
| Amount and date only | 6 | 19.4% |

### Tier ordering

§9 lists T3 last, as a fuzzy last resort. That is the wrong place for it
here, and the difference can be measured instead of argued. T3 needs the
payout to match exactly *as well as* the salvaged reference, which makes
it stronger evidence than T1 and T2's amount-and-date agreement, not
weaker. Put it last and T2 claims those lines first on arithmetic alone,
and the reference never gets looked at. Both orders were run here:

| Order | Matches | Reference-backed | Amount-only |
|---|---|---|---|
| `T0 → T3 → T1 → T2 → T2b` (reported) | 31 | 25 | 6 |
| `T0 → T1 → T2 → T2b → T3` (§9 literal) | 31 | 15 | 16 |

Identical match count and, in both orders, zero false matches. What
changes is that 10 links move from resting on arithmetic coincidence to resting on a
recovered identifier that the arithmetic then confirms. On a larger or
busier ledger that difference is where false matches would first appear.

## The analyst and the verifier

**Analyst transport: `replay`.** `gemini-2.5-pro` via openai at http://127.0.0.1:8090/v1. 0 proposal(s), 0 verified into links, 0 rejected by the verifier, 2 declined by the model as unresolvable.

The analyst proposed nothing and declined all 2 residue line(s).
That is the correct answer here, not a shortfall: those lines are the
ambiguity trap, where two settlements of identical value fall on the same
date with no reference to separate them. §10 makes "I cannot resolve
this" a first-class output and the prompt asks for it explicitly, so a
model that declines is scoring well. Had it guessed, the verifier would
have rejected the guess at `determinacy`. The run is safe either way,
but only one of those outcomes is the model being right.

Responses go to `runs/analyst_cache.jsonl`, keyed by a hash of the exact
prompt, and replay on later runs. A report whose figures move between runs
is not reproducible, so the model is called once and its answer is pinned.
`--analyst live` records, the default replays.

### Verifier, measured against deliberate mistakes

A verifier that accepts everything looks exactly like a model that is
always right. With no model calls this run, the verifier is measured
against a fixed set of fake proposals shaped like the mistakes this task
actually produces. A case rejected for the *wrong* reason counts as a
failure, not a pass.

| Fabricated mistake | Should be | Verifier said | |
|---|---|---|---|
| a genuinely right answer; the suite is worthless if nothing passes | accept | accept | ✓ |
| summed the sale amounts and forgot the fee and GST were deducted | reject at sum_balances | reject at sum_balances | ✓ |
| arithmetic right to the rupee, wrong to the paisa | reject at sum_balances | reject at sum_balances | ✓ |
| GST rounded down rather than half-up on the fee (EC10) | reject at line_arithmetic | reject at line_arithmetic | ✓ |
| perfect arithmetic on a line where two candidates fit equally well, the failure no amount of recomputation can catch | reject at determinacy | reject at determinacy | ✓ |
| a plausible-looking id that is not in the report | reject at existence | reject at existence | ✓ |
| claimed a settlement another bank line already owns | reject at availability | reject at availability | ✓ |
| added money that never left Razorpay to force the sum to balance | reject at settlement_status | reject at settlement_status | ✓ |
| reached outside the date window for a number that happened to fit | reject at date_window | reject at date_window | ✓ |
| counted one settlement line twice so the total would reach the credit | reject at distinct_lines | reject at distinct_lines | ✓ |
| high confidence, no actual link; must not be read as a match | reject at proposal_present | reject at proposal_present | ✓ |

**11/11 adjudicated correctly.**

One row matters more than the rest: the balanced guess on an indeterminate
line. Its arithmetic is flawless. The proposed settlements net to the
credit exactly. An arithmetic-only verifier would accept it and write a
coin flip down as a verified fact.

So recomputing the maths is necessary but not enough. The verifier also
refuses any proposal for a line the engine already proved indeterminate.
A claim that no evidence can disprove is not a finding.

## Planted edge cases (§8)

| # | Case | Verdict | Detail |
|---|---|---|---|
| EC01 | A 40-payment batch collapsing into one bank credit | handled | 1/1 line(s) resolved correctly |
| EC02 | Fee + 18% GST netting, so no bank amount equals any sale amount | partial | 29/31 resolved, 2 left unresolved, and the open line(s) are the EC07 ambiguity trap, correctly declined there, not a failure of this case |
| EC03 | A refund buried inside a later settlement batch | handled | 4/4 line(s) resolved correctly |
| EC04 | A chargeback debit landing 3 days after the payment settled | handled | 1/1 line(s) resolved correctly |
| EC05 | A chargeback reversal 10 days after the chargeback | handled | 1/1 line(s) resolved correctly |
| EC06 | A truncated narration with the UTR cut in half | handled | 1/1 line(s) resolved correctly |
| EC07 | Two identical-amount payments on the same day (ambiguity trap) | correctly declined | 2 line(s) correctly declined: nothing in the data separates the candidates, so any pairing would be a coin flip |
| EC08 | A T+2 settlement crossing a long weekend | handled | 2/2 line(s) resolved correctly |
| EC09 | An on-hold settlement excluded from its batch's bank credit | handled | 1/1 line(s) resolved correctly |
| EC10 | Paisa rounding drift on GST | handled | verifier rejects it: rejected at `line_arithmetic`: pay_C1: booked fee 29.98/GST 5.39, schedule gives 29.98/5.40 |
| EC11 | A bank credit that is not a settlement at all (loan disbursal) | handled | 1/1 line(s) resolved correctly |

## Audit trail

`runs/run_log.jsonl`, 90 events. Every tier records the rule that fired, its inputs and its confidence, and every near-miss is recorded too: a rule that *almost* fired is the most useful thing in the file when a number looks wrong. Each bank line gets a closing `decision` record, and `run.py` replays the log after every run to check it reproduces all 38 verdicts on its own. A trail that cannot be replayed is not an audit trail, so the run fails if it drifts.

## Settlement-side

- On-hold line correctly excluded from every match: **yes**
- Settlements never claimed by any bank line: 2

