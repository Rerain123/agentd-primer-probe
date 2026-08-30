"""Isolated WF-05 v0.1 variation-tolerance workflow."""

from __future__ import annotations

import copy
import time
from collections import Counter
from pathlib import Path
from typing import Any

from ..alignment import MafftAdapter
from ..config import config_fingerprint
from ..errors import InputValidationError, ToolUnavailableError
from ..models import EvidenceLevel, SequenceRecord, WorkflowResult, WorkflowStatus
from ..primer3_tool import Primer3Adapter
from ..rules import RuleEngine
from ..sequence import normalize_sequence, read_fasta, reverse_complement
from ..wf05_tool_provenance import (
    tool_binding_provenance,
    verify_existing_tool_snapshots,
)


WF05_POLICY_VERSION = "wf05-v0.1-rules-2026-08-26"
WF05_MAX_SEQUENCE_RECORDS = 2000
WF05_MAX_SINGLE_SEQUENCE_CHARS = 2_000_000
WF05_MAX_TOTAL_SEQUENCE_CHARS = 25_000_000
WF05_MAX_INPUT_FILE_BYTES = 50_000_000
WF05_RETRY_FIELDS = {
    "allow_fallback",
    "assay_id",
    "forward_primer",
    "input_artifact_id",
    "probe",
    "reference_sequence_id",
    "reverse_primer",
    "sequences",
    "target_id",
    "target_species",
}

_COMPLEMENT_WITH_GAP = str.maketrans("ACGT-", "TGCA-")
_RISK_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
_WF05_STEPS = (
    "WF05_STEP1_SEQUENCE_SET",
    "WF05_STEP2_ALIGNMENT",
    "WF05_STEP3_VARIANT_SCAN",
    "WF05_STEP4_TM_IMPACT",
    "WF05_STEP5_COVERAGE",
    "WF05_STEP6_DECISION",
)


def _orient(sequence: str, reverse: bool) -> str:
    return sequence.translate(_COMPLEMENT_WITH_GAP)[::-1] if reverse else sequence


def _reference_columns(reference_alignment: str) -> dict[int, int]:
    mapping: dict[int, int] = {}
    position = -1
    for column, base in enumerate(reference_alignment):
        if base != "-":
            position += 1
            mapping[position] = column
    return mapping


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _duration_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _decision_reasons(variation: dict[str, Any]) -> list[str]:
    verdict = str(variation.get("verdict") or "INCONCLUSIVE")
    if verdict == "REDESIGN_RECOMMENDED":
        reasons: list[str] = []
        thresholds = variation.get("thresholds") or {}
        if float(variation.get("coverage_100pct_match") or 0.0) < float(
            thresholds.get("acceptable_coverage") or 0.95
        ):
            reasons.append("EXACT_COVERAGE_BELOW_ACCEPTABLE_THRESHOLD")
        if variation.get("high_frequency_three_prime_variant"):
            reasons.append("HIGH_FREQUENCY_THREE_PRIME_VARIANT")
        return reasons or ["REDESIGN_POLICY_TRIGGERED"]
    if verdict == "ACCEPTABLE":
        return ["ACCEPTABLE_WITH_REVIEW"]
    if verdict == "ROBUST":
        return ["ROBUST_POLICY_MET"]
    return ["INCONCLUSIVE"]


def enrich_wf05_result(result: dict[str, Any]) -> dict[str, Any]:
    """Attach the public WF-05 control contract to one canonical result."""

    if result.get("workflow") != "WF-05":
        return result
    selected = copy.deepcopy(result)
    variation = selected.get("variation") or {}
    execution_status = str(selected.get("status") or WorkflowStatus.FAILED.value)
    decision = str(variation.get("verdict") or "INCONCLUSIVE")
    sequence_warning = variation.get("sequences_warning")
    retry_allowed = (
        execution_status in {WorkflowStatus.PARTIAL.value, WorkflowStatus.FAILED.value}
        or decision != "ROBUST"
        or bool(sequence_warning)
    )
    if execution_status in {WorkflowStatus.PARTIAL.value, WorkflowStatus.FAILED.value}:
        next_action = "RETRY"
    elif decision == "REDESIGN_RECOMMENDED":
        next_action = "HANDOFF_WF01"
    elif sequence_warning:
        next_action = "RETRY"
    else:
        next_action = "END"
    selected.update(
        {
            "workflow_version": WF05_POLICY_VERSION,
            "parent_job_id": (selected.get("provenance") or {}).get("parent_job_id"),
            "execution_status": execution_status,
            "decision": decision,
            "decision_reasons": _decision_reasons(variation),
            "steps": copy.deepcopy(selected.get("workflow_trace") or []),
            "next_action": next_action,
            "retry": {
                "allowed": retry_allowed,
                "accepted_fields": sorted(WF05_RETRY_FIELDS),
                "tool": "retry_wf05_variation" if retry_allowed else None,
            },
            "report_tool": "get_wf05_report",
        }
    )
    return selected


def summarize_wf05_result(result: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded agent-facing view without source sequence bodies."""

    if result.get("workflow") != "WF-05":
        raise InputValidationError("任务不是WF-05结果")
    selected = enrich_wf05_result(result)
    variation = copy.deepcopy(selected.get("variation") or {})
    variation.pop("per_sequence", None)
    risk_sites = variation.get("high_risk_sites") or []
    variation["high_risk_sites"] = copy.deepcopy(risk_sites[:50])
    variation["risk_sites_returned"] = min(len(risk_sites), 50)
    variation["risk_sites_total"] = len(risk_sites)
    return {
        "job_id": selected.get("job_id"),
        "assay_id": selected.get("assay_id"),
        "workflow": "WF-05",
        "workflow_version": selected.get("workflow_version"),
        "parent_job_id": selected.get("parent_job_id"),
        "execution_status": selected.get("execution_status"),
        "decision": selected.get("decision"),
        "decision_reasons": copy.deepcopy(selected.get("decision_reasons") or []),
        "target": copy.deepcopy(selected.get("target") or {}),
        "variation": variation,
        "steps": copy.deepcopy(selected.get("steps") or []),
        "next_action": selected.get("next_action"),
        "retry": copy.deepcopy(selected.get("retry") or {}),
        "evidence_level": selected.get("evidence_level"),
        "report_tool": selected.get("report_tool"),
        "warnings": copy.deepcopy(selected.get("warnings") or []),
        "errors": copy.deepcopy(selected.get("errors") or []),
    }


class WF05VariationToleranceV01:
    """Execute WF-05 without importing another workflow implementation."""

    def __init__(self, config: dict[str, Any]) -> None:
        verify_existing_tool_snapshots()
        self.config = copy.deepcopy(config)

    @staticmethod
    def _records(payload: dict[str, Any]) -> tuple[list[SequenceRecord], SequenceRecord]:
        if payload.get("fasta_path"):
            source_path = Path(str(payload["fasta_path"]))
            try:
                file_size = source_path.stat().st_size
            except OSError as error:
                raise InputValidationError(
                    "WF-05输入文件无法读取",
                    details={"error": str(error)},
                ) from error
            if file_size > WF05_MAX_INPUT_FILE_BYTES:
                raise InputValidationError(
                    "WF-05输入文件超过运行时资源上限",
                    details={
                        "size_bytes": file_size,
                        "maximum": WF05_MAX_INPUT_FILE_BYTES,
                    },
                )
            records = read_fasta(
                source_path,
                max_records=WF05_MAX_SEQUENCE_RECORDS,
                max_sequence_length=WF05_MAX_SINGLE_SEQUENCE_CHARS,
                max_total_length=WF05_MAX_TOTAL_SEQUENCE_CHARS,
            )
        else:
            raw_records = payload.get("sequences")
            if not isinstance(raw_records, list) or not raw_records:
                raise InputValidationError("WF-05需要sequences或input_artifact_id代表性序列集")
            if len(raw_records) > WF05_MAX_SEQUENCE_RECORDS:
                raise InputValidationError(
                    "WF-05输入记录数超过运行时资源上限",
                    details={
                        "record_count": len(raw_records),
                        "maximum": WF05_MAX_SEQUENCE_RECORDS,
                    },
                )
            records = []
            for index, item in enumerate(raw_records):
                if isinstance(item, str):
                    records.append(
                        SequenceRecord(
                            id=f"sequence_{index + 1}",
                            sequence=normalize_sequence(item, allow_iupac=True),
                        )
                    )
                    continue
                if not isinstance(item, dict) or not item.get("sequence"):
                    raise InputValidationError(
                        "sequences中的每一项必须是序列文本或含sequence字段的对象",
                        details={"index": index},
                    )
                records.append(
                    SequenceRecord(
                        id=str(item.get("id") or f"sequence_{index + 1}"),
                        sequence=normalize_sequence(
                            str(item["sequence"]),
                            allow_iupac=True,
                        ),
                        description=str(item.get("description") or ""),
                        source=str(item.get("source") or "user"),
                    )
                )
        if len(records) < 2:
            raise InputValidationError("WF-05至少需要2条序列")
        if len(records) > WF05_MAX_SEQUENCE_RECORDS:
            raise InputValidationError(
                "WF-05输入记录数超过运行时资源上限",
                details={
                    "record_count": len(records),
                    "maximum": WF05_MAX_SEQUENCE_RECORDS,
                },
            )
        lengths = [len(record.sequence) for record in records]
        if max(lengths) > WF05_MAX_SINGLE_SEQUENCE_CHARS:
            raise InputValidationError(
                "WF-05单条输入记录超过运行时资源上限",
                details={
                    "maximum_length": max(lengths),
                    "maximum": WF05_MAX_SINGLE_SEQUENCE_CHARS,
                },
            )
        total_length = sum(lengths)
        if total_length > WF05_MAX_TOTAL_SEQUENCE_CHARS:
            raise InputValidationError(
                "WF-05输入记录总量超过运行时资源上限",
                details={
                    "total_length": total_length,
                    "maximum": WF05_MAX_TOTAL_SEQUENCE_CHARS,
                },
            )
        identifiers = [record.id for record in records]
        duplicate_ids = sorted(
            identifier
            for identifier, count in Counter(identifiers).items()
            if count > 1
        )
        if duplicate_ids:
            raise InputValidationError(
                "WF-05序列标识必须唯一",
                details={"duplicate_ids": duplicate_ids},
            )
        requested_reference = str(payload.get("reference_sequence_id") or "").strip()
        if requested_reference:
            reference = next(
                (record for record in records if record.id == requested_reference),
                None,
            )
            if reference is None:
                raise InputValidationError(
                    "reference_sequence_id不在输入序列集中",
                    details={"reference_sequence_id": requested_reference},
                )
        else:
            reference = records[0]
        reference.sequence = normalize_sequence(reference.sequence)
        ordered = [reference, *(record for record in records if record is not reference)]
        return ordered, reference

    @staticmethod
    def _oligo_orientation(
        reference: str,
        start: int,
        end: int,
        oligo_sequence: str,
    ) -> bool:
        binding = reference[start : end + 1]
        if binding == oligo_sequence:
            return False
        if reverse_complement(binding) == oligo_sequence:
            return True
        raise InputValidationError(
            "寡核苷酸与参考序列坐标不一致",
            details={"start": start, "end": end, "oligo": oligo_sequence},
        )

    @staticmethod
    def _input_summary(payload: dict[str, Any], sequence_count: int) -> dict[str, Any]:
        excluded = {
            "fasta_path",
            "reference_sequence",
            "sequences",
            "target_db",
            "host_db",
            "cross_db",
        }
        selected = {
            key: copy.deepcopy(value)
            for key, value in payload.items()
            if key not in excluded and not key.startswith("_")
        }
        selected["sequence_count"] = sequence_count
        artifact = payload.get("_wf05_input_artifact")
        if isinstance(artifact, dict):
            selected["input_artifact"] = copy.deepcopy(artifact)
        return selected

    def _failed_result(
        self,
        payload: dict[str, Any],
        records: list[SequenceRecord],
        reference: SequenceRecord,
        trace: list[dict[str, Any]],
        warnings: list[str],
        primer3: Primer3Adapter,
        mafft: MafftAdapter,
        error: ToolUnavailableError,
        *,
        failed_step: str,
        duration_ms: float,
        attempted_tools: list[str],
    ) -> WorkflowResult:
        existing = {
            str(item.get("step")): copy.deepcopy(item)
            for item in trace
            if isinstance(item, dict) and item.get("step")
        }
        completed_trace: list[dict[str, Any]] = []
        for step in _WF05_STEPS:
            if step in existing:
                completed_trace.append(existing[step])
            elif step == failed_step:
                completed_trace.append(
                    {
                        "step": step,
                        "status": "ERROR",
                        "retryable": True,
                        "duration_ms": duration_ms,
                        "error_code": error.code,
                    }
                )
            elif step == "WF05_STEP6_DECISION":
                completed_trace.append(
                    {
                        "step": step,
                        "status": "INCONCLUSIVE",
                        "retryable": True,
                        "decision": "INCONCLUSIVE",
                        "automatic_cross_workflow_call": False,
                    }
                )
            else:
                completed_trace.append(
                    {
                        "step": step,
                        "status": "NOT_RUN",
                        "retryable": False,
                    }
                )
        retry_context = payload.get("_wf05_retry_context")
        provenance = {
            "agentd_version": "wf05-v0.1",
            "config_schema_version": self.config["schema_version"],
            "config_sha256": config_fingerprint(self.config),
            "workflow_policy_version": WF05_POLICY_VERSION,
            "runtime_snapshot": "wf05-runtime-snapshot-v1",
            "expert_contract": tool_binding_provenance()["expert_contract"],
            "existing_tool_bindings": tool_binding_provenance()["tools"],
            "workflow_tool_attempts": list(dict.fromkeys(attempted_tools)),
            "tool_boundary": {
                "allowed_existing_tools": ["C0", "C5", "C7", "P1"],
                "optional_not_evaluated": ["C21"],
                "cross_workflow_calls": [],
            },
        }
        if isinstance(retry_context, dict):
            provenance.update(
                {
                    "parent_job_id": retry_context.get("parent_job_id"),
                    "retry_reason": retry_context.get("reason"),
                    "retry_applied_fields": copy.deepcopy(
                        retry_context.get("applied_fields") or []
                    ),
                }
            )
        return WorkflowResult(
            workflow="WF-05",
            status=WorkflowStatus.FAILED,
            assay_id=str(
                payload.get("assay_id")
                or f"{payload.get('target_id') or reference.id}_variation_v01"
            ),
            input=self._input_summary(payload, len(records)),
            target={
                "target_id": payload.get("target_id") or reference.id,
                "organism": payload.get("target_species"),
                "reference_sequence_id": reference.id,
                "reference_length_nt": len(reference.sequence),
                "sequence_count": len(records),
            },
            candidates=[],
            variation={
                "sequences_analyzed": len(records),
                "high_risk_sites": [],
                "verdict": "INCONCLUSIVE",
            },
            self_check={
                "verdict": "INCONCLUSIVE",
                "risks": _unique(warnings),
                "execution_status_separate_from_decision": True,
            },
            workflow_trace=completed_trace,
            tool_status={
                "P1": primer3.status(),
                "C5": mafft.status(),
                "C7": {"tool": "C7_schema", "available": True},
            },
            warnings=_unique(warnings),
            errors=[error.to_dict()],
            provenance=provenance,
            evidence_level=EvidenceLevel.IN_SILICO_ONLY,
        )

    def run(self, payload: dict[str, Any]) -> WorkflowResult:
        if not isinstance(payload, dict):
            raise InputValidationError("WF-05 payload必须为对象")
        for field in ("forward_primer", "reverse_primer"):
            if not payload.get(field):
                raise InputValidationError(f"WF-05需要{field}")

        trace: list[dict[str, Any]] = []
        warnings: list[str] = []
        step_started = time.perf_counter()
        records, reference = self._records(payload)
        n = len(records)
        settings = self.config["variation"]
        minimum_sequences = int(settings["minimum_sequences"])
        sequence_warning = (
            None
            if n >= minimum_sequences
            else f"序列数不足(N={n}<{minimum_sequences})，覆盖率统计置信度低"
        )
        if sequence_warning:
            warnings.append(sequence_warning)
        trace.append(
            {
                "step": "WF05_STEP1_SEQUENCE_SET",
                "status": "COMPLETED",
                "retryable": False,
                "sequence_count": n,
                "minimum_recommended": minimum_sequences,
                "duration_ms": _duration_ms(step_started),
                "policy_version": WF05_POLICY_VERSION,
            }
        )

        allow_fallback = bool(
            payload.get(
                "allow_fallback",
                self.config["tools"]["allow_development_fallback"],
            )
        )
        primer3 = Primer3Adapter(self.config, allow_fallback=allow_fallback)
        mafft = MafftAdapter(
            self.config["tools"]["mafft_executable"],
            allow_fallback=allow_fallback,
        )
        forward = normalize_sequence(str(payload["forward_primer"]))
        reverse = normalize_sequence(str(payload["reverse_primer"]))
        probe = (
            normalize_sequence(str(payload["probe"]))
            if payload.get("probe")
            else None
        )
        dependency_started = time.perf_counter()
        try:
            candidate = primer3.validate_pair(
                forward,
                reverse,
                probe=probe,
                template=reference.sequence,
                sequence_id=str(payload.get("target_id") or reference.id),
            )
        except ToolUnavailableError as error:
            return self._failed_result(
                payload,
                records,
                reference,
                trace,
                warnings,
                primer3,
                mafft,
                error,
                failed_step="WF05_STEP2_ALIGNMENT",
                duration_ms=_duration_ms(dependency_started),
                attempted_tools=["P1"],
            )
        if not candidate.amplicon_sequence_5to3:
            raise InputValidationError(
                "在参考序列中未定位到完整扩增区间",
                details={
                    "reference_id": reference.id,
                    "suggestion": "确认参考序列及正反向输入与坐标方向一致",
                },
            )

        step_started = time.perf_counter()
        try:
            aligned, alignment_method, alignment_warnings = mafft.align(records)
        except ToolUnavailableError as error:
            return self._failed_result(
                payload,
                records,
                reference,
                trace,
                warnings,
                primer3,
                mafft,
                error,
                failed_step="WF05_STEP2_ALIGNMENT",
                duration_ms=_duration_ms(step_started),
                attempted_tools=["P1", "C5"],
            )
        warnings.extend(alignment_warnings)
        if len(aligned) < 2:
            raise InputValidationError(
                "比对工具不可用且输入不能作为等长预比对序列使用"
            )
        width = len(aligned[0].sequence)
        if any(len(record.sequence) != width for record in aligned):
            raise InputValidationError("多序列比对结果长度不一致")
        trace.append(
            {
                "step": "WF05_STEP2_ALIGNMENT",
                "status": (
                    "PARTIAL" if alignment_method.endswith("fallback") else "COMPLETED"
                ),
                "retryable": alignment_method.endswith("fallback"),
                "method": alignment_method,
                "alignment_width": width,
                "duration_ms": _duration_ms(step_started),
            }
        )

        columns = _reference_columns(aligned[0].sequence)
        oligos: list[dict[str, Any]] = []
        for role, oligo in (
            ("forward_primer", candidate.forward_primer),
            ("reverse_primer", candidate.reverse_primer),
            ("probe", candidate.probe),
        ):
            if oligo is None:
                continue
            if oligo.position_start is None or oligo.position_end is None:
                raise InputValidationError(f"无法定位{role}结合位点")
            needed = list(range(oligo.position_start, oligo.position_end + 1))
            if any(position not in columns for position in needed):
                raise InputValidationError(f"{role}结合位点未完整映射到比对")
            oligos.append(
                {
                    "role": role,
                    "oligo": oligo,
                    "positions": needed,
                    "columns": [columns[position] for position in needed],
                    "reverse": self._oligo_orientation(
                        reference.sequence,
                        oligo.position_start,
                        oligo.position_end,
                        oligo.sequence_5to3,
                    ),
                }
            )

        step_started = time.perf_counter()
        per_sequence: list[dict[str, Any]] = []
        for record in aligned:
            total_mismatches = 0
            has_three_prime_mismatch = False
            per_oligo: dict[str, dict[str, Any]] = {}
            invalid = False
            for item in oligos:
                observed_binding = "".join(
                    record.sequence[column] for column in item["columns"]
                )
                observed = _orient(observed_binding, bool(item["reverse"]))
                expected = item["oligo"].sequence_5to3
                mismatches = [
                    index
                    for index, (left, right) in enumerate(zip(expected, observed))
                    if left != right
                ]
                invalid = invalid or any(base not in "ACGT" for base in observed)
                three_prime = item["role"] != "probe" and any(
                    index >= len(expected) - 5 for index in mismatches
                )
                total_mismatches += len(mismatches)
                has_three_prime_mismatch = has_three_prime_mismatch or three_prime
                per_oligo[item["role"]] = {
                    "observed_sequence_5to3": observed,
                    "mismatch_positions_1based": [index + 1 for index in mismatches],
                    "three_prime_5nt_mismatch": three_prime,
                }
            per_sequence.append(
                {
                    "sequence_id": record.id,
                    "total_mismatches": total_mismatches,
                    "has_three_prime_5nt_mismatch": has_three_prime_mismatch,
                    "contains_gap_or_ambiguity": invalid,
                    "oligos": per_oligo,
                }
            )

        minimum_observations = int(settings["minimum_variant_observations"])
        risk_sites: list[dict[str, Any]] = []
        reference_alignment = aligned[0].sequence
        for item in oligos:
            oligo = item["oligo"]
            for genomic_offset, (reference_position, column) in enumerate(
                zip(item["positions"], item["columns"])
            ):
                reference_base_genomic = reference_alignment[column]
                counts = Counter(
                    record.sequence[column]
                    for record in aligned
                    if record.sequence[column] in "ACGT"
                )
                for alt_genomic, count in sorted(counts.items()):
                    if (
                        alt_genomic == reference_base_genomic
                        or count < minimum_observations
                    ):
                        continue
                    if item["reverse"]:
                        oligo_position = len(item["positions"]) - genomic_offset - 1
                        ref_base = reverse_complement(reference_base_genomic)
                        alt_base = reverse_complement(alt_genomic)
                    else:
                        oligo_position = genomic_offset
                        ref_base = reference_base_genomic
                        alt_base = alt_genomic
                    if item["role"] == "probe":
                        risk_level = "MEDIUM"
                        location = "probe"
                    elif oligo_position >= oligo.length_nt - 5:
                        risk_level = "HIGH"
                        location = f"{item['role']}_3prime_5nt"
                    else:
                        risk_level = "LOW"
                        location = f"{item['role']}_5prime_or_middle"
                    mutated = list(oligo.sequence_5to3)
                    mutated[oligo_position] = alt_base
                    mutated_sequence = "".join(mutated)
                    variant_tm = primer3.calc_tm(
                        mutated_sequence,
                        probe=item["role"] == "probe",
                    )
                    reference_tm = oligo.tm_celsius
                    shift = (
                        float(variant_tm["tm_celsius"]) - float(reference_tm)
                        if reference_tm is not None
                        else None
                    )
                    risk_sites.append(
                        {
                            "position": oligo_position + 1,
                            "reference_position_1based": reference_position + 1,
                            "ref": ref_base,
                            "alt": alt_base,
                            "observations": count,
                            "frequency": round(count / n, 6),
                            "risk_level": risk_level,
                            "tm_shift_celsius": (
                                round(shift, 3) if shift is not None else None
                            ),
                            "possible_dropout": (
                                shift is not None
                                and abs(shift) > float(settings["tm_shift_warning_c"])
                            ),
                            "location": location,
                            "oligo": item["role"],
                            "variant_sequence_5to3": mutated_sequence,
                            "tm_method": variant_tm["method"],
                        }
                    )
        risk_sites.sort(
            key=lambda site: (
                _RISK_ORDER[site["risk_level"]],
                -site["frequency"],
                site["reference_position_1based"],
            )
        )
        trace.append(
            {
                "step": "WF05_STEP3_VARIANT_SCAN",
                "status": "COMPLETED",
                "retryable": False,
                "minimum_observations": minimum_observations,
                "risk_site_count": len(risk_sites),
                "duration_ms": _duration_ms(step_started),
            }
        )
        trace.append(
            {
                "step": "WF05_STEP4_TM_IMPACT",
                "status": "PARTIAL" if not primer3.available else "COMPLETED",
                "retryable": not primer3.available,
                "evaluated_site_count": len(risk_sites),
                "method": (
                    risk_sites[0]["tm_method"] if risk_sites else "NOT_REQUIRED"
                ),
            }
        )

        step_started = time.perf_counter()
        exact_count = sum(
            not item["contains_gap_or_ambiguity"] and item["total_mismatches"] == 0
            for item in per_sequence
        )
        one_count = sum(
            not item["contains_gap_or_ambiguity"]
            and item["total_mismatches"] <= 1
            and not item["has_three_prime_5nt_mismatch"]
            for item in per_sequence
        )
        two_count = sum(
            not item["contains_gap_or_ambiguity"] and item["total_mismatches"] <= 2
            for item in per_sequence
        )
        exact_coverage = exact_count / n
        high_risk = [site for site in risk_sites if site["risk_level"] == "HIGH"]
        high_frequency = any(
            site["frequency"] >= float(settings["high_frequency"])
            for site in high_risk
        )
        if exact_coverage < float(settings["acceptable_coverage"]) or high_frequency:
            verdict = "REDESIGN_RECOMMENDED"
        elif exact_coverage < float(settings["robust_coverage"]) or high_risk:
            verdict = "ACCEPTABLE"
        else:
            verdict = "ROBUST"
        trace.append(
            {
                "step": "WF05_STEP5_COVERAGE",
                "status": "COMPLETED",
                "retryable": False,
                "coverage_100pct_match": round(exact_coverage, 6),
                "duration_ms": _duration_ms(step_started),
            }
        )

        redesign_handoff = None
        if verdict == "REDESIGN_RECOMMENDED":
            redesign_handoff = {
                "status": "RECOMMENDED",
                "target_workflow": "WF-01",
                "automatic_execution": False,
                "reason": "WF-05只生成独立交接建议，不直接调用其他工作流",
                "exclude_reference_positions_1based": sorted(
                    {
                        int(site["reference_position_1based"])
                        for site in risk_sites
                        if site["risk_level"] in {"HIGH", "MEDIUM"}
                    }
                ),
            }
            warnings.append("当前结果建议创建独立WF-01新任务；WF-05未自动执行跨工作流调用")
        trace.append(
            {
                "step": "WF05_STEP6_DECISION",
                "status": "COMPLETED",
                "retryable": verdict != "ROBUST",
                "decision": verdict,
                "automatic_cross_workflow_call": False,
            }
        )

        variation = {
            "sequences_analyzed": n,
            "sequences_warning": sequence_warning,
            "reference_sequence_id": reference.id,
            "alignment_method": alignment_method,
            "minimum_variant_observations": minimum_observations,
            "minimum_detectable_frequency": round(minimum_observations / n, 6),
            "coverage_100pct_match": round(exact_coverage, 6),
            "coverage_1_mismatch": round(one_count / n, 6),
            "coverage_2_mismatches": round(two_count / n, 6),
            "coverage_counts": {
                "exact": exact_count,
                "one_non_3prime": one_count,
                "two": two_count,
                "total": n,
            },
            "per_sequence": per_sequence,
            "high_risk_sites": risk_sites,
            "high_frequency_three_prime_variant": high_frequency,
            "verdict": verdict,
            "redesign_route": "WF-01" if redesign_handoff else None,
            "redesign_handoff": redesign_handoff,
            "thresholds": {
                "robust_coverage": settings["robust_coverage"],
                "acceptable_coverage": settings["acceptable_coverage"],
                "high_frequency": settings["high_frequency"],
                "tm_shift_warning_c": settings["tm_shift_warning_c"],
            },
        }

        candidate.rules = RuleEngine().evaluate(candidate)
        RuleEngine.apply_verdict(candidate)
        candidate.rank = 1
        if candidate.verdict == "FAIL":
            warnings.append("已有输入未通过基础规则检查；该结果不改变WF-05执行状态")
        execution_status = WorkflowStatus.SUCCEEDED
        if alignment_method.endswith("fallback") or not primer3.available:
            execution_status = WorkflowStatus.PARTIAL

        result = WorkflowResult(
            workflow="WF-05",
            status=execution_status,
            assay_id=str(
                payload.get("assay_id")
                or f"{payload.get('target_id') or reference.id}_variation_v01"
            ),
            input=self._input_summary(payload, n),
            target={
                "target_id": payload.get("target_id") or reference.id,
                "organism": payload.get("target_species"),
                "reference_sequence_id": reference.id,
                "reference_length_nt": len(reference.sequence),
                "sequence_count": n,
            },
            candidates=[candidate],
            variation=variation,
            self_check={
                "verdict": verdict,
                "risks": _unique(warnings),
                "execution_status_separate_from_decision": True,
            },
            workflow_trace=trace,
            tool_status={
                "P1": primer3.status(),
                "C5": mafft.status(),
                "C21": {
                    "tool": "C21_IQ_TREE",
                    "available": False,
                    "status": "NOT_EVALUATED",
                    "required": False,
                },
                "C7": {"tool": "C7_schema", "available": True},
            },
            warnings=_unique(warnings),
            provenance={
                "agentd_version": "wf05-v0.1",
                "config_schema_version": self.config["schema_version"],
                "config_sha256": config_fingerprint(self.config),
                "workflow_policy_version": WF05_POLICY_VERSION,
                "runtime_snapshot": "wf05-runtime-snapshot-v1",
                "expert_contract": tool_binding_provenance()["expert_contract"],
                "existing_tool_bindings": tool_binding_provenance()["tools"],
                "workflow_tool_attempts": ["P1", "C5"],
                "tool_boundary": {
                    "allowed_existing_tools": ["C0", "C5", "C7", "P1"],
                    "optional_not_evaluated": ["C21"],
                    "cross_workflow_calls": [],
                },
                **(
                    {
                        "parent_job_id": payload["_wf05_retry_context"].get(
                            "parent_job_id"
                        ),
                        "retry_reason": payload["_wf05_retry_context"].get("reason"),
                        "retry_applied_fields": copy.deepcopy(
                            payload["_wf05_retry_context"].get("applied_fields") or []
                        ),
                    }
                    if isinstance(payload.get("_wf05_retry_context"), dict)
                    else {}
                ),
            },
            evidence_level=EvidenceLevel.IN_SILICO_ONLY,
        )
        return result
