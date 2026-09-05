"""The fee schedule. One file, read by both the generator and the verifier.

That is not cheating: a merchant genuinely has the published rate card. The
generator applies it forward to produce net amounts, the verifier applies it
backward to check an LLM proposal, and the analyst is *shown* it but is never
trusted to apply it (project-statement.md §10).
"""

from decimal import Decimal

from money import apply_rate

PLATFORM_FEE_RATE = Decimal("0.02")
GST_ON_FEE_RATE = Decimal("0.18")

# Serialised verbatim to data/fee_schedule.json and later pasted into the
# analyst prompt, so the model sees exactly the rules the verifier enforces.
SCHEDULE = {
    "currency": "INR",
    "platform_fee_rate": str(PLATFORM_FEE_RATE),
    "gst_on_fee_rate": str(GST_ON_FEE_RATE),
    "netting": "net = gross - fee - gst_on_fee",
    "rounding": "half-up to the paisa, applied to the fee first and then to the GST on that fee",
    "refunds": "the platform fee on the original payment is not returned; a refund nets at full face value",
    "known_limitation": "a single flat schedule for every payment method; real merchants have per-method rates",
}


def breakdown(gross_paisa):
    """Forward direction: gross -> (fee, gst_on_fee, net), all int paisa."""
    fee = apply_rate(gross_paisa, PLATFORM_FEE_RATE)
    gst = apply_rate(fee, GST_ON_FEE_RATE)
    return {
        "gross": gross_paisa,
        "fee": fee,
        "gst_on_fee": gst,
        "net": gross_paisa - fee - gst,
    }
