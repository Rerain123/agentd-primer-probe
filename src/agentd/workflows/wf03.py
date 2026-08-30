"""Deterministic WF-03 backend orchestration over the existing P5 tool.

The workflow never implements Primer3, BLAST, MAFFT, or ViennaRNA logic.  P5
owns that scientific tool chain; WF-03 validates user-facing input, invokes P5
once, classifies its result, and emits the documented retry/fallback contract.
"""

from __future__ import annotations

import copy
from typing import Any

from .. import __version__
from ..config import config_fingerprint
from ..database import DatabaseCatalog
from ..errors import InputValidationError
from ..internal_control import InternalControlDesigner
from ..models import Candidate, EvidenceLevel, WorkflowResult, WorkflowStatus


WF03_POLICY_VERSION = "wf03-v1.0-rules-2026-08-19"
WF03_REQUIRED_CANDIDATES = 3
WF03_BACKUP_GENES = ("RPLP0", "B2M", "SDHA", "TBP", "HMBS")
WF03_REENTRY_FIELDS = {
    "preferred_genes",
    "gene_accessions",
    "gene_sequences",
    "gene_fasta_paths",
    "existing_targets",
    "host_db",
    "cross_db",
    "allow_fallback",
}

_HOST_ALIASES = {
    "pig": {"pig", "swine", "猪", "sus scrofa"},
    "cattle": {"cattle", "cow", "bovine", "牛", "bos taurus"},
    "chicken": {"chicken", "poultry", "鸡", "gallus gallus"},
}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def _canonical_host_species(value: Any) -> str:
    selected = _text(value).casefold()
    for canonical, aliases in _HOST_ALIASES.items():
        if selected in {alias.casefold() for alias in aliases}:
            return canonical
    return selected


def _gene_name(item: Any) -> tuple[str, str | None]:
    if isinstance(item, str):
        return item.strip(), None
    if not isinstance(item, dict):
        return "", None
    name = _text(item.get("gene") or item.get("name") or item.get("symbol"))
    accession = _text(
        item.get("refseq_accession")
        or item.get("refseq")
        or item.get("accession")
    )
    return name, accession or None


def _normalize_gene_fields(data: dict) -> dict:
    normalized = copy.deepcopy(data)
    raw_accessions = normalized.get("gene_accessions") or {}
    if not isinstance(raw_accessions, dict):
        raise InputValidationError("gene_accessions必须为基因名到RefSeq编号的对象")
    accessions: dict[str, str] = {}
    for gene, accession in raw_accessions.items():
        gene_name = _text(str(gene))
        accession_text = _text(str(accession))
        if not gene_name or not accession_text:
            raise InputValidationError("gene_accessions中的基因名和RefSeq编号不能为空")
        accessions[gene_name] = accession_text

    preferred = normalized.get("preferred_genes")
    if preferred is not None:
        if not isinstance(preferred, (list, tuple)) or not preferred:
            raise InputValidationError("preferred_genes必须为非空数组")
        genes: list[str] = []
        for item in preferred:
            name, accession = _gene_name(item)
            if not name:
                raise InputValidationError(
                    "preferred_genes中的每一项必须是基因名，或含gene/name/symbol的对象"
                )
            if name not in genes:
                genes.append(name)
            if accession:
                accessions[name] = accession
        normalized["preferred_genes"] = genes
    if accessions:
        normalized["gene_accessions"] = accessions
    return normalized


def _oligo_sequence(target: dict, short_key: str, canonical_key: str) -> str:
    value = target.get(short_key)
    if value is None:
        value = target.get(canonical_key)
    if isinstance(value, dict):
        value = value.get("sequence_5to3") or value.get("sequence")
    return _text(value).upper()


def _normalize_existing_targets(value: Any) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise InputValidationError("existing_targets必须为数组")
    targets: list[dict] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise InputValidationError(
                "existing_targets中的每一项必须为对象",
                details={"index": index - 1},
            )
        forward = _oligo_sequence(item, "fwd_seq", "forward_primer")
        reverse = _oligo_sequence(item, "rev_seq", "reverse_primer")
        if not forward or not reverse:
            raise InputValidationError(
                "existing_targets中的每个靶标都必须提供正、反向引物",
                details={"index": index - 1},
            )
        probe = _oligo_sequence(item, "probe_seq", "probe")
        normalized = {
            "target_id": _text(item.get("target_id")) or f"target_{index}",
            "fwd_seq": forward,
            "rev_seq": reverse,
        }
        if probe:
            normalized["probe_seq"] = probe
        fluorophore = _text(item.get("fluorophore"))
        if not fluorophore and isinstance(item.get("probe"), dict):
            fluorophore = _text(item["probe"].get("fluorophore"))
        if fluorophore:
            normalized["fluorophore"] = fluorophore
        targets.append(normalized)
    return targets


def normalize_wf03_payload(payload: dict) -> tuple[dict, str, dict | None]:
    """Normalize the MCP/REST payload without making scientific decisions."""

    if not isinstance(payload, dict):
        raise InputValidationError("WF-03输入必须为对象")
    original_host = _text(payload.get("host_species"))
    host_species = _canonical_host_species(original_host)
    if not host_species:
        raise InputValidationError("WF-03需要host_species")
    normalized = _normalize_gene_fields(payload)
    normalized["host_species"] = host_species
    if "existing_targets" in normalized:
        normalized["existing_targets"] = _normalize_existing_targets(
            normalized["existing_targets"]
        )
    reentry_context = normalized.pop("_wf03_reentry_context", None)
    if reentry_context is not None and not isinstance(reentry_context, dict):
        raise InputValidationError("WF-03重入上下文无效")
    return normalized, original_host, reentry_context


def _candidate_count(internal: dict) -> int:
    objects = internal.get("candidate_objects")
    if isinstance(objects, list):
        return len(objects)
    for key in ("candidates", "primers"):
        value = internal.get(key)
        if isinstance(value, list):
            return len(value)
    return 0


def _p5_failed(internal: dict) -> bool:
    verdict = _text(internal.get("verdict")).upper()
    status = _text(internal.get("status")).upper()
    return verdict == "FAIL" or status in {
        "FAILED",
        "FAIL",
        "INVALID_INPUT",
        "NOT_CONFIGURED",
        "TOOL_UNAVAILABLE",
        "P2_CONTRACT_ERROR",
    }


def _failure_reason(internal: dict) -> tuple[str, str]:
    cross_check = internal.get("cross_check") or {}
    specificity = internal.get("specificity_check") or {}
    if _text(internal.get("status")).upper() == "P2_CONTRACT_ERROR":
        return (
            "P2_CONTRACT_ERROR",
            "P5收到的P2结果不符合候选映射契约",
        )
    if _text(cross_check.get("verdict")).upper() == "FAIL":
        return (
            "TARGET_CROSS_DIMER_FAILED",
            "P5判定内标候选与已有靶标存在不可接受的交叉二聚体风险",
        )
    if _text(specificity.get("verdict")).upper() == "FAIL":
        return "P2_SPECIFICITY_FAILED", "P5内部P2特异性验证失败"

    attempts = [
        item for item in internal.get("gene_attempts", []) if isinstance(item, dict)
    ]
    conservation_attempts = [
        item
        for item in attempts
        if isinstance(item.get("conservation"), (int, float))
    ]
    if conservation_attempts and all(
        _text(item.get("status")).upper() == "FAIL"
        for item in conservation_attempts
    ):
        return (
            "ALL_HOUSEKEEPING_GENES_LOW_CONSERVATION",
            "P5已将所有完成保守性评估的管家基因标记为FAIL",
        )

    attempt_statuses = {_text(item.get("status")).upper() for item in attempts}
    if "TOOL_UNAVAILABLE" in attempt_statuses or _text(internal.get("status")).upper() == "TOOL_UNAVAILABLE":
        return (
            "P5_REQUIRED_TOOL_UNAVAILABLE",
            "P5所需的Entrez、C5、P1或P2依赖不可用",
        )
    sequence_failures = {
        "SEQUENCE_FETCH_FAILED",
        "INSUFFICIENT_SEQUENCES",
        "NOT_CONFIGURED",
    }
    if attempt_statuses and attempt_statuses <= sequence_failures:
        return (
            "HOUSEKEEPING_SEQUENCE_UNAVAILABLE",
            "未获得足量且可追溯的宿主管家基因同源序列",
        )
    if internal.get("gene_selected") and _candidate_count(internal) == 0:
        return (
            "P5_NO_ACCEPTED_CANDIDATES",
            "P5已选择管家基因，但P1设计或P5硬性规则过滤后没有可用候选",
        )
    if _text(internal.get("status")).upper() == "NOT_CONFIGURED":
        return (
            "HOST_OR_GENE_NOT_CONFIGURED",
            "P5未配置该宿主的默认管家基因，需提供自定义基因和RefSeq编号",
        )
    if _text(internal.get("status")).upper() == "INVALID_INPUT":
        return "P5_INVALID_INPUT", "P5拒绝了当前输入"
    return "P5_INTERNAL_CONTROL_FAILED", "P5未获得可交付的内标候选"


def _fallback_plan(reason_code: str, reason: str, recommendation: Any) -> dict:
    if reason_code == "P5_REQUIRED_TOOL_UNAVAILABLE":
        first_action = {
            "priority": 1,
            "action": "RESTORE_P5_DEPENDENCIES_AND_RETRY",
            "description": "恢复P5报告中不可用的Entrez/C5/P1/P2依赖后，以原输入重试。",
        }
    elif reason_code == "HOUSEKEEPING_SEQUENCE_UNAVAILABLE":
        first_action = {
            "priority": 1,
            "action": "PROVIDE_VERSIONED_GENE_SEQUENCES_OR_REFSEQ",
            "description": "提供自定义管家基因RefSeq编号，或至少5条同物种同源序列后重入Step 1。",
        }
    else:
        first_action = {
            "priority": 1,
            "action": "RETRY_WITH_CUSTOM_HOUSEKEEPING_GENE",
            "description": "提供已知稳定的自定义管家基因及RefSeq编号后重入Step 1。",
        }
    return {
        "required": True,
        "reason_code": reason_code,
        "reason": reason,
        "p5_recommendation": recommendation,
        "can_reenter_step1": True,
        "reentry_tool": "retry_wf03_internal_control",
        "accepted_reentry_fields": sorted(WF03_REENTRY_FIELDS),
        "options": [
            first_action,
            {
                "priority": 2,
                "action": "TRY_BACKUP_HOUSEKEEPING_GENES",
                "genes": list(WF03_BACKUP_GENES),
                "description": "备选基因必须同时提供RefSeq编号或版本化同源序列。",
            },
            {
                "priority": 3,
                "action": "USE_EXOGENOUS_HETEROLOGOUS_CONTROL",
                "description": (
                    "若宿主管家基因路径仍不可行，采用人工合成异源序列和独立引物探针；"
                    "WF-03只给出建议，不自动生成异源内标。"
                ),
            },
        ],
    }


def _evidence_level(internal: dict) -> EvidenceLevel:
    specificity = internal.get("specificity_check") or {}
    claimed = _text(internal.get("evidence_level")).casefold()
    specificity_pass = _text(specificity.get("verdict")).upper() == "PASS"
    verified = specificity.get("blast_verified") is True
    # Evidence level describes how the check was executed, not whether every
    # result passed.  A formal BLAST run may legitimately return CAUTION (for
    # example, multiple primary matches that require review) while still
    # retaining blast_verified provenance.  Only the legacy claimed value is
    # gated by PASS; the explicit P2 verification flag is authoritative.
    if verified or (
        specificity_pass
        and claimed == EvidenceLevel.BLAST_VERIFIED.value
    ):
        return EvidenceLevel.BLAST_VERIFIED
    return EvidenceLevel.IN_SILICO_ONLY


def _structure_summary(internal: dict, candidates: list[Candidate]) -> dict:
    structures: list[dict] = []
    for candidate in candidates:
        if isinstance(candidate.amplicon_structure, dict):
            structures.append(candidate.amplicon_structure)
    if not structures:
        serialized = internal.get("candidates") or internal.get("primers") or []
        for item in serialized:
            if isinstance(item, dict) and isinstance(item.get("amplicon_structure"), dict):
                structures.append(item["amplicon_structure"])
    statuses = [
        (_text(item.get("status")) or "NOT_EVALUATED").upper()
        for item in structures
    ]
    if not statuses or all(status == "NOT_EVALUATED" for status in statuses):
        aggregate = "NOT_EVALUATED"
    elif "WARNING" in statuses:
        aggregate = "WARNING"
    elif "CAUTION" in statuses:
        aggregate = "CAUTION"
    elif "NOT_EVALUATED" in statuses:
        aggregate = "PARTIALLY_EVALUATED"
    else:
        aggregate = "PASS"
    return {
        "status": aggregate,
        "candidate_count": len(structures),
        "status_counts": {
            status: statuses.count(status) for status in sorted(set(statuses))
        },
        "source": "P5/P6",
    }


def _target_compatibility(internal: dict, existing_targets: list[dict]) -> dict:
    if not existing_targets:
        return {
            "status": "NOT_EVALUATED",
            "performed": False,
            "reason": "未提供existing_targets，无法声明与靶标引物探针兼容",
            "source": "P5/P1",
        }
    cross_check = internal.get("cross_check")
    if not isinstance(cross_check, dict) or not cross_check.get("performed"):
        return {
            "status": "NOT_EVALUATED",
            "performed": False,
            "reason": "已提供existing_targets，但P5未返回交叉二聚体检查证据",
            "source": "P5/P1",
        }
    return {
        "status": _text(cross_check.get("verdict")).upper() or "NOT_EVALUATED",
        "performed": True,
        "target_count": len(existing_targets),
        "heterodimer_pairs_checked": cross_check.get("heterodimer_pairs_checked"),
        "worst_heterodimer_dg_kcal": cross_check.get(
            "worst_heterodimer_dg_kcal",
            cross_check.get("worst_heterodimer_dg_cal_per_mol"),
        ),
        "source": "P5/P1",
    }


def _public_input(payload: dict, original_host: str) -> dict:
    public = {
        key: copy.deepcopy(value)
        for key, value in payload.items()
        if key not in {"gene_sequences", "gene_fasta_paths"}
        and not key.startswith("_")
    }
    gene_sequences = payload.get("gene_sequences")
    if isinstance(gene_sequences, dict):
        public["gene_sequence_counts"] = {
            str(gene): len(records) if isinstance(records, list) else 1
            for gene, records in gene_sequences.items()
        }
    gene_fasta_paths = payload.get("gene_fasta_paths")
    if isinstance(gene_fasta_paths, dict):
        public["gene_fasta_inputs"] = sorted(str(gene) for gene in gene_fasta_paths)
    input_artifact = payload.get("_wf03_input_artifact")
    if isinstance(input_artifact, dict):
        public["input_artifact"] = copy.deepcopy(input_artifact)
    public["host_species_input"] = original_host
    return public


def _compact_candidate(candidate: dict) -> dict:
    forward = candidate.get("forward_primer") or {}
    reverse = candidate.get("reverse_primer") or {}
    probe = candidate.get("probe") or {}
    amplicon = candidate.get("amplicon") or {}
    structure = candidate.get("amplicon_structure") or {}
    metadata = candidate.get("metadata") or {}
    return {
        "candidate_id": candidate.get("candidate_id"),
        "rank": candidate.get("rank"),
        "score": candidate.get("score"),
        "verdict": candidate.get("verdict"),
        "forward_primer": {
            "sequence_5to3": forward.get("sequence_5to3"),
            "tm_celsius": forward.get("tm_celsius"),
        },
        "reverse_primer": {
            "sequence_5to3": reverse.get("sequence_5to3"),
            "tm_celsius": reverse.get("tm_celsius"),
        },
        "probe": {
            "sequence_5to3": probe.get("sequence_5to3"),
            "tm_celsius": probe.get("tm_celsius"),
            "fluorophore": metadata.get("fluorophore"),
            "quencher": metadata.get("quencher"),
        },
        "amplicon": {
            "length_bp": amplicon.get("length_bp"),
            "sequence_5to3": amplicon.get("sequence_5to3"),
        },
        "amplicon_structure_status": structure.get("status", "NOT_EVALUATED"),
        "evidence_level": candidate.get("evidence_level"),
        "warnings": candidate.get("warnings", []),
    }


def summarize_wf03_result(result: dict, *, include_candidates: bool = True) -> dict:
    """Return an MCP-safe view while the complete result remains persisted."""

    if result.get("workflow") != "WF-03":
        raise InputValidationError("任务不是WF-03结果")
    internal = result.get("internal_control") or {}
    fallback = internal.get("fallback")
    summary = {
        "workflow": "WF-03",
        "job_id": result.get("job_id"),
        "parent_job_id": (result.get("provenance") or {}).get("parent_job_id"),
        "status": result.get("status"),
        "next_action": "WAIT_FOR_USER" if fallback else "END",
        "assay_id": result.get("assay_id"),
        "host_species": (result.get("target") or {}).get("host_species"),
        "gene_selected": internal.get("gene_selected"),
        "refseq_accession": internal.get("refseq_accession"),
        "candidate_count": internal.get("candidate_count", len(result.get("candidates", []))),
        "evidence_level": result.get("evidence_level"),
        "verdict": internal.get("verdict"),
        "validation_summary": internal.get("validation_summary"),
        "fallback": fallback,
        "warnings": result.get("warnings", []),
        "errors": result.get("errors", []),
        "report_tool": "get_wf03_report",
    }
    if include_candidates:
        summary["candidates"] = [
            _compact_candidate(candidate) for candidate in result.get("candidates", [])
        ]
    return summary


class WF03InternalControl:
    """Independent WF-03 entry point; the only direct scientific call is P5."""

    def __init__(self, config: dict, catalog: DatabaseCatalog | None = None) -> None:
        self.config = config
        self.catalog = catalog or DatabaseCatalog(config)

    def run(self, payload: dict) -> WorkflowResult:
        normalized, original_host, reentry_context = normalize_wf03_payload(payload)
        designer = InternalControlDesigner(self.config, self.catalog)
        allow_fallback = bool(
            normalized.get(
                "allow_fallback",
                self.config["tools"]["allow_development_fallback"],
            )
        )
        p5_status = designer.status(allow_fallback=allow_fallback)
        internal = copy.deepcopy(designer.design(copy.deepcopy(normalized)))
        candidate_objects = internal.pop("candidate_objects", [])
        candidates = (
            [item for item in candidate_objects if isinstance(item, Candidate)]
            if isinstance(candidate_objects, list)
            else []
        )
        candidate_count = len(candidates) or _candidate_count(internal)
        evidence = _evidence_level(internal)
        existing_targets = normalized.get("existing_targets", [])
        target_compatibility = _target_compatibility(internal, existing_targets)
        structure_summary = _structure_summary(internal, candidates)

        warnings = [str(item) for item in internal.get("warnings", []) if item]
        if 0 < candidate_count < WF03_REQUIRED_CANDIDATES:
            warnings.append(
                f"P5仅返回{candidate_count}套内标候选，少于WF-03要求的{WF03_REQUIRED_CANDIDATES}套"
            )
        if target_compatibility["status"] == "NOT_EVALUATED":
            warnings.append(target_compatibility["reason"])
        if evidence == EvidenceLevel.IN_SILICO_ONLY and not _p5_failed(internal):
            warnings.append("P2正式BLAST证据未完整通过，evidence_level保持in_silico_only")
        if structure_summary["status"] in {"NOT_EVALUATED", "PARTIALLY_EVALUATED"}:
            warnings.append("P6扩增子二级结构未完整评估，不得按PASS解释")

        fallback = None
        compatibility_failed = target_compatibility["status"] == "FAIL"
        specificity_failed = (
            _text((internal.get("specificity_check") or {}).get("verdict")).upper()
            == "FAIL"
        )
        if (
            _p5_failed(internal)
            or candidate_count == 0
            or compatibility_failed
            or specificity_failed
        ):
            reason_code, reason = _failure_reason(internal)
            if candidate_count == 0 and reason_code == "P5_INTERNAL_CONTROL_FAILED":
                reason_code, reason = "P5_EMPTY_RESULT", "P5未返回任何内标候选"
            fallback = _fallback_plan(
                reason_code,
                reason,
                internal.get("recommendation"),
            )
            status = WorkflowStatus.FAILED
        elif (
            _text(internal.get("verdict")).upper() == "PASS"
            and evidence == EvidenceLevel.BLAST_VERIFIED
            and candidate_count >= WF03_REQUIRED_CANDIDATES
            and (
                not existing_targets
                or target_compatibility["status"] == "PASS"
            )
        ):
            status = WorkflowStatus.SUCCEEDED
        else:
            status = WorkflowStatus.PARTIAL

        validation_summary = {
            "candidate_quantity": {
                "status": (
                    "PASS"
                    if candidate_count >= WF03_REQUIRED_CANDIDATES
                    else ("CAUTION" if candidate_count else "FAIL")
                ),
                "actual": candidate_count,
                "required": WF03_REQUIRED_CANDIDATES,
            },
            "conservation": {
                "status": internal.get("conservation_status", "NOT_EVALUATED"),
                "score": internal.get("conservation"),
                "source": "P5/C5",
            },
            "specificity": {
                "status": (internal.get("specificity_check") or {}).get(
                    "verdict", "NOT_EVALUATED"
                ),
                "evidence_level": evidence.value,
                "source": "P5/P2",
            },
            "target_compatibility": target_compatibility,
            "amplicon_structure": structure_summary,
            "expression_stability": {
                "status": "NOT_EVALUATED",
                "reason": "P5仅评估序列保守性；表达稳定性需geNorm/NormFinder及湿实验确认",
            },
            "channel_assignment": {
                "status": "SUGGESTION_ONLY",
                "suggested_fluorophore": self.config["internal_control"]["default_fluorophore"],
                "reason": "最终多重通道由WF-02调用P4验证和指派",
            },
        }
        internal["candidate_count"] = candidate_count
        internal["validation_summary"] = validation_summary
        internal["fallback"] = fallback
        internal["wf03_orchestration"] = {
            "policy_version": WF03_POLICY_VERSION,
            "direct_tool": "P5.design_internal_control",
            "transitive_tools_owned_by_p5": ["NCBI_ENTREZ", "C5", "P1", "P2", "P6"],
            "not_applicable_tools": {
                "P3": "WF-03内标不执行MGB修饰Tm计算",
                "P4": "独立WF-03不执行多重通道兼容性；由WF-02负责",
            },
            "reentry": reentry_context,
        }
        warnings = _unique(warnings)

        trace = [
            {
                "step": "WF03_INPUT",
                "status": "PASS",
                "host_species": normalized["host_species"],
                "existing_target_count": len(existing_targets),
                "policy_version": WF03_POLICY_VERSION,
            }
        ]
        if reentry_context:
            trace.append(
                {
                    "step": "WF03_STEP1_REENTRY",
                    "status": "PASS",
                    "parent_job_id": reentry_context.get("parent_job_id"),
                    "reason": reentry_context.get("reason"),
                }
            )
        trace.extend(
            [
                {
                    "step": "P5_DESIGN_INTERNAL_CONTROL",
                    "status": internal.get("status"),
                    "verdict": internal.get("verdict"),
                    "candidate_count": candidate_count,
                },
                {
                    "step": "WF03_EVIDENCE_MAPPING",
                    "status": "PASS",
                    "evidence_level": evidence.value,
                    "p6_status": structure_summary["status"],
                    "target_compatibility": target_compatibility["status"],
                },
                {
                    "step": "WF03_REPORT",
                    "status": status.value,
                    "next_action": "WAIT_FOR_USER" if fallback else "END",
                },
            ]
        )

        dependencies = p5_status.get("dependencies", {}) if isinstance(p5_status, dict) else {}
        tool_status = {
            "P5": p5_status,
            "C0": self.catalog.status(),
            **{key: value for key, value in dependencies.items() if isinstance(value, dict)},
            **{
                key: value
                for key, value in (internal.get("tool_status") or {}).items()
                if isinstance(value, dict)
            },
            "P3": {
                "tool": "P3_MGB",
                "status": "NOT_APPLICABLE",
                "reason": "P5内标设计不执行MGB修饰Tm计算",
            },
            "P4": {
                "tool": "P4_Multiplex",
                "status": "NOT_APPLICABLE",
                "reason": "独立WF-03不负责最终荧光通道兼容性",
            },
            "C7": {"tool": "C7_schema", "available": True},
        }
        self_check_verdict = (
            "FAIL"
            if status == WorkflowStatus.FAILED
            else ("CAUTION" if warnings else "PASS")
        )
        host = normalized["host_species"]
        provenance = {
            "agentd_version": __version__,
            "config_schema_version": self.config["schema_version"],
            "config_sha256": config_fingerprint(self.config),
            "workflow_policy_version": WF03_POLICY_VERSION,
            "tool_boundary": {
                "workflow_calls": ["P5"],
                "scientific_algorithms_implemented_in_workflow": [],
            },
        }
        if reentry_context and reentry_context.get("parent_job_id"):
            provenance["parent_job_id"] = reentry_context["parent_job_id"]
        workflow_errors = [
            copy.deepcopy(item)
            for item in internal.get("errors") or []
            if isinstance(item, dict)
        ]
        if fallback is not None:
            workflow_errors.append(
                {
                    "code": fallback["reason_code"],
                    "message": fallback["reason"],
                    "details": {
                        "p5_status": internal.get("status"),
                        "p5_verdict": internal.get("verdict"),
                        "fallback_options": fallback["options"],
                    },
                }
            )

        return WorkflowResult(
            workflow="WF-03",
            status=status,
            assay_id=str(normalized.get("assay_id") or f"{host}_internal_control_v1"),
            input=_public_input(normalized, original_host),
            target={
                "host_species": host,
                "host_species_input": original_host,
                "role": "internal_control",
                "gene_selected": internal.get("gene_selected"),
                "refseq_accession": internal.get("refseq_accession"),
            },
            candidates=candidates,
            internal_control=internal,
            self_check={
                "verdict": self_check_verdict,
                "checks": validation_summary,
                "risks": warnings,
            },
            tool_status=tool_status,
            warnings=warnings,
            errors=workflow_errors,
            provenance=provenance,
            workflow_trace=trace,
            evidence_level=evidence,
        )
