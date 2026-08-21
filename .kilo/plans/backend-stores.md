# Stores Backend Spec

## Purpose
Store creation, branch settings, operating status, timezone, currency, inventory scope, and staff store assignment.

## Dependencies
`organizations`, `users`, `permissions`, `audit`.

## Phases
- MVP: one store per organization supported, store profile/timezone/currency.
- Stage 2: multiple stores, transfers, branch-specific settings and prices.
- Commerce: pickup/delivery service area and storefront routing.

## Data/API
Owns `stores` and store settings. Store context is checked against membership on every scoped operation. Store timezone controls business-day reports.

## Validation
Organization isolation, inactive store behavior, timezone boundaries, branch access, and safe store switching.
