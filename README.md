# LedgerLock

**Track 04 · AI Finance Controller · Razorpay AI Buildathon**

> A reconciliation agent that matches a merchant's orders, their Razorpay
> settlements, and their bank statement — and tells you honestly which ones it
> couldn't match, and why.

---

## Run it

```bash
make demo
```

No dependencies, no API key, no network. Python 3.10+ and the standard library.
It generates the data, proves the planted edge cases survived, runs 37 tests,
matches, scores against hidden ground truth, and writes `results.md` and
`exceptions.md`.

**Reproducibility:** seed 42 gives exactly the numbers in `results.md`, every
time, on any machine. The one figure that moves is wall-clock runtime, which is
a measurement of your machine rather than a result — and `results.md` says so
in its own header.

```
make data    regenerate the CSVs + hidden ground truth
make check   prove all 11 planted edge cases survived generation
make test    exercise the paths the dataset itself does not reach
make run     match, score, write the reports
make web     open the viewer in a browser (optional; needs `uv sync` once)
make clean   remove generated artefacts
```

## The viewer

`make demo` is the submission. `make web` is a way to *watch* it.

```bash
uv sync      # once — installs FastAPI for the viewer only
make web     # http://127.0.0.1:8000 (moves up a port if that one is taken)
```

It walks the same pipeline in four steps: the three ledgers and why they cannot
agree, then the engine's verdict on every bank line with the rule and the audit
trail behind each one, then the model's proposals and the verifier accepting or
rejecting them, and finally the answer key, revealed only when you ask for it.

It opens empty. Generate a dataset from a seed, or drop in your own three CSVs;
files are recognised by their columns, not their filenames. Without a
`ground_truth.csv` everything still runs and only the accuracy step stays
locked, because accuracy would be unmeasurable.

The viewer calls `pipeline.execute`, the same function `run.py` calls. It has no
matching logic, no arithmetic and no scoring of its own, so it cannot show a
number that `results.md` does not. It binds `127.0.0.1`, holds an API key only
for the duration of the request it was typed for, and stores nothing.

## The problem

A shop sells 50 things online. The money does **not** arrive as 50 deposits. It
arrives as a handful of lumpy deposits, days later, with fees quietly cut out.

So there are three lists that never agree: what I sold, what the payment
company says it sent me, and what actually landed in my bank. Somebody
reconciles them by hand, every month. It is where money silently goes missing.

They disagree because of batching (40 payments become 1 credit), fee netting
(a platform fee, plus 18% GST *on that fee*), T+2 timing that weekends stretch,
refunds buried in later batches, chargebacks clawed back days afterwards,
narrations the bank truncated mid-reference, and amounts that are genuinely
ambiguous.

## The central design decision

> **The LLM does not do the matching. A deterministic engine does the matching.
> The LLM only explains what the engine could not match — and a verifier checks
> its arithmetic before anything is accepted.**

Most approaches dump the batch into a model and ask it to reconcile. It returns
~75% and a pile of confidently invented matches. In a finance system an
invented match is worse than no match: a wrong "matched" is a silent error
nobody catches, whereas an honest "unresolved" gets a human's attention.

This is enforced by control flow, not by prompting. `src/analyst/` has no
reachable function that writes a link. The only path from a proposal to the
ledger runs through `verifier.verify`.

## Results (seed 42)

| Metric | Value |
|---|---|
| Auto-match rate (T0–T3) | **93.9%** — 31 of 33 genuine settlement lines |
| **False matches** | **0** |
| False refusals | 0 |
| Unresolved | 2 — both correctly declined as ambiguous |
| Correct refusals | 5 of 5 non-settlement lines, each with an arithmetic proof |
| Verifier vs. adversarial suite | 10/10 fabricated proposals adjudicated correctly |
| Planted edge cases | 9 handled, 1 correctly declined, 1 partial, **0 failed** |

Zero false matches is the headline, not the 93.9%.

The one `partial` is EC02 (fee and GST netting), and it is partial only because
two of the 31 settlement lines it spans are the EC07 ambiguity trap, correctly
declined there. `results.md` says so rather than letting the verdict read as a
shortfall.

**What the matches rest on** matters more than the rate: 25 of the 31 links are
backed by a recovered reference *and* an exact amount; only 6 rest on amount
and date agreeing. Amount-and-date matching is only as good as the absence of a
coincidence, and coincidences get likelier as a merchant gets busier.

### The two it would not resolve

Two bank credits of ₹1,463.62 landed on the same date, with the same narration,
against two settlements of identical value. Nothing in the data separates them.
The engine declined both, and the analyst — asked to explain them — also
declined:

> Two distinct settlements, stl_00020 and stl_00021, both have a net payout of
> ₹1463.62 and were settled on the same day as the bank credit. The bank
> narration lacks a Unique Transaction Reference (UTR) to differentiate between
> them, making it impossible to confidently assign the credit to one specific
> settlement.

Their totals do agree in aggregate — the money is accounted for; only the
pairing is unknown. Matching at the set level would be defensible, but it is a
different claim from the per-line links everywhere else in the report, so the
engine does not quietly make it.

### The one it refused

A ₹2,50,000 credit reading `NEFT-RZPX00088421-RAZORPAY CAPITAL LOAN DISB`. It
is Razorpay-branded, carries a UTR-shaped reference, and sits in a plausible
range — everything a naive matcher keys on says "settlement". It is a loan
disbursal. The engine refuses it, with a proof rather than a guess: every
unclaimed settlement within 3 days of that date sums to ₹2,927.24, which cannot
reach ₹2,50,000.

A system that correctly declines is a stronger result than one that matches
everything.

## Using your own model

The analyst speaks two wire protocols, which between them cover essentially
every provider: **Anthropic** (Messages API, via the official SDK, which also
accepts a custom base URL) and **OpenAI-shaped** `/chat/completions` (OpenAI,
OpenRouter, Google's OpenAI-compatible endpoint, vLLM, Ollama, LM Studio, any
local proxy). The OpenAI path is stdlib HTTP, so nothing needs installing.

```bash
# Anthropic (needs `pip install anthropic` and $ANTHROPIC_API_KEY)
python3 run.py --analyst live --analyst-provider anthropic --analyst-model claude-opus-5

# OpenAI, OpenRouter, Gemini — presets carry the base URL and key variable
python3 run.py --analyst live --analyst-provider openrouter --analyst-model <model>

# Any endpoint at all
python3 run.py --analyst live \
  --analyst-protocol openai \
  --analyst-url http://127.0.0.1:8090/v1 \
  --analyst-model gemini-2.5-pro \
  --analyst-key-env MY_KEY_VAR
```

Presets: `anthropic`, `openai`, `openrouter`, `gemini`, `local`. Any part —
base URL, model, protocol, key variable, price — can be overridden.

**API keys are read from the environment, never taken as a flag**, so they stay
out of shell history, the audit trail, the response cache and the reports.

**Cost.** Rates for known models are built in; for anything else the run counts
tokens and declines to assert a price. Supply your own with
`--analyst-price 5.0,25.0` (USD per 1M input,output). A fabricated rate in
`results.md` would be worse than no rate.

**Record and replay.** A live run pins every response to
`runs/analyst_cache.jsonl`, keyed by a hash of the exact prompt plus the
protocol and model, and records the configuration alongside it. Later runs
replay it with no network and no flags. That is what keeps `results.md`
reproducible while still reporting a real model's real answers.

## Layout

```
ledgerlock/
├── README.md
├── ARCHITECTURE.md         the diagram, the tiers, the verifier's checks
├── results.md              the metrics table          ← generated
├── exceptions.md           every unresolved record    ← generated
├── Makefile
├── run.py                  the command-line front door
├── pyproject.toml          viewer dependencies only; the engine needs none
├── data/
│   ├── generate.py         seeded generator + hidden ground truth
│   └── assert_planted.py   proves all 11 edge cases are really there
├── src/
│   ├── money.py            integer paisa; the one rounding rule
│   ├── fees.py             the fee schedule, shared by generator and verifier
│   ├── edge_cases.py
│   ├── matcher/            the deterministic tiers
│   ├── analyst/            the LLM layer: packet, schema, transports
│   ├── verifier/           the arithmetic checker + adversarial suite
│   ├── scoring.py          the six outcome buckets
│   ├── report.py
│   ├── pipeline.py         the one orchestration; run.py and web/ both call it
│   └── web/                the optional viewer
│       ├── server.py       seven routes, no logic
│       ├── session.py      datasets under examination
│       ├── views.py        engine objects → JSON
│       └── static/         one page, vanilla JS, no build step
├── tests/                  37 tests
└── runs/run_log.jsonl      full audit trail          ← generated
```

## Things worth knowing

**The generator was written without reference to how the matcher would solve
it.** Shaping the mess around the tiers makes a high match rate measure nothing
but the author's own assumptions.

**Ground truth is emitted by construction**, never reconstructed by inference
afterwards — which would make the false-match rate circular and worthless. It
is the entire reason to use synthetic data: it is what makes a false-match rate
computable at all.

**`data/assert_planted.py` proves the edge cases are present**, re-reading the
CSVs from disk rather than trusting the generator's own claims. It has already
caught one silently broken case: a refund netted into one of the ambiguity-trap
batches, which made the amounts stop colliding and quietly deleted the trap.

**Tiers and components that contribute nothing say so.** T1 and T2b resolve
zero lines on this dataset, and `results.md` states that plainly along with the
reason, rather than hiding it in an aggregate. Both are exercised directly in
the test suite so they are not unproven code.

**The verifier is measured, not asserted.** A verifier that accepts everything
looks exactly like a model that is always right. It is run every execution
against ten fabricated proposals shaped like the mistakes this task actually
produces — and a case rejected for the *wrong* reason counts as a failure.

## Known weaknesses

Stated openly, because volunteering them is a strength signal.

- Synthetic data is generated from assumed rules. It validates the engine, not
  real-world messiness.
- Subset-sum is bounded (4 settlements, 24 candidates, 200k search nodes). A
  pathological batch exceeds the cap and lands in residue. That is the intended
  failure mode, not a bug.
- A single flat fee schedule. Real merchants have per-method rates.
- No TDS (194-O), no multi-currency, no instant-settlement pricing.
- The 3-day date window is load-bearing: a genuine settlement landing outside
  it would be missed, and could in principle be refused.
- Refusals chain off earlier tiers. A false match could become a false refusal
  downstream. With 0 false matches this run, no such chain exists.
- Analyst cost scales with residue size, not batch size. Untested at 10,000
  records.

## Out of scope

Auth. Multi-tenancy. A database. Real Razorpay API keys. Anything that makes it
look like a product instead of an engine.

The viewer (`make web`) is the one concession, and it is deliberately not a
dashboard: no login, no persistence, no server-side state beyond the run you
just triggered, and nothing it can do that the CLI cannot. It exists so the
engine can be demonstrated on screen. **The engine is the submission.**

The brief asks for throughput, accuracy, and an honest exception list.
