# DGDA catalogue release

This directory contains the deployable snapshot of Bangladesh's official DGDA
registered-drug catalogue.

- `dgda_catalog.csv.gz`: importer-ready compressed catalogue
- `dgda_catalog.manifest.json`: source, checksums, row counts, and generation time

The release contains DAR number, unique OCL registry concept ID, trade name,
generic composition, strength, dosage form, manufacturer, and official source
URL. The DGDA registry does not provide retail prices, barcodes, package sizes,
or prescription classifications, so those values remain unknown/defaulted.

## Deploy or seed a database

From `backend/`:

```sh
PYTHONPATH=. uv run alembic upgrade head
PYTHONPATH=. uv run python scripts/dgda_release.py install
```

The release is checksum-verified before import. Stable DGDA concept references
make subsequent installs update the same catalogue products instead of relying
only on names.

## Refresh the release

Fetch the latest official registry, compile it, and atomically replace both
tracked release files:

```sh
PYTHONPATH=. uv run python scripts/dgda_release.py update
```

Refresh the release and update the configured database in one command:

```sh
PYTHONPATH=. uv run python scripts/dgda_release.py update --install
```

Generated raw records and working CSV files use `backend/var/` and remain
gitignored. Only the compressed release and its manifest belong in Git.

## Official source

The data comes from the Ministry of Health and Family Welfare's public DGDA
collection on the national OCL terminology server:

`https://api.tr.ocl.dghs.gov.bd/orgs/MoHFW/collections/dgda-registered-drugs-valueset/concepts/`
