"""Fetch and compile Bangladesh's official DGDA medicine registry.

The Directorate General of Health Services publishes the DGDA registered-drug
value set through its national Open Concept Lab (OCL) terminology server. This
is the preferred source for the Bangladesh slice of the global catalogue: it is
an official, public API and does not require scraping a third-party directory.

From ``backend/``::

    PYTHONPATH=. uv run python scripts/dgda_catalog.py fetch
    PYTHONPATH=. uv run python scripts/dgda_catalog.py compile
    PYTHONPATH=. uv run python scripts/import_catalog_csv.py \
        --csv var/dgda/dgda_catalog.csv --config scripts/dgda_import.json

The fetch is append-only and resumable. Use ``--max-records`` for a small
validation pass. The official feed does not currently expose retail prices,
package sizes, barcodes, or prescription status, so those importer columns are
left blank (package size/unit use the catalogue defaults).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

API_BASE = "https://api.tr.ocl.dghs.gov.bd"
COLLECTION_PATH = "/orgs/MoHFW/collections/dgda-registered-drugs-valueset/concepts/"
DEFAULT_USER_AGENT = "POS4P-catalogue-importer/0.1 (official DGDA public registry)"
RETRY_STATUS = frozenset({408, 429, 500, 502, 503, 504})
GENERIC_STRENGTH_SEPARATOR = re.compile(r"\s{2,}(?=(?:\d|\.\d))")

CSV_HEADERS = [
    "Brand Id",
    "Registry Id",
    "Product Name",
    "Generic Name",
    "Strength",
    "Dosage Form",
    "Manufacturer",
    "Pack Size",
    "Package Unit",
    "Unit Price",
    "Strip Price",
    "Rx",
    "Source Url",
]


class RegistryUnavailable(RuntimeError):
    """Raised when the official registry cannot be read after bounded retries."""


def split_generic_content(value: str | None) -> tuple[str, str]:
    """Split DGDA's ``generic_content_raw`` into generic name and strength.

    The registry separates the two fields with repeated whitespace, for
    example ``"Amphotericin B  50 mg/vial"``. We deliberately keep ambiguous
    values intact as the generic name rather than inventing a strength.
    """
    cleaned = " ".join((value or "").split())
    if not value:
        return "", ""
    parts = GENERIC_STRENGTH_SEPARATOR.split(value.strip(), maxsplit=1)
    if len(parts) != 2:
        return cleaned, ""
    return " ".join(parts[0].split()), " ".join(parts[1].split())


def concept_to_csv_row(concept: dict[str, Any]) -> dict[str, str]:
    extras = concept.get("extras") if isinstance(concept.get("extras"), dict) else {}
    generic, strength = split_generic_content(extras.get("generic_content_raw"))
    concept_path = str(concept.get("url") or "")
    source_url = f"{API_BASE}{concept_path}" if concept_path.startswith("/") else ""
    return {
        "Brand Id": str(extras.get("dar_number") or concept.get("id") or ""),
        "Registry Id": str(concept.get("id") or ""),
        "Product Name": str(
            extras.get("trade_name") or concept.get("display_name") or ""
        ).strip(),
        "Generic Name": generic,
        "Strength": strength,
        "Dosage Form": str(extras.get("dosage_form") or "").strip(),
        "Manufacturer": str(extras.get("company") or "").strip(),
        "Pack Size": "1",
        "Package Unit": "piece",
        "Unit Price": "",
        "Strip Price": "",
        "Rx": "",
        "Source Url": source_url,
    }


def load_seen_ids(raw_path: Path) -> set[str]:
    seen: set[str] = set()
    if not raw_path.exists():
        return seen
    with raw_path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                concept = json.loads(line)
                concept_id = str(concept["id"])
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            seen.add(concept_id)
    return seen


def load_state(state_path: Path) -> dict[str, int]:
    if not state_path.exists():
        return {"next_page": 1}
    try:
        with state_path.open(encoding="utf-8") as handle:
            state = json.load(handle)
        return {"next_page": max(1, int(state["next_page"]))}
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return {"next_page": 1}


def save_state_atomic(state_path: Path, state: dict[str, int]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(state, handle)
    os.replace(temporary, state_path)


async def fetch_page(
    client: httpx.AsyncClient, page: int, page_size: int
) -> tuple[list[dict[str, Any]], int]:
    url = f"{API_BASE}{COLLECTION_PATH}"
    for attempt in range(5):
        try:
            response = await client.get(
                url,
                params={"page": page, "limit": page_size, "verbose": "true"},
            )
        except httpx.HTTPError as exc:
            if attempt == 4:
                raise RegistryUnavailable(str(exc)) from exc
            await asyncio.sleep(1.5**attempt + random.uniform(0, 0.3))
            continue
        if response.status_code == 200:
            payload = response.json()
            if not isinstance(payload, list):
                raise RegistryUnavailable("DGDA API returned an unexpected payload")
            return payload, int(response.headers.get("num_found", len(payload)))
        if response.status_code not in RETRY_STATUS:
            raise RegistryUnavailable(f"DGDA API returned HTTP {response.status_code}")
        retry_after = min(float(response.headers.get("Retry-After", 0) or 0), 60.0)
        await asyncio.sleep(retry_after or 1.5**attempt + random.uniform(0, 0.3))
    raise RegistryUnavailable("DGDA API retries exhausted")


async def fetch_registry(args: argparse.Namespace) -> None:
    raw_path: Path = args.raw
    state_path: Path = args.state
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    seen = load_seen_ids(raw_path)
    page = load_state(state_path)["next_page"]
    added = 0
    total = 0

    async with httpx.AsyncClient(
        headers={"User-Agent": args.user_agent, "Accept": "application/json"},
        timeout=args.timeout,
        follow_redirects=True,
    ) as client:
        with raw_path.open("a", encoding="utf-8") as sink:
            while True:
                concepts, total = await fetch_page(client, page, args.page_size)
                if not concepts:
                    break
                for concept in concepts:
                    concept_id = str(concept.get("id") or "")
                    if not concept_id or concept_id in seen:
                        continue
                    sink.write(json.dumps(concept, ensure_ascii=False) + "\n")
                    seen.add(concept_id)
                    added += 1
                    if args.max_records and added >= args.max_records:
                        sink.flush()
                        os.fsync(sink.fileno())
                        print(
                            f"validation limit reached: added {added}; "
                            f"{len(seen)}/{total or '?'} concepts cached"
                        )
                        return
                sink.flush()
                os.fsync(sink.fileno())
                page += 1
                save_state_atomic(state_path, {"next_page": page})
                print(f"page {page - 1}: {len(seen)}/{total} concepts cached")
                if len(concepts) < args.page_size or len(seen) >= total:
                    break
                await asyncio.sleep(args.delay)

    print(f"fetch complete: added {added}; {len(seen)}/{total} concepts -> {raw_path}")


def compile_registry(raw_path: Path, csv_path: Path, report_path: Path) -> dict[str, Any]:
    by_id: dict[str, dict[str, Any]] = {}
    invalid_lines = 0
    duplicates = 0
    with raw_path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                concept = json.loads(line)
                concept_id = str(concept["id"])
            except (json.JSONDecodeError, KeyError, TypeError):
                invalid_lines += 1
                continue
            duplicates += concept_id in by_id
            by_id[concept_id] = concept

    rows = [concept_to_csv_row(concept) for concept in by_id.values()]
    rows.sort(key=lambda row: (row["Product Name"].casefold(), row["Brand Id"]))
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_HEADERS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    fill = {field: sum(bool(row[field]) for row in rows) for field in CSV_HEADERS}
    missing_product_names = [row["Brand Id"] for row in rows if not row["Product Name"]]
    report: dict[str, Any] = {
        "source": f"{API_BASE}{COLLECTION_PATH}",
        "rows": len(rows),
        "importable_rows": len(rows) - len(missing_product_names),
        "missing_product_names": missing_product_names,
        "invalid_lines": invalid_lines,
        "duplicate_concept_ids": duplicates,
        "fill": fill,
        "dosage_forms": dict(Counter(row["Dosage Form"] for row in rows)),
        "manufacturers": len({row["Manufacturer"] for row in rows if row["Manufacturer"]}),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser("fetch", help="cache official DGDA concepts")
    fetch_parser.add_argument("--raw", type=Path, default=Path("var/dgda/dgda_raw.jsonl"))
    fetch_parser.add_argument("--state", type=Path, default=Path("var/dgda/dgda_state.json"))
    fetch_parser.add_argument("--page-size", type=int, default=500)
    fetch_parser.add_argument("--max-records", type=int, default=0)
    fetch_parser.add_argument("--delay", type=float, default=0.1)
    fetch_parser.add_argument("--timeout", type=float, default=30.0)
    fetch_parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)

    compile_parser = subparsers.add_parser("compile", help="write importer-ready CSV")
    compile_parser.add_argument("--raw", type=Path, default=Path("var/dgda/dgda_raw.jsonl"))
    compile_parser.add_argument("--csv", type=Path, default=Path("var/dgda/dgda_catalog.csv"))
    compile_parser.add_argument(
        "--report", type=Path, default=Path("var/dgda/dgda_report.json")
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "fetch":
        asyncio.run(fetch_registry(args))
        return
    report = compile_registry(args.raw, args.csv, args.report)
    print(
        f"{report['rows']} official DGDA rows -> {args.csv}; "
        f"manufacturers={report['manufacturers']}, invalid_lines={report['invalid_lines']}"
    )
    print(f"report -> {args.report}")


if __name__ == "__main__":
    main()
