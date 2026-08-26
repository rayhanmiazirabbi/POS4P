"""Package, install, or refresh the official DGDA catalogue release.

Run from ``backend/``::

    PYTHONPATH=. uv run python scripts/dgda_release.py install
    PYTHONPATH=. uv run python scripts/dgda_release.py update --install

``install`` verifies and imports the tracked compressed snapshot. ``update``
downloads the current official registry into a temporary workspace, compiles
it, replaces the release artifact and manifest atomically, and optionally
imports the refreshed data into the configured database.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.db import get_session
from scripts.dgda_catalog import (
    API_BASE,
    COLLECTION_PATH,
    DEFAULT_USER_AGENT,
    compile_registry,
    fetch_registry,
)
from scripts.import_catalog_csv import import_rows, load_config, parse_rows

DEFAULT_ARTIFACT = Path("data/dgda_catalog.csv.gz")
DEFAULT_MANIFEST = Path("data/dgda_catalog.manifest.json")
DEFAULT_CONFIG = Path("scripts/dgda_import.json")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_release(
    csv_path: Path,
    report_path: Path,
    artifact_path: Path = DEFAULT_ARTIFACT,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> dict[str, Any]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_artifact = artifact_path.with_suffix(artifact_path.suffix + ".tmp")
    with (
        csv_path.open("rb") as source,
        temporary_artifact.open("wb") as destination,
        gzip.GzipFile(
            filename="dgda_catalog.csv",
            mode="wb",
            fileobj=destination,
            mtime=0,
        ) as compressed,
    ):
        shutil.copyfileobj(source, compressed)
    os.replace(temporary_artifact, artifact_path)

    manifest: dict[str, Any] = {
        "schemaVersion": 1,
        "source": f"{API_BASE}{COLLECTION_PATH}",
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "artifact": artifact_path.name,
        "compression": "gzip",
        "sha256": sha256_file(artifact_path),
        "uncompressedSha256": sha256_file(csv_path),
        "registryRows": int(report["rows"]),
        "importableRows": int(report["importable_rows"]),
        "missingProductNames": report.get("missing_product_names", []),
        "manufacturers": int(report["manufacturers"]),
    }
    temporary_manifest = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    temporary_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary_manifest, manifest_path)
    return manifest


def unpack_verified_release(
    artifact_path: Path,
    manifest_path: Path,
    destination: Path,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual_artifact_hash = sha256_file(artifact_path)
    if actual_artifact_hash != manifest.get("sha256"):
        raise ValueError("DGDA release artifact checksum does not match its manifest")
    with gzip.open(artifact_path, "rb") as source, destination.open("wb") as target:
        shutil.copyfileobj(source, target)
    actual_csv_hash = sha256_file(destination)
    if actual_csv_hash != manifest.get("uncompressedSha256"):
        raise ValueError("DGDA release CSV checksum does not match its manifest")
    return manifest


async def install_release(args: argparse.Namespace) -> dict[str, int]:
    with tempfile.TemporaryDirectory(prefix="pos4p-dgda-install-") as temporary:
        csv_path = Path(temporary) / "dgda_catalog.csv"
        manifest = unpack_verified_release(args.artifact, args.manifest, csv_path)
        rows = parse_rows(csv_path, load_config(args.config))
        expected = int(manifest["importableRows"])
        if len(rows) != expected:
            raise ValueError(f"DGDA release contains {len(rows)} importable rows; expected {expected}")
        async for session in get_session():
            summary = await import_rows(session, rows, load_config(args.config), dry_run=False)
            break
    print(
        f"DGDA release installed: {len(rows)} rows; "
        f"{summary['created']} created, {summary['updated']} updated"
    )
    return summary


async def update_release(args: argparse.Namespace) -> dict[str, Any]:
    Path("var").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dgda-update-", dir="var") as temporary:
        workspace = Path(temporary)
        raw_path = workspace / "dgda_raw.jsonl"
        state_path = workspace / "dgda_state.json"
        csv_path = workspace / "dgda_catalog.csv"
        report_path = workspace / "dgda_report.json"
        fetch_args = argparse.Namespace(
            raw=raw_path,
            state=state_path,
            page_size=args.page_size,
            max_records=0,
            delay=args.delay,
            timeout=args.timeout,
            user_agent=args.user_agent,
        )
        await fetch_registry(fetch_args)
        compile_registry(raw_path, csv_path, report_path)
        manifest = package_release(csv_path, report_path, args.artifact, args.manifest)
    print(
        f"DGDA release updated: {manifest['importableRows']} importable rows -> "
        f"{args.artifact}"
    )
    if args.install:
        await install_release(args)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    package_parser = subparsers.add_parser(
        "package", help="package an already compiled CSV as the tracked release"
    )
    package_parser.add_argument("--csv", type=Path, default=Path("var/dgda/dgda_catalog.csv"))
    package_parser.add_argument(
        "--report", type=Path, default=Path("var/dgda/dgda_report.json")
    )
    package_parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    package_parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)

    install_parser = subparsers.add_parser(
        "install", help="verify and import the tracked release into the database"
    )
    install_parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    install_parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    install_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    update_parser = subparsers.add_parser(
        "update", help="fetch, compile, and replace the tracked release"
    )
    update_parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    update_parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    update_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    update_parser.add_argument("--install", action="store_true")
    update_parser.add_argument("--page-size", type=int, default=500)
    update_parser.add_argument("--delay", type=float, default=0.1)
    update_parser.add_argument("--timeout", type=float, default=30.0)
    update_parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "package":
        manifest = package_release(args.csv, args.report, args.artifact, args.manifest)
        print(
            f"DGDA release packaged: {manifest['importableRows']} importable rows -> "
            f"{args.artifact}"
        )
        return
    if args.command == "install":
        asyncio.run(install_release(args))
        return
    asyncio.run(update_release(args))


if __name__ == "__main__":
    main()
