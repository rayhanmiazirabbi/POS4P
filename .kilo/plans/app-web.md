# Web App Spec

## Purpose
Next.js 16 client for browser POS, owner dashboard, platform administration, ecommerce storefront, and public pharmacy pages.

## Dependencies
Shared packages plus `Dexie`/IndexedDB for browser POS persistence. Use server-rendered public/catalogue pages where useful and client data fetching for authenticated operational screens.

## Phases
- MVP: responsive browser POS, owner metrics, catalogue/product administration, inventory and purchasing screens, role-aware navigation, IndexedDB offline queue.
- Stage 2: multi-store dashboards, loyalty, reports, notifications, platform administration.
- Commerce/AI: pharmacy storefront, guest checkout, accounts, order tracking, prescription upload, AI-assisted workflows.

## Boundaries
Route groups separate public, authenticated, POS, and platform-admin surfaces. TanStack Query owns API cache; Zustand owns transient UI/session state; Zod validates forms and API payloads. Never put tenant authorization in route visibility alone.

## Validation
Test desktop and mobile browsers, offline/reconnect transitions, refresh during a pending mutation, keyboard POS operation, SSR/auth boundaries, tenant URL isolation, and printer/browser-print fallback.
