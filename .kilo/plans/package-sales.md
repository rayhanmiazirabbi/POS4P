# Sales Package Spec

## Purpose
Cart, sale totals, discount rules, tax/rounding policy, channel metadata, receipt models, and return preparation.

## Dependencies
`types`, `money`, `inventory`, `customers`, `validation`.

## Phases
- MVP: guest/identified cart, line discounts, totals, payment split model, receipt, return constraints.
- Stage 2: richer promotions and loyalty earning/redemption integration.
- Commerce/AI: convert accepted online orders into completed sales.

## Boundaries
Server recalculates all totals. Preserve original sale snapshots; never mutate historical prices. Returns reference original lines and cannot exceed remaining returnable quantity.

## Validation
Rounding, discounts, split payments, due payments, returns, duplicate submit, and client/server total parity.
