"""Money is integer paisa. Everywhere. No floats, ever.

The verifier in this project promises to recompute arithmetic "to the paisa"
(project-statement.md §5) and the generator deliberately plants sub-paisa GST
rounding drift as edge case 10. If amounts were floats, the verifier's own
arithmetic would become a source of drift and there would be no way to tell a
genuine 3-paisa GST gap from IEEE-754 noise.

So: rupee strings exist only at the CSV boundary. Everything inside is an int.
"""

from decimal import Decimal, ROUND_HALF_UP

PAISA_PER_RUPEE = 100
_ONE = Decimal("1")


def parse_rupees(text):
    """'1,499.00' -> 149900. Strict: more than two decimal places is an error."""
    if text is None:
        raise ValueError("expected a rupee amount, got None")
    cleaned = str(text).strip().replace(",", "").replace("₹", "")
    if not cleaned:
        raise ValueError("expected a rupee amount, got an empty string")
    value = Decimal(cleaned)
    places = -value.as_tuple().exponent
    if places > 2:
        raise ValueError(f"{text!r} has sub-paisa precision; refusing to round silently")
    return int(value.scaleb(2))


def parse_rupees_or_zero(text):
    """Bank statements leave the unused side of credit/debit blank. Blank is zero."""
    if text is None or str(text).strip() == "":
        return 0
    return parse_rupees(text)


def format_rupees(paisa):
    """149900 -> '1499.00'. The only place paisa turn back into rupees."""
    if not isinstance(paisa, int):
        raise TypeError(f"money must be int paisa, got {type(paisa).__name__}")
    return f"{Decimal(paisa).scaleb(-2):.2f}"


def apply_rate(paisa, rate):
    """Apply a Decimal rate to a paisa amount, rounding half-up to the paisa.

    This is the single rounding rule in the system. The generator applies it
    forward to produce nets; the verifier applies it backward to check a
    proposal. If the two ever disagree, every number downstream is theatre.
    """
    if not isinstance(paisa, int):
        raise TypeError(f"money must be int paisa, got {type(paisa).__name__}")
    return int((Decimal(paisa) * Decimal(rate)).quantize(_ONE, rounding=ROUND_HALF_UP))


def exact_product(paisa, rate):
    """The unrounded product, for detecting where rounding drift was introduced."""
    return Decimal(paisa) * Decimal(rate)
