# Catalog Backend Spec

## Purpose
Shared medicine catalogue, ingredients, dosage forms, units, barcodes, aliases, revisions, search, and country-specific metadata.

## Dependencies
`organizations` only for admin scope; `products`, `medicine` clients; PostgreSQL `pg_trgm`, `unaccent`, optional `vector`.

## Phases
- MVP: catalogue tables/search/barcode/ingredient combinations and revision history.
- Stage 2: aliases, import tooling, moderation and search ranking.
- AI: embeddings and confirmed supplier-text mappings.

## Data/API
Owns `manufacturers`, `active_ingredients`, `dosage_forms`, `catalog_products`, unit/barcode/alias/revision tables. Catalogue data is shared reference data; pharmacy edits create local product settings, not copies.

## Validation
Duplicate barcodes, combination ingredients, revision audit, fuzzy search, unaccented text, country/status filters, and tenant-safe admin writes.
