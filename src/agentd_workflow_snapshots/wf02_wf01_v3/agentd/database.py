"""Server-injectable data and BLAST database catalog.

The workflow layer consumes logical resource keys instead of hard-coded server
paths.  Local requests can still provide inline sequences or explicit paths.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from .models import SequenceRecord
from .sequence import normalize_sequence, read_fasta


class DatabaseCatalog:
    BLAST_INDEX_SUFFIXES = (
        ".nhr", ".nin", ".nsq", ".ndb", ".njs", ".not", ".ntf", ".nto", ".nal"
    )

    def __init__(self, config: dict, *, catalog_path: str | Path | None = None) -> None:
        configured = catalog_path or os.environ.get("AGENTD_DATABASE_CATALOG") or config.get("databases", {}).get("catalog_path")
        self.path = Path(configured).resolve() if configured else None
        self._base_dir = self.path.parent if self.path else Path.cwd()
        self.resources: dict[str, dict[str, Any]] = {}
        self.load_error: str | None = None
        if self.path:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                resources = raw.get("resources", raw)
                if not isinstance(resources, dict):
                    raise ValueError("resources必须是对象")
                self.resources = {str(key): dict(value) for key, value in resources.items() if isinstance(value, dict)}
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self.load_error = str(exc)

    @property
    def configured(self) -> bool:
        return self.path is not None and self.load_error is None

    def _resolved_path(self, resource: dict) -> Path | None:
        raw = resource.get("path")
        if not raw:
            return None
        path = Path(str(raw))
        return path.resolve() if path.is_absolute() else (self._base_dir / path).resolve()

    def _path_available(self, resource: dict, path: Path | None) -> bool:
        if path is None:
            return False
        if resource.get("kind") != "blast_database":
            return path.is_file()
        if path.is_file():
            return True
        # makeblastdb creates files such as ``database.nin`` and
        # ``database.nsq``; the prefix passed to ``blastn -db`` is not itself
        # necessarily a filesystem entry.
        direct_index = any(Path(f"{path}{suffix}").is_file() for suffix in self.BLAST_INDEX_SUFFIXES)
        split_index = any(
            item.is_file() and item.name.lower().endswith(self.BLAST_INDEX_SUFFIXES)
            for item in path.parent.glob(f"{path.name}.*")
        )
        return direct_index or split_index

    def resolve(self, *keys: str) -> dict | None:
        for key in keys:
            resource = self.resources.get(key)
            if resource is None:
                continue
            resolved = dict(resource)
            path = self._resolved_path(resource)
            if path is not None:
                resolved["path"] = str(path)
                resolved["exists"] = self._path_available(resource, path)
            resolved["key"] = key
            return resolved
        return None

    def resolve_path(self, *keys: str) -> str | None:
        resource = self.resolve(*keys)
        if not resource or not resource.get("path") or not resource.get("exists"):
            return None
        return str(resource["path"])

    def sequence_records(self, *keys: str) -> list[SequenceRecord]:
        path = self.resolve_path(*keys)
        return read_fasta(path) if path else []

    def housekeeping_records(self, payload: dict, host_species: str, gene: str) -> tuple[list[SequenceRecord], dict]:
        supplied_path = payload.get("gene_fasta_paths", {}).get(gene)
        if supplied_path:
            records = read_fasta(supplied_path)
            return records, {"method": "request_gene_fasta", "resource_key": None, "path": str(Path(supplied_path).resolve())}
        supplied = payload.get("gene_sequences", {}).get(gene)
        if supplied:
            raw_sequences = supplied.get("sequences", supplied) if isinstance(supplied, dict) else supplied
            records: list[SequenceRecord] = []
            for index, item in enumerate(raw_sequences):
                if isinstance(item, str):
                    records.append(SequenceRecord(id=f"{gene}_{index+1}", sequence=normalize_sequence(item, allow_iupac=index > 0)))
                else:
                    records.append(
                        SequenceRecord(
                            id=str(item.get("id", f"{gene}_{index+1}")),
                            sequence=normalize_sequence(item["sequence"], allow_iupac=index > 0),
                            description=str(item.get("description", "")),
                            source=str(item.get("source", "request")),
                        )
                    )
            return records, {"method": "request_gene_sequences", "resource_key": None}
        key = f"internal:{host_species}:{gene}:sequences"
        resource = self.resolve(key)
        records = self.sequence_records(key)
        return records, {
            "method": "database_catalog" if records else "not_configured",
            "resource_key": key,
            "resource_version": resource.get("version") if resource else None,
        }

    def enrich_payload(self, workflow: str, payload: dict) -> dict:
        enriched = deepcopy(payload)
        resolutions: list[dict] = []

        def apply_path(field: str, keys: list[str]) -> None:
            if enriched.get(field):
                return
            resource = self.resolve(*keys)
            if resource and resource.get("path") and resource.get("exists"):
                enriched[field] = resource["path"]
                resolutions.append({"field": field, "key": resource["key"], "version": resource.get("version")})

        target_id = str(enriched.get("target_id", "")).strip()
        host = str(enriched.get("host_species", "")).strip()
        if target_id:
            apply_path("target_db", [f"target:{target_id}:blast", f"target:{target_id}"])
        if host:
            apply_path("host_db", [f"host:{host}:blast", f"host:{host}"])
        apply_path("cross_db", ["cross:default:blast", "cross:default"])
        if workflow in {"WF-01", "WF-05"} and not any(enriched.get(key) for key in ("sequence", "sequences", "fasta_path")) and target_id:
            apply_path("fasta_path", [f"target:{target_id}:sequences"])

        if workflow in {"WF-02", "WF-06"} and isinstance(enriched.get("targets"), list):
            child_targets = []
            for target in enriched["targets"]:
                child = {**enriched, **target}
                child.pop("targets", None)
                child = self.enrich_payload("WF-01", child)
                child_targets.append(child)
            enriched["targets"] = child_targets
        if resolutions:
            enriched.setdefault("_database_resolution", []).extend(resolutions)
        return enriched

    def status(self) -> dict:
        items: list[dict] = []
        for key, resource in sorted(self.resources.items()):
            path = self._resolved_path(resource)
            uri_registered = bool(resource.get("uri"))
            version = resource.get("version")
            version_valid = bool(version and str(version).strip() != "SET_ON_SERVER")
            path_available = self._path_available(resource, path)
            items.append(
                {
                    "key": key,
                    "kind": resource.get("kind", "unspecified"),
                    "version": version,
                    "version_valid": version_valid,
                    "required": bool(resource.get("required", False)),
                    "configured": bool(resource.get("path") or uri_registered),
                    "available": path_available and version_valid,
                    "path_available": path_available,
                    "uri_registered": uri_registered,
                }
            )
        required_missing = [item["key"] for item in items if item["required"] and not item["available"]]
        return {
            "tool": "C0_database_catalog",
            "configured": self.configured,
            "catalog_path": str(self.path) if self.path else None,
            "load_error": self.load_error,
            "resource_count": len(items),
            "resources": items,
            "required_missing": required_missing,
        }
