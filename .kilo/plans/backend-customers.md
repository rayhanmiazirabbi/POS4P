# Customers Backend Spec

## Purpose
Organization customers, normalized contact identity, optional POS association, due balance, purchase history, preferences, and ecommerce accounts.

## Dependencies
`organizations`, `stores`, `auth`, `sales`, `payments`, `loyalty`, `orders`, `audit`.

## Phases
- MVP: customer CRUD/search by normalized phone, optional sale linkage, due/history summaries.
- Stage 2: cross-store profile and consent/preferences.
- Commerce: OTP accounts, addresses, order history, saved checkout details.

## Data/API
Owns `customers`, addresses, and account linkage. Guest transactions use null customer IDs. Apply retention/privacy policies without breaking financial records.

## Validation
Phone race/merge policy, guest conversion, cross-tenant denial, sensitive history permissions, account OTP separation, and due reconciliation.
