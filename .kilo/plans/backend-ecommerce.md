# Ecommerce Backend Spec

## Purpose
Online listing settings, pharmacy storefront identity, visibility, pricing, pickup/delivery options, domains, and public catalogue projection.

## Dependencies
`organizations`, `stores`, `products`, `orders`, `customers`, `prescriptions`, `billing`, `audit`.

## Phases
- Commerce: platform subdomain, listing enablement, online name/description/price/images, pickup/delivery flags.
- Later: custom domain, branding, SEO/search projection, delivery integrations.

## Data/API
Owns `ecommerce_product_settings` and storefront/domain settings. Online reads resolve active store products; no copied product database. Respect prescription-required flags.

## Validation
Listing deactivation, stale price/stock, store routing, slug uniqueness, tenant/domain isolation, and public/private field separation.
