# Products Backend Spec

## Purpose
Organization pharmacy products, store-specific products, pricing, SKU, rack, online eligibility, and catalogue linkage.

## Dependencies
`organizations`, `stores`, `catalog`, `suppliers`, `audit`, `ecommerce`.

## Phases
- MVP: custom/catalogue-linked products, store prices, SKU/barcode, minimum stock, active state.
- Stage 2: branch overrides, bulk import, preferred suppliers.
- Commerce: online settings reference store products without duplication.

## Data/API
Owns `pharmacy_products` and `store_products`. `catalog_product_id` is nullable. Enforce organization/store ownership and unique active SKU/barcode policies.

## Validation
Cross-tenant linkage denial, price history, branch overrides, product deactivation with historical sales, and online toggle behavior.
