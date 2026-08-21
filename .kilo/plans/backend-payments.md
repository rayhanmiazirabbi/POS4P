# Payments Backend Spec

## Purpose
Cash, bKash, Nagad, due, split tenders, payment status, refunds, reconciliation, and external payment abstraction.

## Dependencies
`organizations`, `stores`, `sales`, `orders`, `customers`, `audit`, `billing` for platform charges.

## Phases
- MVP: cash/bKash/Nagad/due payment records, change, split tender, manual status and receipt.
- Stage 2: reconciliation and refund workflows.
- Commerce: hosted/async online payment intents and webhooks.

## Data/API
Owns `payments` and refund/reconciliation records. External callbacks are authenticated, idempotent, and never directly mark inventory complete without order/sale state checks.

## Validation
Duplicate webhook, partial payment, overpayment, due permissions, refund, provider timeout, and daily reconciliation tests.
