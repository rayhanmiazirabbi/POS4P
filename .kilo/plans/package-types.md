# Types Package Spec

## Purpose
Canonical TypeScript representations of organizations, stores, products, batches, sales, orders, customers, payments, sync events, and errors.

## Dependencies
`core` only where possible.

## Phases
- MVP: shared entity, enum, DTO, ledger, receipt, and pagination types.
- Stage 2: loyalty, transfer, device, and report types.
- Commerce/AI: ecommerce, prescription, AI job/tool, billing, and supplier-network types.

## Boundaries
Types mirror public API contracts, not database internals. Discriminated unions represent status/event variants. Keep monetary and quantity fields explicit with units/currency.

## Validation
Compile all three apps, fixture compatibility tests, exhaustive switch checks, and API schema drift detection.
