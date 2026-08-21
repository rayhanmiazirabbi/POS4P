# Audit Backend Spec

## Purpose
Tamper-evident audit trail for sensitive business and administrative actions, actor/device context, before/after summaries, and retention/export.

## Dependencies
`organizations`, `users`, `stores`, `auth`, every state-changing module.

## Phases
- MVP: sale void/return, discount, price, stock, purchase, supplier balance, permission, and subscription audit events.
- Stage 2: searchable owner audit view and export.
- Commerce/AI: prescription decisions, order state, AI actions and confirmations.

## Data/API
Owns append-only `audit_logs` with organization/store/user/device, action, entity, safe metadata, request/event ID, and timestamp. Exclude secrets and prescription contents.

## Validation
Append-only enforcement, tenant isolation, actor spoof prevention, redaction, event correlation, clock handling, and retention policy tests.
