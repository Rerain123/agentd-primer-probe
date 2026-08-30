"""Deterministic report payloads owned exclusively by WF-05."""

from __future__ import annotations

from typing import Any

from .errors import InputValidationError
from .models import WorkflowResult


def _format_value(value: Any, digits: int = 3) -> str:
    if value is None:
        return "NOT_EVALUATED"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def candidate_comparison_report(result: WorkflowResult) -> str:
    lines = [
        f"# {result.assay_id} — WF-05",
        "",
        "## Execution",
        "",
        f"- status: {result.status.value}",
        f"- candidates: {len(result.candidates)}",
        f"- trace_steps: {len(result.workflow_trace)}",
        "",
        "## Result items",
        "",
        "| rank | id | score | verdict |",
        "|---:|---|---:|---|",
    ]
    for candidate in result.candidates:
        lines.append(
            "| {rank} | {identifier} | {score} | {verdict} |".format(
                rank=_format_value(candidate.rank, 0),
                identifier=candidate.candidate_id,
                score=_format_value(candidate.score),
                verdict=candidate.verdict,
            )
        )
    if not result.candidates:
        lines.append("| — | — | — | EMPTY |")
    variation = result.variation or {}
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"- value: {variation.get('verdict', 'INCONCLUSIVE')}",
            f"- records: {variation.get('sequences_analyzed', 0)}",
            f"- flagged_items: {len(variation.get('high_risk_sites') or [])}",
        ]
    )
    return "\n".join(lines)


def result_interpretation(result: WorkflowResult) -> dict[str, Any]:
    variation = result.variation or {}
    warnings = list(dict.fromkeys(str(item) for item in result.warnings if item))
    return {
        "method": "wf05_deterministic_template_v1",
        "llm_used": False,
        "summary": {
            "execution_status": result.status.value,
            "decision": variation.get("verdict", "INCONCLUSIVE"),
            "records_analyzed": variation.get("sequences_analyzed", 0),
            "flagged_item_count": len(variation.get("high_risk_sites") or []),
        },
        "warnings": warnings[:10],
        "disclaimer": result.disclaimer,
    }


def generate_reports(result: WorkflowResult) -> dict[str, Any]:
    if result.workflow != "WF-05":
        raise InputValidationError("WF-05报告层不能处理其他工作流")
    return {
        "C29_candidate_comparison_markdown": candidate_comparison_report(result),
        "C30_interpretation": result_interpretation(result),
    }
