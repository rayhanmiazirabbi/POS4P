# Reports Backend Spec

## Purpose
Owner/store reporting queries, daily sales/profit/payment metrics, expenses, low stock, expiry, exports, and safe reporting tools.

## Dependencies
`organizations`, `stores`, `sales`, `payments`, `inventory`, `purchasing`, `customers`, `orders`, `permissions`, `audit`.

## Phases
- MVP: today metrics, profit, payment breakdown, expenses, low-stock and expiry warnings.
- Stage 2: comparisons, branch rollups, supplier/customer/product analysis.
- AI: fixed-parameter reporting tools, never arbitrary SQL from a model.

## Data/API
Owns report read models/materialized views and expense records if not assigned elsewhere. Use store timezone and explicit as-of timestamps.

## Validation
Reconcile against ledgers, refunds/due handling, day boundaries, role redaction, tenant scope, pagination/export, and query performance.
