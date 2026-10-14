"""WF-01 PRD-v0.2 M5 target and batch result-state assembly.

M5 does not run scientific tools.  It classifies the evidence already
produced by M2-M4, preserves candidates from interrupted searches, and keeps
search completeness independent from candidate quantity.
"""

from __future__ import annotations

import copy
import math
from datetime import datetime, timezone
from typing import Any

from .wf01_v02_m3 import FIXED_TOP_CANDIDATES, deduplicate_target_candidates


M5_ENGINE_VERSION = "wf01-v0.2-m5.3"
RECOMMENDED_CANDIDATE_COUNT = 3
TARGET_RESULT_STATUSES = (
    "SUCCEEDED",
    "PARTIAL",
    "PARTIAL_INCOMPLETE",
    "FAILED",
)
BATCH_RESULT_STATUSES = (
    "SUCCEEDED",
    "PARTIALLY_SUCCEEDED",
    "FAILED",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _candidate_id(candidate: dict[str, Any]) -> str:
    return str(candidate.get("candidate_id") or candidate.get("dedupe_key") or "")


def _qualified_order(candidate: dict[str, Any]) -> tuple[Any, ...]:
    rank = candidate.get("rank")
    score = candidate.get("score")
    penalty = candidate.get("primer3_pair_penalty")
    return (
        math.inf if rank is None else int(rank),
        -(float(score) if score is not None else -1.0),
        math.inf if penalty is None else float(penalty),
        _candidate_id(candidate),
    )


def _incomplete_order(candidate: dict[str, Any]) -> tuple[Any, ...]:
    metadata = candidate.get("metadata") or {}
    scoring = metadata.get("wf01_v02_scoring") or {}
    design_rank = (
        candidate.get("rank")
        if scoring.get("score_type") == "DESIGN_SCORE"
        else None
    )
    pre_rank = metadata.get("pre_specificity_rank")
    penalty = candidate.get("primer3_pair_penalty")
    return (
        math.inf if design_rank is None else int(design_rank),
        math.inf if pre_rank is None else int(pre_rank),
        math.inf if penalty is None else float(penalty),
        _candidate_id(candidate),
    )


def _dedupe_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for candidate in candidates:
        key = str(
            candidate.get("dedupe_key")
            or candidate.get("candidate_id")
            or id(candidate)
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(copy.deepcopy(candidate))
    return output


def _recover_pre_specificity_candidates(
    target: dict[str, Any],
) -> list[dict[str, Any]]:
    m3 = target.get("m3_execution") or {}
    m3_candidates = m3.get("all_hard_rule_pass_candidates")
    if isinstance(m3_candidates, list):
        return _dedupe_candidates(m3_candidates)

    m2 = target.get("m2_execution") or {}
    occurrences = m2.get("hard_rule_pass_candidates")
    if not isinstance(occurrences, list) or not occurrences:
        return []
    unique, _ = deduplicate_target_candidates(occurrences)
    return [
        candidate
        for candidate in unique
        if candidate.get("verdict") == "AWAITING_SPECIFICITY"
    ]


def _specificity_incomplete_scope(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    scope: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_id = _candidate_id(candidate)
        specificity = candidate.get("specificity") or {}
        mode_results = [
            specificity.get("target_hit"),
            specificity.get("related_background_nt"),
        ]
        host = specificity.get("host_exclusion") or {}
        mode_results.extend(host.get("object_results") or [])
        for result in mode_results:
            if not isinstance(result, dict) or result.get("status") != "NOT_EVALUATED":
                continue
            database = result.get("database") or {}
            scope.append(
                {
                    "scope_type": "CANDIDATE_SPECIFICITY",
                    "scope_id": candidate_id,
                    "stage": "M4",
                    "mode": result.get("mode"),
                    "object_ids": [
                        item.get("object_id")
                        for item in result.get("objects") or []
                        if isinstance(item, dict)
                    ],
                    "status": "NOT_EVALUATED",
                    "reason": result.get("reason"),
                    "database_resource_key": database.get("resource_key"),
                    "database_version": database.get("version"),
                    "error": copy.deepcopy(result.get("error")),
                }
            )
    return scope


def _primer3_incomplete_scope(target: dict[str, Any]) -> list[dict[str, Any]]:
    m2 = target.get("m2_execution") or {}
    manifest = m2.get("exploration_manifest") or target.get(
        "exploration_manifest"
    )
    if not isinstance(manifest, dict):
        return []
    scope: list[dict[str, Any]] = []
    for unit in manifest.get("units") or []:
        if unit.get("status") == "COMPLETED":
            continue
        scope.append(
            {
                "scope_type": "EXPLORATION_UNIT",
                "scope_id": unit.get("exploration_unit_id"),
                "stage": "M2_PRIMER3",
                "status": unit.get("status") or "NOT_COMPLETED",
                "reason": (
                    "PRIMER3_TOOL_ERROR"
                    if unit.get("status") == "TOOL_ERROR"
                    else "EXPLORATION_UNIT_NOT_COMPLETED"
                ),
                "error": copy.deepcopy(unit.get("tool_error")),
            }
        )
    return scope


def _unique_recommendations(
    incomplete_scope: list[dict[str, Any]],
    *,
    deliverable_count: int,
) -> list[dict[str, str]]:
    actions: dict[str, str] = {}
    reasons = {
        str(item.get("reason") or "") for item in incomplete_scope
    }
    if any("DATABASE" in reason or "VERSION" in reason for reason in reasons):
        actions[
            "CONFIGURE_VERSIONED_SPECIFICITY_DATABASES"
        ] = "配置并登记版本化靶标与 nt/背景 BLAST 数据库；已指定宿主时同时配置对应宿主库后重跑 M4"
    if any("TOOL" in reason or "RUNTIME" in reason for reason in reasons):
        actions[
            "RETRY_AFTER_TOOL_RECOVERY"
        ] = "恢复 Primer3/BLAST 工具后，仅重跑未完成范围"
    if any("PRIMER3" in reason or "EXPLORATION_UNIT" in reason for reason in reasons):
        actions[
            "RETRY_INCOMPLETE_EXPLORATION_UNITS"
        ] = "重跑未完成的 ExplorationUnit，不得把现有候选数当作搜索终点"
    if any("POLICY" in reason or "DEC_" in reason for reason in reasons):
        actions[
            "CONFIRM_PENDING_SCIENTIFIC_POLICIES"
        ] = "完成 DEC-01/DEC-02 业务确认并发布新版策略后重新汇总"
    if any("SCORE" in reason for reason in reasons):
        actions[
            "RECALCULATE_INCOMPLETE_CANDIDATE_FIELDS"
        ] = "补齐候选评分字段后重新执行 M5 汇总"
    if deliverable_count and incomplete_scope:
        actions[
            "REVIEW_INCOMPLETE_EVIDENCE_BEFORE_EXPERIMENT"
        ] = "当前候选仅可作为待复核设计，完成缺失证据后再进入实验"
    elif not deliverable_count:
        actions[
            "REDESIGN_WITH_UNCHANGED_HARD_THRESHOLDS"
        ] = "扩大预定义搜索空间或更换靶区重设计，不得降低硬规则或特异性阈值"
    return [
        {"action": action, "message": message}
        for action, message in actions.items()
    ]


def _candidate_notice(
    target: dict[str, Any],
    *,
    deliverable_count: int,
    excluded_counts: dict[str, int],
) -> dict[str, Any]:
    """Describe a candidate shortfall without creating a user interaction."""
    shortfall = deliverable_count < RECOMMENDED_CANDIDATE_COUNT
    code = (
        "NO_DELIVERABLE_CANDIDATES"
        if deliverable_count == 0
        else "INSUFFICIENT_CANDIDATES"
        if shortfall
        else None
    )
    reason_codes: list[str] = []
    m2_counts = (target.get("m2_execution") or {}).get("candidate_counts") or {}
    m3_counts = (target.get("m3_execution") or {}).get("candidate_counts") or {}
    generated_count = int(
        m2_counts.get("generated")
        or m3_counts.get("generated_occurrences")
        or 0
    )
    if deliverable_count == 0:
        if generated_count == 0:
            reason_codes.append("PRIMER3_GENERATED_NO_CANDIDATES")
        if excluded_counts["hard_rule_rejected"]:
            reason_codes.append("CANDIDATES_REJECTED_BY_HARD_RULES")
        if excluded_counts["hard_rule_incomplete"]:
            reason_codes.append("HARD_RULE_EVIDENCE_INCOMPLETE")
        if excluded_counts["specificity_failed"]:
            reason_codes.append("CANDIDATES_REJECTED_BY_SPECIFICITY")
        if not reason_codes:
            reason_codes.append("NO_CANDIDATE_MET_DELIVERY_REQUIREMENTS")

    if deliverable_count == 0:
        reason_text = {
            "PRIMER3_GENERATED_NO_CANDIDATES": "Primer3 未生成可进入筛选的候选",
            "CANDIDATES_REJECTED_BY_HARD_RULES": "候选未通过硬规则",
            "HARD_RULE_EVIDENCE_INCOMPLETE": "候选的硬规则证据不完整",
            "CANDIDATES_REJECTED_BY_SPECIFICITY": "候选未通过特异性评估",
            "NO_CANDIDATE_MET_DELIVERY_REQUIREMENTS": "没有候选满足当前交付要求",
        }
        details = "；".join(reason_text[item] for item in reason_codes)
        message = f"未获得可交付候选：{details}。已生成空结果报告。"
    elif shortfall:
        message = (
            f"候选不足：当前获得 {deliverable_count} 个可交付候选，"
            f"少于建议的 {RECOMMENDED_CANDIDATE_COUNT} 个；已按现有结果生成报告。"
        )
    else:
        message = None

    return {
        "status": "INSUFFICIENT" if shortfall else "SUFFICIENT",
        "code": code,
        "message": message,
        "candidate_count": deliverable_count,
        "recommended_candidate_count": RECOMMENDED_CANDIDATE_COUNT,
        "reason_codes": reason_codes,
        "stage_counts": {
            "primer3_generated": generated_count,
            **copy.deepcopy(excluded_counts),
        },
    }


class WF01V02M5Engine:
    """Classify one target, then aggregate a batch without tool side effects."""

    @staticmethod
    def _status(
        *,
        deliverable_count: int,
        search_complete: bool,
    ) -> tuple[str, str]:
        if deliverable_count == 0:
            return "FAILED", "NO_DELIVERABLE_CANDIDATES"
        if not search_complete:
            return (
                "PARTIAL_INCOMPLETE",
                "DELIVERABLE_CANDIDATES_WITH_INCOMPLETE_SEARCH",
            )
        if deliverable_count >= FIXED_TOP_CANDIDATES:
            return "SUCCEEDED", "COMPLETE_SEARCH_WITH_AT_LEAST_TEN_CANDIDATES"
        return "PARTIAL", "COMPLETE_SEARCH_WITH_ONE_TO_NINE_CANDIDATES"

    def run_target(self, target: dict[str, Any]) -> dict[str, Any]:
        target_id = str(target.get("target_id") or "")
        m4 = target.get("m4_execution")
        m4_complete = (
            isinstance(m4, dict)
            and m4.get("stage") == "M4_COMPLETE_AWAITING_M5"
        )

        qualified: list[dict[str, Any]] = []
        incomplete: list[dict[str, Any]] = []
        scoring_incomplete: list[dict[str, Any]] = []
        if m4_complete:
            qualified = _dedupe_candidates(
                m4.get("all_qualified_candidates") or []
            )
            incomplete = _dedupe_candidates(
                m4.get("all_specificity_incomplete_candidates") or []
            )
            valid_qualified: list[dict[str, Any]] = []
            for candidate in qualified:
                if candidate.get("score") is None or candidate.get("rank") is None:
                    candidate["verdict"] = "M5_SCORE_NOT_EVALUATED"
                    scoring_incomplete.append(candidate)
                else:
                    valid_qualified.append(candidate)
            qualified = valid_qualified
            incomplete = _dedupe_candidates(incomplete + scoring_incomplete)
        else:
            incomplete = _recover_pre_specificity_candidates(target)

        qualified.sort(key=_qualified_order)
        incomplete.sort(key=_incomplete_order)
        deliverable = _dedupe_candidates(qualified + incomplete)
        qualified_ids = {_candidate_id(item) for item in qualified}
        incomplete_ids = {_candidate_id(item) for item in incomplete}
        for display_rank, candidate in enumerate(deliverable, start=1):
            candidate["display_rank"] = display_rank
            delivery_status = (
                "QUALIFIED"
                if _candidate_id(candidate) in qualified_ids
                else "INCOMPLETE_EVIDENCE"
            )
            candidate.setdefault("metadata", {})["m5_delivery"] = {
                "status": delivery_status,
                "selection_basis": (
                    "M4_FINAL_SCORE"
                    if delivery_status == "QUALIFIED"
                    else "M4_DESIGN_SCORE"
                    if (
                        (candidate.get("metadata") or {})
                        .get("wf01_v02_scoring", {})
                        .get("score_type")
                        == "DESIGN_SCORE"
                    )
                    else "M3_PRE_SPECIFICITY_PRIORITY"
                ),
                "display_rank": display_rank,
                "formal_rank": candidate.get("rank"),
                "score": candidate.get("score"),
            }

        incomplete_scope = _primer3_incomplete_scope(target)
        incomplete_scope.extend(_specificity_incomplete_scope(incomplete))
        if scoring_incomplete:
            incomplete_scope.extend(
                {
                    "scope_type": "CANDIDATE_SCORE",
                    "scope_id": _candidate_id(candidate),
                    "stage": "M4_SCORING",
                    "status": "NOT_EVALUATED",
                    "reason": "M4_QUALIFIED_CANDIDATE_SCORE_OR_RANK_MISSING",
                    "error": None,
                }
                for candidate in scoring_incomplete
            )
        if not m4_complete:
            error = copy.deepcopy(m4.get("error")) if isinstance(m4, dict) else None
            incomplete_scope.append(
                {
                    "scope_type": "STAGE",
                    "scope_id": target_id,
                    "stage": "M4",
                    "status": "NOT_COMPLETED",
                    "reason": (
                        m4.get("search_incomplete_reason")
                        if isinstance(m4, dict)
                        else "M4_RESULT_MISSING"
                    )
                    or "M4_RESULT_INCOMPLETE",
                    "error": error,
                }
            )

        m4_search_complete = bool(m4.get("search_complete")) if m4_complete else False
        m4_technical_complete = (
            bool(m4.get("technical_search_complete")) if m4_complete else False
        )
        local_incomplete = bool(incomplete_scope or incomplete)
        search_complete = (
            m4_search_complete
            and m4_technical_complete
            and not local_incomplete
        )
        technical_search_complete = (
            m4_technical_complete and not local_incomplete
        )
        if not search_complete and m4_complete:
            reason = str(
                m4.get("search_incomplete_reason")
                or "M4_SEARCH_NOT_DECLARED_COMPLETE"
            )
            if reason and not any(
                item.get("reason") == reason for item in incomplete_scope
            ):
                incomplete_scope.append(
                    {
                        "scope_type": "SEARCH_POLICY",
                        "scope_id": target_id,
                        "stage": "M4_M5",
                        "status": "NOT_COMPLETED",
                        "reason": reason,
                        "error": None,
                    }
                )

        status, status_reason = self._status(
            deliverable_count=len(deliverable),
            search_complete=search_complete,
        )
        excluded_counts = {
            "hard_rule_rejected": len(
                (m4 or {}).get("hard_rule_rejected_candidates") or []
            ),
            "hard_rule_incomplete": len(
                (m4 or {}).get("hard_rule_incomplete_candidates") or []
            ),
            "specificity_failed": len(
                (m4 or {}).get("all_specificity_failed_candidates") or []
            ),
        }
        candidate_notice = _candidate_notice(
            target,
            deliverable_count=len(deliverable),
            excluded_counts=excluded_counts,
        )
        incomplete_reasons = sorted(
            {
                str(item.get("reason"))
                for item in incomplete_scope
                if item.get("reason")
            }
        )
        recommendations = _unique_recommendations(
            incomplete_scope,
            deliverable_count=len(deliverable),
        )
        completed_scope = [
            {
                "scope_type": "PRIMER3_EXPLORATION",
                "status": (
                    "COMPLETED"
                    if bool((target.get("m3_execution") or {}).get(
                        "primer3_search_complete"
                    ))
                    else "INCOMPLETE"
                ),
            },
            {
                "scope_type": "SPECIFICITY_EVALUATION",
                "status": (
                    "COMPLETED"
                    if m4_complete and not incomplete
                    else "INCOMPLETE"
                ),
                "qualified_candidate_count": len(qualified),
                "incomplete_candidate_count": len(incomplete),
            },
            {
                "scope_type": "CANDIDATE_DELIVERY",
                "status": "COMPLETED",
                "deliverable_candidate_count": len(deliverable),
                "display_top10_count": min(
                    len(deliverable),
                    FIXED_TOP_CANDIDATES,
                ),
            },
        ]
        return {
            "stage": "M5_COMPLETE_AWAITING_M6",
            "engine_version": M5_ENGINE_VERSION,
            "target_id": target_id,
            "status": status,
            "status_reason": status_reason,
            "search_complete": search_complete,
            "technical_search_complete": technical_search_complete,
            "candidate_counts": {
                "deliverable": len(deliverable),
                "qualified": len(qualified),
                "incomplete_evidence": len(incomplete),
                "top10": min(len(deliverable), FIXED_TOP_CANDIDATES),
                **excluded_counts,
            },
            "top10": copy.deepcopy(deliverable[:FIXED_TOP_CANDIDATES]),
            "all_deliverable_candidates": deliverable,
            "qualified_candidate_ids": sorted(qualified_ids),
            "incomplete_candidate_ids": sorted(incomplete_ids),
            "completed_scope": completed_scope,
            "incomplete_scope": incomplete_scope,
            "incomplete_reasons": incomplete_reasons,
            "recommendations": recommendations,
            "candidate_notice": candidate_notice,
            "result_summary": {
                "status": status,
                "deliverable_candidate_count": len(deliverable),
                "qualified_candidate_count": len(qualified),
                "incomplete_candidate_count": len(incomplete),
                "search_complete": search_complete,
                "technical_search_complete": technical_search_complete,
                "candidate_notice": copy.deepcopy(candidate_notice),
                "message": {
                    "SUCCEEDED": "搜索完整并获得不少于 10 个可交付候选",
                    "PARTIAL": "搜索完整，但仅获得 1–9 个可交付候选",
                    "PARTIAL_INCOMPLETE": "已有可交付候选，但搜索或证据尚未完成",
                    "FAILED": "没有可交付候选",
                }[status],
            },
            "summarized_at": utc_now(),
        }

    @staticmethod
    def summarize_batch(
        target_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        target_total = len(target_results)
        counts = {
            status.lower(): sum(
                result.get("status") == status for result in target_results
            )
            for status in TARGET_RESULT_STATUSES
        }
        deliverable_target_count = target_total - counts["failed"]
        if target_total and counts["succeeded"] == target_total:
            status = "SUCCEEDED"
            status_reason = "ALL_TARGETS_SUCCEEDED"
        elif deliverable_target_count:
            status = "PARTIALLY_SUCCEEDED"
            status_reason = "AT_LEAST_ONE_TARGET_NOT_FULLY_SUCCEEDED"
        else:
            status = "FAILED"
            status_reason = "NO_TARGET_HAS_DELIVERABLE_CANDIDATES"
        search_complete = bool(target_results) and all(
            result.get("search_complete") for result in target_results
        )
        technical_search_complete = bool(target_results) and all(
            result.get("technical_search_complete")
            for result in target_results
        )
        recommendations: list[dict[str, str]] = []
        seen_actions: set[str] = set()
        for result in target_results:
            for recommendation in result.get("recommendations") or []:
                action = str(recommendation.get("action") or "")
                if not action or action in seen_actions:
                    continue
                seen_actions.add(action)
                recommendations.append(copy.deepcopy(recommendation))
        shortfall_target_ids = [
            str(result.get("target_id"))
            for result in target_results
            if (result.get("candidate_notice") or {}).get("status")
            == "INSUFFICIENT"
        ]
        no_candidate_target_ids = [
            str(result.get("target_id"))
            for result in target_results
            if (result.get("candidate_notice") or {}).get("code")
            == "NO_DELIVERABLE_CANDIDATES"
        ]
        candidate_notice = {
            "status": "INSUFFICIENT" if shortfall_target_ids else "SUFFICIENT",
            "code": "ONE_OR_MORE_TARGETS_HAVE_INSUFFICIENT_CANDIDATES"
            if shortfall_target_ids
            else None,
            "message": (
                f"{len(shortfall_target_ids)} 个靶标候选不足；系统已按现有结果生成报告。"
                if shortfall_target_ids
                else None
            ),
            "recommended_candidate_count_per_target": RECOMMENDED_CANDIDATE_COUNT,
            "shortfall_target_ids": shortfall_target_ids,
            "no_candidate_target_ids": no_candidate_target_ids,
        }
        return {
            "stage": "M5_COMPLETE_AWAITING_M6",
            "engine_version": M5_ENGINE_VERSION,
            "status": status,
            "status_reason": status_reason,
            "search_complete": search_complete,
            "technical_search_complete": technical_search_complete,
            "target_counts": {
                "total": target_total,
                **counts,
                "deliverable": deliverable_target_count,
            },
            "candidate_counts": {
                "deliverable": sum(
                    int(result.get("candidate_counts", {}).get("deliverable", 0))
                    for result in target_results
                ),
                "qualified": sum(
                    int(result.get("candidate_counts", {}).get("qualified", 0))
                    for result in target_results
                ),
                "incomplete_evidence": sum(
                    int(
                        result.get("candidate_counts", {}).get(
                            "incomplete_evidence",
                            0,
                        )
                    )
                    for result in target_results
                ),
            },
            "target_status_matrix": [
                {
                    "target_id": result.get("target_id"),
                    "status": result.get("status"),
                    "status_reason": result.get("status_reason"),
                    "search_complete": result.get("search_complete"),
                    "technical_search_complete": result.get(
                        "technical_search_complete"
                    ),
                    "deliverable_candidate_count": result.get(
                        "candidate_counts",
                        {},
                    ).get("deliverable", 0),
                    "incomplete_reasons": copy.deepcopy(
                        result.get("incomplete_reasons") or []
                    ),
                }
                for result in target_results
            ],
            "failed_target_ids": [
                str(result.get("target_id"))
                for result in target_results
                if result.get("status") == "FAILED"
            ],
            "incomplete_target_ids": [
                str(result.get("target_id"))
                for result in target_results
                if result.get("status") == "PARTIAL_INCOMPLETE"
            ],
            "candidate_notice": candidate_notice,
            "recommendations": recommendations,
            "result_summary": {
                "status": status,
                "target_total": target_total,
                "deliverable_target_count": deliverable_target_count,
                "failed_target_count": counts["failed"],
                "search_complete": search_complete,
                "candidate_notice": copy.deepcopy(candidate_notice),
                "message": {
                    "SUCCEEDED": "所有靶标均完整成功",
                    "PARTIALLY_SUCCEEDED": "至少一个靶标可交付，但批次并非全部完整成功",
                    "FAILED": "所有靶标均无可交付候选",
                }[status],
            },
            "summarized_at": utc_now(),
        }
