"""WF-01 PRD-v0.2 M6 canonical report and export renderers.

M6 never reruns or changes scientific calculations.  It assembles every
persisted M1-M5 result into one immutable report model, validates structural
completeness, then renders the Top10 HTML, Chinese process HTML and a
human-readable scientific-data ZIP by default. CSV and PDF are rendered from
that same model only when the user explicitly requests them.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import html
import json
import math
import re
import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from .errors import ToolUnavailableError
from .identifiers import wf01_batch_code


M6_ENGINE_VERSION = "wf01-v0.2-m6.6"
CANONICAL_REPORT_SCHEMA_VERSION = "wf01-prd-v0.2-canonical-report-v1"
EXPORT_CONTRACT_VERSION = "wf01-prd-v0.2-export-v3"
RULE_CODES = tuple(f"R{index:02d}" for index in range(1, 22))
SCOPE_RULES = {
    "R13": ("NOT_APPLICABLE", "MGB", "WF-01 v0.2 本期不做 MGB"),
    "R14": ("NOT_APPLICABLE", "多重设计", "WF-01 v0.2 每个靶标独立设计，不做多重"),
    "R15": ("NOT_APPLICABLE", "内标", "WF-01 v0.2 本期不做内标"),
    "R16": ("NOT_EVALUATED", "保守性", "WF-01 v0.2 本期不评价保守性"),
    "R17": ("NOT_APPLICABLE", "多重产物长度差", "WF-01 v0.2 本期不做多重"),
}
DISCLAIMER = {
    "evidence_scope": "in_silico_only_unless_candidate_is_blast_verified",
    "experimental_validation": False,
    "research_use_only": True,
    "text": (
        "本报告为计算机模拟设计与数据库证据汇总。标记为 blast_verified 的候选仅表示已完成"
        "所记录数据库、版本和阈值下的计算核查；其余候选仅为 in_silico_only。所有结果均未"
        "经过湿实验验证，仅供科研设计和复核，不得直接作为临床诊断、生产放行或其他最终决策依据。"
    ),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _candidate_key(candidate: dict[str, Any]) -> str:
    return str(
        candidate.get("dedupe_key")
        or candidate.get("candidate_id")
        or _sha256_bytes(_json_bytes(candidate))
    )


def _candidate_id(candidate: dict[str, Any]) -> str:
    return str(candidate.get("candidate_id") or _candidate_key(candidate))


def _deep_merge_missing(base: dict[str, Any], extra: dict[str, Any]) -> None:
    """Fill absent report fields without overwriting the richer stage result."""
    for key, value in extra.items():
        if key not in base or base[key] in (None, "", [], {}):
            base[key] = copy.deepcopy(value)
        elif isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge_missing(base[key], value)


def _rule_evaluation_state(status: str) -> str:
    if status in {"PASS", "WARNING", "FAIL", "CAUTION"}:
        return "EVALUATED"
    if status == "NOT_APPLICABLE":
        return "NOT_APPLICABLE"
    return "NOT_EVALUATED"


def _rule(
    code: str,
    *,
    category: str,
    status: str,
    actual: Any,
    threshold: str,
    source: str,
    message: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "category": category,
        "status": status,
        "evaluation_state": _rule_evaluation_state(status),
        "actual": copy.deepcopy(actual),
        "threshold": threshold,
        "source": source,
        "message": message,
    }


def _specificity_placeholder(reason: str) -> dict[str, Any]:
    def mode(name: str) -> dict[str, Any]:
        return {
            "mode": name,
            "status": "NOT_EVALUATED",
            "objects": [],
            "database": None,
            "thresholds": None,
            "method": "not_run",
            "queries": {},
            "hits": [],
            "reason": reason,
            "error": None,
            "evaluation_state": "NOT_EVALUATED",
        }

    host = mode("HOST_EXCLUSION")
    host["object_results"] = []
    return {
        "overall_status": "NOT_EVALUATED",
        "target_hit": mode("TARGET_HIT"),
        "host_exclusion": host,
        "related_background_nt": mode("RELATED_BACKGROUND_NT"),
        "report_annotation": True,
        "reason": reason,
    }


def _candidate_thermodynamics(candidate: dict[str, Any]) -> dict[str, Any]:
    metadata = candidate.get("metadata") or {}
    scoring = metadata.get("wf01_v02_scoring") or {}
    required_metadata = {
        name: metadata.get(name)
        for name in (
            "primer3_version",
            "thermo_method",
            "salt_monovalent_mm",
            "salt_divalent_mm",
            "dntp_mm",
            "dna_conc_nm",
            "oligo_conc_nm",
        )
    }
    oligos = {}
    for name in ("forward_primer", "reverse_primer", "probe"):
        value = candidate.get(name) or {}
        oligos[name] = {
            "sequence_5to3": value.get("sequence_5to3"),
            "length_nt": value.get("length_nt"),
            "tm_celsius": value.get("tm_celsius"),
            "gc_percent": value.get("gc_percent"),
            "hairpin_dg_kcal": value.get("hairpin_dg_kcal"),
            "homodimer_dg_kcal": value.get("homodimer_dg_kcal"),
            "three_prime_dimer_dg_kcal": value.get(
                "three_prime_dimer_dg_kcal"
            ),
            "thermo_method": value.get("thermo_method"),
        }
    values_present = [
        value
        for oligo in oligos.values()
        for key, value in oligo.items()
        if key not in {"sequence_5to3"} and value is not None
    ]
    return {
        "status": "EVALUATED" if values_present else "NOT_EVALUATED",
        "metadata": required_metadata,
        "oligos": oligos,
        "pair_heterodimer_dg_kcal": candidate.get(
            "pair_heterodimer_dg_kcal"
        ),
        "three_prime_heterodimer_dg_kcal": candidate.get(
            "three_prime_heterodimer_dg_kcal"
        ),
        "three_prime_involved": candidate.get("three_prime_involved"),
        "scoring_dimension": copy.deepcopy(
            (scoring.get("dimensions") or {}).get("thermodynamics")
        ),
    }


def _r21_number_present(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _r21_mode_documented(
    mode: dict[str, Any] | None,
    *,
    allow_not_applicable: bool = False,
    allow_not_evaluated: bool = False,
) -> bool:
    if not isinstance(mode, dict):
        return False
    status = str(mode.get("status") or "").upper()
    if status in {"PASS", "FAIL", "WARNING", "CAUTION"}:
        return True
    if status == "NOT_EVALUATED":
        return bool(allow_not_evaluated and mode.get("reason"))
    return bool(
        allow_not_applicable
        and status == "NOT_APPLICABLE"
        and mode.get("reason")
    )


def _r21_specificity_documented(
    specificity: dict[str, Any],
    membership: dict[str, Any],
) -> bool:
    """Require evaluated BLAST for deliverables, or an explicit reason otherwise."""
    requires_evaluated_blast = any(
        bool(membership.get(name))
        for name in ("top10", "qualified", "deliverable")
    )
    if not requires_evaluated_blast:
        return bool(
            specificity.get("overall_status")
            and (
                specificity.get("reason")
                or specificity.get("report_annotation")
                or specificity.get("target_hit")
            )
        )

    target_documented = _r21_mode_documented(
        specificity.get("target_hit"),
        allow_not_evaluated=True,
    )
    host = specificity.get("host_exclusion") or {}
    host_results = host.get("object_results") or []
    host_documented = (
        bool(host_results)
        and all(
            _r21_mode_documented(item)
            or _r21_mode_documented(item, allow_not_evaluated=True)
            for item in host_results
            if isinstance(item, dict)
        )
    ) or _r21_mode_documented(
        host,
        allow_not_applicable=True,
        allow_not_evaluated=True,
    )
    related_documented = _r21_mode_documented(
        specificity.get("related_background_nt"),
        allow_not_evaluated=True,
    )
    return target_documented and host_documented and related_documented


def _candidate_scoring(candidate: dict[str, Any]) -> dict[str, Any]:
    scoring = copy.deepcopy(
        (candidate.get("metadata") or {}).get("wf01_v02_scoring") or {}
    )
    if not scoring:
        scoring = {
            "status": "NOT_EVALUATED",
            "weights": {
                "thermodynamics": 0.357,
                "specificity": 0.357,
                "amplicon_features": 0.286,
            },
            "dimensions": {},
            "base_score": None,
            "penalties": [],
            "total_penalty_percent": None,
            "final_score": candidate.get("score"),
        }
    scoring["reported_score"] = candidate.get("score")
    scoring["reported_rank"] = candidate.get("rank")
    scoring["display_rank"] = candidate.get("display_rank")
    return scoring


def _exclusion_reasons(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    reasons: list[dict[str, Any]] = []
    for rule in candidate.get("rules") or []:
        if rule.get("status") in {"FAIL", "NOT_EVALUATED"}:
            reasons.append(
                {
                    "type": "RULE",
                    "code": rule.get("code"),
                    "status": rule.get("status"),
                    "message": rule.get("message"),
                    "actual": copy.deepcopy(rule.get("actual")),
                }
            )
    specificity = candidate.get("specificity") or {}
    if specificity.get("overall_status") in {"FAIL", "NOT_EVALUATED"}:
        reasons.append(
            {
                "type": "SPECIFICITY",
                "code": "R19",
                "status": specificity.get("overall_status"),
                "message": specificity.get("reason"),
                "actual": None,
            }
        )
    verdict = str(candidate.get("verdict") or "")
    if not reasons and verdict not in {"PASS", "CAUTION", "AWAITING_SPECIFICITY"}:
        reasons.append(
            {
                "type": "VERDICT",
                "code": None,
                "status": verdict or "EXCLUDED",
                "message": "候选未进入最终合格集合",
                "actual": None,
            }
        )
    return reasons


def _normalise_rules(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    existing: dict[str, dict[str, Any]] = {}
    for item in candidate.get("rules") or []:
        code = str(item.get("code") or "")
        if code in RULE_CODES and code not in existing:
            value = copy.deepcopy(item)
            value["evaluation_state"] = _rule_evaluation_state(
                str(value.get("status") or "NOT_EVALUATED")
            )
            existing[code] = value

    for code in RULE_CODES[:12]:
        if code not in existing:
            existing[code] = _rule(
                code,
                category="hard_or_soft",
                status="NOT_EVALUATED",
                actual=None,
                threshold="见 PRD v0.2 R01-R12",
                source="M2 rule engine",
                message="上游候选未保存该规则结果；M6 不伪造科学判定",
            )
    for code, (status, threshold, message) in SCOPE_RULES.items():
        existing[code] = _rule(
            code,
            category="scope",
            status=status,
            actual=None,
            threshold=threshold,
            source="PRD v0.2 scope",
            message=message,
        )
    if "R18" not in existing:
        thermo = _candidate_thermodynamics(candidate)
        existing["R18"] = _rule(
            "R18",
            category="metadata",
            status=(
                "PASS"
                if thermo["status"] == "EVALUATED"
                else "NOT_EVALUATED"
            ),
            actual=thermo["metadata"],
            threshold="热力学软件、版本、算法和浓度元数据完整",
            source="M6 canonical report",
            message="M6 根据已保存的热力学元数据汇总，不重新计算",
        )
    specificity = candidate.get("specificity") or {}
    if "R19" not in existing:
        specificity_status = str(
            specificity.get("overall_status") or "NOT_EVALUATED"
        )
        existing["R19"] = _rule(
            "R19",
            category="specificity",
            status={
                "CAUTION": "WARNING",
                "PASS": "PASS",
                "FAIL": "FAIL",
            }.get(specificity_status, "NOT_EVALUATED"),
            actual={"overall_status": specificity_status},
            threshold="靶标命中与背景证据完整；指定宿主时追加宿主排除证据",
            source="M4 specificity engine",
            message="M6 仅汇总 M4 已保存的特异性状态",
        )
    existing["R20"] = _rule(
        "R20",
        category="metadata",
        status="PASS",
        actual={
            "evidence_level": candidate.get("evidence_level")
            or "in_silico_only",
            "experimental_validation": False,
            "disclaimer_present": True,
        },
        threshold="明确证据等级、实验验证状态和免责声明",
        source="M6 canonical report",
        message="候选证据等级与未实验验证声明已写入报告",
    )
    # R21 is finalized after all report-only candidate sections are attached.
    existing.pop("R21", None)
    return [existing[code] for code in RULE_CODES if code in existing]


def _candidate_for_report(
    candidate: dict[str, Any],
    *,
    membership: dict[str, Any],
) -> dict[str, Any]:
    value = copy.deepcopy(candidate)
    value["candidate_id"] = _candidate_id(value)
    value["dedupe_key"] = _candidate_key(value)
    value["report_membership"] = copy.deepcopy(membership)
    value["specificity"] = copy.deepcopy(
        value.get("specificity")
        or _specificity_placeholder(
            "NOT_RUN_CANDIDATE_EXCLUDED_BEFORE_SPECIFICITY"
            if membership.get("excluded")
            else "SPECIFICITY_RESULT_NOT_SAVED"
        )
    )
    value["thermodynamic_metadata"] = _candidate_thermodynamics(value)
    value["scoring_detail"] = _candidate_scoring(value)
    value["amplicon_secondary_structure"] = {
        "status": "NOT_EVALUATED",
        "reason": "OUT_OF_SCOPE_WF01_V0_2",
        "method": None,
        "result": None,
    }
    value["conservation"] = {
        "status": "NOT_EVALUATED",
        "reason": "OUT_OF_SCOPE_WF01_V0_2",
        "result": None,
    }
    value["exclusion_reasons"] = _exclusion_reasons(value)
    value["rules"] = _normalise_rules(value)

    forward = value.get("forward_primer") or {}
    reverse = value.get("reverse_primer") or {}
    probe = value.get("probe") or {}
    amplicon = value.get("amplicon") or {}
    specificity = value.get("specificity") or {}
    required_fields = {
        "candidate_id": bool(value["candidate_id"]),
        "forward_primer_sequence": bool(forward.get("sequence_5to3")),
        "reverse_primer_sequence": bool(reverse.get("sequence_5to3")),
        "probe_sequence": bool(probe.get("sequence_5to3")),
        "amplicon_sequence": bool(amplicon.get("sequence_5to3")),
        "amplicon_length": _r21_number_present(amplicon.get("length_bp")),
        "forward_primer_tm": _r21_number_present(forward.get("tm_celsius")),
        "reverse_primer_tm": _r21_number_present(reverse.get("tm_celsius")),
        "probe_tm": _r21_number_present(probe.get("tm_celsius")),
        "forward_hairpin_dg": _r21_number_present(
            forward.get("hairpin_dg_kcal")
        ),
        "reverse_hairpin_dg": _r21_number_present(
            reverse.get("hairpin_dg_kcal")
        ),
        "probe_hairpin_dg": _r21_number_present(
            probe.get("hairpin_dg_kcal")
        ),
        "forward_homodimer_dg": _r21_number_present(
            forward.get("homodimer_dg_kcal")
        ),
        "reverse_homodimer_dg": _r21_number_present(
            reverse.get("homodimer_dg_kcal")
        ),
        "probe_homodimer_dg": _r21_number_present(
            probe.get("homodimer_dg_kcal")
        ),
        "forward_three_prime_dimer_dg": _r21_number_present(
            forward.get("three_prime_dimer_dg_kcal")
        ),
        "reverse_three_prime_dimer_dg": _r21_number_present(
            reverse.get("three_prime_dimer_dg_kcal")
        ),
        "probe_three_prime_dimer_dg": _r21_number_present(
            probe.get("three_prime_dimer_dg_kcal")
        ),
        "pair_heterodimer_dg": _r21_number_present(
            value.get("pair_heterodimer_dg_kcal")
        ),
        "three_prime_heterodimer_dg": _r21_number_present(
            value.get("three_prime_heterodimer_dg_kcal")
        ),
        "thermodynamic_metadata": (
            (value.get("thermodynamic_metadata") or {}).get("status")
            == "EVALUATED"
        ),
        "blast_summary_or_explicit_exclusion": (
            _r21_specificity_documented(specificity, membership)
        ),
        "scoring_detail": isinstance(value.get("scoring_detail"), dict),
        "rules_R01_R20": {
            str(item.get("code")) for item in value.get("rules") or []
        }
        == set(RULE_CODES[:20]),
    }
    missing = [name for name, present in required_fields.items() if not present]
    value["rules"].append(
        _rule(
            "R21",
            category="metadata",
            status="PASS" if not missing else "FAIL",
            actual={
                "required_fields": required_fields,
                "missing_fields": missing,
                "conservation_scoring": {
                    "status": "DEFERRED",
                    "included_in_r21_pass_condition": False,
                    "reason": "DEFERRED_BY_CURRENT_RELEASE_SCOPE",
                },
            },
            threshold=(
                "引物、探针和扩增产物序列、Tm、关键 ΔG、扩增子长度及 "
                "BLAST 摘要齐全；保守性评分本版本暂缓"
            ),
            source="M6 canonical report validator",
            message=(
                "候选报告必需数据齐全；保守性评分本版本暂缓"
                if not missing
                else "候选报告缺少必需数据"
            ),
        )
    )
    return value


def _candidate_sources(target: dict[str, Any]) -> list[tuple[str, list[dict[str, Any]]]]:
    m2 = target.get("m2_execution") or {}
    m3 = target.get("m3_execution") or {}
    m4 = target.get("m4_execution") or {}
    m5 = target.get("m5_execution") or {}
    hard_rule_rejected = (
        m4.get("hard_rule_rejected_candidates")
        or m3.get("all_hard_rule_rejected_candidates")
    )
    hard_rule_rejected_collection = "HARD_RULE_REJECTED"
    if not hard_rule_rejected:
        hard_rule_rejected = m2.get("hard_rule_rejected_candidates") or []
        hard_rule_rejected_collection = "M2_HARD_RULE_REJECTED"
    hard_rule_incomplete = (
        m4.get("hard_rule_incomplete_candidates")
        or m3.get("all_hard_rule_incomplete_candidates")
    )
    hard_rule_incomplete_collection = "HARD_RULE_INCOMPLETE"
    if not hard_rule_incomplete:
        hard_rule_incomplete = m2.get("hard_rule_incomplete_candidates") or []
        hard_rule_incomplete_collection = "M2_HARD_RULE_INCOMPLETE"
    return [
        ("TOP10", m5.get("top10") or []),
        ("DELIVERABLE", m5.get("all_deliverable_candidates") or []),
        ("QUALIFIED", m4.get("all_qualified_candidates") or []),
        (
            "SPECIFICITY_INCOMPLETE",
            m4.get("all_specificity_incomplete_candidates") or [],
        ),
        (
            "SPECIFICITY_FAILED",
            m4.get("all_specificity_failed_candidates") or [],
        ),
        (
            hard_rule_rejected_collection,
            hard_rule_rejected,
        ),
        (
            hard_rule_incomplete_collection,
            hard_rule_incomplete,
        ),
        (
            "HARD_RULE_PASS",
            m3.get("all_hard_rule_pass_candidates")
            or m2.get("hard_rule_pass_candidates")
            or [],
        ),
    ]


def _assemble_candidates(target: dict[str, Any]) -> dict[str, Any]:
    records: dict[str, dict[str, Any]] = {}
    memberships: dict[str, set[str]] = {}
    for collection, candidates in _candidate_sources(target):
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            key = _candidate_key(candidate)
            memberships.setdefault(key, set()).add(collection)
            if key not in records:
                records[key] = copy.deepcopy(candidate)
            else:
                _deep_merge_missing(records[key], candidate)

    m5 = target.get("m5_execution") or {}
    top10_keys = {
        _candidate_key(item)
        for item in m5.get("top10") or []
        if isinstance(item, dict)
    }
    qualified_keys = {
        _candidate_key(item)
        for item in (target.get("m4_execution") or {}).get(
            "all_qualified_candidates"
        )
        or []
        if isinstance(item, dict)
    }
    excluded_collections = {
        "SPECIFICITY_FAILED",
        "HARD_RULE_REJECTED",
        "HARD_RULE_INCOMPLETE",
        "M2_HARD_RULE_REJECTED",
        "M2_HARD_RULE_INCOMPLETE",
    }
    reported: list[dict[str, Any]] = []
    for key, candidate in records.items():
        categories = sorted(memberships[key])
        excluded = bool(set(categories) & excluded_collections)
        reported.append(
            _candidate_for_report(
                candidate,
                membership={
                    "collections": categories,
                    "top10": key in top10_keys,
                    "qualified": key in qualified_keys,
                    "deliverable": "DELIVERABLE" in categories,
                    "excluded": excluded,
                    "exclusion_categories": sorted(
                        set(categories) & excluded_collections
                    ),
                },
            )
        )

    def order(item: dict[str, Any]) -> tuple[Any, ...]:
        membership = item["report_membership"]
        return (
            0 if membership["top10"] else 1,
            math.inf
            if item.get("display_rank") is None
            else int(item["display_rank"]),
            math.inf if item.get("rank") is None else int(item["rank"]),
            _candidate_id(item),
        )

    reported.sort(key=order)
    by_key = {_candidate_key(item): item for item in reported}

    def select(candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        output = []
        seen: set[str] = set()
        for item in candidates:
            if not isinstance(item, dict):
                continue
            key = _candidate_key(item)
            if key in by_key and key not in seen:
                seen.add(key)
                output.append(copy.deepcopy(by_key[key]))
        return output

    m4 = target.get("m4_execution") or {}
    occurrences = []
    m2 = target.get("m2_execution") or {}
    for collection in (
        "hard_rule_pass_candidates",
        "hard_rule_rejected_candidates",
        "hard_rule_incomplete_candidates",
    ):
        for item in m2.get(collection) or []:
            if isinstance(item, dict):
                occurrences.append(
                    {
                        "source_collection": collection,
                        "candidate_id": item.get("candidate_id"),
                        "dedupe_key": item.get("dedupe_key")
                        or _candidate_key(item),
                        "exploration_unit_id": (
                            item.get("metadata") or {}
                        ).get("exploration_unit_id"),
                        "parameter_run_id": (
                            item.get("metadata") or {}
                        ).get("parameter_run_id"),
                        "source_metadata": copy.deepcopy(
                            item.get("metadata") or {}
                        ),
                        "primer3_pair_penalty": item.get(
                            "primer3_pair_penalty"
                        ),
                        "forward_sequence_5to3": (
                            item.get("forward_primer") or {}
                        ).get("sequence_5to3"),
                        "reverse_sequence_5to3": (
                            item.get("reverse_primer") or {}
                        ).get("sequence_5to3"),
                        "probe_sequence_5to3": (
                            item.get("probe") or {}
                        ).get("sequence_5to3"),
                        "m2_verdict": item.get("verdict"),
                    }
                )
    return {
        "top10": select(m5.get("top10") or []),
        "all_qualified_candidates": select(
            m4.get("all_qualified_candidates") or []
        ),
        "all_deliverable_candidates": select(
            m5.get("all_deliverable_candidates") or []
        ),
        "all_excluded_candidates": [
            copy.deepcopy(item)
            for item in reported
            if item["report_membership"]["excluded"]
        ],
        "all_specificity_incomplete_candidates": select(
            m4.get("all_specificity_incomplete_candidates") or []
        ),
        "all_primer3_candidates": copy.deepcopy(reported),
        "primer3_candidate_occurrences": occurrences,
    }


def _target_events(batch: dict[str, Any], target_id: str) -> list[dict[str, Any]]:
    return [
        copy.deepcopy(item)
        for item in batch.get("events") or []
        if item.get("target_id") in (None, target_id)
    ]


def _specificity_evidence(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    databases: dict[str, dict[str, Any]] = {}
    hits: list[dict[str, Any]] = []
    for candidate in candidates:
        specificity = candidate.get("specificity") or {}
        modes = [
            specificity.get("target_hit"),
            specificity.get("related_background_nt"),
        ]
        modes.extend(
            (specificity.get("host_exclusion") or {}).get(
                "object_results"
            )
            or []
        )
        for result in modes:
            if not isinstance(result, dict):
                continue
            database = result.get("database")
            if isinstance(database, dict):
                key = str(
                    database.get("resource_key")
                    or database.get("key")
                    or _sha256_bytes(_json_bytes(database))
                )
                databases[key] = copy.deepcopy(database)
            for hit in result.get("hits") or []:
                hits.append(
                    {
                        "candidate_id": candidate.get("candidate_id"),
                        "mode": result.get("mode"),
                        "database_resource_key": (
                            database or {}
                        ).get("resource_key"),
                        "database_version": (database or {}).get("version"),
                        "thresholds": copy.deepcopy(result.get("thresholds")),
                        "hit": copy.deepcopy(hit),
                    }
                )
    return {
        "database_resources": list(databases.values()),
        "hit_evidence": hits,
        "candidate_count": len(candidates),
        "hit_count": len(hits),
    }


def _target_report(batch: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    candidates = _assemble_candidates(target)
    m2 = target.get("m2_execution") or {}
    m3 = target.get("m3_execution") or {}
    m4 = target.get("m4_execution") or {}
    m5 = target.get("m5_execution") or {}
    all_candidates = candidates["all_primer3_candidates"]
    rule_summary = {
        code: {
            status: sum(
                any(
                    rule.get("code") == code and rule.get("status") == status
                    for rule in candidate.get("rules") or []
                )
                for candidate in all_candidates
            )
            for status in (
                "PASS",
                "WARNING",
                "FAIL",
                "NOT_EVALUATED",
                "NOT_APPLICABLE",
            )
        }
        for code in RULE_CODES
    }
    return {
        "target_id": target.get("target_id"),
        "ordinal": target.get("ordinal"),
        "input": {
            "template": copy.deepcopy(target.get("template")),
            "parameters": copy.deepcopy(target.get("parameters")),
            "issues": copy.deepcopy(target.get("issues") or []),
            "target_object": copy.deepcopy(target.get("target_object")),
            "host_objects": copy.deepcopy(target.get("host_objects") or []),
            "related_objects": copy.deepcopy(
                target.get("related_objects") or []
            ),
            "background_objects": copy.deepcopy(
                target.get("background_objects") or []
            ),
        },
        "conclusion": {
            "status": target.get("status"),
            "status_reason": m5.get("status_reason"),
            "result_summary": copy.deepcopy(
                target.get("result_summary")
                or m5.get("result_summary")
                or {}
            ),
            "search_complete": bool(target.get("search_complete")),
            "technical_search_complete": bool(
                target.get("technical_search_complete")
            ),
            "completed_scope": copy.deepcopy(
                m5.get("completed_scope") or []
            ),
            "incomplete_scope": copy.deepcopy(
                m5.get("incomplete_scope") or []
            ),
            "incomplete_reasons": copy.deepcopy(
                target.get("incomplete_reasons")
                or m5.get("incomplete_reasons")
                or []
            ),
            "recommendations": copy.deepcopy(
                target.get("recommendations")
                or m5.get("recommendations")
                or []
            ),
            "candidate_notice": copy.deepcopy(
                m5.get("candidate_notice") or {}
            ),
        },
        "candidate_counts": copy.deepcopy(
            target.get("candidate_counts")
            or m5.get("candidate_counts")
            or {}
        ),
        **candidates,
        "rule_summary": rule_summary,
        "search_records": {
            "parameter_runs": copy.deepcopy(m2.get("parameter_runs") or []),
            "exploration_manifest": copy.deepcopy(
                m2.get("exploration_manifest")
                or target.get("exploration_manifest")
                or {}
            ),
            "exploration_units": copy.deepcopy(
                m2.get("exploration_units") or []
            ),
            "m3_manifest_validation": copy.deepcopy(
                m3.get("manifest_validation") or {}
            ),
            "m3_deduplication": copy.deepcopy(
                m3.get("deduplication") or {}
            ),
            "m4_provider_status": copy.deepcopy(
                m4.get("provider_status") or {}
            ),
            "m4_resource_resolution": copy.deepcopy(
                m4.get("resource_resolution") or {}
            ),
            "m4_policy": {
                "policy_version": m4.get("policy_version"),
                "policy_status": m4.get("policy_status"),
                "ranking_status": m4.get("ranking_status"),
                "search_incomplete_reason": m4.get(
                    "search_incomplete_reason"
                ),
            },
            "events": _target_events(
                batch, str(target.get("target_id") or "")
            ),
        },
        "specificity_evidence": _specificity_evidence(all_candidates),
        "out_of_scope": {
            "mgb": {
                "status": "NOT_APPLICABLE",
                "reason": "OUT_OF_SCOPE_WF01_V0_2",
            },
            "multiplex": {
                "status": "NOT_APPLICABLE",
                "reason": "OUT_OF_SCOPE_WF01_V0_2",
            },
            "internal_control": {
                "status": "NOT_APPLICABLE",
                "reason": "OUT_OF_SCOPE_WF01_V0_2",
            },
            "conservation": {
                "status": "NOT_EVALUATED",
                "reason": "OUT_OF_SCOPE_WF01_V0_2",
            },
            "amplicon_secondary_structure": {
                "status": "NOT_EVALUATED",
                "reason": "OUT_OF_SCOPE_WF01_V0_2",
            },
        },
    }


def validate_canonical_report(report: dict[str, Any]) -> dict[str, Any]:
    """Validate completeness without treating missing scientific evidence as PASS."""
    issues: list[dict[str, Any]] = []

    def issue(code: str, path: str, message: str) -> None:
        issues.append({"code": code, "path": path, "message": message})

    for name in (
        "report_id",
        "batch_overview",
        "target_index",
        "targets",
        "out_of_scope",
        "disclaimer",
    ):
        if name not in report:
            issue("MISSING_REPORT_SECTION", name, "缺少报告必需章节")
    targets = report.get("targets") or []
    target_index = report.get("target_index") or []
    expected = int(
        (report.get("batch_overview") or {}).get("target_total", 0)
    )
    if len(targets) != expected or len(target_index) != expected:
        issue(
            "TARGET_COUNT_MISMATCH",
            "targets",
            "报告靶标数与批次总数不一致",
        )
    if {item.get("target_id") for item in targets} != {
        item.get("target_id") for item in target_index
    }:
        issue(
            "TARGET_INDEX_MISMATCH",
            "target_index",
            "靶标索引与完整靶标数据不一致",
        )

    for target_position, target in enumerate(targets):
        base = f"targets[{target_position}]"
        for section in (
            "input",
            "conclusion",
            "top10",
            "all_qualified_candidates",
            "all_excluded_candidates",
            "all_primer3_candidates",
            "primer3_candidate_occurrences",
            "search_records",
            "specificity_evidence",
            "out_of_scope",
        ):
            if section not in target:
                issue(
                    "MISSING_TARGET_SECTION",
                    f"{base}.{section}",
                    "缺少靶标报告必需章节",
                )
        for candidate_position, candidate in enumerate(
            target.get("all_primer3_candidates") or []
        ):
            candidate_path = (
                f"{base}.all_primer3_candidates[{candidate_position}]"
            )
            rule_map = {
                item.get("code"): item
                for item in candidate.get("rules") or []
            }
            if set(rule_map) != set(RULE_CODES):
                issue(
                    "RULE_SET_INCOMPLETE",
                    f"{candidate_path}.rules",
                    "候选必须包含 R01-R21",
                )
            for code, expected_status in {
                "R13": "NOT_APPLICABLE",
                "R14": "NOT_APPLICABLE",
                "R15": "NOT_APPLICABLE",
                "R16": "NOT_EVALUATED",
                "R17": "NOT_APPLICABLE",
            }.items():
                if (rule_map.get(code) or {}).get("status") != expected_status:
                    issue(
                        "INVALID_SCOPE_RULE_STATE",
                        f"{candidate_path}.rules.{code}",
                        f"{code} 必须为 {expected_status}",
                    )
            if (rule_map.get("R21") or {}).get("status") != "PASS":
                issue(
                    "CANDIDATE_REPORT_INCOMPLETE",
                    candidate_path,
                    "R21 报告完整性未通过",
                )
            if (
                candidate.get("amplicon_secondary_structure") or {}
            ).get("status") != "NOT_EVALUATED":
                issue(
                    "SECONDARY_STRUCTURE_SCOPE_LEAK",
                    f"{candidate_path}.amplicon_secondary_structure",
                    "本期扩增子二级结构必须明确为 NOT_EVALUATED",
                )

    scope = report.get("out_of_scope") or {}
    for name in ("mgb", "multiplex", "internal_control"):
        if (scope.get(name) or {}).get("status") != "NOT_APPLICABLE":
            issue(
                "INVALID_OUT_OF_SCOPE_STATE",
                f"out_of_scope.{name}",
                "本期外能力必须标记 NOT_APPLICABLE",
            )
    for name in ("conservation", "amplicon_secondary_structure"):
        if (scope.get(name) or {}).get("status") != "NOT_EVALUATED":
            issue(
                "INVALID_NOT_EVALUATED_STATE",
                f"out_of_scope.{name}",
                "本期未评价能力必须标记 NOT_EVALUATED",
            )
    if not (report.get("disclaimer") or {}).get("text"):
        issue(
            "DISCLAIMER_MISSING",
            "disclaimer.text",
            "必须包含未实验验证与用途免责声明",
        )
    return {
        "status": "COMPLETE" if not issues else "INCOMPLETE",
        "validator_version": M6_ENGINE_VERSION,
        "checked_at": utc_now(),
        "issue_count": len(issues),
        "issues": issues,
    }


class WF01V02CanonicalReportAssembler:
    """Build the immutable M6 data source consumed by every renderer."""

    def assemble(self, batch: dict[str, Any]) -> dict[str, Any]:
        generated_at = utc_now()
        report_id = f"wf01-report-{batch['batch_id']}-v{batch.get('task_version', 1)}"
        targets = [
            _target_report(batch, target)
            for target in batch.get("targets") or []
        ]
        m5 = batch.get("m5_execution") or {}
        m7 = batch.get("m7_execution") or {}
        version_diff_status = (
            "NOT_APPLICABLE"
            if batch.get("source_batch_id") is None
            else (
                "M7_APPLIED" if m7.get("stage") == "M7_VERSION_CREATED" else "PENDING_M7"
            )
        )
        report = {
            "schema_version": CANONICAL_REPORT_SCHEMA_VERSION,
            "export_contract_version": EXPORT_CONTRACT_VERSION,
            "report_id": report_id,
            "batch_id": batch.get("batch_id"),
            "batch_code": (
                batch.get("batch_code")
                or wf01_batch_code(
                    str(batch.get("batch_id") or ""),
                    batch.get("created_at"),
                    workflow=str(batch.get("workflow") or "WF-01"),
                )
            ),
            "task_version": batch.get("task_version"),
            "source_batch_id": batch.get("source_batch_id"),
            "source_version": batch.get("source_version"),
            "workflow": batch.get("workflow"),
            "prd_version": batch.get("prd_version"),
            "generated_at": generated_at,
            "generator": {
                "stage": "M6",
                "engine_version": M6_ENGINE_VERSION,
                "scientific_recalculation": False,
                "source_stages": ["M1", "M2", "M3", "M4", "M5"],
            },
            "data_scope": {
                "scope": "COMPLETE_BATCH",
                "target_filter_applied": False,
                "pagination_applied": False,
                "ui_filter_can_mutate_export": False,
                "user_download_formats": [
                    "html",
                    "process_html",
                    "rules_html",
                    "scientific_data",
                    "csv",
                    "pdf",
                ],
                "default_generated_formats": [
                    "html",
                    "process_html",
                    "rules_html",
                    "scientific_data",
                ],
                "on_demand_formats": ["csv", "pdf"],
                "user_json_download": False,
            },
            "batch_overview": {
                "task_name": (
                    (batch.get("preflight") or {})
                    .get("normalised_input", {})
                    .get("task_name")
                ),
                "status": batch.get("status"),
                "status_reason": m5.get("status_reason"),
                "search_complete": bool(batch.get("search_complete")),
                "technical_search_complete": bool(
                    batch.get("technical_search_complete")
                ),
                "target_total": len(targets),
                "target_counts": copy.deepcopy(
                    batch.get("target_counts") or {}
                ),
                "candidate_counts": copy.deepcopy(
                    batch.get("candidate_counts") or {}
                ),
                "candidate_notice": copy.deepcopy(
                    m5.get("candidate_notice") or {}
                ),
                "created_at": batch.get("created_at"),
                "m5_summarized_at": m5.get("summarized_at"),
            },
            "target_index": [
                {
                    "target_id": target["target_id"],
                    "ordinal": target["ordinal"],
                    "record_name": (
                        target.get("input", {})
                        .get("template", {})
                        .get("record_name")
                    ),
                    "status": target["conclusion"]["status"],
                    "status_reason": target["conclusion"]["status_reason"],
                    "search_complete": target["conclusion"][
                        "search_complete"
                    ],
                    "technical_search_complete": target["conclusion"][
                        "technical_search_complete"
                    ],
                    "candidate_counts": copy.deepcopy(
                        target["candidate_counts"]
                    ),
                    "candidate_notice": copy.deepcopy(
                        target["conclusion"].get("candidate_notice") or {}
                    ),
                }
                for target in targets
            ],
            "targets": targets,
            "batch_search_records": {
                "events": copy.deepcopy(batch.get("events") or []),
                "m2_outcome": batch.get("m2_outcome"),
                "m3_outcome": batch.get("m3_outcome"),
                "m4_outcome": batch.get("m4_outcome"),
                "m5_outcome": batch.get("m5_outcome"),
                "failed_target_ids": copy.deepcopy(
                    m5.get("failed_target_ids") or []
                ),
                "incomplete_target_ids": copy.deepcopy(
                    m5.get("incomplete_target_ids") or []
                ),
                "recommendations": copy.deepcopy(
                    batch.get("recommendations")
                    or m5.get("recommendations")
                    or []
                ),
            },
            "out_of_scope": {
                "mgb": {
                    "status": "NOT_APPLICABLE",
                    "reason": "OUT_OF_SCOPE_WF01_V0_2",
                },
                "multiplex": {
                    "status": "NOT_APPLICABLE",
                    "reason": "OUT_OF_SCOPE_WF01_V0_2",
                },
                "internal_control": {
                    "status": "NOT_APPLICABLE",
                    "reason": "OUT_OF_SCOPE_WF01_V0_2",
                },
                "conservation": {
                    "status": "NOT_EVALUATED",
                    "reason": "OUT_OF_SCOPE_WF01_V0_2",
                },
                "amplicon_secondary_structure": {
                    "status": "NOT_EVALUATED",
                    "reason": "OUT_OF_SCOPE_WF01_V0_2",
                },
            },
            "disclaimer": copy.deepcopy(DISCLAIMER),
            "version_diff": {
                "status": version_diff_status,
                "source_batch_id": batch.get("source_batch_id"),
                "source_version": batch.get("source_version"),
                "changes": copy.deepcopy(batch.get("version_diff") or []),
                "redesign_contract_version": m7.get(
                    "redesign_contract_version"
                ),
                "reason": m7.get("reason"),
                "reused_target_ids": copy.deepcopy(
                    m7.get("reused_target_ids") or []
                ),
                "rerun_target_ids": copy.deepcopy(
                    m7.get("rerun_target_ids") or []
                ),
                "source_report_id": m7.get("source_report_id"),
                "source_report_content_sha256": m7.get(
                    "source_report_content_sha256"
                ),
            },
        }
        report["completeness"] = validate_canonical_report(report)
        digest_source = copy.deepcopy(report)
        digest_source.pop("content_sha256", None)
        report["content_sha256"] = _sha256_bytes(_json_bytes(digest_source))
        return report


def _format_number(value: Any, digits: int = 3) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return str(value)


def _process_datetime(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "未记录"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone(timedelta(hours=8)))
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _process_human_size(value: Any) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "未记录"
    units = ["B", "KB", "MB", "GB"]
    unit = units[0]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            break
        size /= 1024
    digits = 0 if unit == "B" else 1
    return f"{size:.{digits}f} {unit}"


def _process_short_hash(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) <= 24:
        return text or "未记录"
    return f"{text[:12]}…{text[-8:]}"


def _process_status_class(value: Any) -> str:
    text = str(value or "")
    if any(word in text for word in ("未通过", "失败", "拒绝")):
        return "fail"
    if any(word in text for word in ("关注", "未完成", "未评估", "等待")):
        return "warning"
    if any(word in text for word in ("通过", "完成", "合格", "可交付", "不适用")):
        return "pass"
    return "neutral"


def _html_cell(header: str, value: Any) -> str:
    if header == "E-value" and value is not None:
        try:
            return html.escape(f"{float(value):.3g}")
        except (TypeError, ValueError):
            return html.escape(str(value))
    text = _format_number(value)
    if "时间" in header:
        text = _process_datetime(value)
    elif "数据大小" in header:
        text = _process_human_size(value)
    elif "校验值" in header:
        full = str(value or "").strip()
        return (
            f'<abbr class="checksum" title="{html.escape(full)}">'
            f"{html.escape(_process_short_hash(full))}</abbr>"
        )
    if header in {"设计参数", "实际参数"} and "；" in text:
        items = "".join(
            f"<li>{html.escape(item)}</li>"
            for item in text.split("；")
            if item.strip()
        )
        return f'<ul class="parameter-list">{items}</ul>'
    if "序列" in header and text not in {"-", "未记录"}:
        return f'<span class="sequence">{html.escape(text)}</span>'
    if header in {"筛选结论", "检测结论", "执行结果", "数据状态", "结论"}:
        return (
            f'<span class="status {_process_status_class(text)}">'
            f"{html.escape(text)}</span>"
        )
    return html.escape(text)


def _html_table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{html.escape(value)}</th>" for value in headers)
    body = []
    for row in rows:
        cells = "".join(
            f"<td>{_html_cell(headers[index], value)}</td>"
            for index, value in enumerate(row)
        )
        body.append(f"<tr>{cells}</tr>")
    if not body:
        body.append(
            f'<tr><td colspan="{len(headers)}" class="empty">无记录</td></tr>'
        )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        + head
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def _html_table_without_columns(
    headers: list[str],
    rows: list[list[Any]],
    omitted_headers: set[str],
) -> str:
    """Hide audit-only columns from the reader-facing HTML report."""
    kept_indexes = [
        index for index, header in enumerate(headers) if header not in omitted_headers
    ]
    visible_headers = [headers[index] for index in kept_indexes]
    visible_rows = [
        [row[index] if index < len(row) else None for index in kept_indexes]
        for row in rows
    ]
    return _html_table(visible_headers, visible_rows)


def _candidate_rows(candidates: list[dict[str, Any]]) -> list[list[Any]]:
    rows = []
    for candidate in candidates:
        rows.append(
            [
                candidate.get("display_rank") or candidate.get("rank"),
                candidate.get("candidate_id"),
                (candidate.get("forward_primer") or {}).get(
                    "sequence_5to3"
                ),
                (candidate.get("reverse_primer") or {}).get(
                    "sequence_5to3"
                ),
                (candidate.get("probe") or {}).get("sequence_5to3"),
                (candidate.get("amplicon") or {}).get("length_bp"),
                candidate.get("score"),
                candidate.get("verdict"),
                candidate.get("evidence_level"),
            ]
        )
    return rows


_PROCESS_STATUS_LABELS = {
    "COMPLETED": "已完成",
    "SUCCEEDED": "已完成",
    "PARTIAL": "部分完成",
    "PARTIALLY_SUCCEEDED": "部分完成",
    "PASS": "通过",
    "PASS_WITH_WARNINGS": "通过，需关注",
    "WARNING": "需关注",
    "CAUTION": "需关注",
    "FAIL": "未通过",
    "FAILED": "未通过",
    "QUALIFIED": "合格",
    "DELIVERABLE": "可交付",
    "SELECTED": "已入选",
    "RECOMMENDED": "推荐",
    "EXCLUDED": "未进入交付集合",
    "HARD_RULE_PASS": "硬性检测通过",
    "HARD_RULE_REJECTED": "硬性检测未通过",
    "HARD_RULE_INCOMPLETE": "硬性检测未完成",
    "SPECIFICITY_NOT_EVALUATED": "特异性检测未完成",
    "SPECIFICITY_INCOMPLETE": "特异性检测未完成",
    "SPECIFICITY_FAILED": "特异性检测未通过",
    "REJECTED_HARD_RULE": "硬性检测未通过",
    "REJECTED_SPECIFICITY": "特异性检测未通过",
    "NOT_EVALUATED": "未评估",
    "NOT_APPLICABLE": "不适用",
    "INCOMPLETE": "未完成",
    "COMPLETE": "完整",
    "RUNNING": "执行中",
    "PENDING": "等待执行",
    "TOOL_ERROR": "工具执行失败",
    "FINAL": "已完成评分",
    "AWAITING_SPECIFICITY": "等待特异性检测",
    "HARD_RULES_NOT_EVALUATED": "硬性检测未完成",
    "PARTIAL_INCOMPLETE": "部分检测未完成",
    "IN_SILICO_ONLY": "仅完成计算机模拟",
    "BLAST_VERIFIED": "已完成数据库核查",
}

_PROCESS_STAGE_LABELS = {
    "M1": "输入预检",
    "M2": "候选生成与硬性检测",
    "M3": "去重与综合评分",
    "M4": "特异性检测与最终排序",
    "M5": "结果汇总与交付判定",
    "M6": "报告生成",
    "M7": "版本化重新设计",
}

_PROCESS_TOOL_LABELS = {
    "WF01_PREFLIGHT": "输入预检程序",
    "MAFFT": "MAFFT 序列比对",
    "Primer3": "Primer3 引物与探针设计",
    "Primer3_THERMODYNAMICS": "Primer3 热力学计算",
    "WF01_HARD_RULE_ENGINE": "候选硬性检测",
    "WF01_DEDUPLICATION": "候选去重",
    "WF01_SCORING": "候选综合评分",
    "BLAST+": "BLAST+ 特异性搜索",
    "BLASTN": "BLAST+ 特异性搜索",
    "WF01_SPECIFICITY_ENGINE": "特异性判定",
    "WF01_DECISION_ENGINE": "交付结果判定",
    "WF01_REPORT_ASSEMBLER": "报告生成程序",
    "WF01_VERSIONING_ENGINE": "版本管理程序",
}

_PROCESS_MODE_LABELS = {
    "TARGET_HIT": "靶标覆盖检查",
    "HOST_EXCLUSION": "宿主非特异性检查",
    "RELATED_BACKGROUND_NT": "近缘种与背景序列检查",
}

_PROCESS_PARAMETER_LABELS = {
    "product_min_bp": "扩增子最短长度",
    "product_max_bp": "扩增子最长长度",
    "primer_min_size": "引物最短长度",
    "primer_opt_size": "引物推荐长度",
    "primer_max_size": "引物最长长度",
    "primer_min_tm": "引物最低熔解温度",
    "primer_opt_tm": "引物推荐熔解温度",
    "primer_max_tm": "引物最高熔解温度",
    "primer_min_gc": "引物最低 GC 含量",
    "primer_max_gc": "引物最高 GC 含量",
    "probe_min_size": "探针最短长度",
    "probe_opt_size": "探针推荐长度",
    "probe_max_size": "探针最长长度",
    "probe_tm_delta_min": "探针与引物最小温差",
    "probe_tm_delta_max": "探针与引物最大温差",
}

_PROCESS_CHECK_LABELS = {
    "R01": "引物熔解温度及两条引物温差",
    "R02": "扩增子长度",
    "R03": "引物 3′ 端碱基组成与连续 G 风险",
    "R04": "引物长度",
    "R05": "探针 5′ 端碱基",
    "R06": "探针位置及与引物的间距",
    "R07": "探针与引物的熔解温度关系",
    "R08": "探针长度",
    "R09": "引物自身 3′ 端二聚体风险",
    "R10": "正反向引物 3′ 端交叉二聚体风险",
    "R11": "引物及探针发卡结构风险",
    "R12": "非 3′ 端弱二聚体风险",
    "R13": "MGB 探针适用性",
    "R14": "多重反应兼容性",
    "R15": "内标设计",
    "R16": "靶序列保守性",
    "R17": "多重扩增产物长度差异",
    "R18": "热力学计算元数据完整性",
    "R19": "靶标命中、背景序列及可选宿主排除特异性",
    "R20": "科学证据等级",
    "R21": "报告字段完整性",
}


def _process_status(value: Any) -> str:
    if value is True:
        return "是"
    if value is False:
        return "否"
    text = str(value or "").strip()
    mapped = _PROCESS_STATUS_LABELS.get(text)
    if mapped is not None:
        return mapped
    if re.fullmatch(r"[A-Z][A-Z0-9_]+", text):
        return "状态已记录"
    return text or "未记录"


def _process_reason(value: Any) -> str:
    text = str(value or "").strip()
    reasons = {
        "NO_RELATED_OR_BACKGROUND_OBJECTS": "未提供近缘种或背景物种，因此本项不适用",
        "HOST_NOT_PROVIDED_OPTIONAL_CHECK_SKIPPED": "未提供宿主，宿主专库排除为可选项且本次不适用",
        "NO_DELIVERABLE_CANDIDATES": "没有候选同时满足当前交付要求",
        "COMPLETE_SEARCH_WITH_ONE_TO_NINE_CANDIDATES": "完整搜索后获得的合格候选少于十套",
        "AT_LEAST_ONE_TARGET_NOT_FULLY_SUCCEEDED": "至少一个靶标存在未完成或未通过项目",
        "M2_TOOL_ERROR": "候选生成工具执行失败",
        "M3_INPUT_INCOMPLETE": "评分所需数据不完整",
        "AT_LEAST_ONE_REQUIRED_MODE_NOT_EVALUATED": "至少一类必需的特异性检查尚未完成",
        "SPECIFICITY_OR_PRIMER3_INCOMPLETE": "特异性检查或候选生成过程尚未完成",
        "TARGET_DATABASE_OR_VERSION_NOT_RESOLVED": "靶标数据库或数据库版本未能确定",
        "NT_DATABASE_OR_VERSION_NOT_RESOLVED": "背景 nt 数据库未配置，且未启用远程 NCBI BLAST 服务",
        "NT_SEARCH_TOOL_ERROR": "背景特异性搜索服务执行失败",
        "TARGET_SEARCH_TOOL_ERROR": "靶标命中搜索工具执行失败",
        "HOST_DATABASE_OR_VERSION_NOT_RESOLVED": "宿主数据库或数据库版本未能确定",
        "HOST_SEARCH_TOOL_ERROR": "宿主排除搜索工具执行失败",
    }
    if text in reasons:
        return reasons[text]
    if re.fullmatch(r"[A-Z][A-Z0-9_]+", text):
        return "具体原因已记录在内部审计数据中"
    return text


def _process_message(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "未提供补充说明"
    mapped = _process_reason(text)
    if mapped != text:
        return mapped
    replacements = {
        "M2": "候选生成阶段",
        "M3": "评分排序阶段",
        "M4": "特异性检测阶段",
        "M5": "交付判定阶段",
        "NOT_EVALUATED": "未评估",
        "PASS": "通过",
        "FAIL": "未通过",
    }
    for source, target in replacements.items():
        text = re.sub(rf"\b{source}\b", target, text)
    return text


def _process_threshold(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\bR\d{2}(?:_SOFT)?\b", "", text)
    return re.sub(r"\s{2,}", " ", text).strip(" ：:;；") or "按当前设计规范判定"


def _process_target_name(target: dict[str, Any]) -> str:
    template = (target.get("input") or {}).get("template") or {}
    return str(
        template.get("record_name")
        or template.get("original_record_name")
        or f"靶标 {target.get('ordinal') or ''}"
    )


def _process_balanced_name(value: Any) -> str:
    text = str(value or "").strip()
    if text.count("（") > text.count("）"):
        text += "）" * (text.count("（") - text.count("）"))
    return text


def _process_region_name(value: Any) -> str:
    text = str(value or "").strip()
    replacements = {
        "5UTR": "5′UTR",
        "3UTR": "3′UTR",
        "5'UTR": "5′UTR",
        "3'UTR": "3′UTR",
    }
    return replacements.get(text.upper().replace(" ", ""), text)


def _process_target_display(target: dict[str, Any]) -> str:
    raw_name = _process_target_name(target)
    parts = [part.strip() for part in raw_name.split("|") if part.strip()]
    if len(parts) >= 3:
        accession = parts[0]
        organism = _process_region_name(parts[1])
        region = _process_region_name(parts[2])
        coordinate = parts[3].replace("-", "–") if len(parts) >= 4 else ""
        suffix = f"（{coordinate} nt）" if coordinate else ""
        return f"{organism} · {region} · {accession}{suffix}"
    return " · ".join(_process_region_name(part) for part in parts) or raw_name


def _process_report_title(report: dict[str, Any]) -> str:
    targets = report.get("targets") or []
    if len(targets) != 1:
        task_name = str((report.get("batch_overview") or {}).get("task_name") or "")
        return task_name if task_name and task_name != "wf01_batch" else "多靶标引物探针设计"
    target = targets[0]
    target_input = target.get("input") or {}
    target_object = target_input.get("target_object") or {}
    organism = _process_balanced_name(
        (target_object.get("original_input") or {}).get("name")
        or target_object.get("normalized_name")
    )
    raw_name = _process_target_name(target)
    parts = [part.strip() for part in raw_name.split("|") if part.strip()]
    region = _process_region_name(parts[2]) if len(parts) >= 3 else ""
    if organism and region and region.lower() not in organism.lower():
        return f"{organism} {region} 引物探针设计"
    return f"{organism or _process_target_display(target)} 引物探针设计"


def _process_candidate_labels(target: dict[str, Any]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for index, candidate in enumerate(
        target.get("all_primer3_candidates") or [], start=1
    ):
        candidate_id = str(candidate.get("candidate_id") or _candidate_key(candidate))
        rank = candidate.get("display_rank") or candidate.get("rank")
        serial = f"候选 {index:03d}"
        labels[candidate_id] = (
            f"{serial}（最终第 {rank} 名）" if rank is not None else serial
        )
    return labels


def _process_candidate_label(
    candidate: dict[str, Any], labels: dict[str, str]
) -> str:
    candidate_id = str(candidate.get("candidate_id") or _candidate_key(candidate))
    return labels.get(candidate_id, "候选（编号未记录）")


def _process_profile(value: Any) -> str:
    values = value if isinstance(value, list) else [value]
    mapped = {
        "default": "标准参数",
        "L1": "放宽参数一级",
        "L2": "放宽参数二级",
    }
    labels = [mapped.get(str(item), "自定义参数") for item in values if item]
    return "、".join(labels) or "标准参数"


def _process_parameters(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return "未记录"
    parts = []
    for key, item in value.items():
        label = _PROCESS_PARAMETER_LABELS.get(str(key))
        if label is None:
            continue
        unit = " ℃" if "tm" in str(key) else " bp" if "product" in str(key) else " nt" if "size" in str(key) else "%" if "gc" in str(key) else ""
        parts.append(f"{label}：{_format_number(item)}{unit}")
    return "；".join(parts) or "使用系统规定参数"


def _process_rule_actual(candidate: dict[str, Any], rule: dict[str, Any]) -> str:
    code = str(rule.get("code") or "")
    forward = candidate.get("forward_primer") or {}
    reverse = candidate.get("reverse_primer") or {}
    probe = candidate.get("probe") or {}
    amplicon = candidate.get("amplicon") or {}
    summaries = {
        "R01": (
            f"正向引物 {_format_number(forward.get('tm_celsius'))} ℃；"
            f"反向引物 {_format_number(reverse.get('tm_celsius'))} ℃；"
            f"温差 {_format_number(abs(float(forward.get('tm_celsius') or 0) - float(reverse.get('tm_celsius') or 0)))} ℃"
        ),
        "R02": f"扩增子长度 {_format_number(amplicon.get('length_bp'))} bp",
        "R03": (
            f"正向引物 3′ 端 {str(forward.get('sequence_5to3') or '')[-5:]}；"
            f"反向引物 3′ 端 {str(reverse.get('sequence_5to3') or '')[-5:]}"
        ),
        "R04": (
            f"正向引物 {_format_number(forward.get('length_nt'))} nt；"
            f"反向引物 {_format_number(reverse.get('length_nt'))} nt"
        ),
        "R05": f"探针 5′ 端碱基 {str(probe.get('sequence_5to3') or '')[:1] or '未记录'}",
        "R06": (
            f"扩增子位置 {_format_number(amplicon.get('start'))}–{_format_number(amplicon.get('end'))}；"
            "探针位于两条引物之间"
        ),
        "R07": (
            f"探针 {_format_number(probe.get('tm_celsius'))} ℃；"
            f"引物平均 {_format_number(((float(forward.get('tm_celsius') or 0) + float(reverse.get('tm_celsius') or 0)) / 2))} ℃"
        ),
        "R08": f"探针长度 {_format_number(probe.get('length_nt'))} nt",
        "R09": (
            f"正向引物 3′ 二聚体 ΔG {_format_number(forward.get('three_prime_dimer_dg_kcal'))} kcal/mol；"
            f"反向引物 {_format_number(reverse.get('three_prime_dimer_dg_kcal'))} kcal/mol"
        ),
        "R10": f"正反向引物 3′ 交叉二聚体 ΔG {_format_number(candidate.get('three_prime_heterodimer_dg_kcal'))} kcal/mol",
        "R11": (
            f"正向引物发卡 ΔG {_format_number(forward.get('hairpin_dg_kcal'))}；"
            f"反向引物 {_format_number(reverse.get('hairpin_dg_kcal'))}；"
            f"探针 {_format_number(probe.get('hairpin_dg_kcal'))} kcal/mol"
        ),
        "R12": f"正反向引物二聚体 ΔG {_format_number(candidate.get('pair_heterodimer_dg_kcal'))} kcal/mol",
        "R18": "热力学软件、算法与浓度元数据已检查",
        "R19": f"综合特异性结论：{_process_status((candidate.get('specificity') or {}).get('overall_status'))}",
        "R20": "检测证据来源已记录",
        "R21": (
            "引物、探针和扩增产物序列、Tm、关键 ΔG、扩增子长度及 "
            "BLAST 摘要已检查；保守性评分本版本暂缓"
        ),
    }
    actual = rule.get("actual")
    if code in summaries:
        return summaries[code]
    if isinstance(actual, (str, int, float, bool)):
        return _format_number(actual)
    return str(rule.get("message") or "详见检测说明")


def _process_specificity_modes(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    specificity = candidate.get("specificity") or {}
    modes: list[dict[str, Any]] = []
    target = specificity.get("target_hit")
    if isinstance(target, dict):
        modes.append(target)
    host = specificity.get("host_exclusion") or {}
    object_results = host.get("object_results") or []
    if object_results:
        modes.extend(item for item in object_results if isinstance(item, dict))
    elif host:
        modes.append(host)
    related = specificity.get("related_background_nt")
    if isinstance(related, dict):
        modes.append(related)
    return modes


def _candidate_dg_value(value: Any) -> str:
    if not _r21_number_present(value):
        return "未记录"
    return f"{_format_number(value, 2)} kcal/mol"


def _process_failed_rule_codes(candidate: dict[str, Any]) -> set[str]:
    return {
        str(rule.get("code") or "").strip().upper()
        for rule in candidate.get("rules") or []
        if isinstance(rule, dict)
        and str(rule.get("status") or "").strip().upper() == "FAIL"
    }


def _process_failed_data_html(value: Any, *, failed: bool = False) -> str:
    escaped = html.escape(str(value))
    return f'<span class="failed-data">{escaped}</span>' if failed else escaped


def _candidate_thermodynamics_panel_html(
    candidate: dict[str, Any],
    failed_rule_codes: set[str] | None = None,
) -> str:
    failed_rule_codes = failed_rule_codes or set()
    rows: list[str] = []
    for label, key in (
        ("正向引物", "forward_primer"),
        ("反向引物", "reverse_primer"),
        ("TaqMan 探针", "probe"),
    ):
        oligo = candidate.get(key) or {}
        rows.append(
            "<tr>"
            f"<th scope=\"row\">{html.escape(label)}</th>"
            f"<td>{_process_failed_data_html(_candidate_dg_value(oligo.get('hairpin_dg_kcal')), failed='R11' in failed_rule_codes)}</td>"
            f"<td>{_process_failed_data_html(_candidate_dg_value(oligo.get('homodimer_dg_kcal')), failed='R12' in failed_rule_codes)}</td>"
            f"<td>{_process_failed_data_html(_candidate_dg_value(oligo.get('three_prime_dimer_dg_kcal')), failed='R09' in failed_rule_codes and key != 'probe')}</td>"
            "</tr>"
        )
    pair_value = _candidate_dg_value(
        candidate.get("pair_heterodimer_dg_kcal")
    )
    three_prime_value = _candidate_dg_value(
        candidate.get("three_prime_heterodimer_dg_kcal")
    )
    return f"""
      <section class="evidence-panel thermo-evidence">
        <div class="evidence-heading"><div><span>热力学关键数据</span><h4>关键 ΔG</h4></div><small>单位：kcal/mol；数值越负，相关结构或结合通常越稳定。</small></div>
        <div class="table-wrap compact"><table class="evidence-table"><thead><tr><th>组成</th><th>发卡 ΔG</th><th>同源二聚体 ΔG</th><th>3′端自身二聚体 ΔG</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
        <div class="pair-dg-grid">
          <div><span>正反向引物异源二聚体 ΔG</span><strong>{_process_failed_data_html(pair_value, failed='R12' in failed_rule_codes)}</strong></div>
          <div><span>正反向引物 3′端异源二聚体 ΔG</span><strong>{_process_failed_data_html(three_prime_value, failed='R10' in failed_rule_codes)}</strong></div>
        </div>
      </section>
    """


def _specificity_object_name(mode: dict[str, Any]) -> str:
    names: list[str] = []
    for item in mode.get("objects") or []:
        if not isinstance(item, dict):
            continue
        original = item.get("original_input") or {}
        name = (
            original.get("name")
            if isinstance(original, dict)
            else None
        )
        name = name or item.get("scientific_name") or item.get("identifier")
        if name and str(name) not in names:
            names.append(str(name))
    return "、".join(names)


def _specificity_hit_evidence(mode: dict[str, Any]) -> str:
    hits = [item for item in mode.get("hits") or [] if isinstance(item, dict)]
    if not hits:
        status = str(mode.get("status") or "").upper()
        reason = _process_reason(mode.get("reason"))
        if status == "NOT_APPLICABLE":
            return reason or "本项不适用"
        if str(mode.get("mode") or "") in {
            "HOST_EXCLUSION",
            "RELATED_BACKGROUND_NT",
        } and status == "PASS":
            return "未记录达到判定阈值的非特异性命中"
        if status == "NOT_EVALUATED":
            return reason or "本项尚未完成"
        return reason or "未保存代表性命中详情"

    summaries: list[str] = []
    for hit in hits[:2]:
        subject = (
            hit.get("organism")
            or hit.get("title")
            or hit.get("subject_title")
            or hit.get("subject_id")
            or "未命名序列"
        )
        identity = hit.get("identity_percent")
        coverage = hit.get("query_coverage_pct")
        if coverage is None:
            coverage = hit.get("coverage_percent")
        evalue = hit.get("evalue")
        facts = []
        if _r21_number_present(identity):
            facts.append(f"相似度 {_format_number(identity, 2)}%")
        if _r21_number_present(coverage):
            facts.append(f"覆盖率 {_format_number(coverage, 2)}%")
        if evalue is not None:
            try:
                evalue_text = f"{float(evalue):.3g}"
            except (TypeError, ValueError):
                evalue_text = str(evalue)
            facts.append(f"E-value {evalue_text}")
        summaries.append(
            f"{subject}（{'，'.join(facts)}）" if facts else str(subject)
        )
    suffix = f"；另有 {len(hits) - 2} 条命中" if len(hits) > 2 else ""
    return "；".join(summaries) + suffix


def _candidate_specificity_panel_html(
    candidate: dict[str, Any],
    *,
    highlight_failures: bool = False,
) -> str:
    rows: list[str] = []
    for mode in _process_specificity_modes(candidate):
        mode_code = str(mode.get("mode") or "")
        label = _PROCESS_MODE_LABELS.get(mode_code, "特异性检查")
        object_name = _specificity_object_name(mode)
        if object_name:
            label = f"{label}（{object_name}）"
        status = _process_status(mode.get("status"))
        is_failed = (
            highlight_failures
            and str(mode.get("status") or "").strip().upper() == "FAIL"
        )
        hit_count = len(mode.get("hits") or [])
        rows.append(
            "<tr>"
            f"<th scope=\"row\">{html.escape(label)}</th>"
            f"<td><span class=\"status {_process_status_class(status)}\">{html.escape(status)}</span></td>"
            f"<td>{_process_failed_data_html(hit_count, failed=is_failed)}</td>"
            f"<td>{_process_failed_data_html(_specificity_hit_evidence(mode), failed=is_failed)}</td>"
            "</tr>"
        )
    if not rows:
        rows.append(
            '<tr><td colspan="4" class="empty">本候选未保存特异性检测摘要</td></tr>'
        )
    return f"""
      <section class="evidence-panel specificity-evidence">
        <div class="evidence-heading"><div><span>BLAST 特异性检测</span><h4>适用范围命中摘要</h4></div><small>核查靶标覆盖和近缘种/背景序列；提供宿主时追加宿主专库排除。</small></div>
        <div class="table-wrap compact"><table class="evidence-table"><thead><tr><th>检测范围</th><th>结论</th><th>命中数</th><th>代表性证据</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
      </section>
    """


def _process_penalties(candidate: dict[str, Any]) -> str:
    scoring = candidate.get("scoring_detail") or {}
    penalties = scoring.get("penalties") or []
    descriptions = []
    fallback = {
        "R01_SOFT": "引物熔解温度偏低",
        "R03_SOFT": "引物 3′ 端序列构成需要关注",
        "R11": "发卡结构风险需要关注",
        "R12": "弱二聚体风险需要关注",
    }
    for item in penalties:
        if not isinstance(item, dict):
            continue
        description = (
            item.get("message")
            or item.get("description")
            or fallback.get(str(item.get("code") or ""))
            or "软性检测扣分"
        )
        percent = item.get("penalty_percent")
        if percent is None:
            percent = item.get("percent")
        descriptions.append(
            f"{description}（-{_format_number(percent)}%）"
            if percent is not None
            else str(description)
        )
    return "；".join(descriptions) or "无软性扣分"


def _process_tables(
    report: dict[str, Any],
    artifact_manifest: dict[str, Any] | None = None,
) -> dict[str, tuple[list[str], list[list[Any]]]]:
    artifact_manifest = artifact_manifest or {}
    target_names = {
        str(target.get("target_id") or ""): _process_target_display(target)
        for target in report.get("targets") or []
    }
    tool_rows: list[list[Any]] = []
    for entry in artifact_manifest.get("artifacts") or []:
        tools = [
            _PROCESS_TOOL_LABELS.get(str(tool), "科学处理工具")
            for tool in entry.get("tools") or []
        ]
        tool_rows.append(
            [
                _PROCESS_STAGE_LABELS.get(str(entry.get("stage")), str(entry.get("stage") or "")),
                "、".join(tools),
                target_names.get(str(entry.get("targetId") or ""), "整个任务"),
                "数据已保存",
                entry.get("createdAt"),
                entry.get("sizeBytes"),
                entry.get("sha256"),
            ]
        )
    if not tool_rows:
        for stage in report.get("generator", {}).get("source_stages") or []:
            tool_rows.append(
                [
                    _PROCESS_STAGE_LABELS.get(str(stage), str(stage)),
                    "对应科学处理程序",
                    "整个任务",
                    "过程结果已写入报告",
                    report.get("generated_at"),
                    "",
                    "",
                ]
            )

    input_rows: list[list[Any]] = []
    search_rows: list[list[Any]] = []
    candidate_rows: list[list[Any]] = []
    rule_rows: list[list[Any]] = []
    score_rows: list[list[Any]] = []
    specificity_rows: list[list[Any]] = []
    hit_rows: list[list[Any]] = []
    attention_rows: list[list[Any]] = []

    for target in report.get("targets") or []:
        target_name = _process_target_display(target)
        target_input = target.get("input") or {}
        template = target_input.get("template") or {}
        target_object = target_input.get("target_object") or {}
        hosts = target_input.get("host_objects") or []
        target_label = _process_balanced_name(
            (target_object.get("original_input") or {}).get("name")
            or target_object.get("normalized_name")
            or "未记录"
        )
        host_labels = [
            (item.get("original_input") or {}).get("name")
            or item.get("scientific_name")
            for item in hosts
            if isinstance(item, dict)
        ]
        input_rows.append(
            [
                target_name,
                template.get("source_file"),
                template.get("length_nt"),
                target_label,
                "、".join(str(item) for item in host_labels if item) or "未记录",
                _process_parameters(target_input.get("parameters")),
                template.get("checksum_sha256"),
            ]
        )

        search_records = target.get("search_records") or {}
        parameter_runs = search_records.get("parameter_runs") or []
        units = search_records.get("exploration_units") or []
        for unit_index, unit in enumerate(units, start=1):
            counts = unit.get("counts") or {}
            region = unit.get("region") or {}
            search_rows.append(
                [
                    target_name,
                    unit_index,
                    _process_profile(
                        unit.get("parameter_profile_labels")
                        or unit.get("parameter_run_id")
                    ),
                    f"{_format_number(region.get('start'))}–{_format_number(region.get('end'))}",
                    region.get("length_nt"),
                    _process_parameters(unit.get("effective_parameters")),
                    _process_status(unit.get("status")),
                    counts.get("generated"),
                    counts.get("hard_rule_pass"),
                    counts.get("hard_rule_rejected"),
                    counts.get("hard_rule_not_evaluated"),
                    unit.get("started_at"),
                    unit.get("completed_at"),
                ]
            )
        if not units:
            for run_index, run in enumerate(parameter_runs, start=1):
                search_rows.append(
                    [
                        target_name,
                        run_index,
                        _process_profile(run.get("profile_labels") or run.get("profile")),
                        "完整模板",
                        template.get("length_nt"),
                        _process_parameters(run.get("parameters")),
                        "搜索记录已保存",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                    ]
                )

        labels = _process_candidate_labels(target)
        for candidate in target.get("all_primer3_candidates") or []:
            candidate_label = _process_candidate_label(candidate, labels)
            forward = candidate.get("forward_primer") or {}
            reverse = candidate.get("reverse_primer") or {}
            probe = candidate.get("probe") or {}
            amplicon = candidate.get("amplicon") or {}
            metadata = candidate.get("metadata") or {}
            candidate_rows.append(
                [
                    target_name,
                    candidate_label,
                    _process_status(candidate.get("verdict")),
                    candidate.get("display_rank") or candidate.get("rank"),
                    forward.get("sequence_5to3"),
                    forward.get("length_nt"),
                    forward.get("tm_celsius"),
                    forward.get("gc_percent"),
                    reverse.get("sequence_5to3"),
                    reverse.get("length_nt"),
                    reverse.get("tm_celsius"),
                    reverse.get("gc_percent"),
                    probe.get("sequence_5to3"),
                    probe.get("length_nt"),
                    probe.get("tm_celsius"),
                    probe.get("gc_percent"),
                    amplicon.get("sequence_5to3"),
                    amplicon.get("length_bp"),
                    _process_profile(metadata.get("parameter_profile_labels") or metadata.get("parameter_run_id")),
                    candidate.get("primer3_pair_penalty"),
                ]
            )
            for rule in candidate.get("rules") or []:
                code = str(rule.get("code") or "")
                rule_name = _PROCESS_CHECK_LABELS.get(code)
                if rule_name is None:
                    continue
                status = _process_status(rule.get("status"))
                rule_rows.append(
                    [
                        target_name,
                        candidate_label,
                        rule_name,
                        status,
                        _process_rule_actual(candidate, rule),
                        _process_threshold(rule.get("threshold")),
                        _process_message(rule.get("message")),
                    ]
                )
                if rule.get("status") in {
                    "WARNING",
                    "CAUTION",
                    "FAIL",
                    "NOT_EVALUATED",
                    "INCOMPLETE",
                }:
                    attention_rows.append(
                        [target_name, candidate_label, rule_name, status, _process_message(rule.get("message"))]
                    )

            scoring = candidate.get("scoring_detail") or {}
            dimensions = scoring.get("dimensions") or {}
            score_rows.append(
                [
                    target_name,
                    candidate_label,
                    (dimensions.get("thermodynamics") or {}).get("raw_score"),
                    (dimensions.get("specificity") or {}).get("raw_score"),
                    (dimensions.get("amplicon_features") or {}).get("raw_score"),
                    scoring.get("base_score"),
                    scoring.get("applied_penalty_percent", scoring.get("total_penalty_percent")),
                    scoring.get("final_score", candidate.get("score")),
                    candidate.get("display_rank") or candidate.get("rank"),
                    _process_penalties(candidate),
                ]
            )

            for mode in _process_specificity_modes(candidate):
                mode_name = _PROCESS_MODE_LABELS.get(str(mode.get("mode")), "特异性检查")
                database = mode.get("database") or {}
                specificity_rows.append(
                    [
                        target_name,
                        candidate_label,
                        mode_name,
                        _process_status(mode.get("status")),
                        "BLAST+",
                        database.get("version") or "未记录",
                        "核酸序列比对" if mode.get("method") not in (None, "", "not_run") else "未执行",
                        len(mode.get("hits") or []),
                        _process_reason(mode.get("reason")),
                    ]
                )

        for evidence in (target.get("specificity_evidence") or {}).get("hit_evidence") or []:
            hit = evidence.get("hit") or {}
            candidate_id = str(evidence.get("candidate_id") or "")
            hit_rows.append(
                [
                    target_name,
                    labels.get(candidate_id, "候选（编号未记录）"),
                    _PROCESS_MODE_LABELS.get(str(evidence.get("mode")), "特异性检查"),
                    evidence.get("database_version") or "未记录",
                    hit.get("query_id"),
                    hit.get("subject_id"),
                    hit.get("identity_percent"),
                    hit.get("coverage_percent"),
                    hit.get("evalue"),
                    hit.get("query_start"),
                    hit.get("query_end"),
                    hit.get("subject_start"),
                    hit.get("subject_end"),
                    hit.get("three_prime_mismatches"),
                    hit.get("title") or hit.get("subject_title") or hit.get("organism"),
                ]
            )

        conclusion = target.get("conclusion") or {}
        for item in conclusion.get("incomplete_reasons") or []:
            if isinstance(item, dict):
                message = _process_message(
                    item.get("message") or item.get("reason") or "存在未完成检测"
                )
            else:
                message = _process_message(item)
            attention_rows.append([target_name, "整个靶标", "未完成项目", "未完成", message])

    return {
        "tool_runs": (
            ["处理阶段", "使用工具", "适用范围", "数据状态", "保存时间", "数据大小（字节）", "数据校验值"],
            tool_rows,
        ),
        "inputs": (
            ["靶标", "输入文件", "序列长度（nt）", "目标物种", "宿主", "设计参数", "序列校验值"],
            input_rows,
        ),
        "searches": (
            ["靶标", "搜索轮次", "参数档位", "设计区域", "区域长度（nt）", "实际参数", "执行结果", "生成候选", "硬性检测通过", "硬性检测未通过", "未完成检测", "开始时间", "结束时间"],
            search_rows,
        ),
        "candidates": (
            ["靶标", "候选编号", "筛选结论", "最终排名", "正向引物序列", "正向长度", "正向引物熔解温度 Tm（℃）", "正向引物 GC 含量（%）", "反向引物序列", "反向长度", "反向引物熔解温度 Tm（℃）", "反向引物 GC 含量（%）", "探针序列", "探针长度", "探针熔解温度 Tm（℃）", "探针 GC 含量（%）", "扩增子序列", "扩增子长度（bp）", "参数档位", "Primer3 配对罚分（越低越优）"],
            candidate_rows,
        ),
        "rules": (
            ["靶标", "候选编号", "检测项目", "检测结论", "实际数据", "判定要求", "说明"],
            rule_rows,
        ),
        "scores": (
            ["靶标", "候选编号", "热力学得分", "特异性得分", "扩增子特征得分", "加权基础分", "软性扣分（%）", "最终得分", "最终排名", "扣分说明"],
            score_rows,
        ),
        "specificity": (
            ["靶标", "候选编号", "检测对象", "检测结论", "使用工具", "数据库版本", "检测方法", "命中数量", "补充说明"],
            specificity_rows,
        ),
        "hits": (
            ["靶标", "候选编号", "检测对象", "数据库版本", "查询序列名称", "命中序列名称", "相似度（%）", "覆盖率（%）", "E-value", "查询起点", "查询终点", "命中起点", "命中终点", "3′ 端错配数", "命中说明"],
            hit_rows,
        ),
        "attention": (
            ["靶标", "候选编号", "项目", "结论", "原因或建议"],
            attention_rows,
        ),
    }


def _process_candidate_status(candidate: dict[str, Any]) -> str:
    status = _process_status(candidate.get("verdict"))
    if status != "状态已记录":
        return status
    membership = candidate.get("report_membership") or {}
    if membership.get("deliverable"):
        return "可交付"
    if membership.get("qualified"):
        return "合格"
    if membership.get("excluded"):
        return "未进入交付集合"
    return "已完成筛选"


def _process_sequence_html(value: Any, *, failed: bool = False) -> str:
    text = str(value or "").strip()
    css_class = "sequence failed-data" if failed else "sequence"
    return (
        f'<span class="{css_class}">{html.escape(text)}</span>'
        if text
        else '<span class="muted">未记录</span>'
    )


def _process_candidate_result_note(candidate: dict[str, Any]) -> str:
    failed: list[str] = []
    warnings: list[str] = []
    pending: list[str] = []
    for rule in candidate.get("rules") or []:
        label = _PROCESS_CHECK_LABELS.get(str(rule.get("code") or ""))
        if label is None:
            continue
        status = str(rule.get("status") or "")
        collection = (
            failed
            if status == "FAIL"
            else warnings
            if status in {"WARNING", "CAUTION"}
            else pending
            if status in {"NOT_EVALUATED", "INCOMPLETE"}
            else None
        )
        if collection is not None and label not in collection:
            collection.append(label)
    status = _process_candidate_status(candidate)
    if failed:
        return f"未通过：{'、'.join(failed[:3])}"
    if warnings:
        return f"需关注：{'、'.join(warnings[:3])}"
    if any(word in status for word in ("通过", "合格", "可交付", "入选", "推荐")):
        return "已通过全部必需检测"
    if pending:
        return f"尚未完成：{'、'.join(pending[:3])}"
    return "筛选结论已记录"


def _process_candidate_results_html(report: dict[str, Any]) -> str:
    target_blocks: list[str] = []
    for target in report.get("targets") or []:
        candidates = target.get("all_primer3_candidates") or []
        labels = _process_candidate_labels(target)
        hard_pass = sum(
            "HARD_RULE_PASS" in ((candidate.get("report_membership") or {}).get("collections") or [])
            for candidate in candidates
        )
        hard_failed = sum(
            bool(
                {
                    "HARD_RULE_REJECTED",
                    "M2_HARD_RULE_REJECTED",
                }
                & set((candidate.get("report_membership") or {}).get("collections") or [])
            )
            for candidate in candidates
        )
        deliverable = sum(
            bool((candidate.get("report_membership") or {}).get("deliverable"))
            for candidate in candidates
        )
        summary_cards = (
            '<div class="result-overview">'
            f'<div><span>全部生成候选</span><strong>{len(candidates)}</strong></div>'
            f'<div><span>进入特异性检测</span><strong>{hard_pass}</strong></div>'
            f'<div><span>硬性检测未通过</span><strong>{hard_failed}</strong></div>'
            f'<div><span>最终可交付</span><strong>{deliverable}</strong></div>'
            "</div>"
        )
        rows: list[list[Any]] = []
        for candidate in candidates:
            amplicon = candidate.get("amplicon") or {}
            metadata = candidate.get("metadata") or {}
            scoring = candidate.get("scoring_detail") or {}
            rows.append(
                [
                    _process_candidate_label(candidate, labels),
                    _process_candidate_status(candidate),
                    scoring.get("final_score", candidate.get("score")),
                    candidate.get("display_rank") or candidate.get("rank"),
                    amplicon.get("length_bp"),
                    _process_profile(
                        metadata.get("parameter_profile_labels")
                        or metadata.get("parameter_run_id")
                    ),
                    _process_candidate_result_note(candidate),
                ]
            )
        target_blocks.append(
            f'<div class="target-group"><h3>{html.escape(_process_target_display(target))}</h3>'
            f"{summary_cards}"
            f"{_html_table(['候选编号', '筛选结论', '最终得分', '最终排名', '扩增子长度（bp）', '参数档位', '主要结果'], rows)}</div>"
        )
    return "".join(target_blocks) or '<p class="empty">无候选筛选结果</p>'


def _process_candidate_records_html(report: dict[str, Any]) -> str:
    target_blocks: list[str] = []
    first_record = True
    for target in report.get("targets") or []:
        target_name = _process_target_display(target)
        labels = _process_candidate_labels(target)
        records: list[str] = []
        for candidate in target.get("all_primer3_candidates") or []:
            failed_rule_codes = _process_failed_rule_codes(candidate)
            label = _process_candidate_label(candidate, labels)
            status = _process_candidate_status(candidate)
            forward = candidate.get("forward_primer") or {}
            reverse = candidate.get("reverse_primer") or {}
            probe = candidate.get("probe") or {}
            amplicon = candidate.get("amplicon") or {}
            metadata = candidate.get("metadata") or {}
            rank = candidate.get("display_rank") or candidate.get("rank")
            penalty = candidate.get("primer3_pair_penalty")
            summary_metrics = [
                f"扩增子 {_format_number(amplicon.get('length_bp'))} bp",
                f"参数：{_process_profile(metadata.get('parameter_profile_labels') or metadata.get('parameter_run_id'))}",
                (
                    f"Primer3 配对罚分 {_format_number(penalty)}（越低越优）"
                    if penalty is not None
                    else "Primer3 配对罚分未记录"
                ),
            ]
            oligo_rows = []
            for name, key, item in (
                ("正向引物", "forward_primer", forward),
                ("反向引物", "reverse_primer", reverse),
                ("TaqMan 探针", "probe", probe),
            ):
                is_probe = key == "probe"
                sequence_failed = (
                    "R05" in failed_rule_codes or "R06" in failed_rule_codes
                    if is_probe
                    else "R03" in failed_rule_codes
                )
                length_failed = (
                    "R08" in failed_rule_codes
                    if is_probe
                    else "R04" in failed_rule_codes
                )
                tm_failed = (
                    "R07" in failed_rule_codes
                    if is_probe
                    else bool({"R01", "R07"} & failed_rule_codes)
                )
                oligo_rows.append(
                    "<tr>"
                    f"<th scope=\"row\">{name}</th>"
                    f"<td>{_process_sequence_html(item.get('sequence_5to3'), failed=sequence_failed)}</td>"
                    f"<td>{_process_failed_data_html(_format_number(item.get('length_nt')) + ' nt', failed=length_failed)}</td>"
                    f"<td>{_process_failed_data_html(_format_number(item.get('tm_celsius')) + ' ℃', failed=tm_failed)}</td>"
                    f"<td>{html.escape(_format_number(item.get('gc_percent')))}%</td>"
                    "</tr>"
                )
            open_attr = " open" if first_record else ""
            first_record = False
            records.append(
                f"""
                <details class="record candidate-record"{open_attr}>
                  <summary>
                    <span class="record-title">{html.escape(label)}</span>
                    <span class="record-meta">{' · '.join(html.escape(item) for item in summary_metrics)}</span>
                    <span class="status {_process_status_class(status)}">{html.escape(status)}</span>
                  </summary>
                  <div class="record-body">
                    <div class="metric-grid">
                      <div><span>最终排名</span><strong>{html.escape(str(rank)) if rank is not None else '未进入最终排名'}</strong></div>
                      <div><span>扩增子区间</span><strong>{_process_failed_data_html(_format_number(amplicon.get('start')) + '–' + _format_number(amplicon.get('end')), failed='R06' in failed_rule_codes)}</strong></div>
                      <div><span>扩增子长度</span><strong>{_process_failed_data_html(_format_number(amplicon.get('length_bp')) + ' bp', failed='R02' in failed_rule_codes)}</strong></div>
                      <div><span>Primer3 配对罚分</span><strong>{html.escape(_format_number(penalty))}</strong><small>数值越低越优</small></div>
                    </div>
                    <div class="table-wrap compact"><table class="oligo-table"><thead><tr><th>组成</th><th>5′→3′ 序列</th><th>长度</th><th>熔解温度（Tm）</th><th>GC 含量</th></tr></thead><tbody>{''.join(oligo_rows)}</tbody></table></div>
                    {_candidate_thermodynamics_panel_html(candidate, failed_rule_codes)}
                    {_candidate_specificity_panel_html(candidate, highlight_failures=True)}
                    <div class="sequence-panel"><span>扩增子序列（5′→3′）</span>{_process_sequence_html(amplicon.get('sequence_5to3'))}</div>
                  </div>
                </details>
                """
            )
        record_content = "".join(records) or '<p class="empty">无候选记录</p>'
        target_blocks.append(
            f'<div class="target-group"><h3>{html.escape(target_name)}</h3>'
            f'<p class="target-count">共 {len(records)} 个候选；展开任一候选可查看引物、探针和扩增子详情。</p>'
            '<p class="failure-legend"><span class="failed-data">红色数据</span>表示对应的硬性检测项目未通过。</p>'
            f"{record_content}</div>"
        )
    return "".join(target_blocks) or '<p class="empty">无候选记录</p>'


def _process_rule_records_html(report: dict[str, Any]) -> str:
    target_blocks: list[str] = []
    for target in report.get("targets") or []:
        target_name = _process_target_display(target)
        labels = _process_candidate_labels(target)
        candidate_blocks: list[str] = []
        for candidate in target.get("all_primer3_candidates") or []:
            label = _process_candidate_label(candidate, labels)
            rows: list[list[Any]] = []
            counts = {"pass": 0, "fail": 0, "warning": 0, "neutral": 0}
            for rule in candidate.get("rules") or []:
                code = str(rule.get("code") or "")
                rule_name = _PROCESS_CHECK_LABELS.get(code)
                if rule_name is None:
                    continue
                status = _process_status(rule.get("status"))
                status_class = _process_status_class(status)
                counts[status_class] += 1
                rows.append(
                    [
                        rule_name,
                        status,
                        _process_rule_actual(candidate, rule),
                        _process_threshold(rule.get("threshold")),
                        _process_message(rule.get("message")),
                    ]
                )
            count_parts = []
            for key, name in (("pass", "通过"), ("fail", "未通过"), ("warning", "需关注"), ("neutral", "其他")):
                if counts[key]:
                    count_parts.append(f"{name} {counts[key]} 项")
            candidate_status = _process_candidate_status(candidate)
            candidate_blocks.append(
                f"""
                <details class="record rule-record">
                  <summary>
                    <span class="record-title">{html.escape(label)}</span>
                    <span class="record-meta">{' · '.join(count_parts) or '无检测记录'}</span>
                    <span class="status {_process_status_class(candidate_status)}">{html.escape(candidate_status)}</span>
                  </summary>
                  <div class="record-body">{_html_table(['检测项目', '检测结论', '实际数据', '判定要求', '说明'], rows)}</div>
                </details>
                """
            )
        candidate_content = "".join(candidate_blocks) or '<p class="empty">无逐项检测记录</p>'
        target_blocks.append(
            f'<div class="target-group"><h3>{html.escape(target_name)}</h3>'
            f'<p class="target-count">按候选分组保存全部逐项检测。候选标题会直接显示通过、未通过和需关注的项目数量。</p>'
            f"{candidate_content}</div>"
        )
    return "".join(target_blocks) or '<p class="empty">无逐项检测记录</p>'


def _process_input_sequences_html(report: dict[str, Any]) -> str:
    blocks: list[str] = []
    for target in report.get("targets") or []:
        target_name = _process_target_display(target)
        template = ((target.get("input") or {}).get("template") or {})
        filename = str(template.get("source_file") or "未记录文件名")
        sequence = str(template.get("sequence") or "").strip()
        length = template.get("length_nt")
        if length is None and sequence:
            length = len(sequence)
        sequence_content = (
            _process_sequence_html(sequence)
            if sequence
            else '<span class="muted">当前报告数据中没有保存可展示的原始序列，请查看原始输入文件。</span>'
        )
        blocks.append(
            f"""
            <article class="input-sequence-card">
              <div class="input-sequence-heading">
                <div><strong>原始输入序列</strong><span>{html.escape(target_name)}</span></div>
                <div class="input-file-meta"><span>文件名</span><strong>{html.escape(filename)}</strong><span>序列长度</span><strong>{html.escape(_format_number(length))} nt</strong></div>
              </div>
              <div class="sequence-panel"><span>完整序列（5′→3′）</span>{sequence_content}</div>
            </article>
            """
        )
    return "".join(blocks) or '<p class="empty">无原始输入序列</p>'


def _process_unmet_checks_for_unit(
    target: dict[str, Any], unit: dict[str, Any]
) -> str:
    unit_id = str(unit.get("exploration_unit_id") or "")
    labels: list[str] = []
    for candidate in target.get("all_primer3_candidates") or []:
        metadata = candidate.get("metadata") or {}
        if str(metadata.get("exploration_unit_id") or "") != unit_id:
            continue
        for rule in candidate.get("rules") or []:
            code = str(rule.get("code") or "")
            if code not in _PROCESS_CHECK_LABELS or code not in RULE_CODES[:12]:
                continue
            if str(rule.get("status") or "") not in {
                "FAIL",
                "WARNING",
                "CAUTION",
            }:
                continue
            label = _PROCESS_CHECK_LABELS[code]
            if label not in labels:
                labels.append(label)
    if labels:
        visible = labels[:4]
        suffix = f"等 {len(labels)} 项" if len(labels) > len(visible) else ""
        return f"上一轮未满足：{'、'.join(visible)}{suffix}"
    counts = unit.get("counts") or {}
    if int(counts.get("hard_rule_rejected") or 0) > 0:
        return "上一轮没有候选通过硬性检测"
    if int(counts.get("hard_rule_not_evaluated") or 0) > 0:
        return "上一轮存在未完成的硬性检测"
    return "上一轮没有获得通过候选"


def _process_search_rows_for_html(
    report: dict[str, Any],
) -> tuple[list[str], list[list[Any]]]:
    headers = [
        "靶标",
        "搜索轮次",
        "参数档位",
        "设计区域",
        "区域长度（nt）",
        "为何使用放宽规则",
        "执行结果",
        "生成候选",
        "硬性检测通过",
        "硬性检测未通过",
        "未完成检测",
        "开始时间",
        "结束时间",
    ]
    rows: list[list[Any]] = []
    for target in report.get("targets") or []:
        target_name = _process_target_display(target)
        template = ((target.get("input") or {}).get("template") or {})
        units = ((target.get("search_records") or {}).get("exploration_units") or [])
        previous_by_region: dict[tuple[Any, Any], dict[str, Any]] = {}
        success_by_region: dict[tuple[Any, Any], bool] = {}
        visible_round = 0
        for unit in units:
            region = unit.get("region") or {}
            region_key = (region.get("start"), region.get("end"))
            previous = previous_by_region.get(region_key)
            if success_by_region.get(region_key):
                continue
            visible_round += 1
            counts = unit.get("counts") or {}
            reason = "" if previous is None else _process_unmet_checks_for_unit(target, previous)
            rows.append(
                [
                    target_name,
                    visible_round,
                    _process_profile(
                        unit.get("parameter_profile_labels")
                        or unit.get("parameter_run_id")
                    ),
                    f"{_format_number(region.get('start'))}–{_format_number(region.get('end'))}",
                    region.get("length_nt"),
                    reason,
                    _process_status(unit.get("status")),
                    counts.get("generated"),
                    counts.get("hard_rule_pass"),
                    counts.get("hard_rule_rejected"),
                    counts.get("hard_rule_not_evaluated"),
                    unit.get("started_at"),
                    unit.get("completed_at"),
                ]
            )
            previous_by_region[region_key] = unit
            if int(counts.get("hard_rule_pass") or 0) > 0:
                success_by_region[region_key] = True
        if not units:
            parameter_runs = ((target.get("search_records") or {}).get("parameter_runs") or [])
            for run in parameter_runs[:1]:
                rows.append(
                    [
                        target_name,
                        1,
                        _process_profile(run.get("profile_labels") or run.get("profile")),
                        "完整模板",
                        template.get("length_nt"),
                        "",
                        "搜索记录已保存",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                    ]
                )
    return headers, rows


def render_process_detail_html(report: dict[str, Any]) -> bytes:
    """Render a Chinese, human-readable scientific process report."""
    overview = report["batch_overview"]
    artifact_manifest = report.get("_scientific_artifact_manifest") or {}
    tables = _process_tables(report, artifact_manifest)
    sections = [
        ("tools", "实验过程与科学工具", "按执行顺序展示每一步使用的工具、处理对象和完成时间。", "tool_runs", True),
        ("inputs", "任务输入与设计参数", "展示传入的文件、原始输入序列和设计参数。", "inputs", True),
        ("searches", "Primer3 候选生成过程", "标准参数成功时只展示标准轮次；只有未获得通过候选时，才展示放宽轮次及上一轮未满足的项目。", "searches", True),
        ("candidate-results", "候选筛选结果", "先汇总候选数量和筛选结论，再查看每个候选的得分、排名及主要未通过原因。", "candidate_results", True),
        ("candidates", "全部候选详情", "保留所有生成候选；每个候选单独展示引物、探针和扩增子。", "candidates", True),
        ("rules", "逐项检测结果", "按候选归类，直接显示各候选通过、未通过及需关注的项目数。", "rules", False),
        (
            "scores",
            "评分与排序过程",
            "展示三维评分、软性扣分和最终排名；引物 Tm 在 55–65°C 内同等，50–<55°C 统一扣 5%，范围外候选在硬规则阶段排除。",
            "scores",
            False,
        ),
        ("specificity", "特异性检测汇总", "展示靶标覆盖、背景序列检查，以及用户指定时的宿主排除。", "specificity", False),
        ("hits", "特异性命中明细", "列出数据库命中的关键数据；显著性 E-value 越小，命中证据越强。", "hits", False),
        ("attention", "未通过、需关注与未完成项目", "集中列出需要复核、补充或重新设计的数据。", "attention", True),
    ]
    section_html: list[str] = []
    navigation: list[str] = []
    for slug, title, description, key, initially_open in sections:
        if key == "candidate_results":
            headers, rows = [], []
        else:
            headers, rows = tables[key]
        display_count = len(rows)
        if key == "candidate_results":
            content = _process_candidate_results_html(report)
            display_count = sum(
                len(target.get("all_primer3_candidates") or [])
                for target in report.get("targets") or []
            )
        elif key == "candidates":
            content = _process_candidate_records_html(report)
        elif key == "rules":
            content = _process_rule_records_html(report)
        elif key == "searches":
            visible_headers, visible_rows = _process_search_rows_for_html(report)
            content = _html_table(visible_headers, visible_rows)
            display_count = len(visible_rows)
        elif key == "tool_runs":
            content = _html_table_without_columns(
                headers,
                rows,
                {"数据大小（字节）", "数据校验值"},
            )
        elif key == "inputs":
            content = (
                _html_table_without_columns(headers, rows, {"序列校验值"})
                + _process_input_sequences_html(report)
            )
        elif key in {"specificity", "hits"}:
            content = _html_table_without_columns(
                headers,
                rows,
                {"数据库版本"},
            )
        else:
            content = _html_table(headers, rows)
        navigation.append(
            f'<a href="#{slug}"><span>{html.escape(title)}</span><strong>{display_count}</strong></a>'
        )
        section_html.append(
            f"""
            <details class="report-section" id="{slug}"{' open' if initially_open else ''}>
              <summary class="section-heading"><div><h2>{html.escape(title)}</h2><p>{html.escape(description)}</p></div><span>{display_count} 条记录</span></summary>
              <div class="section-content">{content}</div>
            </details>
            """
        )
    report_title = _process_report_title(report)
    candidate_count = len(tables["candidates"][1])
    rule_count = len(tables["rules"][1])
    document = f"""<!doctype html>
<html lang="zh-CN"><head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="agentd-report-id" content="{html.escape(report['report_id'])}">
  <meta name="agentd-content-sha256" content="{html.escape(report['content_sha256'])}">
  <link rel="icon" href="data:,"><title>{html.escape(report_title)}科学过程详情报告</title>
  <style>
    :root{{--ink:#17251f;--muted:#66756e;--line:#d8e3dc;--brand:#087c57;--soft:#eef7f1;--warn:#fff6e8;--danger:#fff0ee}}
    *{{box-sizing:border-box}} html{{scroll-behavior:smooth}} body{{margin:0;background:#f3f6f4;color:var(--ink);font-family:"Microsoft YaHei","Noto Sans CJK SC",Arial,sans-serif;font-size:14px;line-height:1.65}}
    main{{max-width:1500px;margin:auto;padding:34px 24px 72px}} header,.notice,.contents,.report-section,.purpose{{background:#fff;border:1px solid var(--line);border-radius:18px;margin-bottom:20px;box-shadow:0 5px 18px rgba(26,70,52,.045)}}
    header{{padding:30px}} .eyebrow{{color:var(--brand);font-weight:800;font-size:13px;letter-spacing:.08em}} h1{{font-size:30px;line-height:1.3;margin:.25em 0 .35em}} h2{{font-size:20px;margin:0}} h3{{font-size:16px;margin:0 0 4px}} p{{color:var(--muted)}} .muted{{color:var(--muted)}}
    .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:12px;margin-top:20px}} .card{{background:var(--soft);padding:14px 16px;border-radius:12px}} .card span,.metric-grid span{{display:block;color:var(--muted);font-size:12px}} .card strong{{display:block;margin-top:3px;font-size:17px}}
    .notice{{padding:16px 20px;background:var(--warn);border-color:#efd3a8}} .contents{{padding:18px 20px}} .contents h2{{font-size:16px;margin-bottom:12px}} .contents nav{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:8px}} .contents a{{display:flex;justify-content:space-between;gap:12px;color:var(--ink);text-decoration:none;background:#f7faf8;border:1px solid var(--line);padding:9px 12px;border-radius:10px}} .contents a:hover{{border-color:var(--brand)}} .contents a strong{{color:var(--brand)}}
    .report-section{{scroll-margin-top:16px;overflow:hidden}} .section-heading{{display:grid;grid-template-columns:22px minmax(0,1fr) auto;align-items:start;gap:18px;padding:21px 24px;cursor:pointer;list-style:none;text-align:left}} .section-heading::-webkit-details-marker{{display:none}} .section-heading::before{{content:"＋";color:var(--brand);font-weight:800;font-size:20px;line-height:1.2;text-align:center}} .report-section[open]>.section-heading::before{{content:"－"}} .section-heading>div{{min-width:0}} .section-heading p{{margin:3px 0 0}} .section-heading>span{{white-space:nowrap;background:var(--soft);padding:5px 10px;border-radius:999px;font-size:12px}} .section-content{{padding:0 24px 24px;border-top:1px solid var(--line)}}
    .table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:12px;max-height:560px;margin-top:18px}} .table-wrap.compact{{max-height:none}} table{{width:100%;border-collapse:collapse;font-size:13px}} thead th{{position:sticky;top:0;z-index:1;background:#e8f3ec;text-align:left;white-space:nowrap}} th,td{{padding:10px 11px;border-bottom:1px solid var(--line);vertical-align:top}} tbody tr:nth-child(even){{background:#fafcfb}} tbody tr:last-child>td,tbody tr:last-child>th{{border-bottom:0}} td{{max-width:430px;word-break:break-word}} .empty{{padding:30px;text-align:center;color:var(--muted)}}
    .status{{display:inline-flex;align-items:center;white-space:nowrap;border-radius:999px;padding:3px 9px;font-size:12px;font-weight:700}} .status.pass{{color:#06704d;background:#e8f7ef}} .status.fail{{color:#b7352b;background:var(--danger)}} .status.warning{{color:#96600d;background:#fff2d7}} .status.neutral{{color:#53625b;background:#eef1ef}}
    .parameter-list{{min-width:220px;margin:0;padding-left:18px}} .sequence{{font-family:Consolas,"SFMono-Regular",monospace;letter-spacing:.025em;overflow-wrap:anywhere;word-break:break-all;color:#173f31}} .failed-data{{display:inline;color:#b42318!important;background:#fff0f0!important;border-radius:6px;padding:2px 5px;font-weight:800;box-decoration-break:clone;-webkit-box-decoration-break:clone}} .sequence.failed-data{{color:#b42318!important}} .checksum{{font-family:Consolas,monospace;text-decoration:underline dotted;cursor:help}}
    .input-sequence-card{{margin-top:16px;padding:16px;border:1px solid var(--line);border-radius:12px;background:#fbfdfc}} .input-sequence-heading{{display:flex;justify-content:space-between;gap:20px;align-items:center}} .input-sequence-heading>div:first-child>strong{{display:block;font-size:16px}} .input-sequence-heading>div:first-child>span{{display:block;color:var(--muted);font-size:12px;margin-top:2px}} .input-file-meta{{display:grid;grid-template-columns:auto auto;gap:2px 10px;align-items:center}} .input-file-meta>span{{color:var(--muted);font-size:12px}} .input-file-meta>strong{{font-size:13px}}
    .result-overview{{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:10px;margin-top:14px}} .result-overview>div{{padding:12px 14px;border-radius:10px;background:var(--soft)}} .result-overview span{{display:block;color:var(--muted);font-size:12px}} .result-overview strong{{display:block;margin-top:2px;font-size:20px}}
    .target-group{{padding-top:20px}} .target-group+.target-group{{margin-top:20px;border-top:1px dashed var(--line)}} .target-count{{margin:0 0 4px}} .failure-legend{{margin:0 0 12px;font-size:12px;color:var(--muted)}} .record{{border:1px solid var(--line);border-radius:12px;background:#fbfdfc;margin:10px 0;overflow:hidden}} .record>summary{{display:grid;grid-template-columns:minmax(190px,.75fr) minmax(320px,2fr) auto;align-items:center;gap:14px;padding:13px 15px;cursor:pointer;list-style:none;position:relative}} .record>summary::-webkit-details-marker{{display:none}} .record>summary::before{{content:"›";color:var(--brand);font-size:22px;font-weight:800;position:absolute;left:13px}} .record[open]>summary::before{{transform:rotate(90deg)}} .record-title{{font-weight:800;padding-left:18px}} .record-meta{{color:var(--muted);font-size:12px}} .record-body{{border-top:1px solid var(--line);padding:16px;background:#fff}}
    .metric-grid{{display:grid;grid-template-columns:repeat(4,minmax(130px,1fr));gap:10px;margin-bottom:14px}} .metric-grid>div{{background:var(--soft);border-radius:10px;padding:10px 12px}} .metric-grid strong{{display:block;margin-top:2px}} .metric-grid small{{display:block;color:var(--muted)}} .oligo-table th[scope=row]{{white-space:nowrap;background:#f5f9f6}} .sequence-panel{{margin-top:14px;padding:13px 14px;border:1px solid var(--line);border-radius:10px;background:#f8faf9}} .sequence-panel>span:first-child{{display:block;color:var(--muted);font-size:12px;margin-bottom:6px}}
    .evidence-panel{{margin-top:14px;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:#fff}} .evidence-heading{{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;padding:12px 14px;background:#f5f9f6}} .evidence-heading span{{display:block;color:var(--muted);font-size:11px}} .evidence-heading h4{{margin:2px 0 0;font-size:15px}} .evidence-heading small{{max-width:440px;color:var(--muted);text-align:right}} .evidence-table th[scope=row]{{white-space:normal;background:#fbfdfc}} .pair-dg-grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:12px 14px}} .pair-dg-grid>div{{padding:9px 10px;border-radius:8px;background:var(--soft)}} .pair-dg-grid span{{display:block;color:var(--muted);font-size:11px}} .pair-dg-grid strong{{display:block;margin-top:2px}}
    .purpose{{padding:22px 24px}} .purpose p{{margin-bottom:0}} footer{{text-align:center;color:var(--muted);font-size:12px}}
    @media(max-width:850px){{main{{padding:18px 12px 50px}} header{{padding:22px}} h1{{font-size:24px}} .section-heading{{padding:17px}} .section-content{{padding:0 16px 18px}} .record>summary{{grid-template-columns:1fr auto}} .record-meta{{grid-column:1/-1;padding-left:18px}} .metric-grid,.result-overview,.pair-dg-grid{{grid-template-columns:1fr 1fr}} .input-sequence-heading{{display:block}} .input-file-meta{{margin-top:10px;grid-template-columns:auto 1fr}} .evidence-heading{{display:block}} .evidence-heading small{{display:block;margin-top:6px;text-align:left}}}}
    @media print{{body{{background:#fff}} main{{max-width:none;padding:8mm}} .contents{{display:none}} .report-section>.section-content,.record>.record-body{{display:block!important}} .table-wrap{{max-height:none;overflow:visible}}}}
  </style>
</head><body><main>
  <header>
    <span class="eyebrow">WF-01 科学过程详情</span>
    <h1>{html.escape(report_title)}<br>科学过程详情报告</h1>
    <p>本报告按科学流程解释输入、参数、候选生成、逐项检测、评分和特异性核查。页面默认收起数据量较大的章节，点击章节或候选名称即可查看完整记录。</p>
    <div class="cards">
      <div class="card"><span>任务编号</span><strong>{html.escape(str(report.get('batch_code') or '-'))}</strong></div>
      <div class="card"><span>任务状态</span><strong>{html.escape(_process_status(overview.get('status')))}</strong></div>
      <div class="card"><span>靶标数量</span><strong>{_format_number(overview.get('target_total'))}</strong></div>
      <div class="card"><span>全部候选记录</span><strong>{candidate_count}</strong></div>
      <div class="card"><span>逐项检测记录</span><strong>{rule_count}</strong></div>
      <div class="card"><span>可交付候选</span><strong>{_format_number((overview.get('candidate_counts') or dict()).get('deliverable'))}</strong></div>
    </div>
  </header>
  <div class="notice"><strong>怎样阅读：</strong>先查看“未通过、需关注与未完成项目”，再按需展开候选和逐项检测。前十名交付详情请看主报告；本报告保留全部科学过程和所有候选记录。</div>
  <section class="contents"><h2>报告目录</h2><nav>{''.join(navigation)}</nav></section>
  {''.join(section_html)}
  <section class="purpose"><h2>用途声明</h2><p>{html.escape(report['disclaimer']['text'])}</p></section>
  <footer>生成时间：{html.escape(_process_datetime(report.get('generated_at')))}（北京时间） · 与主报告使用同一份数据</footer>
</main></body></html>"""
    return document.encode("utf-8")


_RULE_VALIDATION_PASS_STATUSES = {"PASS", "NOT_APPLICABLE"}


def _rule_validation_mark(status: Any) -> tuple[str, str]:
    return (
        ("✓", "pass")
        if str(status or "") in _RULE_VALIDATION_PASS_STATUSES
        else ("✗", "fail")
    )


def _rule_validation_execution(status: Any) -> str:
    value = str(status or "NOT_EVALUATED")
    if value == "NOT_APPLICABLE":
        return "不适用"
    if value in {"NOT_EVALUATED", "PENDING", "TOOL_ERROR", "INCOMPLETE"}:
        return "未执行或未完成"
    return "已执行"


def _rule_validation_problem(rule: dict[str, Any]) -> str:
    status = str(rule.get("status") or "NOT_EVALUATED")
    message = _process_message(rule.get("message"))
    if status == "PASS":
        return "无"
    if status == "NOT_APPLICABLE":
        return f"无；本任务不适用。{message}"
    if status in {"WARNING", "CAUTION", "PASS_WITH_WARNINGS"}:
        return f"检测到需关注项：{message}"
    if status == "FAIL":
        return f"未通过：{message}"
    return f"未执行、未完成或证据不足：{message}"


def _rule_validation_candidates(target: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = target.get("top10") or target.get(
        "all_deliverable_candidates"
    ) or []
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        key = _candidate_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        output.append(candidate)
    return output


def _rule_validation_rule_map(
    candidate: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    return {
        str(rule.get("code")): rule
        for rule in candidate.get("rules") or []
        if isinstance(rule, dict) and str(rule.get("code")) in RULE_CODES
    }


def _rule_validation_effective_rule(
    candidate: dict[str, Any],
    code: str,
    rule_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rule = copy.deepcopy(
        rule_map.get(code) or _rule_validation_missing_rule(code)
    )
    soft_codes = {
        "R01": "R01_SOFT",
        "R03": "R03_SOFT",
    }
    soft_code = soft_codes.get(code)
    if soft_code is None or str(rule.get("status")) != "PASS":
        return rule
    penalties = (candidate.get("scoring_detail") or {}).get("penalties") or []
    matched = [
        item
        for item in penalties
        if isinstance(item, dict)
        and str(item.get("rule_code") or item.get("code") or "")
        == soft_code
    ]
    if not matched:
        return rule
    messages = [
        str(
            item.get("message")
            or item.get("description")
            or item.get("condition")
            or "触发软性警戒条件"
        )
        for item in matched
    ]
    rule["status"] = "WARNING"
    rule["evaluation_state"] = "EVALUATED"
    rule["message"] = f"硬性门槛已通过，但触发{soft_code}：" + "；".join(
        messages
    )
    rule["threshold"] = (
        f"{rule.get('threshold') or '按当前设计规范判定'}；"
        "同时检查对应软性警戒项"
    )
    return rule


def _rule_validation_missing_rule(code: str) -> dict[str, Any]:
    return _rule(
        code,
        category="validation",
        status="NOT_EVALUATED",
        actual=None,
        threshold="按 R-01～R-21 设计规范执行",
        source="rules validation report",
        message="该候选没有保存本条规则的执行记录",
    )


def _rule_code_display(code: str) -> str:
    return f"R-{code[1:]}" if re.fullmatch(r"R\d{2}", code) else code


def _rule_validation_table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{html.escape(value)}</th>" for value in headers)
    body: list[str] = []
    for row in rows:
        mark = str(row[0])
        mark_class = "pass" if mark == "✓" else "fail"
        cells = [
            f'<td class="mark {mark_class}" aria-label="{"通过" if mark == "✓" else "有问题"}">{html.escape(mark)}</td>'
        ]
        cells.extend(
            f"<td>{html.escape(_format_number(value))}</td>"
            for value in row[1:]
        )
        body.append(f"<tr>{''.join(cells)}</tr>")
    if not body:
        body.append(
            f'<tr><td colspan="{len(headers)}" class="empty">无验证记录</td></tr>'
        )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        + head
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def render_rules_validation_html(report: dict[str, Any]) -> bytes:
    """Render a dedicated R-01–R-21 execution and issue report."""
    overview = report["batch_overview"]
    summary_blocks: list[str] = []
    detail_blocks: list[str] = []
    total_checks = 0
    passed_checks = 0
    issue_checks = 0
    deliverable_candidates = 0

    for target in report.get("targets") or []:
        target_name = _process_target_display(target)
        candidates = _rule_validation_candidates(target)
        deliverable_candidates += len(candidates)
        candidate_labels = _process_candidate_labels(target)
        summary_rows: list[list[Any]] = []
        candidate_rule_maps = [
            _rule_validation_rule_map(candidate) for candidate in candidates
        ]

        for code in RULE_CODES:
            if not candidates:
                summary_rows.append(
                    [
                        "✗",
                        _rule_code_display(code),
                        _PROCESS_CHECK_LABELS.get(code, code),
                        "未执行或未完成",
                        "0/0",
                        "没有可交付候选，无法验证该条规则",
                    ]
                )
                total_checks += 1
                issue_checks += 1
                continue

            rules = [
                _rule_validation_effective_rule(candidate, code, rule_map)
                for candidate, rule_map in zip(candidates, candidate_rule_maps)
            ]
            statuses = [str(rule.get("status") or "NOT_EVALUATED") for rule in rules]
            ok_count = sum(
                status in _RULE_VALIDATION_PASS_STATUSES
                for status in statuses
            )
            all_ok = ok_count == len(rules)
            if all(status == "NOT_APPLICABLE" for status in statuses):
                execution = "不适用"
            elif any(
                status in {"NOT_EVALUATED", "PENDING", "TOOL_ERROR", "INCOMPLETE"}
                for status in statuses
            ):
                execution = "未完成"
            else:
                execution = "已执行"
            problems = []
            for candidate, rule, status in zip(candidates, rules, statuses):
                if status in _RULE_VALIDATION_PASS_STATUSES:
                    continue
                problems.append(
                    f"{_process_candidate_label(candidate, candidate_labels)}："
                    f"{_rule_validation_problem(rule)}"
                )
            if problems:
                problem = "；".join(problems)
            elif all(status == "NOT_APPLICABLE" for status in statuses):
                problem = "无；本任务范围内该规则不适用"
            else:
                problem = "无"
            summary_rows.append(
                [
                    "✓" if all_ok else "✗",
                    _rule_code_display(code),
                    _PROCESS_CHECK_LABELS.get(code, code),
                    execution,
                    f"{ok_count}/{len(rules)}",
                    problem,
                ]
            )
            total_checks += 1
            if all_ok:
                passed_checks += 1
            else:
                issue_checks += 1

        summary_blocks.append(
            f"""
            <section class="target-section">
              <div class="target-heading"><div><span>设计对象</span><h2>{html.escape(target_name)}</h2></div><strong>{len(candidates)} 套交付候选</strong></div>
              <p>总览行为该条规则对本设计对象全部交付候选的聚合结果；任一候选有问题即显示 ✗。</p>
              {_rule_validation_table(['结果', '规则', '验证项', '执行情况', '无问题候选', '问题说明'], summary_rows)}
            </section>
            """
        )

        candidate_blocks: list[str] = []
        for candidate_index, (candidate, rule_map) in enumerate(
            zip(candidates, candidate_rule_maps), start=1
        ):
            rows: list[list[Any]] = []
            candidate_passed = 0
            for code in RULE_CODES:
                rule = _rule_validation_effective_rule(
                    candidate, code, rule_map
                )
                status = str(rule.get("status") or "NOT_EVALUATED")
                mark, _ = _rule_validation_mark(status)
                if mark == "✓":
                    candidate_passed += 1
                rows.append(
                    [
                        mark,
                        _rule_code_display(code),
                        _PROCESS_CHECK_LABELS.get(code, code),
                        _rule_validation_execution(status),
                        _process_status(status),
                        _process_rule_actual(candidate, rule),
                        _process_threshold(rule.get("threshold")),
                        _rule_validation_problem(rule),
                    ]
                )
            label = _process_candidate_label(candidate, candidate_labels)
            candidate_blocks.append(
                f"""
                <details class="candidate"{' open' if candidate_index == 1 else ''}>
                  <summary><span>{html.escape(label)}</span><strong>{candidate_passed}/21 项无问题</strong></summary>
                  {_rule_validation_table(['结果', '规则', '验证项', '执行情况', '原始状态', '实际结果', '判定要求', '问题说明'], rows)}
                </details>
                """
            )
        if not candidate_blocks:
            candidate_blocks.append(
                '<div class="empty-state">本设计对象没有可交付候选，因此无法对具体方案执行 21 条规则验证。</div>'
            )
        detail_blocks.append(
            f"""
            <section class="detail-section">
              <h2>{html.escape(target_name)}：候选逐条验证</h2>
              {''.join(candidate_blocks)}
            </section>
            """
        )

    report_title = _process_report_title(report)
    document = f"""<!doctype html>
<html lang="zh-CN"><head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="agentd-report-id" content="{html.escape(report['report_id'])}">
  <meta name="agentd-content-sha256" content="{html.escape(report['content_sha256'])}">
  <link rel="icon" href="data:,"><title>{html.escape(report_title)}21条规则验证报告</title>
  <style>
    :root{{--ink:#18231e;--muted:#657169;--line:#d8e1db;--green:#087c57;--green-soft:#e9f6ef;--red:#bd3028;--red-soft:#fff0ee;--blue:#315f9b;--soft:#f5f8f6}}
    *{{box-sizing:border-box}} body{{margin:0;background:#f2f5f3;color:var(--ink);font-family:"Microsoft YaHei","Noto Sans CJK SC",Arial,sans-serif;font-size:14px;line-height:1.65}} main{{max-width:1480px;margin:auto;padding:34px 22px 72px}}
    header,.legend,.target-section,.detail-section,.disclaimer{{background:#fff;border:1px solid var(--line);border-radius:18px;box-shadow:0 6px 20px rgba(30,58,43,.05);margin-bottom:20px}} header{{padding:30px}} .eyebrow{{color:var(--blue);font-weight:800;letter-spacing:.08em}} h1{{font-size:30px;line-height:1.3;margin:.25em 0}} h2{{font-size:20px;margin:0}} p{{color:var(--muted)}}
    .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-top:20px}} .card{{background:var(--soft);border-radius:12px;padding:13px 15px}} .card span{{display:block;color:var(--muted);font-size:12px}} .card strong{{display:block;font-size:20px;margin-top:2px}} .card.issue strong{{color:var(--red)}}
    .legend{{padding:16px 20px}} .legend strong{{margin-right:16px}} .legend .yes{{color:var(--green)}} .legend .no{{color:var(--red)}} .target-section,.detail-section{{padding:24px}} .target-heading{{display:flex;justify-content:space-between;align-items:flex-start;gap:18px}} .target-heading span{{color:var(--muted);font-size:12px}} .target-heading strong{{background:var(--soft);padding:5px 10px;border-radius:999px}}
    .table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:12px;margin-top:16px}} table{{width:100%;border-collapse:collapse;font-size:13px}} th{{background:#edf3ef;text-align:left;white-space:nowrap}} th,td{{padding:10px 11px;border-bottom:1px solid var(--line);vertical-align:top}} tbody tr:last-child td{{border-bottom:0}} tbody tr:nth-child(even){{background:#fbfcfb}} td{{max-width:430px;word-break:break-word}} .mark{{font-size:24px;font-weight:900;text-align:center;line-height:1}} .mark.pass{{color:var(--green);background:var(--green-soft)}} .mark.fail{{color:var(--red);background:var(--red-soft)}}
    .candidate{{border:1px solid var(--line);border-radius:12px;margin-top:14px;overflow:hidden}} .candidate summary{{display:flex;justify-content:space-between;gap:18px;cursor:pointer;padding:14px 16px;font-weight:800;background:var(--soft)}} .candidate>.table-wrap{{border-width:1px 0 0;border-radius:0;margin-top:0}} .empty,.empty-state{{padding:28px;text-align:center;color:var(--muted)}} .disclaimer{{padding:20px 24px}} footer{{text-align:center;color:var(--muted);font-size:12px}}
    @media(max-width:760px){{main{{padding:18px 10px 48px}} header,.target-section,.detail-section{{padding:20px}} h1{{font-size:24px}} .target-heading{{display:block}}}} @media print{{body{{background:#fff}} main{{max-width:none;padding:8mm}} header,.legend,.target-section,.detail-section,.disclaimer{{box-shadow:none}} .table-wrap{{overflow:visible}} .candidate{{break-inside:avoid}}}}
  </style>
</head><body><main>
  <header>
    <span class="eyebrow">WF-01 规则执行审核</span>
    <h1>{html.escape(report_title)}<br>21条规则验证报告</h1>
    <p>本报告核对最终交付候选的 R-01～R-21 执行记录。每条规则都显示执行情况、实际结果和问题原因，不以综合评分代替逐条判定。</p>
    <div class="cards">
      <div class="card"><span>任务编号</span><strong>{html.escape(str(report.get('batch_code') or '-'))}</strong></div>
      <div class="card"><span>设计对象</span><strong>{_format_number(overview.get('target_total'))}</strong></div>
      <div class="card"><span>交付候选</span><strong>{deliverable_candidates}</strong></div>
      <div class="card"><span>总览无问题</span><strong>{passed_checks}/{total_checks}</strong></div>
      <div class="card issue"><span>总览有问题</span><strong>{issue_checks}</strong></div>
    </div>
  </header>
  <section class="legend"><strong class="yes">✓ 无问题</strong><strong class="no">✗ 有问题</strong><span>警告、失败、未执行或证据不足均记为 ✗；已完成适用性判定且本任务不适用的规则记为 ✓，并单独标注“不适用”。</span></section>
  {''.join(summary_blocks)}
  {''.join(detail_blocks)}
  <section class="disclaimer"><h2>用途声明</h2><p>{html.escape(report['disclaimer']['text'])}</p></section>
  <footer>生成时间：{html.escape(_process_datetime(report.get('generated_at')))}（北京时间） · 与主报告、过程报告使用同一份数据</footer>
</main></body></html>"""
    return document.encode("utf-8")


def _process_csv(headers: list[str], rows: list[list[Any]]) -> bytes:
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow(["" if value is None else value for value in row])
    return output.getvalue().encode("utf-8-sig")


def render_scientific_data_archive(report: dict[str, Any]) -> bytes:
    """Package human-readable process data without exposing code-shaped JSON."""
    artifact_manifest = report.get("_scientific_artifact_manifest") or {}
    tables = _process_tables(report, artifact_manifest)
    process_html = render_process_detail_html(report)
    rules_html = render_rules_validation_html(report)
    filenames = {
        "tool_runs": "01_科学工具与数据清单.csv",
        "inputs": "02_任务输入与设计参数.csv",
        "searches": "03_Primer3候选生成过程.csv",
        "candidates": "04_全部候选原始结果.csv",
        "rules": "05_逐项检测结果.csv",
        "scores": "06_评分与排序过程.csv",
        "specificity": "07_特异性检测汇总.csv",
        "hits": "08_特异性命中明细.csv",
        "attention": "09_未通过需关注与未完成项目.csv",
    }
    readme = (
        "WF-01 科学工具数据包\r\n\r\n"
        "本数据包使用中文表格保存科学工具产生的数据和计算结果。\r\n"
        "为便于阅读和保护运行环境，不包含 JSON、程序代码、命令行、内部字段名或访问密钥。\r\n"
        "服务端仍保留带校验值的不可变原始证据，可用于内部审计和问题追溯。\r\n"
        f"任务编号：{report.get('batch_code') or '-'}\r\n"
        f"生成时间：{report.get('generated_at') or '-'}\r\n"
        f"主报告数据校验值：{report.get('content_sha256') or '-'}\r\n"
    ).encode("utf-8-sig")
    buffer = BytesIO()
    with zipfile.ZipFile(
        buffer,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:
        archive.writestr("使用说明.txt", readme)
        archive.writestr("科学过程详情报告.html", process_html)
        archive.writestr("21条规则验证报告.html", rules_html)
        for key, filename in filenames.items():
            headers, rows = tables[key]
            archive.writestr(filename, _process_csv(headers, rows))
    return buffer.getvalue()


_DELIVERY_STATUS_LABELS = {
    "COMPLETED": "设计完成",
    "SUCCEEDED": "设计完成",
    "PARTIALLY_SUCCEEDED": "设计完成，部分评价待补充",
    "PARTIAL_INCOMPLETE": "候选已生成，部分评价待补充",
    "PASS": "推荐",
    "PASS_WITH_WARNINGS": "可用，需关注",
    "QUALIFIED": "合格",
    "FAILED": "未通过",
    "NOT_EVALUATED": "未评估",
    "NOT_APPLICABLE": "不适用",
}

_DELIVERY_CHECK_LABELS = {
    "R01": "引物熔解温度及两条引物温差",
    "R02": "扩增子长度",
    "R03": "引物 3′ 端碱基组成与连续 G 风险",
    "R04": "引物长度",
    "R05": "探针 5′ 端碱基",
    "R06": "探针位置及与引物的间距",
    "R07": "探针与引物的熔解温度关系",
    "R08": "探针长度",
    "R09": "引物自身 3′ 端二聚体风险",
    "R10": "正反向引物 3′ 端交叉二聚体风险",
    "R11": "引物及探针发卡结构风险",
    "R12": "非 3′ 端弱二聚体风险",
    "R19": "靶标命中、背景序列及可选宿主排除特异性",
}

_DELIVERY_PRIMARY_CHECK_CODES = tuple(_DELIVERY_CHECK_LABELS)

_DELIVERY_WARNING_LABELS = {
    "R01_SOFT": "引物熔解温度低于优选范围",
    "R02_SOFT": "扩增子长度位于设计边界",
    "R03_SOFT": "引物 3′ 端序列构成需要复核",
    "R11": _DELIVERY_CHECK_LABELS["R11"],
    "R12": _DELIVERY_CHECK_LABELS["R12"],
    "R19": _DELIVERY_CHECK_LABELS["R19"],
}

_DELIVERY_SCORING_DIMENSIONS = {
    "thermodynamics": "热力学",
    "specificity": "特异性",
    "amplicon_features": "扩增子特征",
}

_DELIVERY_PENALTY_LABELS = {
    "R01_SOFT": "引物熔解温度低于 55°C",
    "R03_SOFT": "引物 3′ 端序列构成需要复核",
    "R11": "发卡结构需要关注",
    "R12": "非 3′ 端弱二聚体需要关注",
}


def _delivery_number(value: Any, digits: int = 1) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return round(number, digits) if math.isfinite(number) else None


def _delivery_score(value: Any) -> str:
    number = _delivery_number(value, 1)
    return f"{number:.1f}/100" if number is not None else "未评估"


def _delivery_percent(value: Any) -> str:
    number = _delivery_number(value, 1)
    return f"{number:.1f}%" if number is not None else "未评估"


def _delivery_status(value: Any) -> str:
    key = str(value or "").upper()
    return _DELIVERY_STATUS_LABELS.get(key, "结果待复核")


def _delivery_score_label(candidate: dict[str, Any]) -> str:
    scoring = candidate.get("scoring_detail") or (
        (candidate.get("metadata") or {}).get("wf01_v02_scoring") or {}
    )
    label = str(scoring.get("score_label") or "").strip()
    return label if label in {"设计评分", "综合评分"} else "综合评分"


def _delivery_candidates_score_label(candidates: list[dict[str, Any]]) -> str:
    labels = {_delivery_score_label(candidate) for candidate in candidates}
    return labels.pop() if len(labels) == 1 else "评分"


def _delivery_original_sequences(targets: list[dict[str, Any]]) -> str:
    items: list[str] = []
    for target in targets:
        template = (target.get("input") or {}).get("template") or {}
        sequence = re.sub(r"\s+", "", str(template.get("sequence") or ""))
        if not sequence:
            continue
        target_name = _delivery_target_name(target)
        length_nt = template.get("length_nt") or len(sequence)
        items.append(
            '<div class="original-sequence-item">'
            '<div class="original-sequence-meta">'
            f'<strong>{html.escape(target_name)}</strong>'
            f'<span>{html.escape(str(length_nt))} nt</span>'
            '</div>'
            f'<div class="original-sequence" aria-label="{html.escape(target_name)}原始模板序列">'
            f'{html.escape(sequence.upper())}</div>'
            '</div>'
        )
    if not items:
        return ""
    return (
        '<section class="original-sequences">'
        '<div class="original-sequences-heading">'
        '<div><span>本次设计输入</span><h2>原始模板序列</h2></div>'
        '<small>按设计对象列出，序列方向为 5′→3′</small>'
        '</div>'
        f'{"".join(items)}'
        '</section>'
    )


def _delivery_scoring_policy(candidates: list[dict[str, Any]]) -> str:
    labels = {_delivery_score_label(candidate) for candidate in candidates}
    if labels == {"设计评分"}:
        items = [
            ("热力学", "权重 55.5%"),
            ("特异性", "未评估，不参与设计评分"),
            ("扩增子特征", "权重 44.5%"),
            ("保守性", "本期未评估，不计分"),
        ]
        formula = (
            "二维归一化加权基础分 ×（1 − 软规则累计扣分比例）；"
            "仅适用于本次仅序列设计路线。"
        )
    elif len(labels) > 1:
        items = [
            ("仅序列路线", "热力学 55.5% + 扩增子特征 44.5%"),
            ("完整路线", "热力学 35.7% + 特异性 35.7% + 扩增子特征 28.6%"),
            ("软规则", "在对应路线的加权基础分上扣分"),
            ("保守性", "本期未评估，不计分"),
        ]
        formula = "各靶标按其实际执行路线独立评分和排名，两种分数不混合比较。"
    else:
        items = [
            ("热力学", "权重 35.7%"),
            ("特异性", "权重 35.7%"),
            ("扩增子特征", "权重 28.6%"),
            ("保守性", "本期未评估，不计分"),
        ]
        formula = (
            "三维加权基础分 ×（1 − 软规则累计扣分比例）；"
            "必需特异性评估未完成时不生成综合评分。"
        )
    cards = "".join(
        f"<div><span>{html.escape(label)}</span>"
        f"<strong>{html.escape(value)}</strong></div>"
        for label, value in items
    )
    return (
        '<div class="scoring-policy" aria-label="评分规则">'
        f"{cards}"
        '<div class="formula"><span>最终评分</span>'
        f"<strong>{html.escape(formula)}</strong></div></div>"
    )


def _delivery_score_breakdown(candidate: dict[str, Any]) -> str:
    scoring = candidate.get("scoring_detail") or {}
    dimensions = scoring.get("dimensions") or {}
    weights = scoring.get("effective_weights") or scoring.get("weights") or {}
    score_label = _delivery_score_label(candidate)
    design_only = score_label == "设计评分"
    dimension_items: list[str] = []
    for key, label in _DELIVERY_SCORING_DIMENSIONS.items():
        detail = dimensions.get(key) or {}
        value = detail.get("raw_score")
        weight = weights.get(key)
        participates = detail.get("participates_in_score")
        if participates is None:
            participates = not (
                design_only and key == "specificity"
            )
        score_text = _delivery_score(value)
        if participates:
            weight_text = (
                f"权重 {float(weight) * 100:.1f}%"
                if isinstance(weight, (int, float))
                else "权重未提供"
            )
        else:
            weight_text = "本路线不参与"
        dimension_items.append(
            '<div class="score-dimension">'
            f'<span>{html.escape(label)} · {html.escape(weight_text)}</span>'
            f'<strong>{html.escape(score_text)}</strong>'
            "</div>"
        )

    base_score = scoring.get("base_score")
    penalty = scoring.get("applied_penalty_percent")
    if penalty is None:
        penalty = scoring.get("total_penalty_percent")
    final_score = scoring.get("final_score")
    if final_score is None:
        final_score = candidate.get("score")
    penalty_number = _delivery_number(penalty, 1) or 0.0
    penalty_items = []
    for item in scoring.get("penalties") or []:
        code = str(item.get("rule_code") or "")
        label = _DELIVERY_PENALTY_LABELS.get(code) or str(
            item.get("condition") or "其他软规则"
        )
        percent = _delivery_number(item.get("penalty_percent"), 1)
        penalty_items.append(
            "<li>"
            f"<span>{html.escape(label)}</span>"
            f"<strong>-{percent:.1f}%</strong>"
            "</li>"
            if percent is not None
            else f"<li><span>{html.escape(label)}</span></li>"
        )
    penalty_detail = (
        f'<ul class="penalty-list">{"".join(penalty_items)}</ul>'
        if penalty_items
        else '<p class="no-penalty">无软规则扣分</p>'
    )
    breakdown_title = (
        "二维设计评分与软规则扣分"
        if design_only
        else "三维基础分与软规则扣分"
    )
    breakdown_note = (
        "特异性未评估，不参与设计评分"
        if design_only
        else "保守性本期未评估，不参与评分"
    )
    final_score_label = f"最终{score_label}"
    return f"""
        <section class="score-panel">
          <div class="score-panel-heading"><div><span>评分构成</span><h4>{html.escape(breakdown_title)}</h4></div><small>{html.escape(breakdown_note)}</small></div>
          <div class="dimension-scores">{''.join(dimension_items)}</div>
          <div class="score-equation">
            <div><span>加权基础分</span><strong>{html.escape(_delivery_score(base_score))}</strong></div>
            <b>×</b>
            <div><span>扣分后保留</span><strong>{100.0 - penalty_number:.1f}%</strong></div>
            <b>=</b>
            <div class="final"><span>{html.escape(final_score_label)}</span><strong>{html.escape(_delivery_score(final_score))}</strong></div>
          </div>
          {penalty_detail}
        </section>
    """


def _delivery_target_name(target: dict[str, Any]) -> str:
    target_input = target.get("input") or {}
    target_object = target_input.get("target_object") or {}
    original = target_object.get("original_input") or {}
    if isinstance(original, dict):
        name = original.get("name") or original.get("accession")
        if name:
            return str(name)
    for key in ("normalized_name", "identifier"):
        if target_object.get(key):
            return str(target_object[key])
    template = target_input.get("template") or {}
    return str(template.get("record_name") or "设计靶标")


def _delivery_host_names(target: dict[str, Any]) -> str:
    names: list[str] = []
    for host in (target.get("input") or {}).get("host_objects") or []:
        original = host.get("original_input") or {}
        name = original.get("name") if isinstance(original, dict) else None
        name = name or host.get("identifier") or host.get("normalized_name")
        if name and str(name) not in names:
            names.append(str(name))
    return "、".join(names) if names else "未指定"


def _delivery_amplicon_gc(candidate: dict[str, Any]) -> float | None:
    dimensions = (candidate.get("scoring_detail") or {}).get("dimensions") or {}
    actual = (dimensions.get("amplicon_features") or {}).get("actual") or {}
    value = _delivery_number(actual.get("gc_percent"), 1)
    if value is not None:
        return value
    sequence = str((candidate.get("amplicon") or {}).get("sequence_5to3") or "")
    if not sequence:
        return None
    return round(100.0 * sum(base in "GC" for base in sequence.upper()) / len(sequence), 1)


def _delivery_target_coverage(candidate: dict[str, Any]) -> float | None:
    target_hit = ((candidate.get("specificity") or {}).get("target_hit") or {})
    values: list[float] = []
    for hit in target_hit.get("hits") or []:
        value = hit.get("query_coverage_pct")
        if value is None:
            value = hit.get("coverage_percent")
        number = _delivery_number(value, 1)
        if number is not None:
            values.append(number)
    return min(values) if values else None


def _delivery_conservation(candidate: dict[str, Any]) -> str:
    return "本版本暂缓"


def _delivery_metric(label: str, value: str, *, primary: bool = False) -> str:
    class_name = "metric primary" if primary else "metric"
    return (
        f'<div class="{class_name}"><span>{html.escape(label)}</span>'
        f'<strong>{html.escape(value)}</strong></div>'
    )


def _delivery_warning_code(value: Any) -> str:
    if isinstance(value, str):
        return value.upper()
    if isinstance(value, dict):
        return str(value.get("code") or value.get("rule_code") or "").upper()
    return ""


def _delivery_warning_detail(code: str, candidate: dict[str, Any]) -> str:
    if code == "R01_SOFT":
        values: list[str] = []
        for label, key in (
            ("正向引物", "forward_primer"),
            ("反向引物", "reverse_primer"),
        ):
            tm = _delivery_number((candidate.get(key) or {}).get("tm_celsius"), 1)
            if tm is not None and tm < 55:
                values.append(f"{label} {tm:.1f}°C")
        return (
            f"{'、'.join(values)}低于 55°C，建议通过梯度实验确认扩增条件。"
            if values
            else "至少一条引物的熔解温度低于优选范围，建议通过梯度实验确认。"
        )
    if code == "R02_SOFT":
        length = (candidate.get("amplicon") or {}).get("length_bp")
        return (
            f"扩增子长度为 {length} bp，处于允许范围边界。"
            if length is not None
            else "扩增子长度处于允许范围边界。"
        )
    if code == "R03_SOFT":
        findings: list[str] = []
        for label, key in (
            ("正向引物", "forward_primer"),
            ("反向引物", "reverse_primer"),
        ):
            sequence = str((candidate.get(key) or {}).get("sequence_5to3") or "").upper()
            if not sequence:
                continue
            last5 = sequence[-5:]
            gc_count = last5.count("G") + last5.count("C")
            repeated = next(
                (base for base in "ACGT" if base * 3 in last5),
                None,
            )
            reasons: list[str] = []
            if gc_count == 0:
                reasons.append("不含 G/C")
            if repeated:
                reasons.append(f"含连续 3 个 {repeated}")
            if reasons:
                findings.append(f"{label}末 5 个碱基 {last5}，{'，'.join(reasons)}")
        return (
            f"{'；'.join(findings)}。建议实验前重点复核扩增稳定性。"
            if findings
            else "至少一条引物的 3′ 端无 G/C 或含连续 3 个相同碱基，建议实验前复核。"
        )
    if code == "R11":
        return "检测到需要关注的发卡结构倾向，建议结合实验条件复核。"
    if code == "R12":
        return "检测到需要关注的非 3′ 端弱二聚体倾向，建议结合实验条件复核。"
    if code == "R19":
        return "至少一项特异性结果需要人工复核，不应仅依据综合评分确定最终方案。"
    return "该项设计检查需要在实验前进一步复核。"


def _delivery_check_panel(candidate: dict[str, Any]) -> str:
    rules = {
        str(item.get("code") or "").upper(): item
        for item in candidate.get("rules") or []
        if isinstance(item, dict)
    }
    passed: list[str] = []
    failed: list[str] = []
    pending: list[str] = []
    attention: list[tuple[str, str]] = []
    warning_rule_codes: set[str] = set()
    for code in _DELIVERY_PRIMARY_CHECK_CODES:
        rule = rules.get(code) or {}
        status = str(rule.get("status") or "NOT_EVALUATED").upper()
        label = _DELIVERY_CHECK_LABELS[code]
        if status == "PASS":
            passed.append(label)
        elif status == "FAIL":
            failed.append(label)
        elif status == "WARNING":
            warning_rule_codes.add(code)
            attention.append((label, _delivery_warning_detail(code, candidate)))
        else:
            pending.append(label)

    seen_warning_codes = set(warning_rule_codes)
    for item in candidate.get("warnings") or []:
        code = _delivery_warning_code(item)
        if not code or code in seen_warning_codes:
            continue
        seen_warning_codes.add(code)
        attention.append(
            (
                _DELIVERY_WARNING_LABELS.get(code, "其他设计检查"),
                _delivery_warning_detail(code, candidate),
            )
        )

    if failed:
        headline = f"{len(passed)} 项通过，{len(failed)} 项未通过"
    elif attention:
        headline = (
            f"{len(passed)} 项已执行核心检测通过，"
            f"{len(attention)} 项软规则需要关注"
        )
    elif pending:
        headline = f"{len(passed)} 项通过，{len(pending)} 项尚未完成"
    else:
        headline = f"{len(passed)} 项已执行核心检测全部通过"

    passed_items = "".join(
        f'<li><span aria-hidden="true">✓</span>{html.escape(label)}</li>'
        for label in passed
    )
    failed_items = "".join(
        f'<li><strong>{html.escape(label)}</strong><span>未达到当前设计要求。</span></li>'
        for label in failed
    )
    attention_items = "".join(
        f'<li><strong>{html.escape(label)}</strong><span>{html.escape(detail)}</span></li>'
        for label, detail in attention
    )
    pending_items = "".join(
        f'<li><strong>{html.escape(label)}</strong><span>本次流程未完成该项评价。</span></li>'
        for label in pending
    )
    issue_groups = ""
    if failed_items:
        issue_groups += f'<div class="check-issues failed-checks"><h5>未通过项目</h5><ul>{failed_items}</ul></div>'
    if attention_items:
        issue_groups += f'<div class="check-issues attention-checks"><h5>需要关注</h5><ul>{attention_items}</ul></div>'
    if pending_items:
        issue_groups += f'<div class="check-issues pending-checks"><h5>尚未完成</h5><ul>{pending_items}</ul></div>'
    return f"""
        <section class="check-panel">
          <div class="check-heading"><div><span>检测结果</span><h4>{html.escape(headline)}</h4></div><div class="check-counts"><span class="pass-count">通过 {len(passed)}</span><span class="attention-count">关注 {len(attention)}</span><span class="fail-count">未通过 {len(failed)}</span></div></div>
          <div class="passed-checks"><h5>已通过的检测</h5><ul>{passed_items}</ul></div>
          {issue_groups}
        </section>
    """


def _delivery_oligo_row(label: str, oligo: dict[str, Any]) -> str:
    sequence = str(oligo.get("sequence_5to3") or "-")
    length = oligo.get("length_nt")
    tm = _delivery_number(oligo.get("tm_celsius"), 1)
    gc = _delivery_number(oligo.get("gc_percent"), 1)
    facts = []
    if isinstance(length, int):
        facts.append(f"{length} 个碱基")
    if tm is not None:
        facts.append(f"熔解温度 {tm:.1f}°C")
    if gc is not None:
        facts.append(f"GC {gc:.1f}%")
    return f"""
      <div class="oligo-row">
        <div class="oligo-title"><strong>{html.escape(label)}</strong><span>{html.escape(' · '.join(facts))}</span></div>
        <div class="sequence">5′-{html.escape(sequence)}-3′</div>
      </div>
    """


def _delivery_candidate_summary_table(
    candidates: list[dict[str, Any]],
) -> str:
    if not candidates:
        return '<div class="empty-state">当前没有可展示的候选方案。</div>'
    score_label = _delivery_candidates_score_label(candidates)
    rows = []
    for ordinal, candidate in enumerate(candidates, start=1):
        verdict_key = str(candidate.get("verdict") or "").upper()
        status_class = (
            "attention" if verdict_key == "PASS_WITH_WARNINGS" else "recommended"
        )
        sequences = [
            str((candidate.get(key) or {}).get("sequence_5to3") or "-")
            for key in ("forward_primer", "reverse_primer", "probe")
        ]
        rows.append(
            '<tr class="candidate-summary-row">'
            f'<th scope="row">候选 {ordinal:02d}</th>'
            + "".join(
                f'<td><span class="summary-sequence">{html.escape(sequence)}</span></td>'
                for sequence in sequences
            )
            + f'<td><strong class="summary-score">{html.escape(_delivery_score(candidate.get("score")))}</strong></td>'
            + f'<td><span class="candidate-status {status_class}">{html.escape(_delivery_status(candidate.get("verdict")))}</span></td>'
            + "</tr>"
        )
    return f"""
      <div class="candidate-summary-table">
        <table>
          <thead><tr><th>候选方案</th><th>正向引物（5′→3′）</th><th>反向引物（5′→3′）</th><th>TaqMan 探针（5′→3′）</th><th>{html.escape(score_label)}</th><th>结论</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
      </div>
    """


def _delivery_candidate_parameter_table(
    candidate: dict[str, Any],
    *,
    target_gene_id: str,
) -> str:
    forward = candidate.get("forward_primer") or {}
    reverse = candidate.get("reverse_primer") or {}
    probe = candidate.get("probe") or {}
    amplicon = candidate.get("amplicon") or {}
    score_label = _delivery_score_label(candidate)

    def measure(value: Any, unit: str, decimals: int = 1) -> str:
        number = _delivery_number(value, decimals)
        return "-" if number is None else f"{number:.{decimals}f}{unit}"

    def oligo_rows(label: str, oligo: dict[str, Any]) -> str:
        sequence = html.escape(str(oligo.get("sequence_5to3") or "-"))
        return f"""
          <tr><th>{html.escape(label)}</th><td colspan="5"><span class="detail-sequence">{sequence}</span></td></tr>
          <tr><th>{html.escape(label)} Tm 值</th><td>{measure(oligo.get('tm_celsius'), '°C')}</td><th>{html.escape(label)} GC 含量</th><td>{measure(oligo.get('gc_percent'), '%')}</td><th>{html.escape(label)}长度</th><td>{measure(oligo.get('length_nt'), ' nt', 0)}</td></tr>
        """

    amplicon_sequence = html.escape(str(amplicon.get("sequence_5to3") or "-"))
    return f"""
      <section class="candidate-parameter-detail">
        <div class="candidate-parameter-heading"><span>候选方案数据</span><h4>引物探针详细参数</h4></div>
        <div class="candidate-data-table-wrap"><table class="candidate-data-table"><tbody>
          <tr><th>目标基因 ID</th><td>{html.escape(target_gene_id)}</td><th>扩增子长度</th><td>{measure(amplicon.get('length_bp'), ' bp', 0)}</td><th>引物对{html.escape(score_label)}</th><td><strong class="detail-score">{html.escape(_delivery_score(candidate.get('score')))}</strong></td></tr>
          {oligo_rows('正向引物', forward)}
          {oligo_rows('反向引物', reverse)}
          {oligo_rows('探针', probe)}
        </tbody></table></div>
        <div class="detail-amplicon"><span>扩增子序列（5′→3′）</span><div class="sequence">5′-{amplicon_sequence}-3′</div></div>
      </section>
    """


def _delivery_candidate_card(
    candidate: dict[str, Any],
    ordinal: int,
    *,
    target_gene_id: str,
) -> str:
    amplicon = candidate.get("amplicon") or {}
    scoring = candidate.get("scoring_detail") or {}
    dimensions = scoring.get("dimensions") or {}
    thermo_actual = (dimensions.get("thermodynamics") or {}).get("actual") or {}
    primer_delta = _delivery_number(thermo_actual.get("primer_delta_tm_celsius"), 2)
    if primer_delta is None:
        forward_tm = _delivery_number((candidate.get("forward_primer") or {}).get("tm_celsius"), 2)
        reverse_tm = _delivery_number((candidate.get("reverse_primer") or {}).get("tm_celsius"), 2)
        if forward_tm is not None and reverse_tm is not None:
            primer_delta = round(abs(forward_tm - reverse_tm), 2)
    thermodynamics_score = (
        dimensions.get("thermodynamics") or {}
    ).get("raw_score")
    specificity_score = (dimensions.get("specificity") or {}).get("raw_score")
    if specificity_score is None:
        specificity_score = (candidate.get("specificity") or {}).get("specificity_score")
    amplicon_gc = _delivery_amplicon_gc(candidate)
    amplicon_feature_score = (
        dimensions.get("amplicon_features") or {}
    ).get("raw_score")
    coverage = _delivery_target_coverage(candidate)
    score_label = _delivery_score_label(candidate)
    verdict_key = str(candidate.get("verdict") or "").upper()
    status_class = "attention" if verdict_key == "PASS_WITH_WARNINGS" else "recommended"
    metrics = "".join(
        [
            _delivery_metric("扩增子长度", f"{amplicon.get('length_bp')} bp" if amplicon.get("length_bp") is not None else "未提供"),
            _delivery_metric("扩增子 GC", f"{amplicon_gc:.1f}%" if amplicon_gc is not None else "未评估"),
            _delivery_metric(score_label, _delivery_score(candidate.get("score")), primary=True),
            _delivery_metric("热力学评分", _delivery_score(thermodynamics_score)),
            _delivery_metric("特异性评分", _delivery_score(specificity_score)),
            _delivery_metric("扩增子特征评分", _delivery_score(amplicon_feature_score)),
            _delivery_metric("BLAST 查询覆盖率", _delivery_percent(coverage)),
            _delivery_metric("保守性评分", _delivery_conservation(candidate)),
            _delivery_metric("两条引物温差", f"{primer_delta:.2f}°C" if primer_delta is not None else "未评估"),
        ]
    )
    score_breakdown = _delivery_score_breakdown(candidate)
    check_panel = _delivery_check_panel(candidate)
    return f"""
      <article class="candidate-card">
        <div class="candidate-heading">
          <div><span class="rank">{ordinal:02d}</span><h3>候选方案 {ordinal:02d}</h3></div>
          <div class="candidate-result"><span class="candidate-status {status_class}">{html.escape(_delivery_status(candidate.get('verdict')))}</span><strong>{html.escape(_delivery_score(candidate.get('score')))}</strong></div>
        </div>
        {_delivery_candidate_parameter_table(candidate, target_gene_id=target_gene_id)}
        <div class="metrics">{metrics}</div>
        {score_breakdown}
        {check_panel}
        {_candidate_thermodynamics_panel_html(candidate)}
        {_candidate_specificity_panel_html(candidate)}
      </article>
    """


def render_html(report: dict[str, Any]) -> bytes:
    """Render the concise, user-facing delivery report with top-10 details."""
    overview = report["batch_overview"]
    target_sections: list[str] = []
    target_names = [_delivery_target_name(target) for target in report["targets"]]
    report_candidates = [
        candidate
        for target in report["targets"]
        for candidate in list(target.get("top10") or [])[:10]
    ]
    report_score_label = _delivery_candidates_score_label(report_candidates)
    scoring_policy = _delivery_scoring_policy(report_candidates)
    original_sequences = _delivery_original_sequences(report["targets"])
    displayed_total = 0
    for target in report["targets"]:
        target_name = _delivery_target_name(target)
        target_input = target.get("input") or {}
        template = target_input.get("template") or {}
        source_file = str(template.get("source_file") or "").strip()
        source_file_name = (
            source_file.replace("\\", "/").rsplit("/", 1)[-1]
            if source_file
            else "未提供"
        )
        candidates = list(target.get("top10") or [])[:10]
        attention_count = sum(
            1
            for candidate in candidates
            if str(candidate.get("verdict") or "").upper() == "PASS_WITH_WARNINGS"
            or bool(candidate.get("warnings"))
        )
        fully_passed_count = sum(
            1
            for candidate in candidates
            if str(candidate.get("verdict") or "").upper() == "PASS"
            and not candidate.get("warnings")
        )
        displayed_total += len(candidates)
        target_gene_id = str(
            template.get("record_name")
            or target.get("target_id")
            or "未提供"
        )
        score_label = _delivery_candidates_score_label(candidates)
        summary_table = _delivery_candidate_summary_table(candidates)
        cards = "".join(
            _delivery_candidate_card(
                candidate,
                index,
                target_gene_id=target_gene_id,
            )
            for index, candidate in enumerate(candidates, start=1)
        ) or '<div class="empty-state">当前没有可展示的候选方案。</div>'
        candidate_notice = (target.get("conclusion") or {}).get(
            "candidate_notice"
        ) or {}
        shortfall_notice = (
            '<div class="candidate-shortfall"><strong>候选不足</strong>'
            f'<p>{html.escape(str(candidate_notice.get("message") or ""))}</p></div>'
            if candidate_notice.get("status") == "INSUFFICIENT"
            else ""
        )
        target_sections.append(
            f"""
            <section class="target-section">
              <div class="target-heading">
                <div><span class="section-kicker">设计对象</span><h2>{html.escape(target_name)}</h2></div>
                <span class="target-status">{html.escape(_delivery_status((target.get('conclusion') or {}).get('status')))}</span>
              </div>
              <div class="target-summary">
                <div><span>宿主</span><strong>{html.escape(_delivery_host_names(target))}</strong></div>
                <div><span>输入文件</span><strong class="input-file-name">{html.escape(source_file_name)}</strong></div>
                <div><span>FASTA 序列记录</span><strong>{html.escape(str(template.get('record_name') or '未提供'))}</strong></div>
                <div><span>输入序列长度</span><strong>{html.escape(str(template.get('length_nt') or '-'))} nt</strong></div>
                <div><span>可交付候选</span><strong>{html.escape(str((target.get('candidate_counts') or {}).get('deliverable') or 0))} 套</strong></div>
              </div>
              {shortfall_notice}
              <div class="section-intro"><div><h2>优先候选方案列表</h2><p>按{html.escape(score_label)}排序，先集中展示前 10 套引物、探针、评分和筛选结论。</p></div><span>全部通过 {fully_passed_count} 套 · 需关注 {attention_count} 套</span></div>
              {summary_table}
              <div class="section-intro detail-intro"><div><h2>候选方案明细</h2><p>按上方列表顺序，逐套展示序列、评分构成、检测结果和关键科学数据。</p></div><span>共 {len(candidates)} 套</span></div>
              <div class="candidate-list">{cards}</div>
            </section>
            """
        )
    subject = "、".join(target_names[:3]) or "本次任务"
    generated_date = str(report.get("generated_at") or "").split("T", 1)[0] or "-"
    deliverable_total = (overview.get("candidate_counts") or {}).get("deliverable") or 0
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="agentd-report-id" content="{html.escape(report['report_id'])}">
  <meta name="agentd-content-sha256" content="{html.escape(report['content_sha256'])}">
  <link rel="icon" href="data:,">
  <title>{html.escape(subject)}引物与探针设计报告</title>
  <style>
    :root{{--ink:#152033;--muted:#667085;--line:#dce3ee;--brand:#2458d3;--brand-soft:#edf3ff;--page:#f4f7fb;--good:#16794f;--good-soft:#e9f8f0;--attention:#a55b0a;--attention-soft:#fff4df}}
    *{{box-sizing:border-box}} body{{margin:0;background:var(--page);color:var(--ink);font-family:"Microsoft YaHei","Noto Sans CJK SC",Arial,sans-serif;line-height:1.55}}
    main{{max-width:1080px;margin:0 auto;padding:38px 22px 72px}} .report-header,.target-section,.notice{{background:#fff;border:1px solid var(--line);border-radius:18px;box-shadow:0 10px 28px rgba(27,48,86,.06)}}
    .report-header{{padding:32px;margin-bottom:22px}} .kicker,.section-kicker{{color:var(--brand);font-size:13px;font-weight:700;letter-spacing:.12em}} h1,h2,h3,p{{margin-top:0}} h1{{font-size:32px;margin:.25em 0 .35em}} .lead{{color:var(--muted);font-size:16px;margin-bottom:24px}}
    .overview{{display:grid;grid-template-columns:minmax(185px,1.25fr) repeat(4,minmax(0,1fr));gap:12px}} .overview>div,.target-summary>div{{background:#f7f9fc;border:1px solid #edf0f5;border-radius:12px;padding:13px 15px;min-width:0}} .overview span,.target-summary span{{display:block;color:var(--muted);font-size:12px;margin-bottom:3px}} .overview strong,.target-summary strong{{display:block;font-size:16px;overflow-wrap:anywhere}} .overview strong.task-code{{font-size:13px;white-space:nowrap;letter-spacing:-.02em}}
    .original-sequences{{margin-top:18px;border:1px solid #d5e1f8;border-radius:12px;background:#fbfdff;overflow:hidden}} .original-sequences-heading{{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;padding:13px 15px;background:#f1f6ff}} .original-sequences-heading span{{display:block;color:var(--muted);font-size:10px}} .original-sequences-heading h2{{margin:2px 0 0;font-size:16px}} .original-sequences-heading small{{color:var(--muted);text-align:right}} .original-sequence-item{{padding:12px 15px;border-top:1px solid #e2e8f3}} .original-sequence-item:first-of-type{{border-top:0}} .original-sequence-meta{{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:7px}} .original-sequence-meta strong{{font-size:13px}} .original-sequence-meta span{{color:var(--muted);font-size:11px;white-space:nowrap}} .original-sequence{{font-family:"Cascadia Mono","Consolas",monospace;color:#203956;background:#fff;border:1px solid #e5eaf2;border-radius:8px;padding:10px 12px;font-size:12px;line-height:1.75;letter-spacing:.045em;overflow-wrap:anywhere;word-break:break-all;user-select:all}}
    .report-note{{margin-top:18px;padding:13px 15px;border-left:4px solid var(--brand);background:var(--brand-soft);border-radius:8px;color:#29466f}} .target-section{{padding:28px;margin-bottom:22px}} .target-heading,.section-intro,.candidate-heading{{display:flex;justify-content:space-between;align-items:flex-start;gap:18px}} .target-heading h2{{font-size:25px;margin:.25em 0 0}} .target-status{{background:var(--brand-soft);color:var(--brand);border-radius:999px;padding:7px 12px;font-size:12px;font-weight:700}}
    .target-summary{{display:grid;grid-template-columns:minmax(0,.8fr) minmax(0,1.3fr) minmax(0,1.05fr) minmax(0,.9fr) minmax(0,.9fr);gap:10px;margin:20px 0 28px}} .target-summary strong.input-file-name{{font-size:14px;letter-spacing:-.01em}} .section-intro{{align-items:center;border-top:1px solid var(--line);padding-top:24px;margin-bottom:14px}} .section-intro.detail-intro{{margin-top:28px}} .section-intro h2{{font-size:21px;margin-bottom:4px}} .section-intro p{{color:var(--muted);margin:0}} .section-intro>span{{white-space:nowrap;background:#f2f4f7;border-radius:999px;padding:6px 11px;font-size:12px}}
    .candidate-summary-table{{width:100%;max-width:100%;min-width:0;overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:#fff}} .candidate-summary-table table{{width:100%;min-width:900px;border-collapse:collapse;font-size:12px}} .candidate-summary-table th,.candidate-summary-table td{{padding:10px 11px;border-bottom:1px solid #e8edf3;text-align:left;vertical-align:middle}} .candidate-summary-table thead th{{background:#f1f6ff;color:#52637d;white-space:nowrap}} .candidate-summary-table tbody th{{white-space:nowrap;color:var(--brand)}} .candidate-summary-table tbody tr:nth-child(even){{background:#fafcff}} .candidate-summary-table tbody tr:last-child>th,.candidate-summary-table tbody tr:last-child>td{{border-bottom:0}} .summary-sequence{{font-family:"Cascadia Mono","Consolas",monospace;color:#203956;white-space:nowrap;font-size:11px;letter-spacing:.015em}} .summary-score{{color:var(--brand);font-size:13px;white-space:nowrap}}
    .candidate-list{{display:grid;gap:16px;min-width:0;max-width:100%}} .candidate-card{{min-width:0;max-width:100%;border:1px solid var(--line);border-radius:15px;padding:20px;background:#fff;overflow:hidden}} .candidate-heading{{min-width:0}} .candidate-heading>div:first-child{{display:flex;align-items:center;gap:10px;min-width:0}} .candidate-heading h3{{margin:0;font-size:19px}} .rank{{display:grid;place-items:center;flex:0 0 30px;width:30px;height:30px;border-radius:50%;background:var(--brand);color:#fff;font-size:12px;font-weight:800}}
    .candidate-result{{display:flex;flex:0 0 auto;align-items:center;gap:10px;white-space:nowrap}} .candidate-result>strong{{color:var(--brand);font-size:18px}} .candidate-status{{border-radius:999px;padding:5px 9px;font-size:12px;font-weight:700}} .candidate-status.recommended{{background:var(--good-soft);color:var(--good)}} .candidate-status.attention{{background:var(--attention-soft);color:var(--attention)}}
    .candidate-parameter-detail{{width:100%;max-width:100%;min-width:0;margin:16px 0;border:1px solid #dfe6ef;border-radius:12px;overflow:hidden;background:#fff}} .candidate-parameter-heading{{padding:11px 13px;background:#f1f6ff}} .candidate-parameter-heading span{{display:block;color:var(--muted);font-size:10px}} .candidate-parameter-heading h4{{margin:2px 0 0;font-size:15px}} .candidate-data-table-wrap{{width:100%;max-width:100%;min-width:0;overflow-x:auto}} .candidate-data-table{{width:100%;min-width:720px;table-layout:fixed;border-collapse:collapse;font-size:12px}} .candidate-data-table th,.candidate-data-table td{{padding:9px 10px;border-top:1px solid #e8edf3;text-align:left;vertical-align:middle}} .candidate-data-table th{{width:13%;background:#f8fafc;color:#52637d;white-space:nowrap}} .candidate-data-table td{{width:20.33%;color:#1f3652;overflow-wrap:anywhere}} .detail-sequence{{font-family:"Cascadia Mono","Consolas",monospace;letter-spacing:.02em;overflow-wrap:anywhere;word-break:break-all}} .detail-score{{color:var(--brand);font-size:14px;white-space:nowrap}} .detail-amplicon{{padding:11px 13px;border-top:1px solid #e8edf3;background:#fbfcfe}} .detail-amplicon>span{{display:block;color:var(--muted);font-size:11px;margin-bottom:5px}}
    .metrics{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:16px 0}} .metric{{background:#f7f9fc;border-radius:10px;padding:10px 12px;min-width:0}} .metric.primary{{background:var(--brand-soft)}} .metric span{{display:block;color:var(--muted);font-size:11px}} .metric strong{{display:block;font-size:14px;overflow-wrap:anywhere}}
    .scoring-policy{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:12px}} .scoring-policy>div{{border:1px solid #d5e1f8;background:#f8fbff;border-radius:10px;padding:10px 12px}} .scoring-policy span{{display:block;color:var(--muted);font-size:10px}} .scoring-policy strong{{display:block;font-size:13px;margin-top:2px}} .scoring-policy .formula{{grid-column:span 4;background:#f2f6ff;color:#29466f}}
    .score-panel{{margin:0 0 16px;border:1px solid #d5e1f8;border-radius:12px;background:#fbfdff;overflow:hidden}} .score-panel-heading{{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;padding:13px 14px;background:#f1f6ff}} .score-panel-heading span{{display:block;color:var(--muted);font-size:10px}} .score-panel-heading h4{{margin:2px 0 0;font-size:15px}} .score-panel-heading small{{color:#667085;text-align:right}} .dimension-scores{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;padding:12px 14px}} .score-dimension{{border:1px solid #e2e8f3;border-radius:9px;padding:9px 10px;background:#fff}} .score-dimension span{{display:block;color:var(--muted);font-size:10px}} .score-dimension strong{{display:block;font-size:14px;margin-top:2px}} .score-equation{{display:grid;grid-template-columns:1fr auto 1fr auto 1fr;gap:9px;align-items:center;margin:0 14px 12px;padding:11px 12px;border-radius:9px;background:#f5f7fa}} .score-equation span{{display:block;color:var(--muted);font-size:10px}} .score-equation strong{{display:block;font-size:13px}} .score-equation b{{color:#98a2b3}} .score-equation .final strong{{color:var(--brand);font-size:15px}} .penalty-list{{list-style:none;margin:0;padding:0 14px 13px}} .penalty-list li{{display:flex;justify-content:space-between;gap:12px;padding:7px 10px;margin-top:5px;border-radius:8px;background:var(--attention-soft);color:#6f4c1f;font-size:12px}} .no-penalty{{margin:0 14px 13px;padding:7px 10px;border-radius:8px;background:var(--good-soft);color:var(--good);font-size:12px}}
    .check-panel{{margin:16px 0;border:1px solid #dfe6ef;border-radius:12px;overflow:hidden}} .check-heading{{display:flex;justify-content:space-between;gap:16px;align-items:center;padding:13px 14px;background:#f7f9fc}} .check-heading span{{color:var(--muted);font-size:11px}} .check-heading h4{{margin:1px 0 0;font-size:16px}} .check-counts{{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}} .check-counts span{{border-radius:999px;padding:4px 8px;font-weight:700}} .check-counts .pass-count{{background:var(--good-soft);color:var(--good)}} .check-counts .attention-count{{background:var(--attention-soft);color:var(--attention)}} .check-counts .fail-count{{background:#fff0f0;color:#b42318}}
    .passed-checks,.check-issues{{padding:13px 14px;border-top:1px solid #e8edf3}} .passed-checks h5,.check-issues h5{{margin:0 0 9px;font-size:12px;color:var(--muted)}} .passed-checks ul,.check-issues ul{{list-style:none;padding:0;margin:0}} .passed-checks ul{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px 14px}} .passed-checks li{{font-size:12px;color:#344054}} .passed-checks li>span{{color:var(--good);font-weight:900;margin-right:6px}} .check-issues li{{display:grid;gap:2px;padding:8px 10px;border-radius:8px;margin-top:6px;font-size:12px}} .check-issues li span{{color:#5f4930}} .attention-checks li{{background:var(--attention-soft)}} .failed-checks li{{background:#fff0f0}} .pending-checks li{{background:#f2f4f7}} .oligos{{border:1px solid #e6eaf0;border-radius:12px;overflow:hidden}} .oligo-row{{padding:12px 14px;border-bottom:1px solid #e6eaf0}} .oligo-row:last-child{{border-bottom:0}} .oligo-title{{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:7px}} .oligo-title span{{color:var(--muted);font-size:12px;text-align:right}} .sequence{{font-family:"Cascadia Mono","Consolas",monospace;background:#f7f9fc;border-radius:7px;padding:8px 10px;word-break:break-all;letter-spacing:.035em;font-size:13px}} .amplicon-row .sequence{{color:#334155}}
    .evidence-panel{{margin:16px 0;border:1px solid #dfe6ef;border-radius:12px;overflow:hidden;background:#fff}} .evidence-heading{{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;padding:13px 14px;background:#f7f9fc}} .evidence-heading span{{display:block;color:var(--muted);font-size:10px}} .evidence-heading h4{{margin:2px 0 0;font-size:15px}} .evidence-heading small{{max-width:430px;color:var(--muted);text-align:right}} .evidence-panel .table-wrap{{overflow-x:auto}} .evidence-table{{width:100%;border-collapse:collapse;font-size:12px}} .evidence-table th,.evidence-table td{{padding:9px 10px;border-top:1px solid #e8edf3;text-align:left;vertical-align:top}} .evidence-table thead th{{border-top:0;background:#fbfcfe;color:#667085}} .evidence-table th[scope=row]{{min-width:120px;color:#344054}} .evidence-table .status{{display:inline-flex;border-radius:999px;padding:3px 7px;font-size:11px;font-weight:700;white-space:nowrap}} .evidence-table .status.pass{{background:var(--good-soft);color:var(--good)}} .evidence-table .status.fail{{background:#fff0f0;color:#b42318}} .evidence-table .status.warning{{background:var(--attention-soft);color:var(--attention)}} .evidence-table .status.neutral{{background:#f2f4f7;color:#667085}} .pair-dg-grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:12px 14px}} .pair-dg-grid>div{{padding:9px 10px;border-radius:8px;background:#f7f9fc}} .pair-dg-grid span{{display:block;color:var(--muted);font-size:10px}} .pair-dg-grid strong{{display:block;margin-top:2px;font-size:13px}}
    .empty-state{{padding:30px;text-align:center;color:var(--muted);background:#f8fafc;border-radius:12px}} .candidate-shortfall{{margin:-10px 0 26px;padding:14px 16px;border:1px solid #f1d5a6;border-radius:10px;background:#fffaf2;color:#6f5635}} .candidate-shortfall strong{{color:var(--attention)}} .candidate-shortfall p{{margin:4px 0 0}} .notice{{padding:22px 26px;background:#fffaf2;border-color:#f1d5a6}} .notice h2{{font-size:18px;margin-bottom:7px}} .notice p{{margin:0;color:#6f5635}} footer{{text-align:center;color:var(--muted);font-size:12px;padding-top:22px}}
    @media(max-width:760px){{.overview,.target-summary,.metrics,.scoring-policy,.pair-dg-grid{{grid-template-columns:1fr 1fr}} .overview strong.task-code{{white-space:normal}} .scoring-policy .formula{{grid-column:span 2}} .dimension-scores{{grid-template-columns:1fr}} .target-heading,.section-intro,.candidate-heading,.check-heading,.score-panel-heading,.evidence-heading,.original-sequences-heading{{align-items:stretch;flex-direction:column}} .candidate-result{{justify-content:space-between}} .check-counts{{justify-content:flex-start}} .oligo-title{{align-items:flex-start;flex-direction:column}} .oligo-title span,.score-panel-heading small,.evidence-heading small,.original-sequences-heading small{{text-align:left}}}}
    @media(max-width:480px){{main{{padding:18px 10px 42px}} .report-header,.target-section{{padding:20px}} .candidate-card{{padding:14px}} .overview,.target-summary,.metrics,.scoring-policy,.passed-checks ul,.score-equation{{grid-template-columns:1fr}} .scoring-policy .formula{{grid-column:auto}} .score-equation b{{display:none}} h1{{font-size:26px}}}}
    @media print{{body{{background:#fff}} main{{max-width:none;padding:8mm}} .report-header,.target-section,.notice{{box-shadow:none;break-inside:avoid}} .candidate-card{{break-inside:avoid}}}}
  </style>
</head>
<body><main>
  <header class="report-header">
    <span class="kicker">引物与探针设计结果</span>
    <h1>{html.escape(subject)}引物与探针设计报告</h1>
    <p class="lead">面向实验方案筛选的简明交付报告</p>
    <div class="overview">
      <div><span>任务编号</span><strong class="task-code">{html.escape(str(report.get('batch_code') or '-'))}</strong></div>
      <div><span>设计对象</span><strong>{html.escape(str(overview.get('target_total') or len(report['targets'])))} 个</strong></div>
      <div><span>可交付候选</span><strong>{html.escape(str(deliverable_total))} 套</strong></div>
      <div><span>本报告展示</span><strong>{displayed_total} 套</strong></div>
      <div><span>生成日期</span><strong>{html.escape(generated_date)}</strong></div>
    </div>
    {original_sequences}
    <div class="report-note">本报告仅展示每个设计对象{html.escape(report_score_label)}排名前 10 的候选方案，并用中文列出与实验筛选直接相关的检测结论。内部规则编号、检索记录和其他过程数据不在本报告中展开。</div>
    {scoring_policy}
  </header>
  {''.join(target_sections)}
  <section class="notice"><h2>使用说明</h2><p>本报告为计算设计与数据库核查结果，尚未经湿实验验证。建议结合扩增效率、标准曲线和实际样本进行验证后再确定最终方案；仅供科研设计与复核使用。</p></section>
  <footer>引物与探针设计交付报告 · {html.escape(generated_date)}</footer>
</main></body></html>"""
    return document.encode("utf-8")


def _xlsx_column_name(index: int) -> str:
    value = index + 1
    output = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        output = chr(65 + remainder) + output
    return output


def _xlsx_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    elif isinstance(value, bool):
        value = "是" if value else "否"
    else:
        value = str(value)
    # XML 1.0 and Excel reject most C0 controls.
    value = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", value)
    return value[:32767]


def _xlsx_status_style(value: Any) -> int:
    normalised = str(value or "").upper()
    if normalised in {
        "PASS",
        "SUCCEEDED",
        "QUALIFIED",
        "COMPLETE",
        "EVALUATED",
        "BLAST_VERIFIED",
    }:
        return 3
    if normalised in {
        "FAIL",
        "FAILED",
        "INCOMPLETE",
        "NOT_EVALUATED",
        "PARTIAL_INCOMPLETE",
    }:
        return 5
    if normalised in {
        "WARNING",
        "CAUTION",
        "PARTIAL",
        "PARTIALLY_SUCCEEDED",
        "NOT_APPLICABLE",
    }:
        return 4
    return 0


def _xlsx_sheet_xml(
    rows: list[list[Any]],
    *,
    widths: list[float] | None = None,
    freeze_header: bool = True,
    autofilter: bool = True,
    status_columns: set[int] | None = None,
) -> bytes:
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    ET.register_namespace("", namespace)
    worksheet = ET.Element(f"{{{namespace}}}worksheet")
    views = ET.SubElement(worksheet, f"{{{namespace}}}sheetViews")
    view = ET.SubElement(
        views,
        f"{{{namespace}}}sheetView",
        {"workbookViewId": "0", "showGridLines": "0"},
    )
    if freeze_header and len(rows) > 1:
        ET.SubElement(
            view,
            f"{{{namespace}}}pane",
            {
                "ySplit": "1",
                "topLeftCell": "A2",
                "activePane": "bottomLeft",
                "state": "frozen",
            },
        )
    if widths:
        columns = ET.SubElement(worksheet, f"{{{namespace}}}cols")
        for index, width in enumerate(widths, start=1):
            ET.SubElement(
                columns,
                f"{{{namespace}}}col",
                {
                    "min": str(index),
                    "max": str(index),
                    "width": str(max(4.0, min(float(width), 80.0))),
                    "customWidth": "1",
                },
            )
    sheet_data = ET.SubElement(worksheet, f"{{{namespace}}}sheetData")
    status_columns = status_columns or set()
    for row_index, row in enumerate(rows, start=1):
        row_element = ET.SubElement(
            sheet_data, f"{{{namespace}}}row", {"r": str(row_index)}
        )
        for column_index, value in enumerate(row):
            reference = f"{_xlsx_column_name(column_index)}{row_index}"
            style = 2 if row_index == 1 else (
                _xlsx_status_style(value)
                if column_index in status_columns
                else 6
            )
            attributes = {"r": reference, "s": str(style)}
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
            ):
                cell = ET.SubElement(
                    row_element, f"{{{namespace}}}c", attributes
                )
                ET.SubElement(cell, f"{{{namespace}}}v").text = str(value)
            else:
                attributes["t"] = "inlineStr"
                cell = ET.SubElement(
                    row_element, f"{{{namespace}}}c", attributes
                )
                inline = ET.SubElement(cell, f"{{{namespace}}}is")
                text_element = ET.SubElement(
                    inline,
                    f"{{{namespace}}}t",
                    {"{http://www.w3.org/XML/1998/namespace}space": "preserve"},
                )
                text_element.text = _xlsx_text(value)
    if rows and rows[0] and autofilter:
        ET.SubElement(
            worksheet,
            f"{{{namespace}}}autoFilter",
            {
                "ref": (
                    f"A1:{_xlsx_column_name(len(rows[0]) - 1)}"
                    f"{max(1, len(rows))}"
                )
            },
        )
    ET.SubElement(
        worksheet,
        f"{{{namespace}}}pageMargins",
        {
            "left": "0.3",
            "right": "0.3",
            "top": "0.5",
            "bottom": "0.5",
            "header": "0.2",
            "footer": "0.2",
        },
    )
    return ET.tostring(worksheet, encoding="utf-8", xml_declaration=True)


def _xlsx_styles_xml() -> bytes:
    # Seven compact styles: normal, title, header, good, caution, bad, wrapped.
    return b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="3">
    <font><sz val="10"/><name val="Microsoft YaHei"/></font>
    <font><b/><sz val="15"/><color rgb="FFFFFFFF"/><name val="Microsoft YaHei"/></font>
    <font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Microsoft YaHei"/></font>
  </fonts>
  <fills count="6">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF087C57"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFE8F3EC"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFFFF0D9"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFFFE5E0"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="2">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border><left/><right/><top/><bottom style="thin"><color rgb="FFD8E3DC"/></bottom><diagonal/></border>
  </borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="7">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="top"/></xf>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center"/></xf>
    <xf numFmtId="0" fontId="2" fillId="2" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="0" fillId="3" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="0" fillId="4" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="0" fillId="5" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def _xlsx_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    overview = report["batch_overview"]
    sheets: list[dict[str, Any]] = []
    sheets.append(
        {
            "name": "批次总览",
            "rows": [
                ["字段", "值"],
                ["报告 ID", report["report_id"]],
                ["数据摘要 SHA256", report["content_sha256"]],
                ["任务名称", overview.get("task_name")],
                ["任务编号", report.get("batch_code")],
                ["内部批次 ID", report["batch_id"]],
                ["任务版本", report.get("task_version")],
                ["版本差异状态", (report.get("version_diff") or {}).get("status")],
                ["来源批次 ID", (report.get("version_diff") or {}).get("source_batch_id")],
                ["来源版本", (report.get("version_diff") or {}).get("source_version")],
                ["复用靶标", (report.get("version_diff") or {}).get("reused_target_ids")],
                ["必须重跑靶标", (report.get("version_diff") or {}).get("rerun_target_ids")],
                ["版本字段差异", (report.get("version_diff") or {}).get("changes")],
                ["批次状态", overview.get("status")],
                ["搜索完整", overview.get("search_complete")],
                ["技术搜索完整", overview.get("technical_search_complete")],
                ["靶标总数", overview.get("target_total")],
                ["报告完整性", report["completeness"]["status"]],
                ["生成时间", report["generated_at"]],
                ["数据范围", "COMPLETE_BATCH"],
                ["前端筛选可裁剪导出", "否"],
            ],
            "widths": [28, 72],
            "status_columns": {1},
        }
    )
    sheets.append(
        {
            "name": "靶标索引",
            "rows": [
                [
                    "靶标 ID",
                    "顺序",
                    "记录名",
                    "状态",
                    "状态原因",
                    "搜索完整",
                    "技术搜索完整",
                    "Top10 数",
                    "可交付数",
                    "合格数",
                    "证据不完整数",
                ],
                *[
                    [
                        item.get("target_id"),
                        item.get("ordinal"),
                        item.get("record_name"),
                        item.get("status"),
                        item.get("status_reason"),
                        item.get("search_complete"),
                        item.get("technical_search_complete"),
                        (item.get("candidate_counts") or {}).get("top10"),
                        (item.get("candidate_counts") or {}).get(
                            "deliverable"
                        ),
                        (item.get("candidate_counts") or {}).get("qualified"),
                        (item.get("candidate_counts") or {}).get(
                            "incomplete_evidence"
                        ),
                    ]
                    for item in report["target_index"]
                ],
            ],
            "widths": [18, 8, 24, 22, 42, 14, 16, 12, 12, 12, 18],
            "status_columns": {3},
        }
    )

    candidate_headers = [
        "靶标 ID",
        "排名",
        "正式排名",
        "候选 ID",
        "正向引物 5'-3'",
        "反向引物 5'-3'",
        "探针 5'-3'",
        "扩增子 bp",
        "得分",
        "判定",
        "证据等级",
        "成员集合",
        "排除类别",
    ]

    def candidate_row(target_id: str, candidate: dict[str, Any]) -> list[Any]:
        membership = candidate.get("report_membership") or {}
        return [
            target_id,
            candidate.get("display_rank"),
            candidate.get("rank"),
            candidate.get("candidate_id"),
            (candidate.get("forward_primer") or {}).get("sequence_5to3"),
            (candidate.get("reverse_primer") or {}).get("sequence_5to3"),
            (candidate.get("probe") or {}).get("sequence_5to3"),
            (candidate.get("amplicon") or {}).get("length_bp"),
            candidate.get("score"),
            candidate.get("verdict"),
            candidate.get("evidence_level"),
            ",".join(membership.get("collections") or []),
            ",".join(membership.get("exclusion_categories") or []),
        ]

    for name, key in (
        ("Top10", "top10"),
        ("全部合格", "all_qualified_candidates"),
        ("全部排除", "all_excluded_candidates"),
        ("全部候选", "all_primer3_candidates"),
    ):
        rows = [candidate_headers]
        for target in report["targets"]:
            rows.extend(
                candidate_row(str(target["target_id"]), candidate)
                for candidate in target[key]
            )
        sheets.append(
            {
                "name": name,
                "rows": rows,
                "widths": [
                    18,
                    9,
                    10,
                    28,
                    28,
                    28,
                    34,
                    12,
                    12,
                    24,
                    20,
                    30,
                    30,
                ],
                "status_columns": {9, 10},
            }
        )

    rule_rows = [
        [
            "靶标 ID",
            "候选 ID",
            "规则",
            "类别",
            "状态",
            "评价状态",
            "实际值",
            "阈值",
            "来源",
            "说明",
        ]
    ]
    for target in report["targets"]:
        for candidate in target["all_primer3_candidates"]:
            for rule in candidate.get("rules") or []:
                rule_rows.append(
                    [
                        target["target_id"],
                        candidate.get("candidate_id"),
                        rule.get("code"),
                        rule.get("category"),
                        rule.get("status"),
                        rule.get("evaluation_state"),
                        rule.get("actual"),
                        rule.get("threshold"),
                        rule.get("source"),
                        rule.get("message"),
                    ]
                )
    sheets.append(
        {
            "name": "规则明细",
            "rows": rule_rows,
            "widths": [16, 26, 9, 16, 18, 18, 44, 38, 26, 46],
            "status_columns": {4, 5},
        }
    )

    hit_rows = [
        [
            "靶标 ID",
            "候选 ID",
            "模式",
            "数据库键",
            "版本",
            "阈值",
            "命中详情",
        ]
    ]
    for target in report["targets"]:
        for item in target["specificity_evidence"]["hit_evidence"]:
            hit_rows.append(
                [
                    target["target_id"],
                    item.get("candidate_id"),
                    item.get("mode"),
                    item.get("database_resource_key"),
                    item.get("database_version"),
                    item.get("thresholds"),
                    item.get("hit"),
                ]
            )
    sheets.append(
        {
            "name": "特异性命中",
            "rows": hit_rows,
            "widths": [16, 26, 24, 34, 20, 48, 80],
            "status_columns": set(),
        }
    )

    search_rows = [["靶标 ID", "记录类型", "完整记录 JSON"]]
    for target in report["targets"]:
        for key, value in target["search_records"].items():
            search_rows.append([target["target_id"], key, value])
    search_rows.append(
        ["BATCH", "batch_search_records", report["batch_search_records"]]
    )
    sheets.append(
        {
            "name": "搜索记录",
            "rows": search_rows,
            "widths": [18, 32, 80],
            "status_columns": set(),
        }
    )

    target_input_rows = [
        ["靶标 ID", "输入类别", "内容 JSON / 序列分块"]
    ]
    for target in report["targets"]:
        template = copy.deepcopy(target["input"].get("template") or {})
        sequence = str(template.pop("sequence", "") or "")
        target_input_rows.append(
            [target["target_id"], "template_metadata", template]
        )
        for offset in range(0, len(sequence), 30000):
            target_input_rows.append(
                [
                    target["target_id"],
                    f"template_sequence_{offset // 30000 + 1:04d}",
                    sequence[offset : offset + 30000],
                ]
            )
        for key in (
            "parameters",
            "issues",
            "target_object",
            "host_objects",
            "related_objects",
            "background_objects",
        ):
            target_input_rows.append(
                [target["target_id"], key, target["input"].get(key)]
            )
    sheets.append(
        {
            "name": "靶标输入",
            "rows": target_input_rows,
            "widths": [18, 32, 80],
            "status_columns": set(),
        }
    )

    sheets.append(
        {
            "name": "免责声明",
            "rows": [
                ["字段", "值"],
                ["证据范围", report["disclaimer"]["evidence_scope"]],
                [
                    "已实验验证",
                    report["disclaimer"]["experimental_validation"],
                ],
                ["仅供科研", report["disclaimer"]["research_use_only"]],
                ["声明", report["disclaimer"]["text"]],
                ["本期外能力", report["out_of_scope"]],
            ],
            "widths": [28, 80],
            "status_columns": set(),
        }
    )

    canonical = json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    canonical_rows: list[list[Any]] = [
        ["分块序号", "Canonical report JSON（按序拼接，不得按界面筛选裁剪）"]
    ]
    for index, offset in enumerate(range(0, len(canonical), 30000), start=1):
        canonical_rows.append([index, canonical[offset : offset + 30000]])
    sheets.append(
        {
            "name": "Canonical JSON",
            "rows": canonical_rows,
            "widths": [12, 80],
            "status_columns": set(),
        }
    )
    return sheets


def render_xlsx(report: dict[str, Any]) -> bytes:
    """Create a dependency-free Office Open XML workbook from the report."""
    sheets = _xlsx_rows(report)
    workbook_ns = (
        "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    )
    rel_ns = (
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    )
    ET.register_namespace("", workbook_ns)
    ET.register_namespace("r", rel_ns)
    workbook = ET.Element(f"{{{workbook_ns}}}workbook")
    book_views = ET.SubElement(workbook, f"{{{workbook_ns}}}bookViews")
    ET.SubElement(
        book_views,
        f"{{{workbook_ns}}}workbookView",
        {"activeTab": "0"},
    )
    sheets_element = ET.SubElement(workbook, f"{{{workbook_ns}}}sheets")
    for index, sheet in enumerate(sheets, start=1):
        ET.SubElement(
            sheets_element,
            f"{{{workbook_ns}}}sheet",
            {
                "name": sheet["name"][:31],
                "sheetId": str(index),
                f"{{{rel_ns}}}id": f"rId{index}",
            },
        )

    content_types = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
    ]
    content_types.extend(
        (
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
        for index in range(1, len(sheets) + 1)
    )
    content_types.append("</Types>")
    root_rels = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
    workbook_rels = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
    ]
    workbook_rels.extend(
        (
            f'<Relationship Id="rId{index}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{index}.xml"/>'
        )
        for index in range(1, len(sheets) + 1)
    )
    workbook_rels.append(
        f'<Relationship Id="rId{len(sheets) + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    workbook_rels.append("</Relationships>")

    output = BytesIO()
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        archive.writestr("[Content_Types].xml", "".join(content_types))
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr(
            "xl/workbook.xml",
            ET.tostring(
                workbook, encoding="utf-8", xml_declaration=True
            ),
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels", "".join(workbook_rels)
        )
        archive.writestr("xl/styles.xml", _xlsx_styles_xml())
        for index, sheet in enumerate(sheets, start=1):
            archive.writestr(
                f"xl/worksheets/sheet{index}.xml",
                _xlsx_sheet_xml(
                    sheet["rows"],
                    widths=sheet["widths"],
                    status_columns=sheet["status_columns"],
                ),
            )
    return output.getvalue()


def render_csv(report: dict[str, Any]) -> bytes:
    """Render the complete candidate result set as an Excel-friendly CSV."""
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(
        [
            "报告 ID",
            "数据摘要 SHA256",
            "任务编号",
            "内部批次 ID",
            "靶标 ID",
            "靶标状态",
            "显示排名",
            "正式排名",
            "候选 ID",
            "正向引物 5'-3'",
            "反向引物 5'-3'",
            "探针 5'-3'",
            "扩增子 bp",
            "得分",
            "判定",
            "证据等级",
            "成员集合",
            "排除类别",
        ]
    )
    for target in report.get("targets") or []:
        target_id = target.get("target_id")
        target_status = (target.get("conclusion") or {}).get("status")
        candidates = target.get("all_primer3_candidates") or [None]
        for candidate in candidates:
            candidate = candidate or {}
            membership = candidate.get("report_membership") or {}
            writer.writerow(
                [
                    report.get("report_id"),
                    report.get("content_sha256"),
                    report.get("batch_code"),
                    report.get("batch_id"),
                    target_id,
                    target_status,
                    candidate.get("display_rank"),
                    candidate.get("rank"),
                    candidate.get("candidate_id"),
                    (candidate.get("forward_primer") or {}).get(
                        "sequence_5to3"
                    ),
                    (candidate.get("reverse_primer") or {}).get(
                        "sequence_5to3"
                    ),
                    (candidate.get("probe") or {}).get("sequence_5to3"),
                    (candidate.get("amplicon") or {}).get("length_bp"),
                    candidate.get("score"),
                    candidate.get("verdict"),
                    candidate.get("evidence_level"),
                    ",".join(membership.get("collections") or []),
                    ",".join(
                        membership.get("exclusion_categories") or []
                    ),
                ]
            )
    # UTF-8 BOM keeps Chinese headers readable in common spreadsheet clients.
    return output.getvalue().encode("utf-8-sig")


def _pdf_font_names() -> tuple[str, str]:
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError as exc:  # pragma: no cover - dependency boundary
        raise ToolUnavailableError(
            "PDF 导出依赖 reportlab；请安装项目 api 依赖"
        ) from exc

    candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/msyhbd.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path(
            "/usr/share/fonts/opentype/noto/"
            "NotoSansCJKsc-Regular.otf"
        ),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    ]
    regular = candidates[0]
    bold = candidates[1]
    try:
        if regular.exists():
            pdfmetrics.registerFont(
                TTFont("AgentDCJK", str(regular), subfontIndex=0)
            )
            if bold.exists():
                pdfmetrics.registerFont(
                    TTFont("AgentDCJKBold", str(bold), subfontIndex=0)
                )
                return "AgentDCJK", "AgentDCJKBold"
            return "AgentDCJK", "AgentDCJK"
        for path in candidates[2:]:
            if not path.exists():
                continue
            pdfmetrics.registerFont(
                TTFont("AgentDCJK", str(path), subfontIndex=0)
            )
            return "AgentDCJK", "AgentDCJK"
    except Exception:
        # CID fonts keep Chinese legible when no embeddable system font exists.
        pass
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    return "STSong-Light", "STSong-Light"


def _pdf_text(value: Any, *, max_chars: int = 1200) -> str:
    text_value = _xlsx_text(value)
    if len(text_value) > max_chars:
        text_value = (
            text_value[:max_chars]
            + "\n[visible PDF content truncated; complete data is in "
            "canonical-report.json]"
        )
    return html.escape(text_value).replace("\n", "<br/>")


def _pdf_table(
    rows: list[list[Any]],
    *,
    widths: list[float],
    font_name: str,
    repeat_rows: int = 1,
) -> Any:
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, LongTable, TableStyle
    from reportlab.lib.styles import ParagraphStyle

    body_style = ParagraphStyle(
        "AgentDTableBody",
        fontName=font_name,
        fontSize=6.2,
        leading=8,
        wordWrap="CJK",
        textColor=colors.HexColor("#17251F"),
    )
    values = [
        [Paragraph(_pdf_text(value), body_style) for value in row]
        for row in rows
    ]
    table = LongTable(
        values,
        colWidths=widths,
        repeatRows=repeat_rows,
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#087C57")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), font_name),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                (
                    "LINEBELOW",
                    (0, 0),
                    (-1, -1),
                    0.25,
                    colors.HexColor("#D8E3DC"),
                ),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#F6FAF7")],
                ),
            ]
        )
    )
    return table


def render_pdf(report: dict[str, Any]) -> bytes:
    """Render the complete canonical report as a paginated PDF."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
        )
    except ImportError as exc:  # pragma: no cover - dependency boundary
        raise ToolUnavailableError(
            "PDF 导出依赖 reportlab；请安装项目 api 依赖"
        ) from exc

    font_name, bold_font = _pdf_font_names()
    buffer = BytesIO()
    page_size = landscape(A4)
    document = SimpleDocTemplate(
        buffer,
        pagesize=page_size,
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title="AgentD WF-01 PRD v0.2 完整报告",
        author="AgentD",
        subject=report["report_id"],
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "AgentDTitle",
        parent=styles["Title"],
        fontName=bold_font,
        fontSize=20,
        leading=25,
        textColor=colors.HexColor("#087C57"),
        alignment=TA_CENTER,
        spaceAfter=10,
    )
    heading = ParagraphStyle(
        "AgentDHeading",
        parent=styles["Heading2"],
        fontName=bold_font,
        fontSize=13,
        leading=17,
        textColor=colors.HexColor("#17251F"),
        spaceBefore=9,
        spaceAfter=6,
    )
    subheading = ParagraphStyle(
        "AgentDSubheading",
        parent=styles["Heading3"],
        fontName=bold_font,
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#087C57"),
        spaceBefore=7,
        spaceAfter=4,
    )
    body = ParagraphStyle(
        "AgentDBody",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=8,
        leading=11,
        wordWrap="CJK",
        textColor=colors.HexColor("#17251F"),
    )
    story: list[Any] = [
        Paragraph("AgentD WF-01 引物探针设计完整报告", title),
        Paragraph(
            (
                f"报告 ID: {_pdf_text(report['report_id'])}<br/>"
                f"数据摘要: {_pdf_text(report['content_sha256'])}<br/>"
                f"生成时间: {_pdf_text(report['generated_at'])}"
            ),
            body,
        ),
        Spacer(1, 4 * mm),
        _pdf_table(
            [
                ["字段", "值"],
                ["任务名称", report["batch_overview"].get("task_name")],
                ["任务编号", report.get("batch_code")],
                ["任务版本", report.get("task_version")],
                ["版本差异状态", (report.get("version_diff") or {}).get("status")],
                ["来源批次 ID", (report.get("version_diff") or {}).get("source_batch_id")],
                ["来源版本", (report.get("version_diff") or {}).get("source_version")],
                ["复用靶标", (report.get("version_diff") or {}).get("reused_target_ids")],
                ["必须重跑靶标", (report.get("version_diff") or {}).get("rerun_target_ids")],
                ["批次状态", report["batch_overview"].get("status")],
                ["靶标总数", report["batch_overview"].get("target_total")],
                [
                    "搜索完整",
                    report["batch_overview"].get("search_complete"),
                ],
                ["报告完整性", report["completeness"].get("status")],
                ["数据范围", "COMPLETE_BATCH"],
            ],
            widths=[45 * mm, 205 * mm],
            font_name=font_name,
        ),
        Spacer(1, 4 * mm),
        _pdf_table(
            [
                ["靶标 ID", "记录名", "状态", "搜索完整", "可交付候选"],
                *[
                    [
                        item.get("target_id"),
                        item.get("record_name"),
                        item.get("status"),
                        item.get("search_complete"),
                        (item.get("candidate_counts") or {}).get(
                            "deliverable"
                        ),
                    ]
                    for item in report["target_index"]
                ],
            ],
            widths=[35 * mm, 60 * mm, 50 * mm, 45 * mm, 45 * mm],
            font_name=font_name,
        ),
    ]
    for target in report["targets"]:
        story.extend(
            [
                PageBreak(),
                Paragraph(
                    (
                        f"靶标 {html.escape(str(target['target_id']))}: "
                        f"{_pdf_text((target['input'].get('template') or {}).get('record_name'))}"
                    ),
                    heading,
                ),
                _pdf_table(
                    [
                        ["字段", "值"],
                        ["状态", target["conclusion"].get("status")],
                        [
                            "状态原因",
                            target["conclusion"].get("status_reason"),
                        ],
                        [
                            "候选说明",
                            (target["conclusion"].get("candidate_notice") or {}).get(
                                "message"
                            ),
                        ],
                        [
                            "搜索完整",
                            target["conclusion"].get("search_complete"),
                        ],
                        [
                            "技术搜索完整",
                            target["conclusion"].get(
                                "technical_search_complete"
                            ),
                        ],
                        [
                            "不完整原因",
                            target["conclusion"].get(
                                "incomplete_reasons"
                            ),
                        ],
                        ["候选计数", target.get("candidate_counts")],
                    ],
                    widths=[45 * mm, 205 * mm],
                    font_name=font_name,
                ),
                Paragraph("Top10", subheading),
                _pdf_table(
                    [
                        [
                            "排名",
                            "候选 ID",
                            "正向引物",
                            "反向引物",
                            "探针",
                            "bp",
                            "得分",
                            "判定",
                        ],
                        *[
                            [
                                candidate.get("display_rank")
                                or candidate.get("rank"),
                                candidate.get("candidate_id"),
                                (
                                    candidate.get("forward_primer") or {}
                                ).get("sequence_5to3"),
                                (
                                    candidate.get("reverse_primer") or {}
                                ).get("sequence_5to3"),
                                (candidate.get("probe") or {}).get(
                                    "sequence_5to3"
                                ),
                                (candidate.get("amplicon") or {}).get(
                                    "length_bp"
                                ),
                                candidate.get("score"),
                                candidate.get("verdict"),
                            ]
                            for candidate in target["top10"]
                        ],
                    ],
                    widths=[
                        12 * mm,
                        35 * mm,
                        45 * mm,
                        45 * mm,
                        52 * mm,
                        12 * mm,
                        16 * mm,
                        32 * mm,
                    ],
                    font_name=font_name,
                ),
                Paragraph("全部候选及排除/完整性状态", subheading),
                _pdf_table(
                    [
                        [
                            "候选 ID",
                            "成员集合",
                            "排除类别",
                            "R21",
                            "证据等级",
                            "排除原因",
                        ],
                        *[
                            [
                                candidate.get("candidate_id"),
                                (
                                    candidate.get("report_membership") or {}
                                ).get("collections"),
                                (
                                    candidate.get("report_membership") or {}
                                ).get("exclusion_categories"),
                                next(
                                    (
                                        rule.get("status")
                                        for rule in candidate.get("rules")
                                        or []
                                        if rule.get("code") == "R21"
                                    ),
                                    None,
                                ),
                                candidate.get("evidence_level"),
                                candidate.get("exclusion_reasons"),
                            ]
                            for candidate in target[
                                "all_primer3_candidates"
                            ]
                        ],
                    ],
                    widths=[
                        40 * mm,
                        48 * mm,
                        42 * mm,
                        16 * mm,
                        30 * mm,
                        74 * mm,
                    ],
                    font_name=font_name,
                ),
                Paragraph("特异性命中证据", subheading),
                _pdf_table(
                    [
                        [
                            "候选 ID",
                            "模式",
                            "数据库/版本",
                            "Query/Subject",
                            "Identity/Coverage/E-value",
                            "完整命中",
                        ],
                        *[
                            [
                                evidence.get("candidate_id"),
                                evidence.get("mode"),
                                (
                                    f"{evidence.get('database_resource_key')}/"
                                    f"{evidence.get('database_version')}"
                                ),
                                (
                                    f"{(evidence.get('hit') or {}).get('query_id')}/"
                                    f"{(evidence.get('hit') or {}).get('subject_id')}"
                                ),
                                (
                                    f"{(evidence.get('hit') or {}).get('identity_percent')}/"
                                    f"{(evidence.get('hit') or {}).get('coverage_percent')}/"
                                    f"{(evidence.get('hit') or {}).get('evalue')}"
                                ),
                                evidence.get("hit"),
                            ]
                            for evidence in target[
                                "specificity_evidence"
                            ]["hit_evidence"]
                        ],
                    ],
                    widths=[
                        35 * mm,
                        32 * mm,
                        45 * mm,
                        45 * mm,
                        42 * mm,
                        51 * mm,
                    ],
                    font_name=font_name,
                ),
                Paragraph("R01-R21 规则明细", subheading),
                _pdf_table(
                    [
                        [
                            "候选 ID",
                            "规则",
                            "状态",
                            "评价状态",
                            "阈值",
                            "说明",
                        ],
                        *[
                            [
                                candidate.get("candidate_id"),
                                rule.get("code"),
                                rule.get("status"),
                                rule.get("evaluation_state"),
                                rule.get("threshold"),
                                rule.get("message"),
                            ]
                            for candidate in target[
                                "all_primer3_candidates"
                            ]
                            for rule in candidate.get("rules") or []
                        ],
                    ],
                    widths=[
                        42 * mm,
                        13 * mm,
                        27 * mm,
                        27 * mm,
                        63 * mm,
                        78 * mm,
                    ],
                    font_name=font_name,
                ),
                Paragraph("热力学与评分明细", subheading),
                _pdf_table(
                    [
                        [
                            "候选 ID",
                            "热力学元数据",
                            "评分维度/权重/基础分/扣分/最终分/排名",
                        ],
                        *[
                            [
                                candidate.get("candidate_id"),
                                candidate.get("thermodynamic_metadata"),
                                candidate.get("scoring_detail"),
                            ]
                            for candidate in target[
                                "all_primer3_candidates"
                            ]
                        ],
                    ],
                    widths=[42 * mm, 96 * mm, 112 * mm],
                    font_name=font_name,
                ),
                Paragraph("输入与搜索记录", subheading),
                _pdf_table(
                    [
                        ["记录", "完整内容"],
                        ["input", target.get("input")],
                        ["search_records", target.get("search_records")],
                        [
                            "specificity_database_resources",
                            target.get("specificity_evidence", {}).get(
                                "database_resources"
                            ),
                        ],
                    ],
                    widths=[58 * mm, 192 * mm],
                    font_name=font_name,
                ),
            ]
        )
    story.extend(
        [
            PageBreak(),
            Paragraph("证据与用途声明", heading),
            Paragraph(_pdf_text(report["disclaimer"]["text"]), body),
            Paragraph("完整数据与审计", heading),
            Paragraph(
                (
                    "本 PDF 的候选、规则、热力学、评分、特异性和搜索记录均来自同一份 "
                    "canonical report，未应用前端筛选或分页。完整 canonical-report.json "
                    "已作为 PDF 附件嵌入，可按下列 SHA256 核对。"
                ),
                body,
            ),
            _pdf_table(
                [
                    ["字段", "值"],
                    ["报告 ID", report["report_id"]],
                    ["Canonical SHA256", report["content_sha256"]],
                    ["附件名", "canonical-report.json"],
                    ["数据范围", "COMPLETE_BATCH"],
                    ["前端筛选可裁剪导出", "否"],
                ],
                widths=[55 * mm, 195 * mm],
                font_name=font_name,
            ),
        ]
    )

    def page(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont(font_name, 7)
        canvas.setFillColor(colors.HexColor("#66756E"))
        canvas.drawString(
            12 * mm,
            7 * mm,
            f"AgentD WF-01 | {report['report_id']}",
        )
        canvas.drawRightString(
            page_size[0] - 12 * mm,
            7 * mm,
            f"第 {doc.page} 页",
        )
        canvas.restoreState()

    document.build(story, onFirstPage=page, onLaterPages=page)
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as exc:  # pragma: no cover - dependency boundary
        raise ToolUnavailableError(
            "PDF 完整数据附件依赖 pypdf；请安装项目 api 依赖"
        ) from exc
    reader = PdfReader(BytesIO(buffer.getvalue()))
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    writer.add_attachment(
        "canonical-report.json",
        _json_bytes(report, pretty=True),
    )
    completed = BytesIO()
    writer.write(completed)
    return completed.getvalue()


def render_all_exports(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Render all user-visible formats and return bytes plus stable metadata."""
    process_html = render_process_detail_html(report)
    rules_html = render_rules_validation_html(report)
    rendered = {
        "html": {
            "media_type": "text/html; charset=utf-8",
            "extension": "html",
            "content": render_html(report),
        },
        "process_html": {
            "media_type": "text/html; charset=utf-8",
            "extension": "html",
            "content": process_html,
        },
        "rules_html": {
            "media_type": "text/html; charset=utf-8",
            "extension": "html",
            "content": rules_html,
        },
        "scientific_data": {
            "media_type": "application/zip",
            "extension": "zip",
            "content": render_scientific_data_archive(report),
        },
        "csv": {
            "media_type": "text/csv; charset=utf-8",
            "extension": "csv",
            "content": render_csv(report),
        },
        "pdf": {
            "media_type": "application/pdf",
            "extension": "pdf",
            "content": render_pdf(report),
        },
    }
    for value in rendered.values():
        content = value["content"]
        value["size_bytes"] = len(content)
        value["sha256"] = _sha256_bytes(content)
        value["canonical_report_id"] = report["report_id"]
        value["canonical_content_sha256"] = report["content_sha256"]
    return rendered


def render_available_exports(
    report: dict[str, Any],
    formats: Iterable[str] = (
        "html",
        "process_html",
        "rules_html",
        "scientific_data",
    ),
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, str]]]:
    """Render selected formats independently from the same canonical report."""
    renderers = {
        "html": (
            "text/html; charset=utf-8",
            "html",
            render_html,
        ),
        "process_html": (
            "text/html; charset=utf-8",
            "html",
            render_process_detail_html,
        ),
        "rules_html": (
            "text/html; charset=utf-8",
            "html",
            render_rules_validation_html,
        ),
        "scientific_data": (
            "application/zip",
            "zip",
            render_scientific_data_archive,
        ),
        "csv": (
            "text/csv; charset=utf-8",
            "csv",
            render_csv,
        ),
        "pdf": (
            "application/pdf",
            "pdf",
            render_pdf,
        ),
    }
    rendered: dict[str, dict[str, Any]] = {}
    failures: dict[str, dict[str, str]] = {}
    selected_formats = [str(value).lower() for value in formats]
    unknown = sorted(set(selected_formats) - set(renderers))
    if unknown:
        raise ValueError(f"Unsupported export formats: {', '.join(unknown)}")
    for format_name in selected_formats:
        media_type, extension, renderer = renderers[format_name]
        try:
            content = renderer(report)
        except Exception as exc:  # export isolation boundary
            failures[format_name] = {
                "code": f"M6_{format_name.upper()}_EXPORT_FAILED",
                "error_type": type(exc).__name__,
                "message": str(exc)[:1000] or type(exc).__name__,
            }
            continue
        rendered[format_name] = {
            "media_type": media_type,
            "extension": extension,
            "content": content,
            "size_bytes": len(content),
            "sha256": _sha256_bytes(content),
            "canonical_report_id": report["report_id"],
            "canonical_content_sha256": report["content_sha256"],
        }
    return rendered, failures
