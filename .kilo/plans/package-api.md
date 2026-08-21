# API Package Spec

## Purpose
Typed FastAPI client, request envelopes, pagination, idempotency, auth headers, error decoding, retries, and sync transport.

## Dependencies
`core`, `types`, `validation`; platform adapters supply storage and connectivity.

## Phases
- MVP: typed CRUD/transaction clients, bearer/session handling, idempotency header, structured errors, timeout policy.
- Stage 2: incremental sync and upload/download cursors.
- Commerce/AI: upload clients, order/prescription endpoints, streaming/progress where needed.

## Boundaries
Never retry non-idempotent requests without an idempotency key. Preserve server error codes. Do not embed business decisions or UI notifications.

## Validation
Mock contract tests against OpenAPI fixtures, timeout/retry tests, duplicate mutation behavior, malformed error handling, and cursor persistence.
