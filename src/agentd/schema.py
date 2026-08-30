"""C7 output assembly and dependency-free structural validation."""

from __future__ import annotations

from .errors import SchemaValidationError
from .models import WorkflowResult


def validate_output(data: dict) -> None:
    required = {
        "schema_version", "job_id", "created_at", "workflow", "status", "assay_id",
        "input", "target", "candidates", "tool_status", "warnings", "errors",
        "provenance", "evidence_level", "disclaimer",
        "reports", "workflow_trace",
    }
    missing = sorted(required - set(data))
    if missing:
        raise SchemaValidationError("输出缺少顶层必需字段", details={"missing": missing})
    if data["schema_version"] != "3.0.0-phase3":
        raise SchemaValidationError("输出Schema版本不匹配", details={"schema_version": data["schema_version"]})
    if data["workflow"] not in {f"WF-{index:02d}" for index in range(1, 6)}:
        raise SchemaValidationError("仅支持WF-01至WF-05", details={"workflow": data["workflow"]})
    if data["status"] not in {"SUCCEEDED", "PARTIAL", "FAILED"}:
        raise SchemaValidationError("非法工作流状态", details={"status": data["status"]})
    if not isinstance(data["candidates"], list):
        raise SchemaValidationError("candidates必须为数组")
    candidate_required = {
        "candidate_id", "forward_primer", "reverse_primer", "probe", "amplicon",
        "evidence_level", "rules", "warnings", "score", "verdict", "metadata",
    }
    for index, candidate in enumerate(data["candidates"]):
        candidate_missing = sorted(candidate_required - set(candidate))
        if candidate_missing:
            raise SchemaValidationError(
                "候选缺少必需字段",
                details={"candidate_index": index, "missing": candidate_missing},
            )
        rule_codes = {rule.get("code") for rule in candidate.get("rules", [])}
        expected_codes = {
            "R-01", "R-01b", "R-02", "R-03", "R-03b", "R-04", "R-05", "R-06",
            "R-07", "R-08", "R-09", "R-10", "R-11", "R-12", "R-13", "R-SUPPLEMENT",
            "R-14", "R-15", "R-16", "R-17", "R-18", "R-19", "R-20", "R-21",
        }
        missing_rules = sorted(expected_codes - rule_codes)
        if missing_rules:
            raise SchemaValidationError(
                "候选规则覆盖不完整",
                details={"candidate_index": index, "missing_rules": missing_rules},
            )


def assemble(result: WorkflowResult) -> dict:
    data = result.to_dict()
    validate_output(data)
    return data
