import hashlib
from pathlib import Path

import pytest

from src.mm.ingest import ROOT

INVENTORY = (ROOT / "source_inventory.md").read_text(encoding="utf-8")
DATA_DIRS = ("data_input", "brand_packs", "evaluation")


def _project_data_files() -> list[Path]:
    files = []
    for name in DATA_DIRS:
        for path in sorted((ROOT / name).rglob("*")):
            if path.is_file() and path.name != ".DS_Store":
                files.append(path)
    return files


@pytest.mark.parametrize("path", _project_data_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_every_project_data_file_is_registered_with_its_current_hash(path):
    relative = str(path.relative_to(ROOT))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    assert relative in INVENTORY, f"{relative} has no inventory row"
    assert digest in INVENTORY, f"{relative}: content hash {digest} is not in the inventory; update the row"


def test_inventory_has_no_placeholder_rows():
    assert "| TBD |" not in INVENTORY
    assert "None yet" not in INVENTORY
