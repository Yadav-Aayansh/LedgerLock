# exceptions.md

Every line the engine did not resolve, individually, with the reason it
actually recorded. Nothing here is aggregated away.

Tiers active: T0, T3, T1, T2, T2b.

## Errors

None. No line was matched to the wrong settlement, and no real
settlement was refused.

## Unresolved settlement lines (2)

Worth being precise about what is and is not unknown here. The 2 lines below total ₹2927.24, and the 2 settlements left over total ₹2927.24 — the same figure. So the money is accounted for in aggregate; what cannot be determined is which credit belongs to which settlement. Matching them at the set level would be defensible and is noted as future work, but it is a different claim from the per-line links everywhere else in this report, so the engine does not quietly make it.

### bank_0024 _[EC07]_

- `2025-06-24` **₹1463.62** — `UPI-RAZORPAYSOFTWARE-SETTLEMENT-CR`
- Honest reason: T1 ambiguous: 2 settlements match this amount and date window

### bank_0025 _[EC07]_

- `2025-06-24` **₹1463.62** — `UPI-RAZORPAYSOFTWARE-SETTLEMENT-CR`
- Honest reason: T1 ambiguous: 2 settlements match this amount and date window

## What the analyst said

- `bank_0024` — **declined**: This bank credit corresponds to a Razorpay settlement, but it is one of two identical-value settlements from the same day, which are indistinguishable from the available data. _Two distinct settlements, stl_00020 and stl_00021, both have a net payout of ₹1463.62 and were settled on the same day as the bank credit. The bank narration lacks a Unique Transaction Reference (UTR) to differentiate between them, making it impossible to confidently assign the credit to one specific settlement._
- `bank_0025` — **declined**: The bank credit is a Razorpay settlement, but it is ambiguous as two distinct settlements have the exact same net amount and were settled on the same day. _Two settlements, stl_00020 and stl_00021, have identical net amounts (1463.62) and settlement dates. The bank narration does not contain a UTR or other distinguishing information, making it impossible to determine which settlement this credit corresponds to._

## Not settlements at all (5)

Ground truth says none of these link to anything. Leaving them alone is
correct; the distinction below is between actively declining and merely
not finding a match.

- `bank_0004` `2025-06-05` ₹-35000.00 — `UPI/DR/512334123/PROPCO REALTY/OFFICE RENT JUN` → **refused**
- `bank_0011` `2025-06-12` ₹-12450.00 — `ACH-DR-VENDOR PAYMENT-SUPPLYCO PACKAGING` → **refused**
- `bank_0016` `2025-06-17` ₹8900.00 — `NEFT-CR-HDFC0001234-DIRECT CUSTOMER TRANSFER` → **refused**
- `bank_0028` `2025-06-24` ₹250000.00 — `NEFT-RZPX00088421-RAZORPAY CAPITAL LOAN DISB` → **refused** _[EC11]_
- `bank_0034` `2025-06-30` ₹-185000.00 — `SALARY JUN 2025 PAYROLL BATCH` → **refused**

## Settlements with no bank line (2)

The other direction: money the report says was settled that no bank line
was matched to. An on-hold line belongs here permanently; the rest are
the mirror image of the unresolved list above.

- `stl_00020` — 1 line(s), payout ₹1463.62, settled 2025-06-24
- `stl_00021` — 1 line(s), payout ₹1463.62, settled 2025-06-24

