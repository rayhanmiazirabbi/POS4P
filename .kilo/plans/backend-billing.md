# Billing Backend Spec

## Purpose
Platform subscription plans, entitlements, billing state, invoices, payment-provider integration, and feature gating.

## Dependencies
`organizations`, `auth`, `audit`, `payments` provider abstractions, outbox worker.

## Phases
- MVP: plan/entitlement model and manual or disabled billing gate suitable for launch.
- Stage 2: subscription lifecycle, invoices, grace periods, owner billing view.
- Commerce: enforce storefront/custom-domain and advanced feature entitlements.

## Data/API
Owns plans, organization subscriptions, entitlements, invoices, and provider event records. Provider webhooks are signature-checked and idempotent.

## Validation
Trial/active/past-due/cancelled states, webhook replay, grace period, tenant feature gating, invoice immutability, and owner-only access.
