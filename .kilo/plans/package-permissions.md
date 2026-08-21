# Permissions Package Spec

## Purpose
Shared role/capability definitions and client-side authorization helpers for owner, manager, cashier, and inventory staff.

## Dependencies
`types`, `api`, `auth`.

## Phases
- MVP: capability matrix, route/action guards, store context, hidden cost/profit restrictions.
- Stage 2: multi-store scope and delegated permissions.
- Commerce/AI: order/prescription and platform-admin capabilities.

## Boundaries
Client guards are presentation only; backend enforcement is mandatory. Default deny unknown capabilities. Record sensitive actions through backend audit, not local checks.

## Validation
Matrix tests for every role/action/scope, direct API denial fixtures, store switching, and stale-session behavior.
