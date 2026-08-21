# Organizations Backend Spec

## Purpose
Tenant lifecycle, organization profile, subscription association, isolation context, and organization-level settings.

## Dependencies
`auth`, `billing`, `audit`.

## Phases
- MVP: create organization, owner membership, profile/settings, current organization context.
- Stage 2: multi-store configuration and organization-wide policies.
- Commerce/AI: storefront branding and feature flags.

## Data/API
Owns `organizations` and tenant settings. All downstream services receive a validated organization context, never a caller-provided unrestricted tenant ID.

## Validation
Cross-tenant access denial, owner bootstrap transaction, suspended subscription behavior, settings defaults, and deletion/retention policy.
