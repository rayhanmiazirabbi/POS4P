# Sync Package Spec

## Purpose
Shared offline outbox, cursor state, conflict/error states, device metadata, replay ordering, and connectivity lifecycle.

## Dependencies
`core`, `types`, `validation`, `api`; SQLite or Dexie adapter supplied by each app.

## Phases
- MVP: transactional local outbox, upload idempotency, incremental download cursor, retry/backoff, sync status.
- Stage 2: conflict resolution UI, device revocation, repair/rebuild flow.
- Commerce/AI: background upload of media/jobs without blocking POS.

## Boundaries
Never mark an event synced before server acknowledgment. Preserve failed events and actionable reasons. Server sequence, not client time, orders remote changes.

## Validation
Offline/reconnect, duplicate upload, out-of-order events, cursor gaps, revoked device, crash during enqueue, and projection conflict tests.
