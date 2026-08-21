# Orders Backend Spec

## Purpose
Online order lifecycle, items, stock reservations, status history, fulfillment, cancellation, and conversion to sale.

## Dependencies
`organizations`, `stores`, `ecommerce`, `customers`, `inventory`, `payments`, `prescriptions`, `sales`, `audit`, `sync`.

## Phases
- Commerce: guest checkout, reserve, accept, prescription gate, prepare, pickup/delivery, cancel, complete.
- Later: delivery providers, customer notifications, richer fulfillment states.

## Data/API
Owns `orders`, `order_items`, `stock_reservations`, `order_status_history`. Use explicit state machine and transactional reservation. Completion invokes sales conversion idempotently.

## Validation
Transition matrix, stock races, payment failure, reservation expiry/release, prescription block, duplicate completion, and customer/store isolation.
