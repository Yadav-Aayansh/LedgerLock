# LedgerLock

**Track 04 · AI Finance Controller · Razorpay AI Buildathon**

> Matches a merchant's orders, their Razorpay settlements and their bank
> statement. And tells you straight which lines it could not match, and why.

---

## Run it

```bash
make demo
```

No dependencies, no API key, no network. Python 3.10 and up, standard library
only. It builds the data, checks the planted edge cases are still there, runs
38 tests, matches, scores against a hidden answer key, and writes `results.md`
and `exceptions.md`.

**It reproduces.** Seed 42 gives the exact numbers in `results.md`, every time,
on any machine. The only figure that moves is wall-clock runtime, which
measures your laptop and not the engine. `results.md` says that in its header.

```
make data    rebuild the CSVs and the hidden answer key
make check   check all 11 planted edge cases survived
make test    cover the paths the dataset itself does not reach
make run     match, score, write the reports
make web     open the viewer in a browser (optional, needs `uv sync` once)
make clean   remove generated files
```

## The viewer

`make demo` is the submission. `make web` is a way to watch it happen.

```bash
uv sync      # once, installs FastAPI for the viewer only
make web     # http://127.0.0.1:8000, moves up a port if that one is busy
```

Four steps. The three ledgers and why they cannot agree. Then the engine's
verdict on every bank line, with the rule and the audit trail behind it. Then
the model's proposals and the verifier accepting or rejecting them. Then the
answer key, which only opens when you ask.

It starts empty. Generate a dataset from a seed, or drop in your own three
CSVs. Files are read by their columns, not their filenames. Without a
`ground_truth.csv` everything still runs and only the accuracy step stays
locked, because there is nothing to measure against.

The viewer calls `pipeline.execute`, the same function `run.py` calls. It does
no matching, no arithmetic and no scoring of its own, so it cannot show you a
number that `results.md` does not have. It binds `127.0.0.1`, holds an API key
only for the request it was typed for, and saves nothing.

## The problem

A shop sells 50 things online. The money does not arrive as 50 deposits. It
arrives as a few lumpy deposits, days later, with fees already cut out.

So there are three lists that never agree. What I sold. What the payment
company says it sent me. What actually landed in the bank. Somebody sits and
reconciles them by hand every month. That is where money goes missing without
anyone noticing.

They disagree for real reasons. 40 payments become 1 credit. Every payment has
a platform fee, and 18% GST on top of that fee. Settlements take T+2 and
weekends stretch it. Refunds turn up inside later batches. Chargebacks get
clawed back days afterwards. The bank truncates the reference mid-way. And some
amounts are simply identical.

## The one design decision

> **The LLM does not do the matching. A deterministic engine does. The LLM only
> speaks about what the engine could not match, and a verifier recomputes its
> arithmetic before anything is accepted.**

The usual approach is to throw the batch at a model and ask it to reconcile. It
comes back with about 75% and a pile of confident, invented matches. In a
finance system an invented match is worse than no match. A wrong "matched" is a
silent error nobody catches. An honest "unresolved" gets a human to look.

This is enforced by control flow, not by prompting. Nothing in `src/analyst/`
can write a link. The only route from a proposal to the ledger goes through
`verifier.verify`.

## Results (seed 42)

| Metric | Value |
|---|---|
| Auto-match rate (T0 to T3) | **93.9%**, 31 of 33 real settlement lines |
| **False matches** | **0** |
| False refusals | 0 |
| Unresolved | 2, both correctly declined as ambiguous |
| Correct refusals | 5 of 5 non-settlement lines, each with an arithmetic proof |
| Verifier against the adversarial suite | 11 of 11 fake proposals judged correctly |
| Planted edge cases | 9 handled, 1 correctly declined, 1 partial, **0 failed** |

Zero false matches is the headline. Not the 93.9%.

The one `partial` is EC02, fee and GST netting. It is partial only because two
of the 31 lines it covers are the EC07 ambiguity trap, correctly declined
there. `results.md` says so instead of letting it read as a shortfall.

**What the matches rest on matters more than the rate.** 25 of the 31 links are
backed by a recovered reference *and* an exact amount. Only 6 rest on amount
and date agreeing. Amount-and-date matching is only safe while no two amounts
collide, and collisions get likelier as a merchant gets busier.

### The two it would not resolve

Two bank credits of ₹1,463.62 landed on the same date, same narration, against
two settlements of the same value. Nothing in the data separates them. The
engine declined both. The analyst, asked to explain them, also declined:

> Two distinct settlements, stl_00020 and stl_00021, both have a net payout of
> ₹1463.62 and were settled on the same day as the bank credit. The bank
> narration lacks a Unique Transaction Reference (UTR) to differentiate between
> them, making it impossible to confidently assign the credit to one specific
> settlement.

The totals do agree in aggregate. The money is accounted for. Only the pairing
is unknown. Matching them at the set level would be defensible, but it is a
different claim from the per-line links everywhere else in the report, so the
engine does not slip it in.

### The one it refused

A ₹2,50,000 credit reading `NEFT-RZPX00088421-RAZORPAY CAPITAL LOAN DISB`. It
is Razorpay branded, it carries a UTR-shaped reference, and the amount is
plausible. Everything a naive matcher looks at says settlement.

It is a loan disbursal. The engine refuses it, and it refuses with a proof, not
a guess: every unclaimed settlement within 3 days of that date adds up to
₹2,927.24, which cannot reach ₹2,50,000.

A system that correctly says no is a stronger result than one that matches
everything.

## Using your own model

The analyst speaks two wire protocols, which between them cover almost every
provider. **Anthropic**, the Messages API through the official SDK, which also
takes a custom base URL. And **OpenAI-shaped** `/chat/completions`, which
covers OpenAI, OpenRouter, Google's OpenAI-compatible endpoint, vLLM, Ollama,
LM Studio and any local proxy. The OpenAI path is stdlib HTTP, so there is
nothing to install.

```bash
# Anthropic (needs `pip install anthropic` and $ANTHROPIC_API_KEY)
python3 run.py --analyst live --analyst-provider anthropic --analyst-model claude-opus-5

# OpenAI, OpenRouter, Gemini. Presets carry the base URL and key variable.
python3 run.py --analyst live --analyst-provider openrouter --analyst-model <model>

# Any endpoint at all
python3 run.py --analyst live \
  --analyst-protocol openai \
  --analyst-url http://127.0.0.1:8090/v1 \
  --analyst-model gemini-2.5-pro \
  --analyst-key-env MY_KEY_VAR
```

Presets: `anthropic`, `openai`, `openrouter`, `gemini`, `local`. Every part can
be overridden: base URL, model, protocol, key variable, price.

**API keys come from the environment, never from a flag.** So they stay out of
shell history, the audit trail, the response cache and the reports.

**Cost.** Rates for known models are built in. For anything else the run counts
tokens and refuses to state a price. Give it your own with
`--analyst-price 5.0,25.0`, USD per 1M input and output. A made-up rate in
`results.md` would be worse than no rate at all.

**Record and replay.** A live run pins every response to
`runs/analyst_cache.jsonl`, keyed by a hash of the exact prompt plus the
protocol and model, and writes the configuration next to it. Later runs replay
it with no network and no flags. That is what keeps `results.md` reproducible
while still reporting a real model's real answers.

Note that changing the prompt or the packet changes the hash, so the cache
stops matching and has to be re-recorded with a live run.

## Layout

```
ledgerlock/
├── README.md
├── ARCHITECTURE.md         the diagram, the tiers, the verifier's checks
├── results.md              the metrics table          ← generated
├── exceptions.md           every unresolved record    ← generated
├── Makefile
├── run.py                  the command-line front door
├── pyproject.toml          viewer dependencies only, the engine needs none
├── data/
│   ├── generate.py         seeded generator plus the hidden answer key
│   └── assert_planted.py   checks all 11 edge cases are really there
├── src/
│   ├── money.py            integer paisa, the one rounding rule
│   ├── fees.py             the fee schedule, shared by generator and verifier
│   ├── edge_cases.py
│   ├── matcher/            the deterministic tiers
│   ├── analyst/            the LLM layer: packet, schema, transports
│   ├── verifier/           the arithmetic checker and the adversarial suite
│   ├── scoring.py          the six outcome buckets
│   ├── report.py
│   ├── pipeline.py         the one orchestration, run.py and web/ both call it
│   └── web/                the optional viewer
│       ├── server.py       seven routes, no logic
│       ├── session.py      datasets under examination
│       ├── views.py        engine objects to JSON
│       └── static/         one page, plain JS, no build step
├── tests/                  38 tests
└── runs/run_log.jsonl      full audit trail          ← generated
```

## Things worth knowing

**The generator was written without looking at how the matcher would solve it.**
If you shape the mess around your own tiers, a high match rate measures nothing
except your own assumptions.

**The answer key is written as the data is built,** never worked out afterwards
by inference. Inferring it would make the false-match rate circular and
worthless. This is the whole reason to use synthetic data. It is what makes a
false-match rate computable at all.

**`data/assert_planted.py` checks the edge cases are still present.** It
re-reads the CSVs from disk instead of trusting what the generator claims. It
has already caught one case that broke silently: a refund got netted into one
of the ambiguity-trap batches, the amounts stopped colliding, and the trap
disappeared. The match rate went up. It looked like progress. It was a deleted
test.

**Parts that contribute nothing say so.** T1 and T2b resolve zero lines on this
dataset, and `results.md` states that plainly with the reason instead of hiding
it in an aggregate. Both are exercised directly in the test suite, so they are
not unproven code.

**The verifier is measured, not claimed.** A verifier that accepts everything
looks exactly like a model that is always right. So it runs on every execution
against 11 fabricated proposals shaped like the mistakes this task actually
produces. A case rejected for the wrong reason counts as a failure, not a pass.

## Known weaknesses

Written down because you should know them before you ask.

- The data is synthetic, built from assumed rules. It validates the engine, not
  real-world mess.
- Subset-sum is bounded: 4 settlements, 24 candidates, 200k search nodes. A
  pathological batch goes over the cap and lands in residue. That is the
  intended failure mode, not a bug.
- One flat fee schedule. Real merchants have per-method rates.
- No TDS (194-O), no multi-currency, no instant-settlement pricing.
- The 3-day date window carries a lot of weight. A real settlement landing
  outside it would be missed, and could even be refused.
- Refusals chain off earlier tiers, so a false match could turn into a false
  refusal further down. With 0 false matches this run, no such chain exists.
- Analyst cost scales with how much residue there is, not batch size. Untested
  at 10,000 records.

## Out of scope

No auth. No multi-tenancy. No database. No real Razorpay API keys. Nothing that
makes this look like a product when it is an engine.

The viewer is the one exception, and it is not a dashboard. No login, no
persistence, no server state beyond the run you just triggered, and nothing it
can do that the CLI cannot. It exists so the engine can be shown on a screen.

**The engine is the submission.** The brief asks for throughput, accuracy, and
an honest exception list. That is what this is.
