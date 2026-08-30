"""Frozen WF-01 candidate-output contract consumed by WF-02.

This module deliberately owns no WF-01 decision rules. It validates the shape
and version of an upstream report, then preserves the ordering of the
authoritative candidate collection. A downstream workflow must not inspect or
reinterpret verdict, evidence, policy, or rule-status fields here.
"""

from __future__ import annotations

import copy
from typing import Any


WF01_CANDIDATE_CONTRACT_VERSION = "wf01-candidate-contract-v1"
WF01_CANONICAL_REPORT_SCHEMA_VERSION = "wf01-prd-v0.2-canonical-report-v2"
WF01_CANDIDATE_COLLECTION_PRIORITY = (
    "all_qualified_candidates",
    "top10",
)


def wf01_candidate_contract_manifest() -> dict[str, Any]:
    """Return a serialisable dependency snapshot for task provenance."""

    return {
        "contract_version": WF01_CANDIDATE_CONTRACT_VERSION,
        "upstream_workflow": "WF-01",
        "supported_report_schema_versions": [
            WF01_CANONICAL_REPORT_SCHEMA_VERSION
        ],
        "candidate_collection_priority": list(
            WF01_CANDIDATE_COLLECTION_PRIORITY
        ),
        "selection": "PRESERVE_UPSTREAM_ORDER_FIRST_N",
        "downstream_business_reinterpretation": "DISABLED",
    }


def _invalid_evidence(
    code: str,
    *,
    report_schema_version: Any = None,
    **details: Any,
) -> dict[str, Any]:
    return {
        "contract_version": WF01_CANDIDATE_CONTRACT_VERSION,
        "contract_status": "INVALID",
        "code": code,
        "upstream_report_schema_version": report_schema_version,
        "details": copy.deepcopy(details),
    }


def select_wf01_candidates(
    report: dict[str, Any],
    *,
    limit: int = 5,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select upstream-qualified candidates without downstream re-judgement.

    Only report structure, schema version, candidate identity, and collection
    integrity are validated. Status-like fields are intentionally opaque.
    """

    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")
    if not isinstance(report, dict):
        return [], _invalid_evidence("WF01_REPORT_NOT_AN_OBJECT")

    schema_version = report.get("schema_version")
    if schema_version != WF01_CANONICAL_REPORT_SCHEMA_VERSION:
        return [], _invalid_evidence(
            "WF01_REPORT_SCHEMA_UNSUPPORTED",
            report_schema_version=schema_version,
            supported_schema_versions=[
                WF01_CANONICAL_REPORT_SCHEMA_VERSION
            ],
        )

    targets = report.get("targets")
    if not isinstance(targets, list) or not targets:
        return [], _invalid_evidence(
            "WF01_REPORT_HAS_NO_TARGET",
            report_schema_version=schema_version,
        )
    target = targets[0]
    if not isinstance(target, dict):
        return [], _invalid_evidence(
            "WF01_REPORT_TARGET_NOT_AN_OBJECT",
            report_schema_version=schema_version,
        )

    collection_name = next(
        (
            name
            for name in WF01_CANDIDATE_COLLECTION_PRIORITY
            if name in target
        ),
        None,
    )
    if collection_name is None:
        return [], _invalid_evidence(
            "WF01_REPORT_CANDIDATE_COLLECTION_MISSING",
            report_schema_version=schema_version,
            expected_collections=list(WF01_CANDIDATE_COLLECTION_PRIORITY),
        )

    source = target.get(collection_name)
    if not isinstance(source, list):
        return [], _invalid_evidence(
            "WF01_REPORT_CANDIDATE_COLLECTION_INVALID",
            report_schema_version=schema_version,
            collection=collection_name,
        )

    candidates: list[dict[str, Any]] = []
    candidate_ids: set[str] = set()
    for ordinal, candidate in enumerate(source, start=1):
        if not isinstance(candidate, dict):
            return [], _invalid_evidence(
                "WF01_REPORT_CANDIDATE_NOT_AN_OBJECT",
                report_schema_version=schema_version,
                collection=collection_name,
                ordinal=ordinal,
            )
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        if not candidate_id:
            return [], _invalid_evidence(
                "WF01_REPORT_CANDIDATE_ID_MISSING",
                report_schema_version=schema_version,
                collection=collection_name,
                ordinal=ordinal,
            )
        if candidate_id in candidate_ids:
            return [], _invalid_evidence(
                "WF01_REPORT_DUPLICATE_CANDIDATE_ID",
                report_schema_version=schema_version,
                collection=collection_name,
                candidate_id=candidate_id,
            )
        candidate_ids.add(candidate_id)
        candidates.append(copy.deepcopy(candidate))

    selected = candidates[:limit]
    conclusion = copy.deepcopy(target.get("conclusion") or {})
    return selected, {
        "contract_version": WF01_CANDIDATE_CONTRACT_VERSION,
        "contract_status": "VALID",
        "upstream_report_schema_version": schema_version,
        "child_target_id": target.get("target_id"),
        "source_collection": collection_name,
        "reported_candidate_count": len(candidates),
        "selected_candidate_count": len(selected),
        "selection_limit": limit,
        "selection": "PRESERVE_UPSTREAM_ORDER_FIRST_N",
        "downstream_business_reinterpretation": "DISABLED",
        "conclusion": conclusion,
    }
