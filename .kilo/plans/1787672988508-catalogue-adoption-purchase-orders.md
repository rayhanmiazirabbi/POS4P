# Catalogue-Led Adoption + Purchase Orders

Simplify shop adoption: one search box merges the global medicine catalogue with the org's own products; each row shows its shop status and offers exactly one next action — sell/restock (in shop), adopt to shelf (catalogue only), or add a purchase order (nowhere). Extend `catalog_products` with `generic_name`, `unit_price`, `strip_price`.

## Resolved decisions
1. **Data population**: both manual entry (owner/manager) and a CSV import script (source dataset TBD; generic column-mapping config).
2. **Generic name**: plain indexed `generic_name` text column, searched alongside name/aliases; structured `ActiveIngredient` M2M stays optional/advanced. Backfill from single-ingredient rows.
3. **Prices**: nullable `unit_price` / `strip_price` on `catalog_products` are *reference* prices — used only to prefill sale price on adoption; store `sale_price` always authoritative; no enforced unit↔strip arithmetic (strip size comes from existing `package_size`/`package_unit`).
4. **Purchase orders**: minimal new document; stock still enters via existing `Purchase` confirm transaction (batches/movements/supplier due untouched). Supplier optional. Items may be free-text names (no catalogue entry required).
5. **Permissions**: search = all store roles; sell/stock-update unchanged; adopt-to-shelf + catalogue writes = owner/manager; PO create/edit/mark-ordered/close/cancel = all store roles incl. cashier; convert-to-purchase = owner/manager (creates a Purchase draft; confirming stays manager+).
6. **Frontend**: web-first. Desktop/mobile wiring into unified search = fast-follow, out of scope.

## Tasks (ordered)

### 1. Migration `0011_catalog_adoption_purchase_orders.py` (single migration, linear chain)
- `catalog_products`: add `generic_name` VARCHAR(240) NULL + index `ix_catalog_product_generic_name`; `unit_price`, `strip_price` NUMERIC(18,2) NULL (use `money_column()` helper semantics).
- Backfill in migration: `UPDATE catalog_products SET generic_name = ai.name FROM catalog_product_ingredients cpi JOIN active_ingredients ai ON ai.id = cpi.active_ingredient_id WHERE cpi.catalog_product_id = catalog_products.id AND (SELECT count(*) FROM catalog_product_ingredients x WHERE x.catalog_product_id = catalog_products.id) = 1` — only when exactly one ingredient exists.
- New tables `purchase_orders`, `purchase_order_items` (models below), with FKs, CHECK-free enum-as-VARCHAR via `enum_column()` house style, `UniqueConstraint("organization_id", "idempotency_key")` on POs.

### 2. Models `backend/app/domains/purchase_orders.py`
- `PurchaseOrderStatus(str, Enum)`: `draft | ordered | closed | cancelled`.
- `PurchaseOrder(StoreScopedMixin, UUIDPrimaryKeyMixin, TimestampMixin, Base)` `__tablename__="purchase_orders"`: `supplier_id` UUID NULL FK suppliers.id, `status` default DRAFT, `expected_at` Date NULL, `note` Text NULL, `ordered_at` DateTime(tz) NULL, `closed_at` DateTime(tz) NULL, `cancelled_at` DateTime(tz) NULL, `idempotency_key` String(128) NOT NULL.
- `PurchaseOrderItem(UUIDPrimaryKeyMixin, TimestampMixin, Base)` `__tablename__="purchase_order_items"`: `purchase_order_id` FK NOT NULL, `catalog_product_id` UUID NULL FK, `pharmacy_product_id` UUID NULL FK, `name` String(240) NOT NULL (free-text/snapshot), `quantity` `quantity_column()` NOT NULL, `est_unit_cost` money NULL.
- Re-export from `app/models/__init__.py`.

### 3. Catalog schema/service updates
- `schemas/catalog.py`: add `generic_name` (≤240), `unit_price`, `strip_price` (Decimal|None) to `ProductCreateRequest`, `ProductUpdateRequest`, `CatalogProductResponse`.
- `services/catalog.py`: create/update persist new fields; revision snapshot `data` includes them; `search_products` matches `name ILIKE` **OR** `generic_name ILIKE` (keep existing filters/order params; no pg_trgm yet — Stage 2).
- Router passes fields through (no signature change otherwise).

### 4. Unified search — `GET /products/search`
- In `routers/products.py` declare **before** `/{product_id}` (same class of bug as the documented `/current` incident, see comment at routers/products.py:68). Params: `q` (required, ≤120), `limit` ≤50 default 25, `offset`. Any authenticated store user (`ContextDep`).
- Service (new function in `services/products.py`, imports catalog domain read helpers): 
  - Match org `pharmacy_products` by name/barcode; match `catalog_products` by name/generic_name/barcode(`catalog_barcodes`)/alias(`catalog_aliases`). Exact barcode match sorts first, then alpha.
  - Dedupe: a pharmacy_product linked to a catalog_product renders as ONE row (`kind="catalog"`); unlinked customs render as `kind="custom"`.
  - Per-row `shop_status` relative to current store: `on_shelf` (active store_product) | `in_org` | `absent`.
- Response `CatalogSearchItemResponse`: `kind`, `catalog_product_id?`, `pharmacy_product_id?`, `store_product_id?`, `name`, `barcode?`, `generic_name?`, `strength?`, `dosage_form_id?/dosage_form?`, `manufacturer_id?/manufacturer?`, `package_size`, `package_unit`, `prescription_required`, `reference_unit_price?`, `reference_strip_price?` (null for custom), `sale_price?`, `available_quantity?`, `sku?` (when on_shelf).

### 5. Adoption — `POST /products/adopt` (owner/manager; declared before `/{product_id}`)
- Body: `catalog_product_id` (required), `store_id` optional (default current pinned store), `sku?`, `sale_price?` (money string), `rack?`, `minimum_stock?`.
- Service logic: find existing org pharmacy_product by `catalog_product_id` (any active state) → reuse/reactivate; else create with `name`, `unit=package_unit`, barcode auto-filled from first `catalog_barcodes` row **only if** org barcode free (catch IntegrityError → skip fill, never fail adoption). Then find-or-create store_product: `sale_price` = provided value else catalog `unit_price`; if neither → 400 with clear message. SKU = provided else deterministic generated (normalized name+strength prefix, collision-safe suffix) — uniqueness per store enforced.
- Audit `product.adopted` (+ outbox parity with other product writes). Response: composite `{pharmacy_product, store_product}`.
- Tests: fresh adopt; second adopt reuses/reactivates; missing-price rejection; cashier 403; concurrent-barcode edge.

### 6. Purchase-order service + router
- `services/purchase_orders.py`: create/list/get/patch-items (items editable only while `draft`), transitions `draft→ordered→closed`, `draft|ordered→cancelled` (invalid → 409), `record_audit` (`purchase_order.created|updated|ordered|closed|cancelled`) + `enqueue_outbox` parity with `services/purchasing.py`.
- `routers/purchase_orders.py`, prefix `/purchase-orders`, mounted in `main.py`:
  - `POST ""` (requires `IdempotencyKeyDep`, unique `(organization_id, key)`), `GET ""` (?status= filter), `GET /{id}`, `POST /{id}/items`, `PATCH /{id}/items/{item_id}`, `DELETE /{id}/items/{item_id}`, `POST /{id}/order`, `POST /{id}/close`, `POST /{id}/cancel` — all store roles (per decision 5).
  - `POST /{id}/to-purchase` (**owner/manager**): resolve each item → store_product at that store via `pharmacy_product_id`, else `catalog_product_id → pharmacy_product → active store_product`. Creates a `Purchase` draft (status=draft, `note="From PO {short_id}"`) reusing `services/purchasing` creation; `batch_number="PENDING"` placeholder (model field is NOT NULL; real batches supplied at confirm — implementer must verify confirm replaces item-level batch data, adjust if confirm ignores item batches). Unresolvable lines skipped and returned in response as `skipped[]`; PO auto-transitions to `closed` if ≥1 line converted. 409 if nothing resolvable.
- Conventions: envelope `{data, requestId}` everywhere; follow `test_api_conventions`.

### 7. CSV import script
- `backend/scripts/import_catalog_csv.py` + `backend/scripts/catalog_import.example.json` (column-mapping config: headers → fields incl. barcodes; configurable country_code default).
- Upserts Manufacturer/DosageForm/ActiveIngredient (from generic_name)/CatalogProduct (+`catalog_barcodes` when mapped). Dedupe priority: barcode → (lower(name), lower(strength), dosage_form, country_code). Creates `CatalogRevision` rows; `--dry-run` prints summary. Run via `uv run python scripts/import_catalog_csv.py ...` from `backend/`.
- Pytest with tiny fixture CSV: insert-new + update-existing + dedupe-by-barcode paths.

### 8. Web frontend
- `packages/api/src/resources.ts`: types `CatalogSearchItem`, `AdoptPayload/AdoptResult`, `PurchaseOrder`, `PurchaseOrderItem`, `PoConvertResult`; client groups: `products.search`, `products.adopt`, new `purchaseOrders` factory wired into `createPharmacyApi`.
- Rebuild `apps/app-web/app/(pos)/catalogue/page.tsx` around unified search:
  - Debounced search box → merged rows: `name · generic · strength · form · manufacturer · ৳ref unit/strip` + status badge + primary action:
    - `on_shelf`: show price/stock; buttons → POS page ("Sell") and inventory receive ("Add stock").
    - `in_org`: "Add to shelf" → `POST /products/current` prefilled (SKU/price editable inline).
    - `absent` catalog row: "Adopt" → `POST /products/adopt` (price prefilled from `unit_price`, editable).
    - `absent` custom/none: owner/manager sees extended create-entry form (all new catalogue fields) then adopt; everyone sees "Add purchase order".
  - Every non-shelf row gets an "Add to PO" affordance: choose open draft PO or create one inline; appends item (qty input; free-text rows keep their label).
  - Keep legacy manual forms in an "Advanced" section; extend with new catalogue fields.
- `apps/app-web/app/(pos)/purchasing/page.tsx`: add Purchase Orders section — list + status filter, create dialog (supplier optional, expected date, note), detail view w/ item editor, order/close/cancel, "Convert to purchase draft" showing skipped lines.
- Role-gating via `packages/permissions` capability matrix (add e.g. `purchasing.orders.manage`, `products.adopt` mappings consistent with decision 5).

### 9. Validation
- Backend: `python -m pytest backend/tests` — new tests: migration up/down; catalog generic-name search + field round-trip + backfill; unified-search merge/dedupe/status/barcode-rank; adopt matrix; PO lifecycle/permissions/409s/convert resolution+skips/idempotency; import fixture; conventions pass.
- Frontend: `pnpm check && pnpm test` at root.
- Manual smoke (dev servers): search known-absent medicine → create catalogue entry → adopt → receive batch → sell; search unknown term → add PO as cashier → convert as manager → confirm purchase.

## Risks / notes for implementer
- Route ordering in `routers/products.py`: `/search` and `/adopt` MUST precede `/{product_id}`.
- `PurchaseItem.batch_number` NOT NULL forces the `"PENDING"` placeholder in conversion; confirm `confirm_purchase` behavior around item batches before relying on it.
- Adoption barcode auto-fill races org-wide unique-active-barcode constraint — treat as best-effort.
- Import script writes shared global data — document ops-only usage in module docstring.
- No offline/offline-queue support for these flows; online-first by design.

## Explicitly out of scope
Desktop/mobile unified search; full PO receiving/partial deliveries; authoritative global pricing; moderation/search-ranking/pg_trgm; catalogue seeding dataset itself (importer only, source TBD).
