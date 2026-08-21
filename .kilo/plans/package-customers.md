# Customers Package Spec

## Purpose
Customer lookup, normalization, optional association with sales, due summaries, history, and account UX shared by POS/ecommerce.

## Dependencies
`types`, `api`, `validation`, `core`.

## Phases
- MVP: phone search/create, optional sale association, due balance and history summary.
- Stage 2: richer profiles, multi-store history, consent/preferences.
- Commerce/AI: OTP account, addresses, saved preferences, order history.

## Boundaries
Guest sale remains first-class. Enforce organization scope and phone uniqueness policy server-side. Minimize exposed purchase data by role and customer consent.

## Validation
Phone normalization, duplicate races, guest checkout conversion, cross-store visibility, deletion/privacy rules, and due reconciliation.
