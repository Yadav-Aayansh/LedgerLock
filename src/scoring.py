"""Score the engine against the hidden ground truth.

The point of this file is the bucket table. A scorer with three buckets
(matched / unmatched / wrong) cannot express the two outcomes this project
exists to demonstrate:

  * refusing a bank credit that is not a settlement at all is a WIN, not an
    unresolved. A scorer that files EC11 under "unresolved" penalises exactly
    the behaviour §8 calls the sharpest result in the project.
  * refusing something that *was* real is its own error class (false refusal),
    distinct from honestly failing to find it. One is over-caution, the other
    is a gap in coverage, and they get fixed by opposite changes.

                       | truth has a link | truth says foreign
    -------------------+------------------+--------------------
    matched, set right | correct_match    | false_match
    matched, set wrong | false_match      | false_match
    refused            | false_refusal    | correct_refusal
    unresolved         | honest_miss      | foreign_unresolved

`foreign_unresolved` is deliberately its own bucket rather than being folded
into correct_refusal: "I could not match this" and "this is not a settlement
and I decline to match it" are different claims, and only the second is the
result worth showing.
"""

import csv
from dataclasses import dataclass, field
from pathlib import Path

from edge_cases import EDGE_CASES
from matcher.decision import MATCHED, REFUSED, UNRESOLVED

CORRECT_MATCH = "correct_match"
FALSE_MATCH = "false_match"
CORRECT_REFUSAL = "correct_refusal"
FALSE_REFUSAL = "false_refusal"
HONEST_MISS = "honest_miss"
FOREIGN_UNRESOLVED = "foreign_unresolved"

GOOD = {CORRECT_MATCH, CORRECT_REFUSAL}
BAD = {FALSE_MATCH, FALSE_REFUSAL}

# EC02 (fee + GST netting) is a property of every settlement line rather than
# of one planted row, so it has no tag in ground_truth.csv. EC10 (paisa
# rounding) bites on the fee-recomputation leg, which is the verifier's job --
# it cannot be scored until that exists.
IMPLICIT = {"EC02": lambda t: t["relation_type"].endswith("settlement")}
# EC10 has no tagged bank row: paisa drift does not break the settlement->bank
# leg (the report's net column is exact). It bites on the fee-RECOMPUTATION
# leg, which is the verifier's job, so it is scored by the adversarial suite.
REDTEAM_SCORED = {"EC10": "gst_rounded_the_intuitive_way"}


@dataclass
class Outcome:
    bank_txn_id: str
    bucket: str
    relation: str
    edge_cases: list
    tier: str
    detail: str = ""
    expected_lines: int = 0
    residue_kind: str = ""


@dataclass
class Score:
    outcomes: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    linkable: int = 0
    foreign: int = 0
    hold_excluded_correctly: bool = False
    unclaimed_settlements: list = field(default_factory=list)
    edge_verdicts: dict = field(default_factory=dict)

    def by_bucket(self, bucket):
        return [o for o in self.outcomes if o.bucket == bucket]

    def n(self, bucket):
        return self.counts.get(bucket, 0)


def load_truth(data_dir: Path):
    with open(Path(data_dir) / "ground_truth.csv", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _split(text):
    return set(text.split("|")) if text else set()


def score(decisions, truth_rows, unclaimed, redteam=None):
    by_bank = {t["bank_txn_id"]: t for t in truth_rows if t["bank_txn_id"]}
    settlement_side = [t for t in truth_rows if not t["bank_txn_id"]]

    s = Score(unclaimed_settlements=sorted(unclaimed))

    for bank_txn_id, d in decisions.items():
        truth = by_bank[bank_txn_id]
        relation = truth["relation_type"]
        is_foreign = relation == "foreign"
        expected = _split(truth["payment_ids"])
        proposed = set(d.payment_ids)
        detail = ""

        if d.status == MATCHED:
            if is_foreign:
                bucket = FALSE_MATCH
                detail = "asserted a match against a line that is not a settlement"
            elif proposed == expected:
                bucket = CORRECT_MATCH
            elif proposed & expected:
                bucket = FALSE_MATCH
                detail = (f"partially right: {len(proposed & expected)} of {len(expected)} "
                          f"lines correct, {len(proposed - expected)} wrong")
            else:
                bucket = FALSE_MATCH
                detail = "matched an entirely different settlement"
        elif d.status == REFUSED:
            bucket = CORRECT_REFUSAL if is_foreign else FALSE_REFUSAL
            if bucket == FALSE_REFUSAL:
                detail = f"refused a real settlement of {len(expected)} line(s)"
        else:
            bucket = FOREIGN_UNRESOLVED if is_foreign else HONEST_MISS

        s.outcomes.append(Outcome(
            bank_txn_id, bucket, relation,
            truth["edge_case_ids"].split("|") if truth["edge_case_ids"] else [],
            d.tier, detail, expected_lines=len(expected),
            residue_kind=d.residue_kind))

    for o in s.outcomes:
        s.counts[o.bucket] = s.counts.get(o.bucket, 0) + 1
    s.linkable = sum(1 for o in s.outcomes if o.relation != "foreign")
    s.foreign = sum(1 for o in s.outcomes if o.relation == "foreign")

    # The settlement side: an on-hold line has no bank counterpart at all.
    # Leaving it unclaimed is correct; sweeping it into a batch is a false match.
    claimed_ids = {pid for d in decisions.values() for pid in d.payment_ids}
    s.hold_excluded_correctly = all(
        not (_split(t["payment_ids"]) & claimed_ids) for t in settlement_side)

    s.edge_verdicts = _edge_verdicts(s, by_bank, redteam or [])
    return s


def _edge_verdicts(s, by_bank, redteam):
    verdicts = {}
    outcome_by_id = {o.bank_txn_id: o for o in s.outcomes}

    for ec, label in EDGE_CASES.items():
        if ec in REDTEAM_SCORED:
            case = next((r for r in redteam if r["name"] == REDTEAM_SCORED[ec]), None)
            if case is None:
                verdicts[ec] = ("deferred", "verifier suite did not run")
            elif case["ok"]:
                verdicts[ec] = ("handled", f"verifier rejects it — {case['summary']}")
            else:
                verdicts[ec] = ("failed", f"verifier {case['actual']}, "
                                          f"expected {case['expected']}")
            continue

        if ec in IMPLICIT:
            rows = [o for o in s.outcomes if IMPLICIT[ec](by_bank[o.bank_txn_id])]
        else:
            rows = [o for o in s.outcomes if ec in o.edge_cases]

        if not rows:
            verdicts[ec] = ("not present", "no tagged row -- check the generator")
            continue

        buckets = [o.bucket for o in rows]
        good = sum(1 for b in buckets if b in GOOD)
        bad = sum(1 for b in buckets if b in BAD)

        # A foreign line that was left alone got the right outcome, but
        # "unresolved" understates it and "handled" overstates it: nothing
        # actively declined it. Say exactly that.
        # A line that was declined because the data contains nothing capable of
        # separating two equally good candidates is not the same as a line
        # nobody could explain. Declining there is the best available outcome,
        # and calling it a plain miss understates it -- but it is still not a
        # resolution, so it does not get to be "handled" either.
        if (not bad and not good and rows
                and all(o.residue_kind == "ambiguous" for o in rows)):
            verdicts[ec] = ("declined", f"{len(rows)} line(s) correctly declined: nothing in "
                                        f"the data separates the candidates, so any pairing "
                                        f"would be a coin flip")
        elif all(o.relation == "foreign" for o in rows) and not bad and not good:
            verdicts[ec] = ("partial",
                            f"{len(rows)} line(s) left alone -- the correct outcome, but "
                            f"passively; nothing actively refused them")
        elif ec == "EC09" and not s.hold_excluded_correctly:
            verdicts[ec] = ("failed", "the on-hold line was swept into a match")
        elif bad:
            verdicts[ec] = ("failed", f"{bad}/{len(rows)} line(s) matched wrongly")
        elif good == len(rows):
            verdicts[ec] = ("handled", f"{good}/{len(rows)} line(s) resolved correctly")
        elif good:
            open_rows = [o for o in rows if o.bucket not in GOOD]
            why = ""
            if open_rows and all(o.residue_kind == "ambiguous" for o in open_rows):
                # Not a gap in this case's handling: the open lines are open
                # because of a different planted case. Say which, rather than
                # letting the verdict read as a shortfall here.
                why = (" — the open line(s) are the EC07 ambiguity trap, correctly "
                       "declined there, not a failure of this case")
            verdicts[ec] = ("partial", f"{good}/{len(rows)} resolved, "
                                       f"{len(rows) - good} left unresolved{why}")
        else:
            verdicts[ec] = ("unresolved", f"0/{len(rows)} resolved, none matched wrongly")
    return verdicts


def metrics(s, decisions, runtime_s, llm_calls=0, llm_rejected=0, cost_inr=None):
    """The §11 table. Denominators are stated in results.md, not hidden."""
    auto = sum(1 for o in s.outcomes
               if o.bucket == CORRECT_MATCH and o.tier.startswith("T"))
    llm = sum(1 for o in s.outcomes
              if o.bucket == CORRECT_MATCH and o.tier.startswith("LLM"))
    asserted = sum(1 for d in decisions.values() if d.status == MATCHED)

    return {
        "auto_match_rate": (auto / s.linkable) if s.linkable else 0.0,
        "auto_matched": auto,
        "llm_assisted_rate": (llm / s.linkable) if s.linkable else 0.0,
        "llm_assisted": llm,
        "false_matches": s.n(FALSE_MATCH),
        "false_match_rate": (s.n(FALSE_MATCH) / asserted) if asserted else 0.0,
        "false_refusals": s.n(FALSE_REFUSAL),
        "unresolved": s.n(HONEST_MISS),
        "correct_refusals": s.n(CORRECT_REFUSAL),
        "foreign_unresolved": s.n(FOREIGN_UNRESOLVED),
        "linkable": s.linkable,
        "foreign": s.foreign,
        "asserted_matches": asserted,
        "verifier_rejection_rate": (llm_rejected / llm_calls) if llm_calls else None,
        "llm_calls": llm_calls,
        "cost_inr": cost_inr,
        "runtime_s": runtime_s,
    }
