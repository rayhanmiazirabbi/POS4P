# Suppliers Backend Spec

## Purpose
Supplier identity, contact, product mappings, payable ledger, payments/adjustments, and supplier performance data.

## Dependencies
`organizations`, `stores`, `products`, `purchasing`, `audit`, `reports`.

## Phases
- MVP: supplier CRUD, purchase due, ledger entries, payments and adjustments with manager/owner control.
- Stage 2: lead time, preferred supplier, purchase-order support.
- Future network: supplier onboarding, confirmations, and commerce integrations.

## Data/API
Owns `suppliers` and `supplier_ledger_entries`. Ledger entries reference purchases, returns, payments, or adjustments and are append-only.

## Validation
Ledger reconciliation, duplicate supplier names policy, adjustment authorization, cross-store visibility, and supplier deactivation with history retained.
