"""The eleven deliberately planted edge cases (project-statement.md §8).

Every one is tagged onto the relevant row of ground_truth.csv by the generator,
so the final report can say handled / partially handled / failed *per case*
instead of hiding behind a single aggregate percentage.
"""

EDGE_CASES = {
    "EC01": "A 40-payment batch collapsing into one bank credit",
    "EC02": "Fee + 18% GST netting, so no bank amount equals any sale amount",
    "EC03": "A refund buried inside a later settlement batch",
    "EC04": "A chargeback debit landing 3 days after the payment settled",
    "EC05": "A chargeback reversal 10 days after the chargeback",
    "EC06": "A truncated narration with the UTR cut in half",
    "EC07": "Two identical-amount payments on the same day (ambiguity trap)",
    "EC08": "A T+2 settlement crossing a long weekend",
    "EC09": "An on-hold settlement excluded from its batch's bank credit",
    "EC10": "Paisa rounding drift on GST",
    "EC11": "A bank credit that is not a settlement at all (loan disbursal)",
}

# EC11 is the sharp one: the correct behaviour is to refuse to match it.
# A scorer that files a correct refusal under "unresolved" penalises exactly
# the behaviour this project is trying to demonstrate.
MUST_REFUSE = {"EC11"}
