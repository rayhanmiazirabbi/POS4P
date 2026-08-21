# Money Package Spec

## Purpose
Currency-safe decimal arithmetic, rounding, allocation, totals, payment amounts, and display formatting.

## Dependencies
`core` only.

## Phases
- MVP: BDT currency, decimal values, line/tender totals, due and change, configurable rounding.
- Stage 2: multi-currency report boundaries if required.
- Commerce/AI: payment/refund reconciliation and tool-safe numeric output.

## Boundaries
Do not use binary floating point for monetary calculations. Keep currency with every amount and make rounding points explicit.

## Validation
Half-rounding, discount allocation, split tenders, refund totals, zero/negative rejection, serialization, and parity with backend Decimal behavior.
