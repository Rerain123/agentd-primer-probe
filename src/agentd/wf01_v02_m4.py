"""WF-01 PRD-v0.2 M4 three-mode specificity evaluation.

The module reuses BLAST/Primer3 measurements but owns a new v0.2 evidence
contract.  Missing objects, databases, versions or tools are always
NOT_EVALUATED.  No development fallback can produce a specificity PASS.
"""

from __future__ import annotations

import copy
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .database import DatabaseCatalog
from .errors import AgentDError, InputValidationError
from .primer3_tool import Primer3Adapter
from .remote_blast import NCBIBlastMCPProvider
from .sequence import reverse_complement
from .specificity import (
    BlastAdapter,
    SHORT_OLIGO_TARGET_MIN_IDENTITY_PCT,
    SHORT_OLIGO_TARGET_MIN_QUERY_COVERAGE_PCT,
    SHORT_OLIGO_TARGET_SEARCH_EVALUE,
)
from .wf01_v02_m3 import (
    FIXED_TOP_CANDIDATES,
    SCORING_WEIGHTS,
    WF01V02ScoringEngine,
    final_scoring_rank_key,
)


M4_ENGINE_VERSION = "wf01-v0.2-m4.6"
SPECIFICITY_POLICY_VERSION = "wf01-v0.2-specificity-document-framework-v4"
SPECIFICITY_POLICY_STATUS = (
    "ACTIVE_FOR_IN_SILICO_RANKING_REQUIRES_EXPERIMENTAL_CALIBRATION"
)
SPECIFICITY_SCORE_BY_STATUS = {
    "PASS": 100.0,
    "CAUTION": 70.0,
    "FAIL": 0.0,
}
THRESHOLDS = {
    "TARGET_HIT": {
        "search_evalue": SHORT_OLIGO_TARGET_SEARCH_EVALUE,
        "decision_uses_evalue": False,
        "pass_identity_pct": 100.0,
        "pass_coverage_pct": 100.0,
        "pass_three_prime_mismatches": 0,
        "caution_identity_pct_min": SHORT_OLIGO_TARGET_MIN_IDENTITY_PCT,
        "caution_coverage_pct_min": SHORT_OLIGO_TARGET_MIN_QUERY_COVERAGE_PCT,
    },
    "HOST_EXCLUSION": {
        "evalue_lt": 0.1,
        "high_risk_dg_lt_kcal": -6.0,
        "high_risk_three_prime_mismatches_max": 1,
        "compatible_amplicon_span_max_bp": 1000,
    },
    "RELATED_BACKGROUND_NT": {
        "evalue_lt": 1e-5,
        "high_risk_identity_pct_min": 95.0,
        "high_risk_coverage_pct_min": 95.0,
        "high_risk_three_prime_mismatches_max": 1,
        "compatible_amplicon_span_max_bp": 1000,
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resource_public(resource: dict[str, Any] | None) -> dict[str, Any] | None:
    if not resource:
        return None
    return {
        "resource_key": resource.get("key"),
        "kind": resource.get("kind"),
        "path": resource.get("path"),
        "uri": resource.get("uri"),
        "version": resource.get("version"),
        "version_valid": resource.get("version_valid"),
        "exists": resource.get("exists"),
        "scope": resource.get("scope"),
        "resolution_status": resource.get("resolution_status"),
        "resolution_reason": resource.get("resolution_reason"),
        "version_semantics": resource.get("version_semantics"),
    }


def _object_tokens(object_: dict[str, Any]) -> list[str]:
    values = [
        object_.get("identifier"),
        object_.get("taxid"),
        object_.get("accession"),
        object_.get("normalized_name"),
        object_.get("scientific_name"),
    ]
    tokens: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in tokens:
            tokens.append(text)
    for value in object_.get("known_accessions") or []:
        text = str(value or "").strip()
        if text and text not in tokens:
            tokens.append(text)
    return tokens


def _accession_token(value: Any) -> str:
    """Normalize an accession for comparing local and NCBI representations."""

    return re.sub(r"\.\d+$", "", str(value or "").strip()).casefold()


class WF01V02SpecificityResourceResolver:
    """Resolve v0.2 logical resources without guessing external identifiers."""

    def __init__(self, catalog: DatabaseCatalog) -> None:
        self.catalog = catalog

    @staticmethod
    def _finalize(
        resource: dict[str, Any] | None,
        *,
        scope: dict[str, Any],
    ) -> dict[str, Any] | None:
        if resource is None:
            return None
        finalized = copy.deepcopy(resource)
        version = str(finalized.get("version") or "").strip()
        version_valid = bool(version and version != "SET_ON_SERVER")
        finalized["version_valid"] = version_valid
        finalized["scope"] = scope
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

    def _resolve_object(
        self,
        role: str,
        object_: dict[str, Any],
        *,
        extra_keys: list[str] | None = None,
    ) -> dict[str, Any] | None:
        keys: list[str] = []
        prefix = role.lower()
        for token in _object_tokens(object_):
            keys.extend((f"{prefix}:{token}:blast", f"{prefix}:{token}"))
        keys.extend(extra_keys or [])
        return self._finalize(
            self.catalog.resolve(*keys),
            scope={
                "role": role,
                "object_id": object_.get("object_id"),
                "identifier_type": object_.get("identifier_type"),
                "identifier": object_.get("identifier"),
            },
        )

    def resolve_target(
        self,
        target: dict[str, Any],
    ) -> dict[str, Any] | None:
        object_ = target.get("target_object") or {}
        return self._resolve_object(
            "target",
            object_,
            extra_keys=[
                f"target:{target.get('target_id')}:blast",
                f"target:{target.get('target_id')}",
                "target:default:blast",
                "target:default",
            ],
        )

    def resolve_host(
        self,
        object_: dict[str, Any],
    ) -> dict[str, Any] | None:
        return self._resolve_object("host", object_)

    def resolve_related_background(
        self,
        objects: list[dict[str, Any]],
        *,
        target_object: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return self._finalize(
            self.catalog.resolve(
                "nt:default:blast",
                "nt:default",
                "cross:default:blast",
                "cross:default",
            ),
            scope={
                "role": "RELATED_BACKGROUND",
                "object_ids": [object_.get("object_id") for object_ in objects],
                "target_object_id": (target_object or {}).get("object_id"),
                "mode": (
                    "EXPLICIT_OBJECTS"
                    if objects
                    else "BROAD_NT_BACKGROUND"
                ),
                "scope_query": [
                    {
                        "identifier_type": object_.get("identifier_type"),
                        "identifier": object_.get("identifier"),
                    }
                    for object_ in objects
                ],
            },
        )


class WF01V02BlastSearchProvider:
    """Batch BLAST search provider used by the v0.2 specificity policy."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        allow_development_fallback: bool = False,
        blast_adapter: BlastAdapter | None = None,
        primer3_adapter: Primer3Adapter | None = None,
    ) -> None:
        executable = config.get("tools", {}).get("blastn_executable", "blastn")
        self.blast = blast_adapter or BlastAdapter(
            executable,
            allow_fallback=allow_development_fallback,
        )
        self.primer3 = primer3_adapter or Primer3Adapter(
            config,
            allow_fallback=allow_development_fallback,
        )

    def status(self) -> dict[str, Any]:
        return {
            "blast": self.blast.status(),
            "primer3": self.primer3.status(),
        }

    def _add_thermodynamics(
        self,
        hits: list[dict[str, Any]],
        queries: dict[str, str],
    ) -> None:
        if not self.primer3.available:
            return
        for hit in hits:
            query = queries.get(str(hit.get("query_id") or ""))
            context = hit.get("hit_sequence")
            if not query or not context:
                continue
            try:
                hit["thermodynamics"] = self.primer3.calc_heterodimer(
                    query,
                    reverse_complement(str(context)),
                )
            except InputValidationError:
                hit["thermodynamics"] = None

    def search(
        self,
        queries: dict[str, str],
        resource: dict[str, Any],
        *,
        mode: str,
        evalue: float,
    ) -> dict[str, Any]:
        hits, method = self.blast.search(
            queries,
            str(resource["path"]),
            evalue=evalue,
            mode=mode,
        )
        # TARGET_HIT is decided only from identity, full-query coverage and
        # 3' mismatches.  A permissive short-oligo search may return many weak
        # local alignments, so avoid thousands of unused Primer3 ΔG calls.
        if mode != "TARGET_HIT":
            self._add_thermodynamics(hits, queries)
        return {
            "hits": hits,
            "method": method,
            "tool": self.status(),
            "raw_tool_output": copy.deepcopy(
                getattr(
                    self.blast,
                    "last_search_evidence",
                    {
                        "tool": "BLAST+",
                        "status": "COMPLETED",
                        "database": str(resource["path"]),
                        "mode": mode,
                        "evalue": evalue,
                        "queries": queries,
                        "raw_output": hits,
                    },
                )
            ),
        }


def _query_role(query_id: str) -> str:
    return query_id.rsplit("_", 1)[-1]


def _candidate_query_ids(candidate_index: int) -> dict[str, str]:
    prefix = f"c{candidate_index:04d}"
    return {
        "forward": f"{prefix}_forward",
        "reverse": f"{prefix}_reverse",
        "probe": f"{prefix}_probe",
    }


def _candidate_queries(
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, str], list[dict[str, str]]]:
    queries: dict[str, str] = {}
    maps: list[dict[str, str]] = []
    for index, candidate in enumerate(candidates, start=1):
        query_ids = _candidate_query_ids(index)
        queries[query_ids["forward"]] = str(
            candidate.get("forward_primer", {}).get("sequence_5to3") or ""
        )
        queries[query_ids["reverse"]] = str(
            candidate.get("reverse_primer", {}).get("sequence_5to3") or ""
        )
        probe = str((candidate.get("probe") or {}).get("sequence_5to3") or "")
        if probe:
            queries[query_ids["probe"]] = probe
        else:
            query_ids.pop("probe")
        maps.append(query_ids)
    return queries, maps


def _candidate_hits(
    hits: list[dict[str, Any]],
    query_ids: dict[str, str],
) -> list[dict[str, Any]]:
    by_id = {query_id: role for role, query_id in query_ids.items()}
    selected: list[dict[str, Any]] = []
    for hit in hits:
        query_id = str(hit.get("query_id") or "")
        if query_id not in by_id:
            continue
        normalized = copy.deepcopy(hit)
        normalized["batch_query_id"] = query_id
        normalized["query_id"] = by_id[query_id]
        selected.append(normalized)
    return selected


def _evalue_below(hit: dict[str, Any], threshold: float) -> bool:
    value = hit.get("evalue")
    return value is not None and float(value) < threshold


def _hit_dg(hit: dict[str, Any]) -> float | None:
    thermo = hit.get("thermodynamics")
    if not isinstance(thermo, dict) or thermo.get("dg_kcal") is None:
        return None
    return float(thermo["dg_kcal"])


def _scientific_name(hit: dict[str, Any]) -> str | None:
    explicit = str(hit.get("scientific_name") or "").strip()
    if explicit:
        return explicit
    bracketed = re.findall(
        r"\[([A-Z][a-z]+(?:\s+[a-z][A-Za-z0-9_.-]*)+)\]",
        str(hit.get("title") or ""),
    )
    return bracketed[-1] if bracketed else None


def _compatible_amplicons(
    hits: list[dict[str, Any]],
    maximum_span_bp: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for hit in hits:
        role = str(hit.get("query_id") or "")
        if role in {"forward", "reverse"}:
            grouped[str(hit.get("accession") or "")][role].append(hit)
    pairs: list[dict[str, Any]] = []
    for accession, roles in grouped.items():
        for forward in roles.get("forward", []):
            for reverse in roles.get("reverse", []):
                if None in (
                    forward.get("subject_start"),
                    forward.get("subject_end"),
                    reverse.get("subject_start"),
                    reverse.get("subject_end"),
                ):
                    continue
                forward_strand = str(forward.get("strand") or "").lower()
                reverse_strand = str(reverse.get("strand") or "").lower()
                forward_position = int(forward["subject_start"])
                reverse_position = int(reverse["subject_start"])
                outward_pair = (
                    forward_strand in {"+", "plus"}
                    and reverse_strand in {"-", "minus"}
                    and forward_position < reverse_position
                ) or (
                    forward_strand in {"-", "minus"}
                    and reverse_strand in {"+", "plus"}
                    and reverse_position < forward_position
                )
                if not outward_pair:
                    continue
                span = abs(reverse_position - forward_position) + 1
                if span <= maximum_span_bp:
                    pairs.append(
                        {
                            "accession": accession,
                            "forward_position": forward_position,
                            "reverse_position": reverse_position,
                            "forward_strand": forward.get("strand"),
                            "reverse_strand": reverse.get("strand"),
                            "span_bp": span,
                        }
                    )
    return pairs


def _not_evaluated(
    mode: str,
    *,
    objects: list[dict[str, Any]],
    resource: dict[str, Any] | None,
    reason: str,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "mode": mode,
        "status": "NOT_EVALUATED",
        "objects": copy.deepcopy(objects),
        "database": _resource_public(resource),
        "thresholds": copy.deepcopy(THRESHOLDS[mode]),
        "method": "not_run",
        "queries": [],
        "hits": [],
        "reason": reason,
        "error": copy.deepcopy(error),
        "evaluated_at": utc_now(),
        "policy_version": SPECIFICITY_POLICY_VERSION,
    }


def _not_applicable(
    mode: str,
    *,
    reason: str,
    objects: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result = {
        "mode": mode,
        "status": "NOT_APPLICABLE",
        "objects": copy.deepcopy(objects or []),
        "database": None,
        "thresholds": copy.deepcopy(THRESHOLDS[mode]),
        "method": "not_run",
        "queries": [],
        "hits": [],
        "reason": reason,
        "error": None,
        "evaluated_at": utc_now(),
        "policy_version": SPECIFICITY_POLICY_VERSION,
    }
    if mode == "HOST_EXCLUSION":
        result["object_results"] = []
    return result


class WF01V02SpecificityPolicy:
    """Apply the provisional DEC-02 table to normalized BLAST hits."""

    @staticmethod
    def _base(
        mode: str,
        *,
        status: str,
        objects: list[dict[str, Any]],
        resource: dict[str, Any],
        method: str,
        queries: dict[str, str],
        hits: list[dict[str, Any]],
        reason: str,
        mode_specific: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "mode": mode,
            "status": status,
            "objects": copy.deepcopy(objects),
            "database": _resource_public(resource),
            "thresholds": copy.deepcopy(THRESHOLDS[mode]),
            "method": method,
            "queries": [
                {"role": role, "sequence_5to3": sequence}
                for role, sequence in queries.items()
            ],
            "hits": copy.deepcopy(hits),
            "reason": reason,
            "mode_specific": mode_specific,
            "error": None,
            "evaluated_at": utc_now(),
            "policy_version": SPECIFICITY_POLICY_VERSION,
        }

    def target_hit(
        self,
        queries: dict[str, str],
        hits: list[dict[str, Any]],
        *,
        object_: dict[str, Any],
        resource: dict[str, Any],
        method: str,
    ) -> dict[str, Any]:
        if method != "blastn":
            return self._base(
                "TARGET_HIT",
                status="NOT_EVALUATED",
                objects=[object_],
                resource=resource,
                method=method,
                queries=queries,
                hits=hits,
                reason="DEVELOPMENT_FALLBACK_CANNOT_PROVE_TARGET_HIT",
                mode_specific={"fallback_result_ignored": True},
            )
        threshold = THRESHOLDS["TARGET_HIT"]
        per_query: dict[str, str] = {}
        perfect_query_ids: list[str] = []
        for role in queries:
            role_hits = [hit for hit in hits if hit.get("query_id") == role]
            exact = [
                hit
                for hit in role_hits
                if float(hit.get("identity_pct", 0.0)) >= threshold["pass_identity_pct"]
                and float(hit.get("query_coverage_pct", 0.0)) >= threshold["pass_coverage_pct"]
                and hit.get("three_prime_mismatches")
                == threshold["pass_three_prime_mismatches"]
            ]
            caution = [
                hit
                for hit in role_hits
                if float(hit.get("identity_pct", 0.0))
                >= threshold["caution_identity_pct_min"]
                and float(hit.get("query_coverage_pct", 0.0))
                >= threshold["caution_coverage_pct_min"]
            ]
            if exact:
                per_query[role] = "PASS"
                perfect_query_ids.append(role)
            elif caution:
                per_query[role] = "CAUTION"
            else:
                per_query[role] = "FAIL"
        if "FAIL" in per_query.values():
            status = "FAIL"
            reason = "AT_LEAST_ONE_OLIGO_LACKS_REQUIRED_TARGET_HIT"
        elif "CAUTION" in per_query.values():
            status = "CAUTION"
            reason = "TARGET_HIT_NOT_EXACT_FOR_EVERY_OLIGO"
        else:
            status = "PASS"
            reason = "EVERY_OLIGO_HAS_EXACT_TARGET_HIT"
        return self._base(
            "TARGET_HIT",
            status=status,
            objects=[object_],
            resource=resource,
            method=method,
            queries=queries,
            hits=hits,
            reason=reason,
            mode_specific={
                "per_query_status": per_query,
                "perfect_query_ids": sorted(perfect_query_ids),
            },
        )

    def host_exclusion(
        self,
        queries: dict[str, str],
        hits: list[dict[str, Any]],
        *,
        object_: dict[str, Any],
        resource: dict[str, Any],
        method: str,
    ) -> dict[str, Any]:
        if method != "blastn":
            return self._base(
                "HOST_EXCLUSION",
                status="NOT_EVALUATED",
                objects=[object_],
                resource=resource,
                method=method,
                queries=queries,
                hits=hits,
                reason="DEVELOPMENT_FALLBACK_CANNOT_PROVE_HOST_EXCLUSION",
                mode_specific={"fallback_result_ignored": True},
            )
        threshold = THRESHOLDS["HOST_EXCLUSION"]
        relevant = [
            hit for hit in hits if _evalue_below(hit, threshold["evalue_lt"])
        ]
        thermodynamics_missing = [
            hit for hit in relevant if _hit_dg(hit) is None
        ]
        high_risk = [
            hit
            for hit in relevant
            if _hit_dg(hit) is not None
            and float(_hit_dg(hit)) < threshold["high_risk_dg_lt_kcal"]
            and hit.get("three_prime_mismatches") is not None
            and int(hit["three_prime_mismatches"])
            <= threshold["high_risk_three_prime_mismatches_max"]
        ]
        compatible = _compatible_amplicons(
            relevant,
            threshold["compatible_amplicon_span_max_bp"],
        )
        if high_risk or compatible:
            status = "FAIL"
            reason = "HOST_HIGH_RISK_HIT_OR_COMPATIBLE_AMPLICON"
        elif thermodynamics_missing:
            return self._base(
                "HOST_EXCLUSION",
                status="NOT_EVALUATED",
                objects=[object_],
                resource=resource,
                method=method,
                queries=queries,
                hits=hits,
                reason="HOST_HIT_THERMODYNAMICS_NOT_EVALUATED",
                mode_specific={
                    "relevant_hit_count": len(relevant),
                    "high_risk_hit_count": len(high_risk),
                    "thermodynamics_missing_hit_count": len(
                        thermodynamics_missing
                    ),
                    "compatible_amplicons": compatible,
                },
            )
        elif relevant:
            status = "CAUTION"
            reason = "HOST_HITS_REQUIRE_REVIEW"
        else:
            status = "PASS"
            reason = "NO_HOST_HIT_BELOW_EVALUE_THRESHOLD"
        return self._base(
            "HOST_EXCLUSION",
            status=status,
            objects=[object_],
            resource=resource,
            method=method,
            queries=queries,
            hits=hits,
            reason=reason,
            mode_specific={
                "relevant_hit_count": len(relevant),
                "high_risk_hit_count": len(high_risk),
                "thermodynamics_missing_hit_count": len(thermodynamics_missing),
                "compatible_amplicons": compatible,
            },
        )

    def related_background(
        self,
        queries: dict[str, str],
        hits: list[dict[str, Any]],
        *,
        objects: list[dict[str, Any]],
        target_object: dict[str, Any],
        resource: dict[str, Any],
        method: str,
    ) -> dict[str, Any]:
        if method != "blastn":
            return self._base(
                "RELATED_BACKGROUND_NT",
                status="NOT_EVALUATED",
                objects=objects,
                resource=resource,
                method=method,
                queries=queries,
                hits=hits,
                reason="DEVELOPMENT_FALLBACK_CANNOT_PROVE_NT_SPECIFICITY",
                mode_specific={"fallback_result_ignored": True},
            )
        threshold = THRESHOLDS["RELATED_BACKGROUND_NT"]
        target_tokens = {token.casefold() for token in _object_tokens(target_object)}
        target_accessions = {
            token
            for value in (
                [target_object.get("accession"), target_object.get("identifier")]
                + list(target_object.get("known_accessions") or [])
            )
            if (token := _accession_token(value))
        }
        cross_hits: list[dict[str, Any]] = []
        for hit in hits:
            if not _evalue_below(hit, threshold["evalue_lt"]):
                continue
            hit_tokens = {
                str(hit.get("accession") or "").casefold(),
                str(hit.get("taxid") or "").casefold(),
                str(_scientific_name(hit) or "").casefold(),
            }
            if target_tokens & {token for token in hit_tokens if token}:
                continue
            if _accession_token(hit.get("accession")) in target_accessions:
                continue
            cross_hits.append(hit)
        high_risk = [
            hit
            for hit in cross_hits
            if float(hit.get("identity_pct", 0.0))
            >= threshold["high_risk_identity_pct_min"]
            and float(hit.get("query_coverage_pct", 0.0))
            >= threshold["high_risk_coverage_pct_min"]
            and hit.get("three_prime_mismatches") is not None
            and int(hit["three_prime_mismatches"])
            <= threshold["high_risk_three_prime_mismatches_max"]
        ]
        compatible = _compatible_amplicons(
            cross_hits,
            threshold["compatible_amplicon_span_max_bp"],
        )
        if compatible:
            status = "FAIL"
            reason = "NON_TARGET_COMPATIBLE_AMPLICON"
        elif high_risk:
            status = "CAUTION"
            reason = "HIGH_SIMILARITY_RELATED_OR_BACKGROUND_HIT"
        else:
            status = "PASS"
            reason = "NO_RELEVANT_NON_TARGET_HIT"
        return self._base(
            "RELATED_BACKGROUND_NT",
            status=status,
            objects=objects,
            resource=resource,
            method=method,
            queries=queries,
            hits=cross_hits,
            reason=reason,
            mode_specific={
                "cross_hit_count": len(cross_hits),
                "high_risk_hit_count": len(high_risk),
                "compatible_amplicons": compatible,
                "scientific_names": sorted(
                    {
                        name
                        for hit in cross_hits
                        if (name := _scientific_name(hit)) is not None
                    }
                ),
            },
        )


class WF01V02M4Engine:
    """Evaluate every M3 hard-rule-pass candidate and rerank qualified results."""

    def __init__(
        self,
        catalog: DatabaseCatalog,
        config: dict[str, Any],
        *,
        allow_development_fallback: bool = False,
        resolver: WF01V02SpecificityResourceResolver | None = None,
        provider: WF01V02BlastSearchProvider | None = None,
        remote_provider: NCBIBlastMCPProvider | None = None,
    ) -> None:
        self.resolver = resolver or WF01V02SpecificityResourceResolver(catalog)
        # The specificity provider is intentionally lazy.  A DESIGN_ONLY target
        # must be able to pass through M4 without probing BLAST or any database.
        self.provider = provider
        self.remote_provider = remote_provider
        self._config = copy.deepcopy(config)
        self._allow_development_fallback = allow_development_fallback
        self.policy = WF01V02SpecificityPolicy()
        self.scoring = WF01V02ScoringEngine()

    def _get_provider(self) -> WF01V02BlastSearchProvider:
        if self.provider is None:
            self.provider = WF01V02BlastSearchProvider(
                self._config,
                allow_development_fallback=self._allow_development_fallback,
            )
        return self.provider

    def _get_remote_provider(self) -> NCBIBlastMCPProvider:
        if self.remote_provider is None:
            self.remote_provider = NCBIBlastMCPProvider()
        return self.remote_provider

    @staticmethod
    def _resource_ready(resource: dict[str, Any] | None) -> bool:
        return bool(resource and resource.get("resolution_status") == "RESOLVED")

    def _search(
        self,
        queries: dict[str, str],
        resource: dict[str, Any] | None,
        *,
        mode: str,
        evalue: float,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if not self._resource_ready(resource):
            return None, None
        provider = self._get_provider()
        try:
            result = provider.search(
                queries,
                resource,
                mode=mode,
                evalue=evalue,
            )
            if (
                not isinstance(result, dict)
                or not isinstance(result.get("hits"), list)
                or not str(result.get("method") or "").strip()
            ):
                return None, {
                    "code": "SPECIFICITY_TOOL_INVALID_RESULT",
                    "message": "特异性工具返回结构不完整",
                    "details": {"mode": mode},
                }
            result.setdefault(
                "raw_tool_output",
                {
                    "tool": "BLAST+",
                    "status": "COMPLETED",
                    "mode": mode,
                    "database": copy.deepcopy(resource),
                    "evalue": evalue,
                    "queries": copy.deepcopy(queries),
                    "raw_output": copy.deepcopy(result["hits"]),
                },
            )
            return result, None
        except AgentDError as exc:
            error = exc.to_dict()
            blast = getattr(provider, "blast", None)
            evidence = getattr(blast, "last_search_evidence", None)
            if isinstance(evidence, dict) and evidence:
                error["raw_tool_output"] = copy.deepcopy(evidence)
            return None, error
        except (
            OSError,
            RuntimeError,
            UnicodeError,
            ValueError,
            subprocess.TimeoutExpired,
        ) as exc:
            error = {
                "code": "SPECIFICITY_TOOL_RUNTIME_ERROR",
                "message": str(exc),
                "details": {
                    "exception_type": type(exc).__name__,
                    "mode": mode,
                },
            }
            blast = getattr(provider, "blast", None)
            evidence = getattr(blast, "last_search_evidence", None)
            if isinstance(evidence, dict) and evidence:
                error["raw_tool_output"] = copy.deepcopy(evidence)
            return None, error

    def _search_related_background(
        self,
        queries: dict[str, str],
        resource: dict[str, Any] | None,
        *,
        target_object: dict[str, Any],
    ) -> tuple[
        dict[str, Any] | None,
        dict[str, Any] | None,
        dict[str, Any] | None,
    ]:
        """Prefer the versioned local nt DB and fall back to remote NCBI MCP."""

        if self._resource_ready(resource):
            result, error = self._search(
                queries,
                resource,
                mode="RELATED_BACKGROUND_NT",
                evalue=THRESHOLDS["RELATED_BACKGROUND_NT"]["evalue_lt"],
            )
            return result, error, resource

        remote = self._get_remote_provider()
        if not remote.configured:
            return None, None, resource
        scope = copy.deepcopy((resource or {}).get("scope") or {})
        scope.update(
            {
                "role": "RELATED_BACKGROUND",
                "mode": "BROAD_NCBI_NT_BACKGROUND",
                "target_object_id": target_object.get("object_id"),
            }
        )
        effective_resource = remote.resource(scope=scope)
        exclusions = [
            str(target_object.get("scientific_name") or "").strip(),
        ]
        exclusions = [value for value in exclusions if value]
        try:
            result = remote.search(
                queries,
                evalue=THRESHOLDS["RELATED_BACKGROUND_NT"]["evalue_lt"],
                exclude_organisms=exclusions,
            )
            if (
                not isinstance(result, dict)
                or not isinstance(result.get("hits"), list)
                or str(result.get("method") or "") != "blastn"
            ):
                return None, {
                    "code": "SPECIFICITY_TOOL_INVALID_RESULT",
                    "message": "远程 NCBI BLAST 返回结果不完整",
                    "details": {"mode": "RELATED_BACKGROUND_NT"},
                }, effective_resource
            return result, None, effective_resource
        except AgentDError as exc:
            error = exc.to_dict()
        except (OSError, RuntimeError, UnicodeError, ValueError) as exc:
            error = {
                "code": "SPECIFICITY_TOOL_RUNTIME_ERROR",
                "message": str(exc),
                "details": {
                    "exception_type": type(exc).__name__,
                    "mode": "RELATED_BACKGROUND_NT",
                },
            }
        evidence = getattr(remote, "last_search_evidence", None)
        if isinstance(evidence, dict) and evidence:
            error["raw_tool_output"] = copy.deepcopy(evidence)
        return None, error, effective_resource

    @staticmethod
    def _overall_status(mode_results: list[dict[str, Any]]) -> str:
        statuses = {result["status"] for result in mode_results}
        if "FAIL" in statuses:
            return "FAIL"
        if "NOT_EVALUATED" in statuses:
            return "NOT_EVALUATED"
        if "CAUTION" in statuses:
            return "CAUTION"
        return "PASS"

    @staticmethod
    def _set_r19(candidate: dict[str, Any], specificity: dict[str, Any]) -> None:
        status_map = {
            "PASS": "PASS",
            "CAUTION": "WARNING",
            "FAIL": "FAIL",
            "NOT_EVALUATED": "NOT_EVALUATED",
        }
        replacement = {
            "code": "R19",
            "category": "specificity",
            "status": status_map[specificity["overall_status"]],
            "actual": {
                "overall_status": specificity["overall_status"],
                "target_hit": specificity["target_hit"]["status"],
                "host_exclusion": specificity["host_exclusion"]["status"],
                "related_background_nt": specificity["related_background_nt"]["status"],
            },
            "threshold": "靶标命中和背景检查必须完成；提供宿主时追加宿主专库排除",
            "source": "WF01-v0.2 M4 specificity engine",
            "message": specificity.get("reason", ""),
        }
        rules = candidate.get("rules") or []
        updated = [
            replacement if rule.get("code") == "R19" else rule for rule in rules
        ]
        if not any(rule.get("code") == "R19" for rule in rules):
            updated.append(replacement)
        candidate["rules"] = updated

    @staticmethod
    def _set_r20(candidate: dict[str, Any]) -> None:
        replacement = {
            "code": "R20",
            "category": "metadata",
            "status": "PASS",
            "actual": {
                "evidence_level": candidate["evidence_level"],
                "experimental_validation": False,
            },
            "threshold": "明确证据等级和实验验证状态",
            "source": "WF01-v0.2 M4 specificity engine",
            "message": "M4 已同步特异性证据等级；结果仍需实验验证",
        }
        rules = candidate.get("rules") or []
        updated = [
            replacement if rule.get("code") == "R20" else rule for rule in rules
        ]
        if not any(rule.get("code") == "R20" for rule in rules):
            updated.append(replacement)
        candidate["rules"] = updated

    @staticmethod
    def _final_rank_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
        return final_scoring_rank_key(candidate)

    def _promote_design_only_score(
        self,
        candidate: dict[str, Any],
    ) -> float | None:
        """Promote M3's two-dimensional priority into the final design score.

        M3 deliberately leaves the public score empty while it does not yet
        know which route M4 will execute.  In DESIGN_ONLY, specificity is not a
        missing scoring calculation: it is outside the selected route.  The
        already calculated thermodynamics/amplicon score therefore becomes the
        final score for that route, while specificity remains explicitly
        NOT_EVALUATED in the scientific evidence.
        """
        metadata = candidate.setdefault("metadata", {})
        scoring = copy.deepcopy(metadata.get("wf01_v02_scoring") or {})
        if not scoring.get("pre_specificity_priority"):
            scoring = self.scoring.score(candidate)
        priority = scoring.get("pre_specificity_priority") or {}
        score = priority.get("after_penalties")
        base_score = priority.get("base")
        if score is None or base_score is None:
            metadata["wf01_v02_scoring"] = scoring
            return None

        evaluated_weight = (
            SCORING_WEIGHTS["thermodynamics"]
            + SCORING_WEIGHTS["amplicon_features"]
        )
        effective_weights = {
            "thermodynamics": round(
                SCORING_WEIGHTS["thermodynamics"] / evaluated_weight,
                6,
            ),
            "specificity": 0.0,
            "amplicon_features": round(
                SCORING_WEIGHTS["amplicon_features"] / evaluated_weight,
                6,
            ),
        }
        dimensions = scoring.setdefault("dimensions", {})
        for name, detail in dimensions.items():
            if isinstance(detail, dict):
                detail["effective_weight"] = effective_weights.get(name, 0.0)
                detail["participates_in_score"] = name != "specificity"

        scoring.update(
            {
                "status": "FINAL_DESIGN_ONLY",
                "score_type": "DESIGN_SCORE",
                "score_label": "设计评分",
                "execution_path": "DESIGN_ONLY",
                "score_scope": ["thermodynamics", "amplicon_features"],
                "effective_weights": effective_weights,
                "base_score": float(base_score),
                "final_score": float(score),
                "formula_note": (
                    "设计评分仅由热力学与扩增子特征归一化加权，"
                    "再应用软规则扣分；特异性不参与本路线评分"
                ),
            }
        )
        metadata["wf01_v02_scoring"] = scoring
        candidate["score"] = float(score)
        return float(score)

    @staticmethod
    def _mark_full_specificity_score(scoring: dict[str, Any]) -> None:
        scoring.update(
            {
                "score_type": "COMPOSITE_SCORE",
                "score_label": "综合评分",
                "execution_path": "FULL_SPECIFICITY",
                "score_scope": [
                    "thermodynamics",
                    "specificity",
                    "amplicon_features",
                ],
                "effective_weights": copy.deepcopy(SCORING_WEIGHTS),
                "formula_note": (
                    "综合评分由热力学、特异性与扩增子特征三维加权，"
                    "再应用软规则扣分"
                ),
            }
        )
        for name, detail in (scoring.get("dimensions") or {}).items():
            if isinstance(detail, dict):
                detail["effective_weight"] = SCORING_WEIGHTS.get(name, 0.0)
                detail["participates_in_score"] = True

    def run_target(self, target: dict[str, Any]) -> dict[str, Any]:
        target_id = str(target.get("target_id") or "")
        m3 = target.get("m3_execution")
        if not isinstance(m3, dict) or m3.get("stage") != "M3_COMPLETE_AWAITING_M4":
            raise InputValidationError(
                "靶标缺少完整 M3 结果，不能进入 M4",
                details={"target_id": target_id},
            )
        execution_path = str(target.get("execution_path") or "").upper()
        if execution_path not in {"DESIGN_ONLY", "FULL_SPECIFICITY"}:
            execution_path = (
                "FULL_SPECIFICITY"
                if target.get("target_object")
                else "DESIGN_ONLY"
            )
        candidates = copy.deepcopy(m3.get("all_hard_rule_pass_candidates") or [])
        if execution_path == "DESIGN_ONLY":
            # The routing decision is authoritative: stale organism values must
            # never turn a sequence-only design into an implicit BLAST task.
            target_object = {}
            host_objects = []
            related_background_objects = []
        else:
            target_object = copy.deepcopy(target.get("target_object") or {})
            host_objects = copy.deepcopy(target.get("host_objects") or [])
            related_background_objects = copy.deepcopy(
                (target.get("related_objects") or [])
                + (target.get("background_objects") or [])
            )
        queries, query_maps = _candidate_queries(candidates)
        # Missing target context is an intentional sequence-only mode, not
        # a request to fall through to a target:default database.
        target_resource = (
            self.resolver.resolve_target(target) if target_object else None
        )
        host_resources = [
            (object_, self.resolver.resolve_host(object_)) for object_ in host_objects
        ]
        related_resource = (
            self.resolver.resolve_related_background(
                related_background_objects,
                target_object=target_object,
            )
            if target_object
            else None
        )

        target_search, target_error = (
            self._search(
                queries,
                target_resource,
                mode="TARGET_HIT",
                evalue=THRESHOLDS["TARGET_HIT"]["search_evalue"],
            )
            if candidates
            else (None, None)
        )
        host_searches: list[
            tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]
        ] = []
        for object_, resource in host_resources:
            search, error = (
                self._search(
                    queries,
                    resource,
                    mode="HOST_EXCLUSION",
                    evalue=THRESHOLDS["HOST_EXCLUSION"]["evalue_lt"],
                )
                if candidates
                else (None, None)
            )
            host_searches.append((object_, resource, search, error))
        related_search, related_error, effective_related_resource = (
            self._search_related_background(
                queries,
                related_resource,
                target_object=target_object,
            )
            if target_object and candidates
            else (None, None, related_resource)
        )
        related_resource = effective_related_resource

        qualified: list[dict[str, Any]] = []
        specificity_failed: list[dict[str, Any]] = []
        specificity_incomplete: list[dict[str, Any]] = []
        for index, candidate in enumerate(candidates):
            query_ids = query_maps[index]
            role_queries = {
                role: queries[query_id] for role, query_id in query_ids.items()
            }
            if not target_object:
                target_result = _not_evaluated(
                    "TARGET_HIT",
                    objects=[],
                    resource=target_resource,
                    reason="TARGET_OBJECT_MISSING",
                )
            elif not self._resource_ready(target_resource):
                target_result = _not_evaluated(
                    "TARGET_HIT",
                    objects=[target_object],
                    resource=target_resource,
                    reason="TARGET_DATABASE_OR_VERSION_NOT_RESOLVED",
                )
            elif target_error:
                target_result = _not_evaluated(
                    "TARGET_HIT",
                    objects=[target_object],
                    resource=target_resource,
                    reason="TARGET_SEARCH_TOOL_ERROR",
                    error=target_error,
                )
            else:
                target_result = self.policy.target_hit(
                    role_queries,
                    _candidate_hits(target_search["hits"], query_ids),
                    object_=target_object,
                    resource=target_resource,
                    method=str(target_search["method"]),
                )

            host_object_results: list[dict[str, Any]] = []
            for object_, resource, search, error in host_searches:
                if not self._resource_ready(resource):
                    result = _not_evaluated(
                        "HOST_EXCLUSION",
                        objects=[object_],
                        resource=resource,
                        reason="HOST_DATABASE_OR_VERSION_NOT_RESOLVED",
                    )
                elif error:
                    result = _not_evaluated(
                        "HOST_EXCLUSION",
                        objects=[object_],
                        resource=resource,
                        reason="HOST_SEARCH_TOOL_ERROR",
                        error=error,
                    )
                else:
                    result = self.policy.host_exclusion(
                        role_queries,
                        _candidate_hits(search["hits"], query_ids),
                        object_=object_,
                        resource=resource,
                        method=str(search["method"]),
                    )
                host_object_results.append(result)
            if host_object_results:
                host_result = {
                    "mode": "HOST_EXCLUSION",
                    "status": self._overall_status(host_object_results),
                    "object_results": host_object_results,
                    "objects": host_objects,
                    "reason": "HOST_EXCLUSION_EVALUATED_FOR_REQUESTED_HOSTS",
                    "evaluated_at": utc_now(),
                    "policy_version": SPECIFICITY_POLICY_VERSION,
                }
            else:
                host_result = _not_applicable(
                    "HOST_EXCLUSION",
                    reason="HOST_NOT_PROVIDED_OPTIONAL_CHECK_SKIPPED",
                )

            if not target_object:
                related_result = _not_evaluated(
                    "RELATED_BACKGROUND_NT",
                    objects=related_background_objects,
                    resource=related_resource,
                    reason="TARGET_OBJECT_MISSING",
                )
            elif not self._resource_ready(related_resource):
                related_result = _not_evaluated(
                    "RELATED_BACKGROUND_NT",
                    objects=related_background_objects,
                    resource=related_resource,
                    reason="NT_DATABASE_OR_VERSION_NOT_RESOLVED",
                )
            elif related_error:
                related_result = _not_evaluated(
                    "RELATED_BACKGROUND_NT",
                    objects=related_background_objects,
                    resource=related_resource,
                    reason="NT_SEARCH_TOOL_ERROR",
                    error=related_error,
                )
            else:
                related_result = self.policy.related_background(
                    role_queries,
                    _candidate_hits(related_search["hits"], query_ids),
                    objects=related_background_objects,
                    target_object=target_object,
                    resource=related_resource,
                    method=str(related_search["method"]),
                )

            # TARGET_HIT and RELATED_BACKGROUND_NT are required whenever a
            # target is present. HOST_EXCLUSION is required only when the user
            # explicitly supplied one or more hosts.
            mode_results = [target_result, related_result]
            if host_object_results:
                mode_results.append(host_result)
            overall = self._overall_status(mode_results)
            specificity_score = SPECIFICITY_SCORE_BY_STATUS.get(overall)
            specificity = {
                "policy_version": SPECIFICITY_POLICY_VERSION,
                "policy_status": SPECIFICITY_POLICY_STATUS,
                "overall_status": overall,
                "specificity_score": specificity_score,
                "required_modes": ["TARGET_HIT", "RELATED_BACKGROUND_NT"],
                "optional_modes": ["HOST_EXCLUSION"],
                "host_exclusion_requested": bool(host_objects),
                "target_hit": target_result,
                "host_exclusion": host_result,
                "related_background_nt": related_result,
                "reason": {
                    "PASS": "ALL_REQUIRED_SPECIFICITY_MODES_PASS",
                    "CAUTION": "AT_LEAST_ONE_MODE_REQUIRES_REVIEW",
                    "FAIL": "AT_LEAST_ONE_MODE_FAILED",
                    "NOT_EVALUATED": "AT_LEAST_ONE_REQUIRED_MODE_NOT_EVALUATED",
                }[overall],
            }
            candidate["specificity"] = specificity
            self._set_r19(candidate, specificity)
            candidate["evidence_level"] = (
                "blast_verified"
                if overall in {"PASS", "CAUTION"}
                and target_result.get("method") == "blastn"
                and all(
                    result.get("method") == "blastn"
                    for result in host_object_results
                )
                and related_result.get("method") == "blastn"
                else "in_silico_only"
            )
            self._set_r20(candidate)
            candidate["specificity_status"] = overall
            rule_summary = candidate.setdefault("metadata", {}).setdefault(
                "wf01_v02_rule_summary",
                {},
            )
            rule_summary["awaiting_specificity"] = False
            rule_summary["specificity_status"] = overall
            if overall == "CAUTION":
                candidate["warnings"] = sorted(
                    set((candidate.get("warnings") or []) + ["R19"])
                )
            if overall == "FAIL":
                candidate["verdict"] = "REJECTED_SPECIFICITY"
                candidate["score"] = None
                candidate["rank"] = None
                specificity_failed.append(candidate)
            elif overall == "NOT_EVALUATED":
                candidate["verdict"] = "SPECIFICITY_NOT_EVALUATED"
                candidate["rank"] = None
                if execution_path == "DESIGN_ONLY":
                    self._promote_design_only_score(candidate)
                else:
                    candidate["score"] = None
                candidate.setdefault("metadata", {})["specificity_follow_up"] = {
                    "status": "AWAITING_REQUIRED_SPECIFICITY_EVIDENCE",
                    "missing_fields": copy.deepcopy(
                        target.get("specificity_missing_fields") or []
                    ),
                    "message": "补充缺失靶标信息或恢复所需数据库/工具后重新运行特异性评估",
                }
                specificity_incomplete.append(candidate)
            else:
                scoring = self.scoring.score(
                    candidate,
                    specificity_score=float(specificity_score),
                )
                self._mark_full_specificity_score(scoring)
                candidate.setdefault("metadata", {})["wf01_v02_scoring"] = scoring
                candidate["score"] = scoring["final_score"]
                candidate["verdict"] = (
                    "PASS_WITH_WARNINGS"
                    if overall == "CAUTION"
                    or bool(scoring["penalties"])
                    else "PASS"
                )
                qualified.append(candidate)

        qualified.sort(key=self._final_rank_key)
        for rank, candidate in enumerate(qualified, start=1):
            candidate["rank"] = rank
        design_ranked: list[dict[str, Any]] = []
        if execution_path == "DESIGN_ONLY":
            design_ranked = [
                candidate
                for candidate in specificity_incomplete
                if candidate.get("score") is not None
            ]
            design_ranked.sort(key=self._final_rank_key)
            for rank, candidate in enumerate(design_ranked, start=1):
                candidate["rank"] = rank
            ranked_ids = {id(candidate) for candidate in design_ranked}
            specificity_incomplete = design_ranked + [
                candidate
                for candidate in specificity_incomplete
                if id(candidate) not in ranked_ids
            ]
            top10 = copy.deepcopy(design_ranked[:FIXED_TOP_CANDIDATES])
        else:
            top10 = copy.deepcopy(qualified[:FIXED_TOP_CANDIDATES])
        all_specificity_evaluated = not specificity_incomplete
        primer3_complete = bool(m3.get("primer3_search_complete"))
        technical_search_complete = primer3_complete and all_specificity_evaluated
        design_ranking_complete = (
            execution_path == "DESIGN_ONLY"
            and primer3_complete
            and len(design_ranked) == len(specificity_incomplete)
        )
        if self.provider is None:
            provider_status = {
                "status": "NOT_INITIALIZED",
                "reason": (
                    "SKIPPED_MISSING_CONTEXT"
                    if execution_path == "DESIGN_ONLY"
                    else "NO_RESOLVED_SPECIFICITY_RESOURCE"
                ),
            }
        elif callable(getattr(self.provider, "status", None)):
            provider_status = self.provider.status()
        else:
            provider_status = {"tool": type(self.provider).__name__}
        if self.remote_provider is None:
            remote_provider_status = {
                "status": "NOT_INITIALIZED",
                "reason": (
                    "LOCAL_RELATED_BACKGROUND_USED"
                    if self._resource_ready(related_resource)
                    else "REMOTE_BACKGROUND_NOT_CONFIGURED_OR_NOT_NEEDED"
                ),
            }
        elif callable(getattr(self.remote_provider, "status", None)):
            remote_provider_status = self.remote_provider.status()
        else:
            remote_provider_status = {"tool": type(self.remote_provider).__name__}
        return {
            "stage": "M4_COMPLETE_AWAITING_M5",
            "engine_version": M4_ENGINE_VERSION,
            "target_id": target_id,
            "execution_path": execution_path,
            "specificity_execution_status": (
                "SKIPPED_MISSING_CONTEXT"
                if execution_path == "DESIGN_ONLY"
                else "COMPLETED"
                if all_specificity_evaluated
                else "NOT_EVALUATED"
            ),
            "policy_version": SPECIFICITY_POLICY_VERSION,
            "policy_status": SPECIFICITY_POLICY_STATUS,
            "provider_status": provider_status,
            "remote_background_provider_status": remote_provider_status,
            "resource_resolution": {
                "target": _resource_public(target_resource),
                "hosts": [
                    {
                        "object_id": object_.get("object_id"),
                        "resource": _resource_public(resource),
                    }
                    for object_, resource in host_resources
                ],
                "related_background": _resource_public(related_resource),
            },
            "raw_search_outputs": {
                "target": {
                    "object_id": target_object.get("object_id"),
                    "resource": copy.deepcopy(target_resource),
                    "result": copy.deepcopy(target_search),
                    "error": copy.deepcopy(target_error),
                },
                "hosts": [
                    {
                        "object_id": object_.get("object_id"),
                        "resource": copy.deepcopy(resource),
                        "result": copy.deepcopy(search),
                        "error": copy.deepcopy(error),
                    }
                    for object_, resource, search, error in host_searches
                ],
                "related_background": {
                    "object_ids": [
                        item.get("object_id")
                        for item in related_background_objects
                    ],
                    "resource": copy.deepcopy(related_resource),
                    "result": copy.deepcopy(related_search),
                    "error": copy.deepcopy(related_error),
                },
            },
            "candidate_counts": {
                "hard_rule_pass_input": len(candidates),
                "specificity_qualified": len(qualified),
                "specificity_failed": len(specificity_failed),
                "specificity_not_evaluated": len(specificity_incomplete),
                "top10": len(top10),
            },
            "design_search_complete": primer3_complete,
            "specificity_complete": all_specificity_evaluated,
            "technical_search_complete": technical_search_complete,
            "search_complete": False,
            "search_incomplete_reason": (
                "SPECIFICITY_SKIPPED_MISSING_CONTEXT"
                if execution_path == "DESIGN_ONLY"
                else
                "DEC_01_DEC_02_POLICY_APPROVAL_PENDING"
                if technical_search_complete
                else "SPECIFICITY_OR_PRIMER3_INCOMPLETE"
            ),
            "ranking_status": (
                "FINAL_DESIGN_ONLY"
                if design_ranking_complete
                else "INCOMPLETE_DESIGN_SCORE"
                if execution_path == "DESIGN_ONLY"
                else
                "FINAL_UNDER_PROVISIONAL_POLICY"
                if technical_search_complete
                else "INCOMPLETE_SPECIFICITY"
            ),
            "ranking_basis": (
                "M4_DESIGN_SCORE"
                if execution_path == "DESIGN_ONLY"
                else "M4_FULL_SPECIFICITY_SCORE"
                if technical_search_complete
                else "INCOMPLETE_SPECIFICITY"
            ),
            "top10_is_final_under_current_policy": (
                design_ranking_complete
                if execution_path == "DESIGN_ONLY"
                else technical_search_complete
            ),
            "top10": top10,
            "all_qualified_candidates": qualified,
            "all_specificity_failed_candidates": specificity_failed,
            "all_specificity_incomplete_candidates": specificity_incomplete,
            "hard_rule_rejected_candidates": copy.deepcopy(
                m3.get("all_hard_rule_rejected_candidates") or []
            ),
            "hard_rule_incomplete_candidates": copy.deepcopy(
                m3.get("all_hard_rule_incomplete_candidates") or []
            ),
        }
