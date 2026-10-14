"""WF-01 PRD-v0.2 M4 orchestration around the authoritative P2 tool.

M4 resolves workflow resources, prepares P2 inputs, maps the P2 result into
WF-01 rules, and performs final ranking. All specificity searches, evidence
thresholds, hit interpretation, and three-mode verdicts belong to P2.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from .database import DatabaseCatalog
from .errors import InputValidationError
from .specificity import (
    P2_POLICY_STATUS,
    P2_POLICY_VERSION,
    P2SpecificityBatchService,
    p2_design_only_result,
)
from .sequence import normalize_sequence
from .wf01_v02_m3 import (
    FIXED_TOP_CANDIDATES,
    SCORING_WEIGHTS,
    WF01V02ScoringEngine,
    final_scoring_rank_key,
)


M4_ENGINE_VERSION = "wf01-v0.2-m4.7-p2-gateway"


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
        "record_count": resource.get("record_count"),
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


class WF01V02SpecificityResourceResolver:
    """Resolve versioned P2 resources without guessing external identifiers."""

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
        catalog_resource = self._resolve_object(
            "target",
            object_,
            extra_keys=[
                f"target:{target.get('target_id')}:blast",
                f"target:{target.get('target_id')}",
                "target:default:blast",
                "target:default",
            ],
        )
        if (
            catalog_resource
            and catalog_resource.get("resolution_status") == "RESOLVED"
        ):
            return catalog_resource

        # Every WF-01 child carries the immutable input snapshot that produced
        # its candidates.  Materialize that snapshot as a task-local search
        # subject when no separately managed target resource is available.
        raw_templates = target.get("templates")
        if not isinstance(raw_templates, list) or not raw_templates:
            template = target.get("template")
            raw_templates = [template] if isinstance(template, dict) else []
        records: list[dict[str, str]] = []
        for ordinal, template in enumerate(raw_templates, start=1):
            if not isinstance(template, dict):
                continue
            sequence = str(template.get("sequence") or "").strip()
            if not sequence:
                continue
            normalized = normalize_sequence(sequence)
            records.append(
                {
                    "id": f"input_{ordinal:04d}",
                    "sequence": normalized,
                    "checksum_sha256": hashlib.sha256(
                        normalized.encode("ascii")
                    ).hexdigest(),
                }
            )
        if not records:
            return catalog_resource

        digest = hashlib.sha256(
            json.dumps(
                records,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        target_id = str(target.get("target_id") or "target")
        return {
            "key": f"target:{target_id}:input-snapshot",
            "kind": "inline_fasta",
            "path": None,
            "uri": None,
            "version": f"sha256:{digest}",
            "version_valid": True,
            "exists": True,
            "scope": {
                "role": "TARGET",
                "object_id": object_.get("object_id"),
                "identifier_type": object_.get("identifier_type"),
                "identifier": object_.get("identifier"),
                "source": "WF01_INPUT_SNAPSHOT",
            },
            "resolution_status": "RESOLVED",
            "resolution_reason": None,
            "version_semantics": "IMMUTABLE_INPUT_SNAPSHOT_SHA256",
            "record_count": len(records),
            "records": records,
            "catalog_fallback": _resource_public(catalog_resource),
        }

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


class WF01V02M4Engine:
    """Map P2 specificity evidence and rerank M3 hard-rule-pass candidates."""

    def __init__(
        self,
        catalog: DatabaseCatalog,
        config: dict[str, Any],
        *,
        allow_development_fallback: bool = False,
        resolver: WF01V02SpecificityResourceResolver | None = None,
        provider: Any | None = None,
        remote_provider: Any | None = None,
        p2_service: P2SpecificityBatchService | None = None,
    ) -> None:
        self.resolver = resolver or WF01V02SpecificityResourceResolver(catalog)
        self.provider = provider
        self.remote_provider = remote_provider
        self.p2_service = p2_service
        self._config = copy.deepcopy(config)
        self._allow_development_fallback = allow_development_fallback
        self.scoring = WF01V02ScoringEngine()

    def _get_p2_service(self) -> P2SpecificityBatchService:
        if self.p2_service is None:
            self.p2_service = P2SpecificityBatchService(
                self._config,
                allow_development_fallback=self._allow_development_fallback,
                provider=self.provider,
                remote_background_provider=self.remote_provider,
            )
        return self.p2_service

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
                "related_background_nt": specificity[
                    "related_background_nt"
                ]["status"],
            },
            "threshold": "P2 必需模式完成，且提供宿主时完成宿主专库排除",
            "source": "P2 result mapped by WF01-v0.2 M4",
            "message": specificity.get("reason", ""),
        }
        rules = candidate.get("rules") or []
        updated = [
            replacement if rule.get("code") == "R19" else rule
            for rule in rules
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
            "source": "P2 evidence mapped by WF01-v0.2 M4",
            "message": "M4 已同步 P2 证据等级；结果仍需实验验证",
        }
        rules = candidate.get("rules") or []
        updated = [
            replacement if rule.get("code") == "R20" else rule
            for rule in rules
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
        metadata = candidate.setdefault("metadata", {})
        scoring = self.scoring.score(
            candidate,
            include_conservation=False,
        )
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
        participating = [
            name
            for name, detail in (scoring.get("dimensions") or {}).items()
            if isinstance(detail, dict)
            and detail.get("participates_in_score")
        ]
        scoring.update(
            {
                "score_type": "COMPOSITE_SCORE",
                "score_label": "综合评分",
                "execution_path": "FULL_SPECIFICITY",
                "score_scope": participating,
                "formula_note": (
                    "完成 MAFFT 保守性时采用保守性、热力学、P2 特异性"
                    "与扩增子特征四维加权；保守性未评估时不虚构分数，"
                    "其余三维按版本化策略重归一化，再应用软规则扣分"
                ),
            }
        )

    @staticmethod
    def _validate_p2_results(
        p2_result: dict[str, Any],
        expected_candidates: int,
    ) -> list[dict[str, Any]]:
        candidate_results = p2_result.get("candidate_results")
        if (
            not isinstance(candidate_results, list)
            or len(candidate_results) != expected_candidates
        ):
            raise InputValidationError(
                "P2 返回的候选结果数量与 M4 输入不一致",
                details={
                    "expected_candidates": expected_candidates,
                    "actual_candidates": (
                        len(candidate_results)
                        if isinstance(candidate_results, list)
                        else None
                    ),
                },
            )
        allowed = {"PASS", "CAUTION", "FAIL", "NOT_EVALUATED"}
        for index, item in enumerate(candidate_results):
            specificity = (
                item.get("specificity") if isinstance(item, dict) else None
            )
            overall = (
                specificity.get("overall_status")
                if isinstance(specificity, dict)
                else None
            )
            if overall not in allowed:
                raise InputValidationError(
                    "P2 返回的特异性状态无效",
                    details={"candidate_index": index, "status": overall},
                )
            for key in (
                "target_hit",
                "host_exclusion",
                "related_background_nt",
            ):
                if not isinstance(specificity.get(key), dict):
                    raise InputValidationError(
                        "P2 返回的模式结果不完整",
                        details={"candidate_index": index, "mode": key},
                    )
            if overall in {"PASS", "CAUTION"} and specificity.get(
                "specificity_score"
            ) is None:
                raise InputValidationError(
                    "P2 已评估结果缺少特异性评分",
                    details={"candidate_index": index, "status": overall},
                )
        return candidate_results

    @staticmethod
    def _design_only_p2_result(
        candidate_count: int,
    ) -> dict[str, Any]:
        return {
            "policy_version": P2_POLICY_VERSION,
            "policy_status": P2_POLICY_STATUS,
            "provider_status": {
                "status": "NOT_INITIALIZED",
                "reason": "P2_NOT_CALLED_ON_DESIGN_ONLY",
            },
            "remote_background_provider_status": {
                "status": "NOT_INITIALIZED",
                "reason": "P2_NOT_CALLED_ON_DESIGN_ONLY",
            },
            "effective_related_resource": None,
            "raw_search_outputs": {
                "target": {
                    "object_id": None,
                    "resource": None,
                    "result": None,
                    "error": None,
                },
                "hosts": [],
                "related_background": {
                    "object_ids": [],
                    "resource": None,
                    "result": None,
                    "error": None,
                },
            },
            "candidate_results": [
                {
                    "specificity": p2_design_only_result(),
                    "evidence_level": "in_silico_only",
                }
                for _ in range(candidate_count)
            ],
        }

    def run_target(self, target: dict[str, Any]) -> dict[str, Any]:
        target_id = str(target.get("target_id") or "")
        m3 = target.get("m3_execution")
        if (
            not isinstance(m3, dict)
            or m3.get("stage") != "M3_COMPLETE_AWAITING_M4"
        ):
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
        candidates = copy.deepcopy(
            m3.get("all_hard_rule_pass_candidates") or []
        )
        if execution_path == "DESIGN_ONLY":
            target_object: dict[str, Any] = {}
            host_objects: list[dict[str, Any]] = []
            related_background_objects: list[dict[str, Any]] = []
        else:
            target_object = copy.deepcopy(target.get("target_object") or {})
            host_objects = copy.deepcopy(target.get("host_objects") or [])
            related_background_objects = copy.deepcopy(
                (target.get("related_objects") or [])
                + (target.get("background_objects") or [])
            )

        queries, query_maps = _candidate_queries(candidates)
        target_resource = (
            self.resolver.resolve_target(target) if target_object else None
        )
        host_resources = [
            (object_, self.resolver.resolve_host(object_))
            for object_ in host_objects
        ]
        related_resource = (
            self.resolver.resolve_related_background(
                related_background_objects,
                target_object=target_object,
            )
            if target_object
            else None
        )

        if execution_path == "DESIGN_ONLY":
            p2_result = self._design_only_p2_result(len(candidates))
        else:
            p2_result = self._get_p2_service().evaluate(
                queries=queries,
                query_maps=query_maps,
                target_object=target_object,
                host_objects=host_objects,
                related_background_objects=related_background_objects,
                target_resource=target_resource,
                host_resources=host_resources,
                related_resource=related_resource,
            )
        candidate_results = self._validate_p2_results(
            p2_result,
            len(candidates),
        )
        related_resource = p2_result.get(
            "effective_related_resource",
            related_resource,
        )

        qualified: list[dict[str, Any]] = []
        specificity_failed: list[dict[str, Any]] = []
        specificity_incomplete: list[dict[str, Any]] = []
        for candidate, p2_candidate in zip(candidates, candidate_results):
            specificity = copy.deepcopy(p2_candidate["specificity"])
            overall = str(specificity["overall_status"])
            specificity_score = specificity.get("specificity_score")
            candidate["specificity"] = specificity
            candidate["evidence_level"] = str(
                p2_candidate.get("evidence_level") or "in_silico_only"
            )
            candidate["specificity_status"] = overall
            self._set_r19(candidate, specificity)
            self._set_r20(candidate)

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
                candidate.setdefault("metadata", {})[
                    "specificity_follow_up"
                ] = {
                    "status": "AWAITING_REQUIRED_SPECIFICITY_EVIDENCE",
                    "missing_fields": copy.deepcopy(
                        target.get("specificity_missing_fields") or []
                    ),
                    "message": (
                        "补充缺失输入或恢复 P2 所需资源/工具后重新运行"
                    ),
                }
                specificity_incomplete.append(candidate)
            else:
                scoring = self.scoring.score(
                    candidate,
                    specificity_score=float(specificity_score),
                )
                self._mark_full_specificity_score(scoring)
                candidate.setdefault("metadata", {})[
                    "wf01_v02_scoring"
                ] = scoring
                candidate["score"] = scoring["final_score"]
                candidate["verdict"] = (
                    "PASS_WITH_WARNINGS"
                    if overall == "CAUTION" or bool(scoring["penalties"])
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
        technical_search_complete = (
            primer3_complete and all_specificity_evaluated
        )
        design_ranking_complete = (
            execution_path == "DESIGN_ONLY"
            and primer3_complete
            and len(design_ranked) == len(specificity_incomplete)
        )
        return {
            "stage": "M4_COMPLETE_AWAITING_M5",
            "engine_version": M4_ENGINE_VERSION,
            "target_id": target_id,
            "execution_path": execution_path,
            "specificity_tool": "P2",
            "specificity_execution_status": (
                "SKIPPED_MISSING_CONTEXT"
                if execution_path == "DESIGN_ONLY"
                else "COMPLETED"
                if all_specificity_evaluated
                else "NOT_EVALUATED"
            ),
            "policy_version": p2_result.get(
                "policy_version",
                P2_POLICY_VERSION,
            ),
            "policy_status": p2_result.get(
                "policy_status",
                P2_POLICY_STATUS,
            ),
            "provider_status": copy.deepcopy(
                p2_result.get("provider_status") or {}
            ),
            "remote_background_provider_status": copy.deepcopy(
                p2_result.get("remote_background_provider_status") or {}
            ),
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
            "raw_search_outputs": copy.deepcopy(
                p2_result.get("raw_search_outputs") or {}
            ),
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
                else "DEC_01_DEC_02_POLICY_APPROVAL_PENDING"
                if technical_search_complete
                else "SPECIFICITY_OR_PRIMER3_INCOMPLETE"
            ),
            "ranking_status": (
                "FINAL_DESIGN_ONLY"
                if design_ranking_complete
                else "INCOMPLETE_DESIGN_SCORE"
                if execution_path == "DESIGN_ONLY"
                else "FINAL_UNDER_PROVISIONAL_POLICY"
                if technical_search_complete
                else "INCOMPLETE_SPECIFICITY"
            ),
            "ranking_basis": (
                "M4_DESIGN_SCORE"
                if execution_path == "DESIGN_ONLY"
                else "M4_MAPPING_OF_P2_SPECIFICITY_SCORE"
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
