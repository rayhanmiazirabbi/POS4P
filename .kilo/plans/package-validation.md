# Validation Package Spec

## Purpose
Zod schemas for forms, API payloads, imports, offline events, and cross-field business validation.

## Dependencies
`types`, `core`.

## Phases
- MVP: product search, sale/cart, payment, customer, purchase, batch, and user forms.
- Stage 2: transfers, loyalty, reports filters, device registration.
- Commerce/AI: checkout, addresses, prescriptions, uploads, invoice extraction review, tool arguments.

## Boundaries
Client validation improves UX; server validation remains authoritative. Normalize phones, decimal strings, dates, and barcode input consistently. Do not use schemas as permission checks.

## Validation
Valid/invalid fixtures, boundary quantities/prices, locale phone formats, unknown-field policy, and parity checks with Pydantic contracts.
