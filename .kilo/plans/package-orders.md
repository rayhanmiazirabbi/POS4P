# Orders Package Spec

## Purpose
Order cart, checkout, status transitions, reservations, pickup/delivery metadata, and order-to-sale conversion models.

## Dependencies
`types`, `validation`, `inventory`, `customers`, `sales`, `payments` API contracts.

## Phases
- Commerce: guest checkout, reservation request, acceptance, preparation, pickup/delivery, cancellation.
- Later: payment webhooks, fulfillment integrations, custom domains.

## Boundaries
An order is not a completed sale. Status transitions are explicit and audited; reservation release is idempotent. Prescription-required lines block fulfillment until approved.

## Validation
State-machine transition tests, stock race, cancellation/refund, guest/account conversion, payment failure, and order-to-sale reconciliation.
