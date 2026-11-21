"""Machine-readable source manifest (source_manifest.json at the project root).

Every project data file must be declared in ENTRIES. Generation fails if a declared file is missing,
or if a data file on disk is not declared. The manifest records the SHA-256 of each file.

Run:
    python -m src.mm.manifest            # write source_manifest.json
    python -m src.mm.manifest --check    # exit 1 if the committed manifest is out of date
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from src.mm.ingest import ROOT

MANIFEST_PATH = ROOT / "source_manifest.json"
DATA_DIRS = ("data_input", "brand_packs", "evaluation")
SCHEMA_VERSION = "marketmind-source-manifest-1.0"
PENDING_BRAND_A = "open: public-derived, pending decision (inventory section 6)"
SYNTHETIC = "Project-created (synthetic)"

ENTRIES: list[dict] = [
    {"path": "data_input/comp_cloudvane_01/brandA_moseta_source.txt", "brand_id": "brandA_moseta", "source_type": "source_text", "owner": "Damini Shrawan", "licence": PENDING_BRAND_A, "source_date": "2026-11-20", "version": "v1.0"},
    {"path": "brand_packs/brandA_moseta/brandA_profile.json", "brand_id": "brandA_moseta", "source_type": "company_profile", "owner": "Damini Shrawan", "licence": SYNTHETIC, "source_date": "2026-11-20", "version": "1.0"},
    {"path": "brand_packs/brandA_moseta/catalogue.json", "brand_id": "brandA_moseta", "source_type": "catalogue", "owner": "Damini Shrawan", "licence": SYNTHETIC, "source_date": "2026-11-20", "version": "1.0"},
    {"path": "brand_packs/brandA_moseta/synthetic_leads.json", "brand_id": "brandA_moseta", "source_type": "lead_data", "owner": "Damini Shrawan", "licence": SYNTHETIC, "source_date": "2026-11-20", "version": "1.0"},
    {"path": "data_input/comp_pulsepeak_02/facilities_and_terms.md", "brand_id": "brandB_pulsepeak", "source_type": "source_text", "owner": "Damini Shrawan", "licence": SYNTHETIC, "source_date": "2026-11-21", "version": "v1.0"},
    {"path": "brand_packs/brandB_pulsepeak/brandB_profile.json", "brand_id": "brandB_pulsepeak", "source_type": "company_profile", "owner": "Damini Shrawan", "licence": SYNTHETIC, "source_date": "2026-11-21", "version": "1.0"},
    {"path": "brand_packs/brandB_pulsepeak/catalogue.json", "brand_id": "brandB_pulsepeak", "source_type": "catalogue", "owner": "Damini Shrawan", "licence": SYNTHETIC, "source_date": "2026-11-21", "version": "1.0"},
    {"path": "brand_packs/brandB_pulsepeak/synthetic_leads.json", "brand_id": "brandB_pulsepeak", "source_type": "lead_data", "owner": "Damini Shrawan", "licence": SYNTHETIC, "source_date": "2026-11-21", "version": "1.0"},
    {"path": "data_input/comp_terraroast_03/bean_origins_and_catalog.md", "brand_id": "brandC_terraroast", "source_type": "source_text", "owner": "Damini Shrawan", "licence": SYNTHETIC, "source_date": "2026-11-21", "version": "v1.0"},
    {"path": "brand_packs/brandC_terraroast/brandC_profile.json", "brand_id": "brandC_terraroast", "source_type": "company_profile", "owner": "Damini Shrawan", "licence": SYNTHETIC, "source_date": "2026-11-21", "version": "1.0"},
    {"path": "brand_packs/brandC_terraroast/catalogue.json", "brand_id": "brandC_terraroast", "source_type": "catalogue", "owner": "Damini Shrawan", "licence": SYNTHETIC, "source_date": "2026-11-21", "version": "1.0"},
    {"path": "brand_packs/brandC_terraroast/synthetic_leads.json", "brand_id": "brandC_terraroast", "source_type": "lead_data", "owner": "Damini Shrawan", "licence": SYNTHETIC, "source_date": "2026-11-21", "version": "1.0"},
    {"path": "evaluation/golden_queries.jsonl", "brand_id": "all_brands", "source_type": "evaluation_labels", "owner": "Damini Shrawan (drafted); Dr. Ashutosh Verma to review", "licence": "Project-created; self-drafted, not evaluation-grade", "source_date": "2026-11-21", "version": "1.0"},
]


def _data_files(root: Path = ROOT) -> set[str]:
    found = set()
    for name in DATA_DIRS:
        for path in (root / name).rglob("*"):
            if path.is_file() and path.name != ".DS_Store":
                found.add(str(path.relative_to(root)))
    return found


def build_manifest(root: Path = ROOT) -> dict:
    declared = {entry["path"] for entry in ENTRIES}
    on_disk = _data_files(root)
    missing = sorted(declared - on_disk)
    undeclared = sorted(on_disk - declared)
    if missing:
        raise FileNotFoundError(f"declared but not on disk: {missing}")
    if undeclared:
        raise ValueError(f"data files not declared in ENTRIES: {undeclared}")

    counters: dict[tuple[str, str], int] = {}
    sources = []
    for entry in sorted(ENTRIES, key=lambda e: e["path"]):
        key = (entry["brand_id"], entry["source_type"])
        counters[key] = counters.get(key, 0) + 1
        sources.append({
            "source_id": f"{entry['brand_id']}:{entry['source_type']}:{counters[key]:03d}",
            "source_type": entry["source_type"],
            "source_ref": entry["path"],
            "source_date": entry["source_date"],
            "owner": entry["owner"],
            "licence": entry["licence"],
            "brand": entry["brand_id"],
            "version": entry["version"],
            "sha256": hashlib.sha256((root / entry["path"]).read_bytes()).hexdigest(),
        })
    return {"schema_version": SCHEMA_VERSION, "sources": sources}


def render(manifest: dict) -> str:
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build or check source_manifest.json")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = render(build_manifest())
    if args.check:
        current = MANIFEST_PATH.read_text(encoding="utf-8") if MANIFEST_PATH.exists() else ""
        if current != text:
            print("source_manifest.json is out of date; run: python -m src.mm.manifest")
            sys.exit(1)
        print("source_manifest.json is up to date")
        return
    MANIFEST_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
