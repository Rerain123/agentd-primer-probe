"""Server-injectable data and BLAST database catalog.

The workflow layer consumes logical resource keys instead of hard-coded server
paths.  Local requests can still provide inline sequences or explicit paths.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from copy import deepcopy
from pathlib import Path
from typing import Any

from .errors import InputValidationError
from .models import SequenceRecord
from .sequence import normalize_sequence, read_fasta


_SELECTOR_GROUP_RE = re.compile(r"[\(\[\{]([^\)\]\}]+)[\)\]\}]")
_SELECTOR_DELIMITER_RE = re.compile(r"[/,，;；|、]+")


def _normalize_selector(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(text.strip().casefold().split())


def _selector_forms(value: Any) -> set[str]:
    """Return equivalent selector fragments without guessing a resource."""

    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return set()
    groups = _SELECTOR_GROUP_RE.findall(text)
    outside = _SELECTOR_GROUP_RE.sub(" ", text)
    forms: set[str] = set()
    for candidate in (text, outside, *groups):
        for fragment in _SELECTOR_DELIMITER_RE.split(candidate):
            normalized = _normalize_selector(fragment)
            if normalized:
                forms.add(normalized)
    return forms


def finalize_versioned_resource(
    resource: dict[str, Any] | None,
    *,
    scope: dict[str, Any],
) -> dict[str, Any] | None:
    """Attach the common P2 resolution contract to a catalog resource."""

    if resource is None:
        return None
    finalized = deepcopy(resource)
    version = str(finalized.get("version") or "").strip()
    version_valid = bool(version and version != "SET_ON_SERVER")
    finalized["version_valid"] = version_valid
    finalized["scope"] = deepcopy(scope)
    if not finalized.get("path") or not finalized.get("exists"):
        finalized["resolution_status"] = "NOT_RESOLVED"
        finalized["resolution_reason"] = "DATABASE_PATH_UNAVAILABLE"
    elif not version_valid:
        finalized["resolution_status"] = "NOT_RESOLVED"
        finalized["resolution_reason"] = "DATABASE_VERSION_MISSING"
    else:
        finalized["resolution_status"] = "RESOLVED"
        finalized["resolution_reason"] = None
    return finalized


class DatabaseCatalog:
    BLAST_INDEX_SUFFIXES = (
        ".nhr", ".nin", ".nsq", ".ndb", ".njs", ".not", ".ntf", ".nto", ".nal"
    )

    def __init__(self, config: dict, *, catalog_path: str | Path | None = None) -> None:
        database_config = config.get("databases", {})
        if not isinstance(database_config, dict):
            database_config = {}
        configured = (
            catalog_path
            or os.environ.get("AGENTD_DATABASE_CATALOG")
            or database_config.get("catalog_path")
        )
        self.path = Path(configured).resolve() if configured else None
        self._base_dir = self.path.parent if self.path else Path.cwd()
        configured_aliases = database_config.get("selector_aliases", {})
        self.selector_aliases = (
            deepcopy(configured_aliases)
            if isinstance(configured_aliases, dict)
            else {}
        )
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

    def _resource_aliases(
        self,
        namespace: str,
        canonical_selector: str,
        resource: dict[str, Any],
    ) -> list[str]:
        aliases: list[str] = []
        namespace_aliases = self.selector_aliases.get(namespace, {})
        if isinstance(namespace_aliases, dict):
            canonical_normalized = _normalize_selector(canonical_selector)
            for configured_selector, configured_values in namespace_aliases.items():
                if _normalize_selector(configured_selector) != canonical_normalized:
                    continue
                values = (
                    configured_values
                    if isinstance(configured_values, list)
                    else [configured_values]
                )
                aliases.extend(str(value) for value in values if value not in (None, ""))
        resource_aliases = resource.get("aliases", [])
        if not isinstance(resource_aliases, list):
            resource_aliases = [resource_aliases]
        aliases.extend(
            str(value) for value in resource_aliases if value not in (None, "")
        )
        return aliases

    def resolve_selector(
        self,
        namespace: str,
        selector: str,
        *,
        suffix: str,
    ) -> dict | None:
        """Resolve a user-facing selector through canonical keys and aliases."""

        requested_forms = _selector_forms(selector)
        raw_selector = _normalize_selector(selector)
        if not requested_forms:
            return None

        expected_kind = {
            "blast": "blast_database",
            "sequences": "fasta",
        }.get(suffix)
        matches: list[tuple[tuple[int, int], str, str]] = []
        prefix = f"{namespace}:"
        suffix_marker = f":{suffix}"
        for key, resource in self.resources.items():
            if not key.startswith(prefix):
                continue
            if expected_kind and resource.get("kind") != expected_kind:
                continue
            body = key[len(prefix):]
            if body.endswith(suffix_marker):
                canonical = body[: -len(suffix_marker)]
                suffix_rank = 0
            elif ":" not in body:
                canonical = body
                suffix_rank = 1
            else:
                continue
            canonical_normalized = _normalize_selector(canonical)
            alias_forms = {
                form
                for alias in self._resource_aliases(namespace, canonical, resource)
                for form in _selector_forms(alias)
            }
            if raw_selector == canonical_normalized:
                match_rank = 0
                match_method = "canonical"
            elif canonical_normalized in requested_forms:
                match_rank = 1
                match_method = "embedded_canonical"
            elif requested_forms & alias_forms:
                match_rank = 2
                match_method = "configured_alias"
            else:
                continue
            matches.append(((match_rank, suffix_rank), key, match_method))

        if not matches:
            return None
        matches.sort(key=lambda item: (item[0], item[1]))
        best_rank = matches[0][0]
        best = [item for item in matches if item[0] == best_rank]
        selected = best[0]
        if len(best) > 1:
            resolved_candidates = [
                (item, self.resolve(item[1]))
                for item in best
            ]
            identities = {
                (
                    resource.get("kind"),
                    resource.get("path"),
                    resource.get("uri"),
                    resource.get("version"),
                )
                for _item, resource in resolved_candidates
                if resource is not None
            }
            if len(identities) != 1:
                raise InputValidationError(
                    "资源选择器匹配到多个目录项",
                    details={
                        "namespace": namespace,
                        "suffix": suffix,
                        "match_count": len(best),
                    },
                )
            selected = min(best, key=lambda item: item[1])
        resolved = self.resolve(selected[1])
        if resolved is not None:
            resolved["selector_match_method"] = (
                "equivalent_catalog_alias"
                if len(best) > 1
                else selected[2]
            )
        return resolved

    def resolve_versioned(
        self,
        *keys: str,
        scope: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Resolve a catalog entry without discarding missing-path evidence."""

        first_unresolved: dict[str, Any] | None = None
        for key in keys:
            finalized = finalize_versioned_resource(
                self.resolve(key),
                scope=scope,
            )
            if finalized is None:
                continue
            if finalized.get("resolution_status") == "RESOLVED":
                return finalized
            if first_unresolved is None:
                first_unresolved = finalized
        return first_unresolved

    def request_resource(
        self,
        path: str | Path,
        *,
        key: str,
        version: str | None,
        scope: dict[str, Any],
        kind: str = "blast_database",
    ) -> dict[str, Any]:
        """Represent a request-supplied path using the same P2 contract."""

        resolved_path = Path(path).resolve()
        resource = {
            "key": key,
            "kind": kind,
            "path": str(resolved_path),
            "version": version,
            "exists": self._path_available(
                {"kind": kind},
                resolved_path,
            ),
        }
        finalized = finalize_versioned_resource(resource, scope=scope)
        assert finalized is not None
        return finalized

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
            apply_resource(field, resource)

        def apply_resource(field: str, resource: dict | None) -> None:
            if enriched.get(field):
                return
            if resource and resource.get("path") and resource.get("exists"):
                enriched[field] = resource["path"]
                resolution = {
                    "field": field,
                    "key": resource["key"],
                    "version": resource.get("version"),
                }
                if resource.get("selector_match_method"):
                    resolution["selector_match_method"] = resource[
                        "selector_match_method"
                    ]
                if resource.get("selector_source_field"):
                    resolution["selector_source_field"] = resource[
                        "selector_source_field"
                    ]
                resolutions.append(resolution)

        target_id = str(enriched.get("target_id", "")).strip()
        target_species = str(enriched.get("target_species", "")).strip()
        host = str(enriched.get("host_species", "")).strip()
        target_selectors = [
            ("target_id", target_id),
            ("target_species", target_species),
        ]

        def resolve_target_resource(*, suffix: str) -> dict | None:
            seen: set[str] = set()
            for source_field, selector in target_selectors:
                normalized = _normalize_selector(selector)
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                resource = self.resolve_selector(
                    "target",
                    selector,
                    suffix=suffix,
                )
                if resource is not None:
                    resource["selector_source_field"] = source_field
                    return resource
            return None

        if (target_id or target_species) and not enriched.get("target_db"):
            apply_resource("target_db", resolve_target_resource(suffix="blast"))
        if host and not enriched.get("host_db"):
            apply_resource(
                "host_db",
                self.resolve_selector("host", host, suffix="blast"),
            )
        apply_path("cross_db", ["cross:default:blast", "cross:default"])
        if (
            workflow in {"WF-01", "WF-05"}
            and not any(
                enriched.get(key)
                for key in ("sequence", "sequences", "fasta_path")
            )
            and (target_id or target_species)
        ):
            apply_resource(
                "fasta_path",
                resolve_target_resource(suffix="sequences"),
            )

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
