# Inventory Package Spec

## Purpose
Shared inventory calculations, FEFO allocation, available-stock math, reservations, movements, adjustments, and transfer models.

## Dependencies
`types`, `core`, `medicine`, `money`.

## Phases
- MVP: on-hand/reserved/available calculations, batch selection, sale/purchase/return/adjustment movement commands.
- Stage 2: branch transfers and projection rebuild tooling.
- Commerce/AI: order reservations, expiry forecasting inputs, reorder inputs.

## Boundaries
Ledger is truth; balances are projections. FEFO uses expiry then deterministic tie-breakers. Allocation must be atomic server-side and represent partial/failed allocation explicitly.

## Validation
Concurrent sale/reservation cases, expired batches, returns, negative adjustment permissions, projection rebuild equality, and unit conversion integration tests.
