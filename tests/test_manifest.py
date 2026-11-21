import hashlib
import json

import pytest

from src.mm import manifest
from src.mm.ingest import ROOT

INVENTORY = (ROOT / "source_inventory.md").read_text(encoding="utf-8")
REQUIRED = {"source_id", "source_type", "source_ref", "source_date", "owner", "licence", "brand", "version", "sha256"}


def test_committed_manifest_is_current():
    assert (ROOT / "source_manifest.json").read_text(encoding="utf-8") == manifest.render(manifest.build_manifest())


def test_every_source_has_the_required_fields():
    data = json.loads((ROOT / "source_manifest.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == manifest.SCHEMA_VERSION
    for source in data["sources"]:
        assert REQUIRED <= source.keys(), source["source_id"]


def test_source_ids_are_unique():
    ids = [s["source_id"] for s in manifest.build_manifest()["sources"]]
    assert len(ids) == len(set(ids))


def test_hashes_match_the_files_on_disk():
    for source in manifest.build_manifest()["sources"]:
        actual = hashlib.sha256((ROOT / source["source_ref"]).read_bytes()).hexdigest()
        assert source["sha256"] == actual, source["source_ref"]


def test_manifest_agrees_with_the_inventory():
    for source in manifest.build_manifest()["sources"]:
        assert source["source_ref"] in INVENTORY
        assert source["sha256"][:12] in INVENTORY, source["source_ref"]


def test_undeclared_data_file_fails_the_build(monkeypatch):
    monkeypatch.setattr(manifest, "ENTRIES", manifest.ENTRIES[1:])
    with pytest.raises(ValueError, match="not declared"):
        manifest.build_manifest()


def test_declared_but_missing_file_fails_the_build(monkeypatch):
    ghost = dict(manifest.ENTRIES[0], path="data_input/comp_ghost_99/missing.txt")
    monkeypatch.setattr(manifest, "ENTRIES", manifest.ENTRIES + [ghost])
    with pytest.raises(FileNotFoundError):
        manifest.build_manifest()


def test_drive_reference_files_are_not_in_the_manifest():
    refs = [s["source_ref"] for s in manifest.build_manifest()["sources"]]
    assert not any("ashutosh" in ref.lower() or "knowledge_base.json" in ref for ref in refs)
