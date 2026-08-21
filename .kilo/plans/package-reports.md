# Reports Package Spec

## Purpose
Typed report filters, KPI models, chart/table formatting, export requests, and owner dashboard calculations.

## Dependencies
`types`, `money`, `api`, `permissions`.

## Phases
- MVP: today sales, profit, payment breakdown, expenses, low stock, expiry warnings.
- Stage 2: comparisons, branch rollups, product/customer/supplier reports.
- Commerce/AI: order funnel and safe natural-language reporting tool outputs.

## Boundaries
Reports use authoritative transaction/projection data and respect user/store scope. Profit must not expose purchase cost to cashier roles.

## Validation
Timezone/day-boundary, refunds/due payments, branch filters, permission redaction, large result pagination, and export consistency.
