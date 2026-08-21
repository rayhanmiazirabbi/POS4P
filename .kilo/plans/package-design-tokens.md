# Design Tokens Package Spec

## Purpose
Cross-platform color, typography, spacing, elevation, motion, density, and semantic status tokens.

## Dependencies
None.

## Phases
- MVP: cashier-readable light theme, status colors, touch/keyboard density, responsive breakpoints.
- Stage 2: dark mode and owner/dashboard density variants.
- Commerce/AI: public storefront branding and pharmacy theme overrides.

## Boundaries
Tokens are platform-neutral values exported to React Native, web, and desktop. Components remain app-specific. Maintain contrast and non-color status cues.

## Validation
Web/native snapshot checks, WCAG contrast, small-screen readability, printer-independent receipt styling, and theme override isolation.
