# Pharmacy Platform

Pharmacy point-of-sale and operations platform built as a phased modular monolith.

## Repository layout

- `packages/*`: platform-neutral TypeScript contracts and business primitives.
- `apps/*`: web, mobile, and desktop application shells.
- `backend/*`: FastAPI modular monolith and database migrations.

PostgreSQL is authoritative for transactional data. Client applications may maintain
offline projections and outboxes, but server replay is idempotent and tenant-scoped.

## Development

```sh
pnpm install
pnpm check
pnpm test
python -m pytest backend/tests
```

The initial foundation uses BDT for money, UTC timestamps, UUIDv7 identifiers, and
explicit organization/store request context. Feature modules must preserve these rules.
