# Inventory Backend Spec

## Purpose
Batch stock, movement ledger, balances, reservations, FEFO allocation, adjustments, expiry, damage, opening stock, and transfers.

## Dependencies
`organizations`, `stores`, `products`, `suppliers`, `sales`, `orders`, `audit`, `sync`.

## Phases
- MVP: batches, movements, balance projection, FEFO, low-stock/expiry queries, authorized adjustments.
- Stage 2: branch transfer workflow and projection rebuild.
- Commerce/AI: order reservations and expiry/demand projections.

## Data/API
Owns `inventory_batches`, `inventory_movements`, `inventory_balances`, reservations, and transfer records. Movement plus balance update occurs transactionally; ledger remains rebuildable truth.

## Validation
Concurrent allocation, batch expiry, returns, negative stock policy, transfer idempotency, balance rebuild, and organization/store RLS.
