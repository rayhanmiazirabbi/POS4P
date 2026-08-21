# Core Package Spec

## Purpose
Platform-neutral domain primitives, result/error conventions, identifiers, dates, event metadata, and shared invariants.

## Dependencies
Minimal runtime dependencies; consumed by every other package.

## Phases
- MVP: UUIDv7/event metadata types, normalized identifiers, domain error taxonomy, quantity and timestamp helpers.
- Stage 2: pagination/filter primitives and projection rebuild contracts.
- Commerce/AI: tool result envelopes and workflow state primitives.

## Boundaries
No UI, network, storage, or platform imports. Keep helpers deterministic and serializable. Validation belongs in `validation`; monetary arithmetic belongs in `money`.

## Validation
Cross-runtime unit tests, serialization round trips, invalid identifier rejection, UTC normalization, and no platform-specific imports.
