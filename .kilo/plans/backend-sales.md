# Sales Backend Spec

## Purpose
POS and channel sales, line items, batch allocations, returns, discounts, customer association, stock decrement, and receipt records.

## Dependencies
`organizations`, `stores`, `products`, `inventory`, `payments`, `customers`, `loyalty`, `audit`, `sync`.

## Phases
- MVP: guest/identified sale, channel, totals, FEFO allocation, return, receipt, void restrictions.
- Stage 2: richer promotions, multi-store reporting, loyalty integration.
- Commerce: accepted order to completed sale conversion.

## Data/API
Owns `sales`, `sale_items`, `sale_item_batch_allocations`, and `returns`. Recalculate totals server-side and commit sale/payment/inventory/outbox atomically.

## Validation
Offline idempotency, insufficient stock, split tender, due, return limits, concurrent checkout, void audit, and historical snapshot integrity.
