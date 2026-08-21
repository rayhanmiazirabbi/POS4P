# Purchasing Backend Spec

## Purpose
Purchase drafts/confirmation, purchase items, batch receipt, supplier liability, purchase returns, and outbox emission.

## Dependencies
`organizations`, `stores`, `products`, `suppliers`, `inventory`, `audit`, `sync`.

## Phases
- MVP: supplier purchase entry, confirmation transaction, batch creation, movements, supplier due, purchase return.
- Stage 2: purchase orders and multi-store receiving.
- AI: OCR extraction review and reorder assistant integration.

## Data/API
Owns `purchases` and `purchase_items`; invokes inventory/supplier domain commands rather than duplicating ledger logic. Confirmation accepts an idempotency key.

## Validation
Atomic rollback, duplicate confirmation, multiple batches, supplier balance reconciliation, return limits, and permission restrictions on costs.
