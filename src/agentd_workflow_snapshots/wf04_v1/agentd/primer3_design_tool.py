"""Standalone Primer3 candidate-generation service.

This boundary intentionally owns only template validation, parameter mapping
and Primer3 execution. Organism context and BLAST specificity belong to WF-01
and are never required by this tool.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from .errors import InputValidationError
from .primer3_tool import Primer3Adapter
from .wf01_v02 import DEFAULT_PARAMETERS, MINIMUM_TEMPLATE_LENGTH
from .wf01_v02_engine import WF01V02TargetEngine


DNA_BASES = frozenset("ACGT")
SUPPORTED_PARAMETERS = frozenset(DEFAULT_PARAMETERS)
PARAMETER_RANGES: dict[str, tuple[float, float]] = {
    "product_min_bp": (70, 200),
    "product_max_bp": (70, 200),
    "primer_min_size": (18, 28),
    "primer_opt_size": (18, 28),
    "primer_max_size": (18, 28),
    "primer_min_tm": (50, 65),
    "primer_opt_tm": (50, 65),
    "primer_max_tm": (50, 65),
    "primer_min_gc": (0, 100),
    "primer_max_gc": (0, 100),
    "probe_min_size": (22, 32),
    "probe_opt_size": (22, 32),
    "probe_max_size": (22, 32),
    "probe_tm_delta_min": (8, 10),
    "probe_tm_delta_max": (8, 10),
}
ORDERED_PARAMETER_GROUPS = (
    ("product_min_bp", "product_max_bp"),
    ("primer_min_size", "primer_opt_size", "primer_max_size"),
    ("primer_min_tm", "primer_opt_tm", "primer_max_tm"),
    ("primer_min_gc", "primer_max_gc"),
    ("probe_min_size", "probe_opt_size", "probe_max_size"),
    ("probe_tm_delta_min", "probe_tm_delta_max"),
)


def _normalize_template(raw: Any) -> str:
    if not isinstance(raw, str):
        raise InputValidationError(
            "Primer3 模板序列必须为文本",
            details={"field": "sequence"},
        )
    sequence = re.sub(r"\s+", "", raw).upper().replace("U", "T")
    if not sequence:
        raise InputValidationError(
            "Primer3 模板序列不能为空",
            details={"field": "sequence"},
        )
    invalid = sorted(set(sequence) - DNA_BASES)
    if invalid:
        raise InputValidationError(
            "Primer3 模板序列含非法或歧义碱基",
            details={
                "field": "sequence",
                "invalid_bases": invalid,
                "allowed_bases": sorted(DNA_BASES),
            },
        )
    if len(sequence) < MINIMUM_TEMPLATE_LENGTH:
        raise InputValidationError(
            f"Primer3 模板长度必须不少于 {MINIMUM_TEMPLATE_LENGTH} nt",
            details={
                "field": "sequence",
                "length_nt": len(sequence),
                "minimum_length_nt": MINIMUM_TEMPLATE_LENGTH,
            },
        )
    return sequence


def _parameters(raw: Any) -> dict[str, int | float]:
    if raw in (None, {}):
        return copy.deepcopy(DEFAULT_PARAMETERS)
    if not isinstance(raw, dict):
        raise InputValidationError(
            "Primer3 parameters 必须为对象",
            details={"field": "parameters"},
        )
    unknown = sorted(set(raw) - SUPPORTED_PARAMETERS)
    if unknown:
        raise InputValidationError(
            "Primer3 parameters 包含不支持的字段",
            details={
                "field": "parameters",
                "unknown_parameters": unknown,
                "supported_parameters": sorted(SUPPORTED_PARAMETERS),
            },
        )
    values = copy.deepcopy(DEFAULT_PARAMETERS)
    for name, value in raw.items():
        if isinstance(value, bool):
            raise InputValidationError(
                f"Primer3 参数 {name} 必须为数值",
                details={"field": f"parameters.{name}", "value": value},
            )
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise InputValidationError(
                f"Primer3 参数 {name} 必须为数值",
                details={"field": f"parameters.{name}", "value": value},
            ) from exc
        if isinstance(DEFAULT_PARAMETERS[name], int):
            if not numeric.is_integer():
                raise InputValidationError(
                    f"Primer3 参数 {name} 必须为整数",
                    details={"field": f"parameters.{name}", "value": value},
                )
            values[name] = int(numeric)
        else:
            values[name] = numeric
    for name, (minimum, maximum) in PARAMETER_RANGES.items():
        if not minimum <= float(values[name]) <= maximum:
            raise InputValidationError(
                f"Primer3 参数 {name} 超出允许范围",
                details={
                    "field": f"parameters.{name}",
                    "value": values[name],
                    "minimum": minimum,
                    "maximum": maximum,
                },
            )
    for group in ORDERED_PARAMETER_GROUPS:
        group_values = [float(values[name]) for name in group]
        if group_values != sorted(group_values):
            raise InputValidationError(
                "Primer3 参数最小值、最优值和最大值顺序无效",
                details={
                    "field": "parameters",
                    "parameters": {name: values[name] for name in group},
                },
            )
    return values


def _public_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(candidate.get(key))
        for key in (
            "candidate_id",
            "forward_primer",
            "reverse_primer",
            "probe",
            "amplicon",
            "primer3_pair_penalty",
            "pair_heterodimer_dg_kcal",
            "three_prime_heterodimer_dg_kcal",
            "three_prime_involved",
            "design_method",
            "evidence_level",
        )
    }


class Primer3DesignTool:
    """Validate one template and return raw Primer3 primer/probe candidates."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = copy.deepcopy(config)

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise InputValidationError("Primer3 工具请求必须为对象")
        sequence = _normalize_template(
            payload.get("sequence", payload.get("template"))
        )
        sequence_id = str(payload.get("sequence_id") or "primer3_target").strip()
        if not sequence_id:
            sequence_id = "primer3_target"
        parameters = _parameters(payload.get("parameters"))
        maximum_candidates = payload.get("maximum_candidates", 50)
        if isinstance(maximum_candidates, bool):
            raise InputValidationError("maximum_candidates 必须为 1–50 的整数")
        try:
            maximum_candidates = int(maximum_candidates)
        except (TypeError, ValueError) as exc:
            raise InputValidationError("maximum_candidates 必须为 1–50 的整数") from exc
        if not 1 <= maximum_candidates <= 50:
            raise InputValidationError(
                "maximum_candidates 必须为 1–50 的整数",
                details={"value": maximum_candidates},
            )
        allow_fallback = payload.get("allow_development_fallback", False)
        if not isinstance(allow_fallback, bool):
            raise InputValidationError("allow_development_fallback 必须为布尔值")

        execution_config = copy.deepcopy(self.config)
        execution_config["maximum_candidates"] = maximum_candidates
        execution_config["standard_design"] = WF01V02TargetEngine._primer3_parameters(
            DEFAULT_PARAMETERS
        )
        adapter = Primer3Adapter(
            execution_config,
            allow_fallback=allow_fallback,
        )
        primer3_parameters = WF01V02TargetEngine._primer3_parameters(parameters)
        candidates = adapter.design(
            sequence,
            sequence_id=sequence_id,
            parameters=primer3_parameters,
        )
        output = [_public_candidate(candidate.to_dict()) for candidate in candidates]
        return {
            "tool": "P1_Primer3_DESIGN",
            "status": "COMPLETED",
            "sequence_id": sequence_id,
            "template_length_nt": len(sequence),
            "minimum_template_length_nt": MINIMUM_TEMPLATE_LENGTH,
            "effective_parameters": parameters,
            "candidate_count": len(output),
            "candidates": output,
            "specificity": {
                "status": "NOT_EVALUATED",
                "reason": "STANDALONE_PRIMER3_TOOL_DOES_NOT_RUN_SPECIFICITY",
                "required_for_full_evaluation": ["target_organism"],
                "optional_context": ["host"],
            },
            "primer3_status": adapter.status(),
        }
