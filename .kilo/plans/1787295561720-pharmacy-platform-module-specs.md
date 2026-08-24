# Pharmacy Platform Module Specs

This index defines the implementation order and shared rules for the 41 module plans in this directory. The target is a phased modular monolith with three clients, shared TypeScript contracts, FastAPI, and PostgreSQL. The detailed module boundaries are intentionally stable across phases; later phases add capabilities without creating a second product model.

## Module Set

### Frontend applications

- `app-mobile.md`
- `app-web.md`
- `app-desktop.md`

### Shared frontend packages

- `package-core.md`
- `package-api.md`
- `package-types.md`
- `package-validation.md`
- `package-auth.md`
- `package-permissions.md`
- `package-medicine.md`
- `package-inventory.md`
- `package-sales.md`
- `package-purchasing.md`
- `package-customers.md`
- `package-loyalty.md`
- `package-orders.md`
- `package-reports.md`
- `package-sync.md`
- `package-money.md`
- `package-design-tokens.md`

### Backend modules

- `backend-auth.md`
- `backend-organizations.md`
- `backend-stores.md`
- `backend-users.md`
- `backend-catalog.md`
- `backend-products.md`
- `backend-inventory.md`
- `backend-purchasing.md`
- `backend-suppliers.md`
- `backend-sales.md`
- `backend-payments.md`
- `backend-customers.md`
- `backend-loyalty.md`
- `backend-ecommerce.md`
- `backend-orders.md`
- `backend-prescriptions.md`
- `backend-reports.md`
- `backend-sync.md`
- `backend-ai.md`
- `backend-billing.md`
- `backend-audit.md`

## Cross-cutting rules

1. Every organization-owned record includes `organization_id`; store-scoped records also include `store_id`. Authorization must verify both database scope and request context.
2. PostgreSQL is the source of truth. Inventory, loyalty, supplier, and audit ledgers are append-only; balance/read-model tables are rebuildable projections.
3. UUIDv7 identifiers, UTC timestamps, decimal money values, normalized phone numbers, and idempotency keys are mandatory for externally created transactional records.
4. Frontends share types, validation, business calculations, API contracts, and design tokens, but not platform-specific visual components.
5. Offline POS mutations are queued with `event_id`, `device_id`, `organization_id`, `store_id`, `user_id`, `event_type`, `created_at`, and `client_sequence`. Server replay must be idempotent.
6. All state-changing endpoints emit audit/outbox events where applicable. External side effects run after commit through the outbox worker.
7. Prescription and AI assistance never bypass pharmacist or staff confirmation requirements.

## Delivery order

1. [x] Foundation: organizations, stores, users, auth, types, validation, money, permissions, API, core, design tokens.
2. [x] Catalogue and stock: catalog, products, medicine, inventory, purchasing, suppliers, inventory/purchasing/sales frontend packages.
3. POS: payments, sales, customers, reports, all three application shells, sync.
4. [x] Retention and multi-store: loyalty, customer history, transfers, richer reports, device management.
5. [x] Commerce: ecommerce, orders, prescriptions, online checkout and listings.
6. [x] Automation and platform: AI, billing, audit hardening, OCR, anomaly detection, supplier-network workflows.

## Required integration checks

- A guest POS sale can complete without a customer and can be replayed once from an offline client.
- A purchase creates batches, ledger movements, balances, supplier liability, and audit/outbox records in one transaction.
- FEFO allocation, return allocation, reservation release, and concurrent stock changes never make available stock negative.
- A store product can be enabled online without copying catalogue or POS product data.
- Tenant and role isolation is tested on every module endpoint, including direct object-ID access.
- Rebuilding projections from ledgers produces the same balances and loyalty totals as incremental processing.
