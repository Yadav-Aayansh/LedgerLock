"""The deterministic tiers (project-statement.md §9).

Each tier is a *global pass*, not a per-row cascade: a tier runs across every
bank line and claims what it is certain about before the next tier is allowed
to look at the leftovers. A per-row cascade would let a weak rule claim a
settlement that a strong rule was about to claim correctly, which is a false
match manufactured by nothing but iteration order.

A settlement group, once claimed, is out of circulation. Two bank lines can
never point at the same payout.

The safety-critical rule -- mutual uniqueness -- is written once, in
`_match_unique`, and every amount-based tier goes through it.
"""

import re

from matcher.decision import MATCHED

UTR_RE = re.compile(r"\bRZPX\d{8}\b")
# Anything UTR-shaped, including fragments and whitespace-injected variants.
# Used only to *explain* a failure, never to make a match.
UTRISH_RE = re.compile(r"RZPX[\d ]{4,14}")

# Tolerance exists to absorb paisa-level reconstruction rounding, nothing else.
# The smallest fee in this dataset is 2% of the smallest sale -- three orders of
# magnitude above this. A tolerance that can swallow a fee is a false-match
# generator, so this number must stay small enough to be obviously incapable
# of it (§9, T1: "tolerance must cover rounding, not fees").
TOLERANCE_PAISA = 5

# A payout initiated on day D lands same-day or the next business morning.
# Three calendar days covers a Friday payout crediting on Monday.
DATE_WINDOW_DAYS = 3

# T2b bounds. §15 states plainly that a pathological batch can exceed these and
# land in residue; that is the intended failure mode. Unbounded subset-sum over
# money amounts is where naive implementations hang, and worse, where they
# manufacture coincidental matches.
MAX_SUBSET_SIZE = 4
MAX_POOL_SIZE = 24
MAX_SEARCH_NODES = 200_000


def _in_window(txn, group):
    return group.settled_at is not None and 0 <= (txn.txn_date - group.settled_at).days <= DATE_WINDOW_DAYS


def _match_unique(state, pool, tier, reason_template, confidence):
    """Accept a bank line only when the choice is forced from both directions.

    The bank line must have exactly one candidate, AND that candidate must be
    the sole candidate of exactly one bank line. Without the second half, two
    identical payouts on one day are resolved by whichever loop index came
    first -- a coin flip recorded as a fact. That is the EC07 trap, and
    declining it is the correct outcome, not a gap.
    """
    wanted = {}
    candidates = {}

    for txn in state.open_txns():
        cands = [g for g in pool
                 if _in_window(txn, g) and abs(g.payout - txn.signed) <= TOLERANCE_PAISA]
        candidates[txn.bank_txn_id] = cands
        for g in cands:
            wanted.setdefault(g.settlement_id, []).append(txn.bank_txn_id)

    for txn in state.open_txns():
        cands = candidates[txn.bank_txn_id]
        if not cands:
            continue

        if len(cands) > 1:
            state.mark_reason(txn, f"{tier} ambiguous: {len(cands)} settlements match this "
                                   f"amount and date window", kind="ambiguous")
            state.audit.log(tier, "ambiguous_candidates", bank_txn_id=txn.bank_txn_id,
                            bank_amount=txn.signed, txn_date=txn.txn_date,
                            candidates=[g.settlement_id for g in cands])
            continue

        group = cands[0]
        rivals = wanted[group.settlement_id]
        if len(rivals) > 1:
            state.mark_reason(txn, f"{tier} contested: {group.settlement_id} is also the only "
                                   f"candidate for {len(rivals) - 1} other bank line(s)",
                              kind="ambiguous")
            state.audit.log(tier, "contested_candidate", bank_txn_id=txn.bank_txn_id,
                            settlement_id=group.settlement_id, contested_by=rivals)
            continue

        if group.settlement_id in state.claimed:
            continue

        delta = txn.signed - group.payout
        state.accept(txn, [group], tier=tier,
                     confidence=confidence if delta == 0 else confidence - 0.15,
                     reason=reason_template.format(lines=len(group.processed)),
                     evidence={"payout": group.payout, "bank_amount": txn.signed,
                               "delta": delta, "settlement_lines": len(group.processed),
                               "day_lag": (txn.txn_date - group.settled_at).days})


# --------------------------------------------------------------------------
# T0
# --------------------------------------------------------------------------

def tier0_exact_utr(state):
    """T0 -- the clean path: a full UTR in the narration *and* an exact payout.

    Both halves are required. A UTR that resolves to a group whose total does
    not match is not a match; it is a lead, and it gets logged as one.
    """
    by_utr = {}
    for group in state.free_groups():
        if group.utr:
            by_utr.setdefault(group.utr, []).append(group)

    for txn in state.open_txns():
        for utr in UTR_RE.findall(txn.narration):
            cands = [g for g in by_utr.get(utr, []) if g.settlement_id not in state.claimed]
            if len(cands) != 1:
                continue
            group = cands[0]

            if group.naive_payout != group.payout:
                # EC09 is live in this group: summing the settlement without
                # checking status would have produced a different number.
                state.audit.log("T0", "on_hold_excluded", bank_txn_id=txn.bank_txn_id,
                                settlement_id=group.settlement_id,
                                naive_total=group.naive_payout, processed_total=group.payout,
                                held=[r.entity_id for r in group.rows if r.status != "processed"])

            if group.payout == txn.signed:
                state.accept(txn, [group], tier="T0", confidence=1.0,
                             reason=f"exact UTR {utr} in narration and exact payout match",
                             evidence={"utr": utr, "payout": group.payout,
                                       "bank_amount": txn.signed,
                                       "settlement_lines": len(group.processed)})
                break

            state.audit.log("T0", "utr_hit_amount_mismatch", bank_txn_id=txn.bank_txn_id,
                            settlement_id=group.settlement_id, utr=utr,
                            expected=group.payout, bank_amount=txn.signed,
                            delta=txn.signed - group.payout)


# --------------------------------------------------------------------------
# T1 / T2 -- same rule, different pool. The split is deliberate.
# --------------------------------------------------------------------------

def tier1_amount_date(state):
    """T1 -- one settlement line, one credit, by amount and date alone.

    Restricted to *singleton* settlements. Summing a group and matching the
    total is batch decomposition, which is T2's job; letting T1 do it would
    collapse the two tiers and make the baseline meaningless.
    """
    pool = [g for g in state.free_groups() if len(g.processed) == 1 and g.settled_at]
    _match_unique(state, pool, "T1",
                  f"unique singleton settlement within {DATE_WINDOW_DAYS}d "
                  f"and {TOLERANCE_PAISA}p",
                  confidence=0.9)


def tier2_batch_decomposition(state):
    """T2 -- the workhorse (§9).

    Group the settlement report by settlement_id, sum the *processed* nets,
    and match that total to a bank credit. This is what dissolves the
    40-payment batch: no one-to-one line exists anywhere in the data, and the
    only thing that equals the bank credit is the group total.

    The on-hold filter lives in SettlementGroup.processed. Summing every row
    that shares a settlement_id -- the obvious implementation -- overshoots by
    the held amount and concludes, wrongly, that the batch does not reconcile.
    """
    pool = [g for g in state.free_groups() if len(g.processed) > 1 and g.settled_at]

    for group in pool:
        if group.naive_payout != group.payout:
            state.audit.log("T2", "on_hold_excluded", settlement_id=group.settlement_id,
                            naive_total=group.naive_payout, processed_total=group.payout,
                            held=[r.entity_id for r in group.rows if r.status != "processed"])

    _match_unique(state, pool, "T2",
                  "settlement group of {lines} lines sums to exactly this credit",
                  confidence=0.95)


# --------------------------------------------------------------------------
# T2b -- bounded subset-sum, on leftovers only
# --------------------------------------------------------------------------

def _subset_solutions(payouts, target, tol, max_size, max_nodes):
    """Every subset of size 2..max_size summing to `target` within `tol`.

    Stops as soon as a second solution is found: for our purposes the only
    question is whether the answer is *forced*, and two solutions already
    settle that. Returns (solutions, nodes_visited, aborted).

    All payouts are the same sign as the target, which keeps the pruning
    sound: once the running total overshoots, no extension can come back.
    """
    order = sorted(range(len(payouts)), key=lambda i: -payouts[i])
    p = [payouts[i] for i in order]
    n = len(p)

    suffix = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suffix[i] = suffix[i + 1] + p[i]

    solutions, stats = [], {"nodes": 0, "aborted": False}

    def walk(start, chosen, total):
        stats["nodes"] += 1
        if stats["nodes"] > max_nodes:
            stats["aborted"] = True
            return
        if total > target + tol:
            return
        if abs(total - target) <= tol and len(chosen) >= 2:
            solutions.append([order[i] for i in chosen])
            return
        if len(chosen) == max_size or start >= n:
            return
        if total + suffix[start] < target - tol:
            return
        for j in range(start, n):
            chosen.append(j)
            walk(j + 1, chosen, total + p[j])
            chosen.pop()
            if stats["aborted"] or len(solutions) > 1:
                return

    walk(0, [], 0)
    return solutions, stats["nodes"], stats["aborted"]


def tier2b_subset_sum(state):
    """T2b -- combinations of leftover settlements, hard-capped.

    Only runs on what every earlier tier left behind, and only proposes when
    the solution is UNIQUE. Subset-sum over money amounts has many
    coincidental answers; accepting the first one found is the single most
    reliable way to manufacture a false match in a reconciliation engine.
    Two solutions means the evidence does not determine the answer, so the
    line stays unresolved.

    Both caps (set size and search nodes) are refusals to guess, not
    optimisations: exceeding either sends the line to residue, which §15
    states openly as a known limitation.
    """
    for txn in state.open_txns():
        target = txn.signed
        if target == 0:
            continue
        sign = 1 if target > 0 else -1

        pool = [g for g in state.free_groups()
                if _in_window(txn, g)
                and g.payout != 0
                and (g.payout > 0) == (target > 0)
                and abs(g.payout) <= abs(target) + TOLERANCE_PAISA]

        if len(pool) < 2:
            continue

        if len(pool) > MAX_POOL_SIZE:
            state.mark_reason(txn, f"T2b declined to search: {len(pool)} candidate settlements "
                                   f"exceeds the pool cap of {MAX_POOL_SIZE}", kind="capped")
            state.audit.log("T2b", "pool_cap_exceeded", bank_txn_id=txn.bank_txn_id,
                            pool=len(pool), cap=MAX_POOL_SIZE)
            continue

        solutions, nodes, aborted = _subset_solutions(
            [g.payout * sign for g in pool], target * sign, TOLERANCE_PAISA,
            MAX_SUBSET_SIZE, MAX_SEARCH_NODES)

        if aborted:
            state.mark_reason(txn, f"T2b hit the {MAX_SEARCH_NODES:,}-node search cap and "
                                   f"stopped rather than guess", kind="capped")
            state.audit.log("T2b", "node_cap_exceeded", bank_txn_id=txn.bank_txn_id,
                            nodes=nodes, cap=MAX_SEARCH_NODES, pool=len(pool))
            continue

        if not solutions:
            continue

        if len(solutions) > 1:
            state.mark_reason(txn, "T2b found more than one combination of settlements summing "
                                   "to this amount; the evidence does not pick one",
                              kind="ambiguous")
            state.audit.log("T2b", "multiple_solutions", bank_txn_id=txn.bank_txn_id,
                            bank_amount=target, nodes=nodes,
                            example=[pool[i].settlement_id for i in solutions[0]])
            continue

        chosen = [pool[i] for i in solutions[0]]
        if any(g.settlement_id in state.claimed for g in chosen):
            continue

        state.accept(txn, chosen, tier="T2b", confidence=0.7,
                     reason=f"unique combination of {len(chosen)} settlements sums to this credit",
                     evidence={"bank_amount": target, "nodes_searched": nodes,
                               "combination": [g.settlement_id for g in chosen],
                               "payouts": [g.payout for g in chosen]})


# --------------------------------------------------------------------------
# Refusal, and the residue explanation
# --------------------------------------------------------------------------

def refuse_impossible(state):
    """Actively decline a bank line that provably cannot be a settlement.

    Not a heuristic and not keyword matching. The test is arithmetic: if every
    unclaimed settlement in the date window, taken together, cannot reach this
    amount, then no combination of them can either. A ₹2,50,000 credit sitting
    beside ₹2,927.24 of unclaimed settlements is not an unresolved line -- it
    is a line we can prove is foreign, and saying so is a stronger result than
    staying quiet (§8, case 11).

    This can only be wrong if a genuine settlement falls outside the date
    window, which is an assumption stated in results.md, not a hidden one.
    """
    for txn in state.open_txns():
        target = txn.signed
        if target == 0:
            continue

        pool = [g for g in state.free_groups() if _in_window(txn, g)]
        reach = sum(g.payout for g in pool if (g.payout > 0) == (target > 0))

        if abs(reach) + TOLERANCE_PAISA < abs(target):
            state.refuse(txn, reason=(
                f"provably not a settlement: every unclaimed settlement within "
                f"{DATE_WINDOW_DAYS} days of {txn.txn_date} sums to "
                f"{reach / 100:,.2f}, which cannot reach {target / 100:,.2f}"),
                evidence={"bank_amount": target, "max_reachable": reach,
                          "pool": [g.settlement_id for g in pool]})


# Which tiers rest a match on a recovered *reference* (a UTR) rather than on
# amount-and-date arithmetic alone. This distinction matters more than the
# headline match rate: an amount+date link is only as good as the absence of a
# coincidence, and coincidences get more likely as a merchant gets busier.
REFERENCE_BACKED = {"T0", "T3"}

MAX_EDIT_DISTANCE = 2


def _within_edit_distance(a, b, k):
    """Bounded Levenshtein. Returns early once every cell exceeds k."""
    if abs(len(a) - len(b)) > k:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > k:
            return False
        prev = cur
    return prev[-1] <= k


def _salvage(token, utr):
    """How, if at all, a narration token can be read as this UTR."""
    if token == utr:
        return "normalised", 0
    if len(token) >= 8 and utr.startswith(token):
        return "truncated", len(utr) - len(token)
    for k in range(1, MAX_EDIT_DISTANCE + 1):
        if _within_edit_distance(token, utr, k):
            return f"edit-distance {k}", k
    return None, None


def tier3_narration_salvage(state):
    """T3 -- recover a reference the bank mangled, corroborated by the amount.

    The bank truncates the narration, drops a digit, or injects whitespace mid
    token. Normalising and allowing a bounded edit distance recovers the UTR.

    The hard rule: a salvaged reference is NEVER sufficient on its own. The
    payout must also match to the paisa, and exactly one settlement may
    survive both filters. String similarity alone is a guess dressed up as
    evidence, and a fuzzy match written into a ledger is indistinguishable
    from a correct one after the fact.

    Confidence is scored by how much of the reference actually survived, so a
    whitespace-mangled UTR is not presented as being as certain as a clean one.
    """
    for txn in state.open_txns():
        tokens = {t.replace(" ", "").strip() for t in UTRISH_RE.findall(txn.narration)}
        tokens = {t for t in tokens if len(t) >= 8}
        if not tokens:
            continue

        hits = []
        for group in state.free_groups():
            if not group.utr or not _in_window(txn, group):
                continue
            for token in sorted(tokens):
                kind, distance = _salvage(token, group.utr)
                if kind:
                    hits.append((group, token, kind, distance))
                    break

        # Arithmetic corroboration, applied before uniqueness is even considered.
        corroborated = [h for h in hits if abs(h[0].payout - txn.signed) <= TOLERANCE_PAISA]

        if not corroborated:
            for group, token, kind, _ in hits:
                state.audit.log("T3", "salvage_without_corroboration",
                                bank_txn_id=txn.bank_txn_id, token=token, kind=kind,
                                settlement_id=group.settlement_id,
                                expected=group.payout, bank_amount=txn.signed)
            continue

        if len(corroborated) > 1:
            state.mark_reason(txn, f"T3 ambiguous: {len(corroborated)} settlements match the "
                                   f"salvaged reference and the amount", kind="ambiguous")
            state.audit.log("T3", "ambiguous_salvage", bank_txn_id=txn.bank_txn_id,
                            candidates=[h[0].settlement_id for h in corroborated])
            continue

        group, token, kind, distance = corroborated[0]
        if group.settlement_id in state.claimed:
            continue

        confidence = {"normalised": 0.95, "truncated": 0.9}.get(kind, 0.85 - 0.05 * distance)
        state.accept(txn, [group], tier="T3", confidence=confidence,
                     reason=(f"narration token {token!r} salvaged to UTR {group.utr} "
                             f"({kind}), corroborated by an exact payout match"),
                     evidence={"token": token, "utr": group.utr, "salvage": kind,
                               "distance": distance, "payout": group.payout,
                               "bank_amount": txn.signed,
                               "settlement_lines": len(group.processed)})


def _one_deletion(short, full):
    """Is `short` `full` with exactly one character removed?"""
    if len(short) != len(full) - 1:
        return False
    i = 0
    while i < len(short) and short[i] == full[i]:
        i += 1
    return short[i:] == full[i + 1:]


def _utr_leads(txn, free):
    """UTR-ish tokens in a narration and the unclaimed settlements they could
    plausibly refer to. Diagnostic only -- this function is deliberately not
    allowed to resolve anything, because salvaging a mangled UTR is T3's job
    and doing it here would smuggle an unmeasured tier into the baseline."""
    leads = []
    for token in UTRISH_RE.findall(txn.narration):
        norm = token.replace(" ", "").strip()
        if len(norm) < 8:
            continue
        hits = [g for g in free if g.utr and (
            g.utr == norm or g.utr.startswith(norm) or _one_deletion(norm, g.utr))]
        leads.append((token.strip(), norm, hits))
    return leads


def annotate_residue(state):
    """Write an honest reason onto every line still unresolved.

    Not a tier: it matches nothing and claims nothing. exceptions.md is a
    deliverable, and "no rule fired" is a default, not a reason. What a human
    needs is what was tried and how close it got -- which is also exactly the
    packet the LLM analyst will be handed on Day 5.
    """
    free = state.free_groups()
    for txn in state.open_txns():
        d = state.decisions[txn.bank_txn_id]
        if d.reason != "no rule fired":
            continue    # a tier already explained itself; don't overwrite it

        in_window = [g for g in free if _in_window(txn, g)]
        leads = [(tok, norm, hits) for tok, norm, hits in _utr_leads(txn, free) if hits]

        if leads:
            tok, norm, hits = leads[0]
            g = hits[0]
            delta = txn.signed - g.payout
            d.residue_kind = "t3_salvage"
            d.reason = (f"narration holds {tok!r}, which normalises to {norm} and points at "
                        f"{g.settlement_id} ({len(g.processed)} line(s), payout "
                        f"{g.payout / 100:,.2f}, off by {delta / 100:+.2f}) — "
                        f"needs T3 narration salvage")
            state.audit.log("residue", "utr_lead", bank_txn_id=txn.bank_txn_id,
                            token=tok, normalised=norm,
                            candidates=[h.settlement_id for h in hits], delta=delta)
        elif not in_window:
            d.residue_kind = "no_candidate"
            d.reason = (f"no usable UTR and no unclaimed settlement settles within "
                        f"{DATE_WINDOW_DAYS} days of {txn.txn_date}")
        else:
            nearest = min(in_window, key=lambda g: abs(g.payout - txn.signed))
            d.residue_kind = "no_candidate"
            d.reason = (f"no usable UTR; {len(in_window)} unclaimed settlement(s) within "
                        f"{DATE_WINDOW_DAYS} days, none matching alone or in any "
                        f"combination of {MAX_SUBSET_SIZE} or fewer (nearest: "
                        f"{nearest.settlement_id} at {nearest.payout / 100:,.2f}, off by "
                        f"{(txn.signed - nearest.payout) / 100:+.2f})")
            state.audit.log("residue", "no_combination", bank_txn_id=txn.bank_txn_id,
                            bank_amount=txn.signed, in_window=len(in_window))


# §9 lists T3 last, as a fuzzy last resort. Running it there would be wrong on
# this data, and the reason is worth stating: because T3 requires the payout to
# match exactly *as well as* the reference, a salvaged UTR is stronger evidence
# than the amount-and-date agreement T1/T2 rely on -- not weaker. Placed last,
# T2 claims those lines first on arithmetic alone and the recovered reference
# is never consulted. Placed early, the same lines are matched on a reference
# AND the arithmetic. The match count is unchanged; the evidence behind it is
# not. Both orders are run and compared in results.md rather than asserted.
TIER_ORDERS = {
    "evidence": [
        ("T0", tier0_exact_utr),
        ("T3", tier3_narration_salvage),
        ("T1", tier1_amount_date),
        ("T2", tier2_batch_decomposition),
        ("T2b", tier2b_subset_sum),
    ],
    "spec": [
        ("T0", tier0_exact_utr),
        ("T1", tier1_amount_date),
        ("T2", tier2_batch_decomposition),
        ("T2b", tier2b_subset_sum),
        ("T3", tier3_narration_salvage),
    ],
}

TIERS = TIER_ORDERS["evidence"]

FINALIZERS = [
    ("REFUSE", refuse_impossible),
    ("residue", annotate_residue),
]
