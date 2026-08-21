# Purchasing Package Spec

## Purpose
Purchase draft/confirmation models, supplier item mapping, batch receipt data, and purchase-return workflows.

## Dependencies
`types`, `money`, `medicine`, `inventory`, `suppliers` API contracts, `validation`.

## Phases
- MVP: supplier purchase entry, items, batch/expiry capture, totals, due tracking, purchase return.
- Stage 2: transfer-aware purchasing and OCR review UI.
- Commerce/AI: extracted invoice confirmation and reorder-to-purchase flow.

## Boundaries
Confirmation is transactional and idempotent. Drafts do not affect stock. Preserve supplier text alongside matched product IDs.

## Validation
Partial/multiple batches, invalid expiry, price/MRP boundaries, retry confirmation, supplier balance reconciliation, and OCR correction fixtures.
