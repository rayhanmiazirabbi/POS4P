# Medicine Package Spec

## Purpose
Medicine search/display logic, ingredient/unit/barcode models, catalogue matching, and human-readable packaging conversion.

## Dependencies
`types`, `validation`, `core`.

## Phases
- MVP: catalogue search, aliases, barcode lookup, combination ingredients, base-unit conversion, package display.
- Stage 2: fuzzy matching and confirmed supplier aliases.
- Commerce/AI: embedding-assisted matching and invoice/voice normalization.

## Boundaries
Store quantities in base units; retain entered packaging for audit. Support non-medicine pharmacy products. Matching produces candidates and confidence, never silent clinical equivalence.

## Validation
Tablet/strip/box conversions, combination products, barcode collisions, alias matching, malformed strength text, and round-trip display tests.
