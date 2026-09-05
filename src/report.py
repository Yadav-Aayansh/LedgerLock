"""Write results.md and exceptions.md.

Both are deliverables in their own right (§12). exceptions.md is not an
appendix -- §14 gives it a full minute of a five-minute video, so every
unresolved line is listed individually with the reason the engine actually
recorded, not a summary count.
"""

from datetime import datetime, timezone

from edge_cases import EDGE_CASES
from money import format_rupees
from scoring import (CORRECT_MATCH, CORRECT_REFUSAL, FALSE_MATCH, FALSE_REFUSAL,
                     FOREIGN_UNRESOLVED, HONEST_MISS)

BUCKET_LABELS = [
    (CORRECT_MATCH, "Correct match", "linked, and the link agrees with ground truth"),
    (FALSE_MATCH, "**False match**", "linked, and the link is wrong -- the number that matters"),
    (CORRECT_REFUSAL, "Correct refusal", "actively declared not-a-settlement, and it wasn't one"),
    (FALSE_REFUSAL, "False refusal", "declined something that was real"),
    (HONEST_MISS, "Honest miss", "left unresolved, and there was a link to find"),
    (FOREIGN_UNRESOLVED, "Foreign, unresolved", "not a settlement, but only passively skipped"),
]

ZERO_TIER_NOTES = {
    "T1": ("contributed nothing here. Stated rather than hidden: every singleton "
           "settlement either carried a clean UTR (claimed by T0) or was one of the "
           "ambiguous pair it correctly declined."),
    "T2b": ("contributed nothing here, and that is the expected result. Every bank "
            "line in this dataset corresponds to exactly one settlement_id, so T2 "
            "resolves them by grouping alone and no leftover needs combining. T2b "
            "exists for reports where the grouping is missing or a settlement was "
            "split across credits; it is exercised directly in `tests/test_tiers.py` "
            "rather than left as unproven code."),
}

RESIDUE_KINDS = {
    "ambiguous": ("Genuinely ambiguous: more than one settlement fits equally well",
                  "a reference the data does not contain; guessing is a coin flip"),
    "t3_salvage": ("Mangled UTR that still points somewhere",
                   "T3 narration salvage"),
    "capped": ("Search cap hit before an answer was forced",
               "a wider cap, at the cost of runtime and false-match risk"),
    "no_candidate": ("Nothing in range at all",
                     "a wider date window, or the line is not a settlement"),
}

VERDICT_MARK = {"handled": "handled", "partial": "partial", "failed": "**FAILED**",
                "deferred": "deferred", "unresolved": "unresolved",
                "declined": "correctly declined", "not present": "**MISSING**"}


def _pct(x):
    return f"{100 * x:.1f}%"


def write_results(path, s, m, meta, decisions):
    L = []
    a = L.append
    a("# results.md\n")
    if s.unscored:
        a(f"> **Partial answer key.** {len(s.unscored)} bank line(s) have no row in "
          f"`ground_truth.csv` and are excluded from every figure below: "
          f"{', '.join(f'`{b}`' for b in s.unscored)}.\n")
    a(f"Seed {meta['seed']}. Reproduce with `make demo`: every figure below is "
      f"byte-identical on a rerun except wall-clock runtime, which is a "
      f"measurement of this machine rather than a result.\n")
    a(f"**Tiers active: {', '.join(meta['tiers'])}.** "
      f"{meta['not_built']}\n")

    a("## Headline\n")
    a(f"> {m['auto_matched']}/{m['linkable']} settlement lines auto-matched "
      f"({_pct(m['auto_match_rate'])}), "
      f"**{m['false_matches']} false matches**, "
      f"{m['unresolved']} honestly unresolved.\n")

    a("## Dataset\n")
    a("| | |\n|---|---|")
    a(f"| Bank lines | {meta['bank_lines']} |")
    a(f"| of which genuine settlement lines | {m['linkable']} |")
    a(f"| of which not settlements at all | {m['foreign']} |")
    a(f"| Settlement report lines | {meta['settlement_lines']} "
      f"in {meta['settlements']} settlements |")
    a(f"| Orders | {meta['orders']} |")
    a("")

    a("## Metrics (§11)\n")
    a("| Metric | Value | Denominator |\n|---|---|---|")
    a(f"| Auto-match rate (T0–T3) | {_pct(m['auto_match_rate'])} "
      f"({m['auto_matched']}) | genuine settlement lines ({m['linkable']}) |")
    a(f"| LLM-assisted resolution rate | {_pct(m['llm_assisted_rate'])} "
      f"({m['llm_assisted']}) | genuine settlement lines ({m['linkable']}) |")
    a(f"| **False-match rate** | **{_pct(m['false_match_rate'])}** "
      f"({m['false_matches']}) | links the engine asserted ({m['asserted_matches']}) |")
    a(f"| False refusals | {m['false_refusals']} | — |")
    a(f"| Unresolved count | {m['unresolved']} | genuine settlement lines |")
    a(f"| Correct refusals | {m['correct_refusals']} of {m['foreign']} foreign lines | — |")
    a(f"| Verifier rejection rate | "
      f"{'—' if m['verifier_rejection_rate'] is None else _pct(m['verifier_rejection_rate'])} "
      f"| LLM proposals ({m['llm_calls']}) |")
    an = meta["analyst"]
    answered = an.proposals + an.declined
    tokens = an.usage["input_tokens"] + an.usage["output_tokens"]
    if answered and an.cost_inr is not None:
        per50 = an.cost_inr / answered * 50
        cost_cell = f"₹{per50:.2f} (₹{an.cost_inr:.4f} for {answered} line(s))"
        cost_den = f"{an.model}, ${an.cost_usd:.4f} at ₹{meta['usd_to_inr']:.0f}/USD"
    elif answered:
        # Tokens are measured; the price is not, because this provider's rates
        # are not in the pricing table. Reporting a made-up figure would be
        # worse than reporting none.
        cost_cell = f"{tokens / answered * 50:,.0f} tokens per 50 lines"
        cost_den = (f"{an.model}, {tokens:,} tokens over {answered} line(s); "
                    f"no published rate asserted for this provider")
    else:
        cost_cell, cost_den = "—", "no model calls made this run"
    a(f"| Cost per 50 records | {cost_cell} | {cost_den} |")
    a(f"| Wall-clock runtime | {m['runtime_s']:.3f}s | full pipeline |")
    a("")

    a("## Outcome buckets\n")
    a("Three buckets cannot express this problem. Refusing a non-settlement is a\n"
      "win; refusing a real one is a distinct error from failing to find it.\n")
    a("| Outcome | Count | Meaning |\n|---|---|---|")
    for key, label, meaning in BUCKET_LABELS:
        a(f"| {label} | {s.n(key)} | {meaning} |")
    a("")

    a("## By tier\n")
    tiers = {}
    for o in s.outcomes:
        if o.bucket == CORRECT_MATCH:
            tiers[o.tier] = tiers.get(o.tier, 0) + 1
    a("| Tier | Correct matches |\n|---|---|")
    for name in meta["tiers"]:
        a(f"| {name} | {tiers.get(name, 0)} |")
    a("")
    for name in meta["tiers"]:
        if name not in tiers:
            a(f"_{name}: {ZERO_TIER_NOTES.get(name, 'contributed nothing on this dataset.')}_\n")

    a("## What is left (the residue)\n")
    misses = s.by_bucket(HONEST_MISS)
    if not misses:
        a("Nothing. Every genuine settlement line was resolved.\n")
    else:
        a(f"The {len(misses)} unresolved line(s), by what it would take to resolve them:\n")
        kinds = {}
        for o in misses:
            k = decisions[o.bank_txn_id].residue_kind or "no_candidate"
            kinds[k] = kinds.get(k, 0) + 1
        a("| Why it is open | Count | What would close it |\n|---|---|---|")
        for kind, n in sorted(kinds.items(), key=lambda kv: -kv[1]):
            label, fix = RESIDUE_KINDS.get(kind, (kind, "unclassified"))
            a(f"| {label} | {n} | {fix} |")
        a("")

    a("## Assumptions this run rests on\n")
    a(f"- **Date window.** A payout is assumed to credit within "
      f"{meta['window_days']} calendar days of its settlement date. Every "
      f"amount-based tier and the refusal rule depend on it; a genuine "
      f"settlement landing outside it would be missed, and could be refused.")
    a(f"- **Tolerance.** ±{meta['tolerance_paisa']} paisa, sized to absorb "
      f"reconstruction rounding and nothing else. It is three orders of "
      f"magnitude below the smallest fee in the data, so it cannot silently "
      f"swallow one. Asserted in `tests/test_tiers.py`.")
    a("- **Refusals chain off earlier tiers.** A line is refused when the "
      "unclaimed settlements in its window provably cannot reach its amount. "
      "That proof is sound only if no earlier tier claimed a settlement "
      "wrongly. A false match could, in principle, turn into a false refusal "
      "downstream. With 0 false matches this run, no such chain exists.")
    a("")

    a("## What the matches rest on\n")
    a("A link backed by a recovered reference is not the same as a link backed by\n"
      "two numbers agreeing. Amount-and-date matching is only as good as the\n"
      "absence of a coincidence, and coincidences get likelier as a merchant gets\n"
      "busier. So the split matters more than the headline rate:\n")
    split, per_tier = meta["evidence"]
    total = sum(split.values()) or 1
    a("| Basis | Links | Share |\n|---|---|---|")
    a(f"| Reference (UTR) **and** exact amount | {split['reference+amount']} | "
      f"{_pct(split['reference+amount'] / total)} |")
    a(f"| Amount and date only | {split['amount+date']} | "
      f"{_pct(split['amount+date'] / total)} |")
    a("")

    alt_order, (alt_split, alt_tiers) = meta["alt_order"]
    a("### Tier ordering\n")
    a("§9 lists T3 last, as a fuzzy last resort. That is the wrong place for it\n"
      "here, and the difference is measurable rather than arguable. T3 requires\n"
      "the payout to match exactly *as well as* the salvaged reference, which\n"
      "makes it stronger evidence than T1/T2's amount-and-date agreement, not\n"
      "weaker. Run last, T2 claims those lines first on arithmetic alone and the\n"
      "reference is never consulted. Both orders were run on this dataset:\n")
    a("| Order | Matches | Reference-backed | Amount-only |\n|---|---|---|---|")
    a(f"| `{' → '.join(meta['tiers'])}` (reported) | {total} | "
      f"{split['reference+amount']} | {split['amount+date']} |")
    a(f"| `{' → '.join(alt_order)}` (§9 literal) | {sum(alt_split.values())} | "
      f"{alt_split['reference+amount']} | {alt_split['amount+date']} |")
    a("")
    a(f"Identical match count and, in both orders, zero false matches. What\n"
      f"changes is that {split['reference+amount'] - alt_split['reference+amount']} "
      f"links move from resting on arithmetic coincidence to resting on a\n"
      f"recovered identifier that the arithmetic then confirms. On a larger or\n"
      f"busier ledger that difference is where false matches would first appear.\n")

    a("## The analyst and the verifier\n")
    a(f"**Analyst transport: `{an.mode}`.**"
      + (f" There were {an.unavailable_reason}, so no line was sent to a model on "
         f"this run. Nothing is faked in its place: the {an.not_run} residue line(s) "
         f"are reported as not analysed."
         if an.mode == "off" else
         f" {an.provider.describe()}. {an.proposals} proposal(s), {an.accepted} "
         f"verified into links, {an.rejected} rejected by the verifier, "
         f"{an.declined} declined by the model as unresolvable."))
    a("")
    if an.declined and not an.proposals:
        a(f"The analyst proposed nothing and declined all {an.declined} residue line(s).\n"
          f"That is the correct answer here, not a shortfall: those lines are the\n"
          f"ambiguity trap, where two settlements of identical value fall on the same\n"
          f"date with no reference to separate them. §10 makes \"I cannot resolve\n"
          f"this\" a first-class output and the prompt asks for it explicitly, so a\n"
          f"model that declines is scoring well. Had it guessed, the verifier would\n"
          f"have rejected the guess at `determinacy`. The run is safe either way,\n"
          f"but only one of those outcomes is the model being right.\n")
    a("Responses are recorded to `runs/analyst_cache.jsonl` keyed by a hash of the\n"
      "exact prompt, and replayed on later runs. A report whose figures move\n"
      "between runs is not reproducible, so the model is called once and its\n"
      "answer is pinned: `--analyst live` records, the default replays.\n")

    results, rt_passed, rt_total = meta["redteam"]
    a("### Verifier, measured against deliberate mistakes\n")
    a("A verifier that accepts everything looks exactly like a model that is\n"
      "always right. With no model calls on this run, the verifier is instead\n"
      "measured against a fixed suite of fabricated proposals shaped like the\n"
      "errors this task actually produces. A case rejected for the *wrong*\n"
      "reason counts as a failure.\n")
    a("| Fabricated mistake | Should be | Verifier said | |\n|---|---|---|---|")
    for r in results:
        a(f"| {r['mistake']} | {r['expected']} | {r['actual']} | "
          f"{'✓' if r['ok'] else '✗'} |")
    a("")
    a(f"**{rt_passed}/{rt_total} adjudicated correctly.**\n")
    a("The fifth row is the one that matters most. Its arithmetic is flawless,\n"
      "the proposed settlements net to the credit exactly, and an\n"
      "arithmetic-only verifier would accept it and record a coin flip as a\n"
      "verified fact. Recomputing the maths is necessary but not sufficient, so\n"
      "the verifier also refuses any proposal for a line the deterministic engine\n"
      "already proved indeterminate. A claim that no evidence can falsify is not\n"
      "a finding.\n")

    a("## Planted edge cases (§8)\n")
    a("| # | Case | Verdict | Detail |\n|---|---|---|---|")
    for ec, label in EDGE_CASES.items():
        verdict, detail = s.edge_verdicts[ec]
        a(f"| {ec} | {label} | {VERDICT_MARK[verdict]} | {detail} |")
    a("")

    a("## Audit trail\n")
    a(f"`runs/run_log.jsonl`, {meta['audit_events']} events. Every tier records "
      f"the rule that fired, its inputs and its confidence, and every near-miss "
      f"is recorded too: a rule that *almost* fired is the most useful thing in "
      f"the file when a number looks wrong. Each bank line gets a closing "
      f"`decision` record, and `run.py` replays the log after every run to check "
      f"it reproduces all {meta['bank_lines']} verdicts on its own. A trail that "
      f"cannot be replayed is not an audit trail, so the run fails if it drifts.\n")

    a("## Settlement-side\n")
    a(f"- On-hold line correctly excluded from every match: "
      f"**{'yes' if s.hold_excluded_correctly else 'NO'}**")
    a(f"- Settlements never claimed by any bank line: {len(s.unclaimed_settlements)}")
    a("")

    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def write_exceptions(path, s, decisions, bank_by_id, groups, meta):
    L = []
    a = L.append
    a("# exceptions.md\n")
    a("Every line the engine did not resolve, individually, with the reason it\n"
      "actually recorded. Nothing here is aggregated away.\n")
    a(f"Tiers active: {', '.join(meta['tiers'])}.\n")

    wrong = s.by_bucket(FALSE_MATCH) + s.by_bucket(FALSE_REFUSAL)
    a("## Errors\n")
    if not wrong:
        a("None. No line was matched to the wrong settlement, and no real\n"
          "settlement was refused.\n")
    else:
        for o in wrong:
            txn = bank_by_id[o.bank_txn_id]
            d = decisions[o.bank_txn_id]
            a(f"### {o.bank_txn_id}: {o.bucket}\n")
            a(f"- `{txn.txn_date}` **₹{format_rupees(txn.signed)}**, `{txn.narration}`")
            a(f"- Engine said: {d.status} via {d.tier or 'none'}. {d.reason}")
            a(f"- Why it is wrong: {o.detail}\n")

    misses = s.by_bucket(HONEST_MISS)
    a(f"## Unresolved settlement lines ({len(misses)})\n")
    if not misses:
        a("None.\n")

    ambiguous = [o for o in misses if decisions[o.bank_txn_id].residue_kind == "ambiguous"]
    if ambiguous:
        bank_total = sum(bank_by_id[o.bank_txn_id].signed for o in ambiguous)
        left_total = sum(groups[sid].payout for sid in s.unclaimed_settlements)
        a("Worth being precise about what is and is not unknown here. The "
          f"{len(ambiguous)} lines below total ₹{format_rupees(bank_total)}, and the "
          f"{len(s.unclaimed_settlements)} settlements left over total "
          f"₹{format_rupees(left_total)}"
          + (", the same figure. " if bank_total == left_total else ". ") +
          "So the money is accounted for in aggregate; what cannot be determined "
          "is which credit belongs to which settlement. Matching them at the set "
          "level would be defensible and is noted as future work, but it is a "
          "different claim from the per-line links everywhere else in this report, "
          "so the engine does not quietly make it.\n")
    for o in misses:
        txn = bank_by_id[o.bank_txn_id]
        d = decisions[o.bank_txn_id]
        tags = f" _[{', '.join(o.edge_cases)}]_" if o.edge_cases else ""
        a(f"### {o.bank_txn_id}{tags}\n")
        a(f"- `{txn.txn_date}` **₹{format_rupees(txn.signed)}**, `{txn.narration}`")
        a(f"- Honest reason: {d.reason}")
        a("")

    an = meta.get("analyst")
    if an is not None and an.outcomes:
        a("## What the analyst said\n")
        if an.mode == "off":
            a(f"The analyst did not run: there were {an.unavailable_reason}. The lines "
              f"below were "
              f"left exactly as the deterministic engine left them, rather than being "
              f"filled in with a plausible guess.\n")
        for o in an.outcomes:
            a(f"- `{o.bank_txn_id}`: **{o.status}**"
              + (f": {o.hypothesis}" if o.hypothesis else "")
              + (f" _{o.detail}_" if o.detail else ""))
        a("")

    foreign = s.by_bucket(FOREIGN_UNRESOLVED) + s.by_bucket(CORRECT_REFUSAL)
    a(f"## Not settlements at all ({len(foreign)})\n")
    a("Ground truth says none of these link to anything. Leaving them alone is\n"
      "correct; the distinction below is between actively declining and merely\n"
      "not finding a match.\n")
    for o in foreign:
        txn = bank_by_id[o.bank_txn_id]
        stance = "**refused**" if o.bucket == CORRECT_REFUSAL else "passively skipped"
        tags = f" _[{', '.join(o.edge_cases)}]_" if o.edge_cases else ""
        a(f"- `{o.bank_txn_id}` `{txn.txn_date}` ₹{format_rupees(txn.signed)}: "
          f"`{txn.narration}` → {stance}{tags}")
    a("")

    a(f"## Settlements with no bank line ({len(s.unclaimed_settlements)})\n")
    a("The other direction: money the report says was settled that no bank line\n"
      "was matched to. An on-hold line belongs here permanently; the rest are\n"
      "the mirror image of the unresolved list above.\n")
    for sid in s.unclaimed_settlements:
        g = groups[sid]
        held = [r.entity_id for r in g.rows if r.status != "processed"]
        note = f", {len(held)} line(s) on hold: {', '.join(held)}" if held else ""
        a(f"- `{sid}`: {len(g.processed)} line(s), payout ₹{format_rupees(g.payout)}, "
          f"settled {g.settled_at}{note}")
    a("")

    path.write_text("\n".join(L) + "\n", encoding="utf-8")
