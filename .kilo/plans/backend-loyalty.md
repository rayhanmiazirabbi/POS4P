# Loyalty Backend Spec

## Purpose
Configurable points program, accounts, append-only transactions, balance projection, expiry, earn/redeem/refund/bonus/adjust flows.

## Dependencies
`organizations`, `customers`, `sales`, `orders`, `audit`, `sync`.

## Phases
- Stage 2: earn/redeem policy, account enrollment, ledger and balance.
- Commerce: consistent POS/order earning and reversal.
- AI: read-only reporting and campaign analysis.

## Data/API
Owns `loyalty_accounts` and `loyalty_transactions`. Every transaction references a source and idempotency key; balance can be rebuilt from the ledger.

## Validation
Concurrent redemption, refund reversal, expiration, policy changes, duplicate source events, and ledger rebuild.
