"""DGDA release packaging and integrity verification."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts.dgda_release import package_release, unpack_verified_release


def test_package_and_unpack_verified_release(tmp_path: Path) -> None:
    csv_path = tmp_path / "catalog.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Brand Id", "Product Name"])
        writer.writerow(["123", "Example"])
    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(
            {
                "rows": 1,
                "importable_rows": 1,
                "missing_product_names": [],
                "manufacturers": 1,
            }
        ),
        encoding="utf-8",
    )
    artifact = tmp_path / "dgda_catalog.csv.gz"
    manifest = tmp_path / "dgda_catalog.manifest.json"

    packaged = package_release(csv_path, report_path, artifact, manifest)
    unpacked = tmp_path / "unpacked.csv"
    verified = unpack_verified_release(artifact, manifest, unpacked)

    assert packaged["importableRows"] == 1
    assert verified == packaged
    assert unpacked.read_bytes() == csv_path.read_bytes()


def test_unpack_rejects_modified_artifact(tmp_path: Path) -> None:
    csv_path = tmp_path / "catalog.csv"
    csv_path.write_text("Product Name\nExample\n", encoding="utf-8")
    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(
            {
                "rows": 1,
                "importable_rows": 1,
                "missing_product_names": [],
                "manufacturers": 1,
            }
        ),
        encoding="utf-8",
    )
    artifact = tmp_path / "dgda_catalog.csv.gz"
    manifest = tmp_path / "dgda_catalog.manifest.json"
    package_release(csv_path, report_path, artifact, manifest)
    artifact.write_bytes(artifact.read_bytes() + b"tampered")

    with pytest.raises(ValueError, match="checksum"):
        unpack_verified_release(artifact, manifest, tmp_path / "unpacked.csv")
