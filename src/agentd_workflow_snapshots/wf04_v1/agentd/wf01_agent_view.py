"""Compact, deterministic WF-01 views intended for language-model clients.

The canonical batch and report objects are scientific persistence artifacts.
They intentionally contain complete candidate evidence and can be several
megabytes.  Those objects must not be used as the control-plane response of an
MCP tool because tool results are normally appended to the model context.
"""

from __future__ import annotations

import copy
import json
from collections import Counter
from typing import Any

from .identifiers import wf01_batch_code


AGENT_VIEW_SCHEMA_VERSION = "wf01-agent-view-v1"


def serialized_size_bytes(value: Any) -> int:
    """Return the UTF-8 size of a compact JSON representation."""
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    )


def _pending_interactions(batch: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in batch.get("agent_interactions") or []
        if (item.get("data") or {}).get("status")
        in {"WAITING_USER", "USER_SUBMITTED", "RUNNING"}
    ]


def next_stage_for_batch(batch: dict[str, Any]) -> str | None:
    """Return the next deterministic stage, or ``None`` at a hard boundary."""
    if _pending_interactions(batch):
        return None
    # A stage runner persists RUNNING before invoking the underlying scientific
    # tool.  Treat that state as an in-flight hard boundary.  Deriving RUN_M4
    # only from m3_outcome while M4 is already running causes agents to retry a
    # non-reentrant stage indefinitely after a slow or interrupted tool call.
    if batch.get("status") == "RUNNING":
        return None
    if batch.get("m6_outcome") == "COMPLETED":
        return None
    if batch.get("m5_outcome") == "COMPLETED":
        return "M6"
    if batch.get("m4_outcome") is not None:
        return "M5"
    if batch.get("m3_outcome") is not None:
        return "M4"
    if batch.get("m2_outcome") is not None:
        return "M3"
    if batch.get("status") == "QUEUED":
        return "M2"
    return None


def next_action_for_batch(batch: dict[str, Any]) -> str:
    """Translate persisted workflow state into an agent control action."""
    if _pending_interactions(batch):
        return "WAIT_FOR_USER"
    if batch.get("status") == "RUNNING":
        return "WAIT_FOR_STAGE_COMPLETION"
    if batch.get("m6_outcome") == "COMPLETED":
        return "COMPLETE"
    stage = next_stage_for_batch(batch)
    if stage:
        return f"RUN_{stage}"
    if batch.get("status") == "PENDING_CONFIRMATION":
        return "WAIT_FOR_USER"
    return "NO_AUTOMATIC_ACTION"


def _compact_oligo(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    keys = (
        "sequence_5to3",
        "length_nt",
        "tm_celsius",
        "gc_percent",
        "position_start",
        "position_end",
    )
    return {key: copy.deepcopy(value[key]) for key in keys if key in value}


def compact_candidate(
    candidate: dict[str, Any],
    *,
    include_rule_details: bool = False,
) -> dict[str, Any]:
    """Remove repeated evidence while retaining report-relevant candidate data."""
    result: dict[str, Any] = {}
    for key in (
        "candidate_id",
        "rank",
        "provisional_rank",
        "score",
        "verdict",
        "evidence_level",
        "primer3_pair_penalty",
    ):
        if key in candidate:
            result[key] = copy.deepcopy(candidate[key])
    for key in ("forward_primer", "reverse_primer", "probe"):
        compact = _compact_oligo(candidate.get(key))
        if compact is not None:
            result[key] = compact
    amplicon = candidate.get("amplicon")
    if isinstance(amplicon, dict):
        result["amplicon"] = {
            key: copy.deepcopy(amplicon[key])
            for key in ("sequence_5to3", "length_bp", "position_start", "position_end")
            if key in amplicon
        }
    if candidate.get("warnings"):
        result["warnings"] = copy.deepcopy(candidate["warnings"])
    specificity = candidate.get("specificity")
    if isinstance(specificity, dict):
        result["specificity"] = {
            key: copy.deepcopy(specificity[key])
            for key in (
                "status",
                "target_status",
                "host_status",
                "related_status",
                "background_status",
            )
            if key in specificity
        }

    rules = candidate.get("rules") or []
    statuses = Counter(
        str(rule.get("status") or "UNKNOWN")
        for rule in rules
        if isinstance(rule, dict)
    )
    result["rule_summary"] = {
        "total": len(rules),
        "status_counts": dict(sorted(statuses.items())),
        "failed_codes": [
            rule.get("code")
            for rule in rules
            if isinstance(rule, dict) and rule.get("status") == "FAIL"
        ],
        "warning_codes": [
            rule.get("code")
            for rule in rules
            if isinstance(rule, dict) and rule.get("status") == "WARNING"
        ],
    }
    if include_rule_details:
        result["rules"] = copy.deepcopy(rules)
    return result


def _target_summary(
    target: dict[str, Any],
    *,
    top_candidate_limit: int,
) -> dict[str, Any]:
    m4 = target.get("m4_execution") or {}
    m5 = target.get("m5_execution") or {}
    candidates = (
        target.get("top10")
        or m5.get("top10")
        or m4.get("top10")
        or []
    )
    summary = {
        "target_id": target.get("target_id"),
        "ordinal": target.get("ordinal"),
        "status": target.get("status"),
        "manifest_status": target.get("manifest_status"),
        "execution_path": target.get("execution_path"),
        "requires_user_action": bool(target.get("requires_user_action")),
        "status_reason": m5.get("status_reason"),
        "candidate_counts": copy.deepcopy(
            target.get("candidate_counts") or m5.get("candidate_counts") or {}
        ),
        "search_complete": bool(target.get("search_complete")),
        "technical_search_complete": bool(
            target.get("technical_search_complete")
        ),
        "result_summary": copy.deepcopy(
            target.get("result_summary") or m5.get("result_summary") or {}
        ),
        "recommendations": copy.deepcopy(
            target.get("recommendations") or m5.get("recommendations") or []
        ),
    }
    if top_candidate_limit > 0:
        summary["top_candidates"] = [
            compact_candidate(candidate)
            for candidate in candidates[:top_candidate_limit]
            if isinstance(candidate, dict)
        ]
        summary["top_candidate_total"] = len(candidates)
    return summary


def _compact_target_manifest(batch: dict[str, Any]) -> dict[str, Any]:
    manifest = batch.get("target_manifest") or {}
    return {
        "schema_version": manifest.get("schema_version"),
        "target_total": manifest.get(
            "target_total", len(batch.get("targets") or [])
        ),
        "counts": copy.deepcopy(manifest.get("counts") or {}),
        "ready_target_ids": copy.deepcopy(
            manifest.get("ready_target_ids") or []
        ),
        "problem_target_ids": copy.deepcopy(
            manifest.get("problem_target_ids") or []
        ),
        "batch_requires_user_action": bool(
            manifest.get("batch_requires_user_action")
        ),
        "batch_issues": [
            {
                "issue_id": issue.get("issue_id"),
                "code": issue.get("code"),
                "severity": issue.get("severity"),
                "field": issue.get("field"),
                "message": issue.get("message"),
            }
            for issue in manifest.get("batch_issues") or []
        ],
        "targets": [
            {
                "target_id": target.get("target_id"),
                "record_name": (target.get("template") or {}).get(
                    "record_name"
                ),
                "length_nt": (target.get("template") or {}).get("length_nt"),
                "manifest_status": target.get("manifest_status"),
                "execution_path": target.get("execution_path"),
                "specificity_missing_fields": copy.deepcopy(
                    target.get("specificity_missing_fields") or []
                ),
                "issues": [
                    {
                        "issue_id": issue.get("issue_id"),
                        "code": issue.get("code"),
                        "severity": issue.get("severity"),
                        "field": issue.get("field"),
                        "message": issue.get("message"),
                    }
                    for issue in target.get("issues") or []
                ],
            }
            for target in batch.get("targets") or []
        ],
    }


def _specificity_object_label(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    original = value.get("original_input")
    if isinstance(original, dict):
        for key in ("name", "scientific_name", "common_name", "accession", "taxid"):
            label = original.get(key)
            if isinstance(label, str) and label.strip():
                return label.strip()
    for key in (
        "normalized_name",
        "scientific_name",
        "identifier",
        "accession",
        "taxid",
    ):
        label = value.get(key)
        if isinstance(label, str) and label.strip():
            return label.strip()
    return None


def _unique_labels(values: list[str | None]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        labels.append(value)
    return labels


def _route_summary(batch: dict[str, Any]) -> dict[str, Any]:
    """Describe the route the backend actually persisted and executed."""
    targets = [
        target
        for target in batch.get("targets") or []
        if isinstance(target, dict)
    ]
    execution_paths = _unique_labels(
        [str(target.get("execution_path") or "") for target in targets]
    )
    path_set = set(execution_paths)
    if path_set == {"DESIGN_ONLY"}:
        mode = "DESIGN_ONLY"
    elif path_set == {"FULL_SPECIFICITY"}:
        mode = "FULL_SPECIFICITY"
    elif path_set:
        mode = "MIXED"
    else:
        mode = "UNKNOWN"

    target_organisms = _unique_labels(
        [
            _specificity_object_label(target.get("target_object"))
            for target in targets
        ]
    )
    hosts = _unique_labels(
        [
            _specificity_object_label(host)
            for target in targets
            for host in target.get("host_objects") or []
        ]
    )
    specificity_statuses = _unique_labels(
        [
            str(
                (target.get("m4_execution") or {}).get(
                    "specificity_execution_status"
                )
                or target.get("specificity_status")
                or "NOT_EVALUATED"
            )
            for target in targets
        ]
    )

    if mode == "DESIGN_ONLY":
        message = (
            "已按基础设计路线完成：Primer3、规则过滤、去重和候选排序；"
            "因未提供靶标，未启动 BLAST+，特异性未评估。"
        )
    elif mode == "FULL_SPECIFICITY":
        context_parts = []
        if target_organisms:
            context_parts.append(f"靶标为{'、'.join(target_organisms)}")
        if hosts:
            context_parts.append(f"宿主为{'、'.join(hosts)}")
        context = "，".join(context_parts)
        context = f"（{context}）" if context else ""
        if specificity_statuses == ["COMPLETED"]:
            evaluated = (
                "靶标命中、宿主排除和综合背景数据库评估"
                if hosts
                else "靶标命中和综合背景数据库评估；未提供宿主，宿主专库排除不适用"
            )
            message = f"已按完整特异性路线完成{context}：执行了{evaluated}。"
        else:
            message = (
                "已按完整特异性路线运行"
                f"{context}；部分特异性评估未完成，具体原因已写入最终报告。"
            )
    elif mode == "MIXED":
        message = (
            "本批任务已按逐靶标混合路线运行：提供靶标信息的记录执行特异性评估，"
            "未提供靶标的记录仅执行基础设计；未完成项及原因已写入最终报告。"
        )
    else:
        message = "任务路线尚未建立；请根据工具返回的输入错误修正模板序列。"

    return {
        "mode": mode,
        "execution_paths": execution_paths,
        "target_organisms": target_organisms,
        "hosts": hosts,
        "specificity_execution_statuses": specificity_statuses,
        "message": message,
    }


def summarize_wf01_batch(
    batch: dict[str, Any],
    *,
    executed_stages: list[str] | None = None,
    top_candidates_per_target: int = 10,
) -> dict[str, Any]:
    """Build a bounded control-plane response from a complete batch snapshot."""
    pending = _pending_interactions(batch)
    stage_outcomes = {
        stage: batch.get(f"{stage.lower()}_outcome")
        for stage in ("M2", "M3", "M4", "M5", "M6")
        if batch.get(f"{stage.lower()}_outcome") is not None
    }
    next_action = next_action_for_batch(batch)
    lifecycle_state = (
        "COMPLETED"
        if next_action == "COMPLETE"
        else "WAITING_USER"
        if next_action == "WAIT_FOR_USER"
        else "IN_PROGRESS"
    )
    batch_code = str(
        batch.get("batch_code")
        or wf01_batch_code(
            str(batch.get("batch_id") or ""),
            batch.get("created_at"),
            workflow=str(batch.get("workflow") or "WF-01"),
        )
    )
    route_summary = _route_summary(batch)
    result: dict[str, Any] = {
        "response_schema_version": AGENT_VIEW_SCHEMA_VERSION,
        "response_mode": "SUMMARY",
        "batch_id": batch.get("batch_id"),
        "batch_code": batch_code,
        "workflow": batch.get("workflow"),
        "task_version": batch.get("task_version"),
        "source_batch_id": batch.get("source_batch_id"),
        "status": batch.get("status"),
        "stage_outcomes": stage_outcomes,
        "target_total": batch.get("target_total", len(batch.get("targets") or [])),
        "target_counts": copy.deepcopy(batch.get("target_counts") or {}),
        "target_manifest": _compact_target_manifest(batch),
        "candidate_counts": copy.deepcopy(batch.get("candidate_counts") or {}),
        "search_complete": bool(batch.get("search_complete")),
        "technical_search_complete": bool(
            batch.get("technical_search_complete")
        ),
        "search_incomplete_reason": batch.get("search_incomplete_reason"),
        "pending_interaction_count": len(pending),
        "pending_interaction_ids": [
            (item.get("data") or {}).get("interactionId") for item in pending
        ],
        "next_action": next_action,
        "route_summary": route_summary,
        "route_message": route_summary["message"],
        "conversation_context": {
            "activeBatchId": batch.get("batch_id"),
            "activeBatchCode": batch_code,
            "workflow": batch.get("workflow"),
            "lifecycleState": lifecycle_state,
            "retainAcrossTurns": True,
            "clearOnlyOnExplicitNewDesignOrTaskSwitch": True,
            "followUpTools": {
                "textualTop10": "get_wf01_report",
                "candidateDetail": "get_wf01_candidate",
                "optionalExport": "get_wf01_export",
                "redesignCurrentTask": "create_wf01_version",
            },
            "newDesignTool": "create_wf01_batch",
        },
        "targets": [
            _target_summary(
                target,
                top_candidate_limit=top_candidates_per_target,
            )
            for target in batch.get("targets") or []
        ],
        "result_summary": copy.deepcopy(batch.get("result_summary") or {}),
        "recommendations": copy.deepcopy(batch.get("recommendations") or []),
        "scientific_artifacts": copy.deepcopy(
            batch.get("scientific_artifacts") or []
        ),
        "ui_message": copy.deepcopy(batch.get("ui_message")),
        "detail_access": {
            "batch_state": "仅当非大模型客户端需要完整状态时使用 REST API。",
            "report_summary": "调用 get_wf01_report，省略 target_id。",
            "candidate_page": "调用 get_wf01_report，并提供 target_id 和 candidate_scope。",
            "candidate_detail": "调用 get_wf01_candidate。",
            "scientific_artifacts": (
                "调用 get_wf01_export，并使用 format_name=scientific_data "
                "下载科学工具数据包，不要把完整原始数据写入模型上下文。"
            ),
        },
    }
    if next_action == "WAIT_FOR_STAGE_COMPLETION":
        result["flowControl"] = {
            "action": "WAIT_FOR_STAGE_COMPLETION",
            "stopCurrentTurn": True,
            "retryInCurrentTurn": False,
        }
    if executed_stages is not None:
        result["executed_stages"] = list(executed_stages)
    m6 = batch.get("m6_execution") or {}
    if m6:
        exports = m6.get("exports") or {}
        artifact_keys = {
            "html": "htmlUrl",
            "process_html": "processHtmlUrl",
            "csv": "csvUrl",
            "pdf": "pdfUrl",
            "scientific_data": "scientificDataUrl",
        }
        result["artifacts"] = {
            artifact_keys[format_name]: value.get("file_url")
            for format_name, value in exports.items()
            if format_name in artifact_keys
            and isinstance(value, dict)
            and value.get("file_url")
        }
        result["report"] = {
            "report_id": batch.get("report_id") or m6.get("report_id"),
            "content_sha256": batch.get("report_content_sha256")
            or m6.get("canonical_content_sha256"),
            "completeness": copy.deepcopy(m6.get("completeness") or {}),
            "generated_formats": sorted(
                format_name
                for format_name in exports
                if format_name != "rules_html"
            ),
            "on_demand_formats": ["csv", "pdf"],
            "export_status": m6.get("export_status"),
            "export_failures": copy.deepcopy(
                m6.get("export_failures") or {}
            ),
        }
        if "html" not in exports:
            fallback_targets = []
            for target in batch.get("targets") or []:
                deliverable = (
                    (target.get("m5_execution") or {}).get(
                        "all_deliverable_candidates"
                    )
                    or []
                )
                total = len(deliverable)
                fallback_targets.append(
                    {
                        "target_id": target.get("target_id"),
                        "candidate_total": total,
                        "default_results": [
                            compact_candidate(candidate)
                            for candidate in deliverable[:10]
                            if isinstance(candidate, dict)
                        ],
                        "all_candidates_on_request": {
                            "condition": "USER_EXPLICITLY_REQUESTS_ALL_CANDIDATES",
                            "tool": "get_wf01_report",
                            "arguments": {
                                "batch_id": batch.get("batch_id"),
                                "target_id": target.get("target_id"),
                                "candidate_scope": "deliverable",
                                "page_size": 25,
                            },
                            "page_count": (total + 24) // 25,
                        },
                    }
                )
            result["text_fallback"] = {
                "status": "AVAILABLE",
                "reason": "HTML_EXPORT_UNAVAILABLE",
                "default_candidate_scope": "top10",
                "instruction": (
                    "只返回 default_results 中排名前 10 的候选。除非用户明确要求查看"
                    "全部候选序列，否则不得检索或展示所有候选。"
                ),
                "targets": fallback_targets,
            }
    return result


def summarize_wf01_report(
    report: dict[str, Any],
    *,
    top_candidates_per_target: int = 10,
) -> dict[str, Any]:
    """Return a narrative-ready view of the immutable canonical report."""
    targets = []
    for target in report.get("targets") or []:
        targets.append(
            {
                "target_id": target.get("target_id"),
                "ordinal": target.get("ordinal"),
                "conclusion": copy.deepcopy(target.get("conclusion") or {}),
                "candidate_counts": copy.deepcopy(
                    target.get("candidate_counts") or {}
                ),
                "top_candidates": [
                    compact_candidate(candidate)
                    for candidate in (target.get("top10") or [])[
                        :top_candidates_per_target
                    ]
                    if isinstance(candidate, dict)
                ],
                "top_candidate_total": len(target.get("top10") or []),
                "rule_summary": copy.deepcopy(target.get("rule_summary") or {}),
            }
        )
    return {
        "response_schema_version": AGENT_VIEW_SCHEMA_VERSION,
        "response_mode": "REPORT_SUMMARY",
        "report_id": report.get("report_id"),
        "batch_id": report.get("batch_id"),
        "batch_code": report.get("batch_code"),
        "generated_at": report.get("generated_at"),
        "batch_overview": copy.deepcopy(report.get("batch_overview") or {}),
        "completeness": copy.deepcopy(report.get("completeness") or {}),
        "targets": targets,
        "detail_access": {
            "candidate_page": "调用 get_wf01_report，并提供 target_id 和 candidate_scope。",
            "candidate_detail": "调用 get_wf01_candidate。",
            "exports": "调用 get_wf01_export。",
        },
    }


def compact_report_page(
    view: dict[str, Any],
    *,
    include_rule_details: bool = False,
) -> dict[str, Any]:
    """Compact one already-paginated canonical report view."""
    target = view.get("target") or {}
    candidate_view = target.get("candidate_view") or {}
    return {
        "response_schema_version": AGENT_VIEW_SCHEMA_VERSION,
        "response_mode": "CANDIDATE_PAGE",
        "canonical_report_id": view.get("canonical_report_id"),
        "canonical_content_sha256": view.get("canonical_content_sha256"),
        "view_filter": copy.deepcopy(view.get("view_filter") or {}),
        "target": {
            "target_id": target.get("target_id"),
            "ordinal": target.get("ordinal"),
            "conclusion": copy.deepcopy(target.get("conclusion") or {}),
            "candidate_counts": copy.deepcopy(target.get("candidate_counts") or {}),
            "rule_summary": copy.deepcopy(target.get("rule_summary") or {}),
            "candidate_view": {
                "scope": candidate_view.get("scope"),
                "page": candidate_view.get("page"),
                "page_size": candidate_view.get("page_size"),
                "total": candidate_view.get("total"),
                "items": [
                    compact_candidate(
                        candidate,
                        include_rule_details=include_rule_details,
                    )
                    for candidate in candidate_view.get("items") or []
                    if isinstance(candidate, dict)
                ],
            },
        },
    }
