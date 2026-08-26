"""Official DGDA registry fetch/compile helpers."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.dgda_catalog import compile_registry, concept_to_csv_row, split_generic_content


def test_split_generic_content() -> None:
    assert split_generic_content("Amphotericin B  50 mg/vial") == (
        "Amphotericin B",
        "50 mg/vial",
    )
    assert split_generic_content("Podophyllotoxin  .15 gm/100 gm") == (
        "Podophyllotoxin",
        ".15 gm/100 gm",
    )
    assert split_generic_content("Ambiguous value") == ("Ambiguous value", "")
    assert split_generic_content(None) == ("", "")


CONCEPT = {
    "id": "341-0160-020--tericin",
    "display_name": "Tericin",
    "url": "/orgs/MoHFW/sources/DGDA-Drugs/concepts/341-0160-020--tericin/",
    "extras": {
        "company": "Beacon Pharmaceuticals PLC",
        "dar_number": "341-0160-020",
        "trade_name": "Tericin",
        "dosage_form": "Injection",
        "generic_content_raw": "Amphotericin B  50 mg/vial",
    },
}


def test_concept_to_csv_row() -> None:
    row = concept_to_csv_row(CONCEPT)
    assert row["Brand Id"] == "341-0160-020"
    assert row["Registry Id"] == "341-0160-020--tericin"
    assert row["Product Name"] == "Tericin"
    assert row["Generic Name"] == "Amphotericin B"
    assert row["Strength"] == "50 mg/vial"
    assert row["Dosage Form"] == "Injection"
    assert row["Manufacturer"] == "Beacon Pharmaceuticals PLC"
    assert row["Pack Size"] == "1"
    assert row["Package Unit"] == "piece"
    assert row["Unit Price"] == ""
    assert row["Rx"] == ""
    assert row["Source Url"].startswith("https://api.tr.ocl.dghs.gov.bd/")


def test_compile_registry_deduplicates_and_reports(tmp_path: Path) -> None:
    raw = tmp_path / "raw.jsonl"
    raw.write_text(
        json.dumps(CONCEPT) + "\n" + json.dumps(CONCEPT) + "\n" + "not-json\n",
        encoding="utf-8",
    )
    csv_path = tmp_path / "catalog.csv"
    report_path = tmp_path / "report.json"
    report = compile_registry(raw, csv_path, report_path)

    assert report["rows"] == 1
    assert report["importable_rows"] == 1
    assert report["missing_product_names"] == []
    assert report["duplicate_concept_ids"] == 1
    assert report["invalid_lines"] == 1
    assert report["manufacturers"] == 1
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["Product Name"] == "Tericin"
    assert json.loads(report_path.read_text(encoding="utf-8"))["rows"] == 1
