# Sync Backend Spec

## Purpose
Device registration, event ingestion, idempotency, incremental server sequence feeds, conflict responses, and device revocation.

## Dependencies
`organizations`, `stores`, `users`, `auth`, all transactional modules, `audit`.

## Phases
- MVP: device records, mutation replay envelope, idempotency table, server sequence, pull cursor, outbox integration.
- Stage 2: conflict/repair endpoints and device administration.
- Commerce/AI: media/job upload status without blocking POS.

## Data/API
Owns `devices`, sync event/idempotency records, and sequence/checkpoint metadata. Dispatch validated events to domain commands; never accept raw SQL or arbitrary entity mutation.

## Validation
Duplicate/out-of-order replay, partial batch, cursor gap, unauthorized device, revoked device, crash recovery, and exactly-once business effect tests.
