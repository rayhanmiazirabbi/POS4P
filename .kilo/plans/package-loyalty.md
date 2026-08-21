# Loyalty Package Spec

## Purpose
Points earning, redemption, expiry, refunds, balances, and customer-facing loyalty summaries.

## Dependencies
`types`, `money`, `customers`, `sales`, `api`, `validation`.

## Phases
- MVP/stage 2: configurable earn/redeem policy, ledger display, account enrollment, refund reversal.
- Commerce: apply loyalty across POS and online orders.
- AI: reporting inputs only; no autonomous points changes.

## Boundaries
Ledger is authoritative; balance is a projection. Define rounding, redemption caps, expiry policy, and transaction references before enabling redemption.

## Validation
Earn/redeem/refund/expire/adjust cases, duplicate events, concurrent redemption, policy changes, and ledger rebuild.
