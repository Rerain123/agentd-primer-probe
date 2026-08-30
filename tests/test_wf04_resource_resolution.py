from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentd.config import load_config
from agentd.database import DatabaseCatalog
from agentd.errors import InputValidationError


def _catalog(tmp_path: Path, resources: dict) -> DatabaseCatalog:
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"resources": resources}), encoding="utf-8")
    return DatabaseCatalog(load_config(), catalog_path=path)


def _resource(prefix: Path, *, version: str = "fixture-v1") -> dict:
    Path(f"{prefix}.nin").write_bytes(b"fixture")
    return {
        "kind": "blast_database",
        "path": str(prefix),
        "version": version,
    }


def test_wf04_target_catalog_falls_back_to_target_species(tmp_path: Path) -> None:
    prefix = tmp_path / "target-db"
    catalog = _catalog(
        tmp_path,
        {
            "target:entity-alpha:blast": {
                **_resource(prefix),
                "aliases": ["Entity Alpha"],
            }
        },
    )

    enriched = catalog.enrich_payload(
        "WF-04",
        {
            "target_id": "local-window-id",
            "target_species": "Entity Alpha",
        },
    )

    assert enriched["target_db"] == str(prefix.resolve())
    resolution = next(
        item
        for item in enriched["_database_resolution"]
        if item["field"] == "target_db"
    )
    assert resolution["selector_source_field"] == "target_species"


def test_equivalent_catalog_alias_entries_collapse_to_one_resource(
    tmp_path: Path,
) -> None:
    prefix = tmp_path / "shared-target-db"
    shared = _resource(prefix)
    catalog = _catalog(
        tmp_path,
        {
            "target:entity-alpha:blast": dict(shared),
            "target:entity-a:blast": dict(shared),
        },
    )

    resolved = catalog.resolve_selector(
        "target",
        "entity-alpha (entity-a)",
        suffix="blast",
    )

    assert resolved is not None
    assert resolved["path"] == str(prefix.resolve())
    assert resolved["selector_match_method"] == "equivalent_catalog_alias"


def test_distinct_catalog_resources_remain_ambiguous(tmp_path: Path) -> None:
    first = tmp_path / "first-db"
    second = tmp_path / "second-db"
    catalog = _catalog(
        tmp_path,
        {
            "target:entity-alpha:blast": _resource(first),
            "target:entity-a:blast": _resource(second),
        },
    )

    with pytest.raises(InputValidationError, match="多个目录项"):
        catalog.resolve_selector(
            "target",
            "entity-alpha (entity-a)",
            suffix="blast",
        )
