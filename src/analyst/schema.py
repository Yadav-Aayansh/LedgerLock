"""The strict JSON contract the analyst must answer in (§10).

`unresolvable_reason` is not an error path. §10 requires "I cannot resolve
this" to be a first-class, rewarded output, so it is a required field with an
explicit null, and an empty `proposed_link` is a valid, complete answer rather
than a malformed one.
"""

PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "hypothesis": {
            "type": "string",
            "description": "One sentence explaining what this bank line probably is.",
        },
        "arithmetic": {
            "type": "string",
            "description": ("The calculation supporting the hypothesis, written out so a "
                            "deterministic verifier can be compared against it. Amounts in "
                            "rupees to two decimal places."),
        },
        "proposed_link": {
            "type": "array",
            "items": {"type": "string"},
            "uniqueItems": True,
            "description": ("Settlement entity ids (pay_/rfnd_/cb_/adj_) that together "
                            "account for this bank line. Each id at most once. EMPTY if "
                            "you cannot determine them: an empty list is a valid and "
                            "preferred answer when the evidence does not force one."),
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "unresolvable_reason": {
            "type": ["string", "null"],
            "description": ("Why this cannot be resolved, or null if you proposed a link. "
                            "Saying so plainly is a correct outcome, not a failure."),
        },
    },
    "required": ["hypothesis", "arithmetic", "proposed_link", "confidence",
                 "unresolvable_reason"],
    "additionalProperties": False,
}

def validate(proposal):
    """Check a decoded reply against the contract. Returns a reason, or None."""
    if not isinstance(proposal, dict):
        return f"expected a JSON object, got {type(proposal).__name__}"

    missing = [k for k in PROPOSAL_SCHEMA["required"] if k not in proposal]
    if missing:
        return f"missing required field(s): {', '.join(missing)}"

    link = proposal["proposed_link"]
    if not isinstance(link, list) or not all(isinstance(e, str) for e in link):
        return "proposed_link must be a list of strings"
    if len(set(link)) != len(link):
        return "proposed_link repeats a settlement id"

    if not isinstance(proposal["hypothesis"], str):
        return "hypothesis must be a string"
    if not isinstance(proposal["arithmetic"], str):
        return "arithmetic must be a string"

    confidence = proposal["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return f"confidence must be a number, got {type(confidence).__name__}"
    if not 0 <= confidence <= 1:
        return f"confidence {confidence} is outside 0..1"

    reason = proposal["unresolvable_reason"]
    if reason is not None and not isinstance(reason, str):
        return "unresolvable_reason must be a string or null"

    return None


SYSTEM = """\
You are a reconciliation analyst for an Indian merchant using Razorpay. You are \
looking at ONE bank statement line that a deterministic matching engine could not \
resolve, together with the settlement records that might explain it.

What you are for: explaining a discrepancy, classifying an exception, and — only \
when the evidence forces exactly one answer — proposing which settlement lines \
account for the bank line.

What you are not for: you do not write to the ledger. Every proposal you make is \
recomputed to the paisa by a deterministic verifier before it can become a link, \
and a proposal that does not balance is discarded. You cannot make something true \
by being confident about it.

Rules:
1. A settlement's NET amount is what reaches the bank, not its gross. Fee and GST \
on the fee are deducted first, per the fee schedule supplied.
2. GST is rounded half-up on the fee, not on the gross. Order of operations matters \
to the paisa.
3. Lines with status other than "processed" were never paid out. They cannot appear \
in a bank credit.
4. If two or more candidates fit equally well, you MUST return an empty \
proposed_link and say so in unresolvable_reason. Choosing between \
indistinguishable candidates is a coin flip, and a wrong link recorded as a fact \
is worse than an honest gap — it is a silent error nobody catches.
5. If the line does not look like a settlement at all, say that, with an empty \
proposed_link.

"I cannot resolve this" is a correct, valuable answer. Prefer it to a guess."""
