# Users Backend Spec

## Purpose
Staff identities, organization/store memberships, roles, invitations, activation, and user profile state.

## Dependencies
`organizations`, `stores`, `auth`, `permissions`, `audit`.

## Phases
- MVP: owner bootstrap, cashier/manager/inventory staff, PIN setup/reset, membership and role changes.
- Stage 2: multi-store memberships and invitations.
- Commerce: customer accounts remain in `customers`, not staff users.

## Data/API
Owns `users`, `organization_users`, `store_users`, roles/permissions references. Prevent removal/demotion of the final organization owner without replacement.

## Validation
Role enforcement, membership races, inactive user sessions, last-owner protection, store scope, and audit coverage.
