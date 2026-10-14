"""Readable WF-03/WF-04 HTML reports using the WF-02 visual language.

This module is deliberately presentation-only.  It accepts the persisted
workflow result and never derives or mutates workflow execution state.
"""

from __future__ import annotations

import html
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any


WORKFLOW_REPORT_RENDERER_VERSION = "wf03-wf04-readable-html-v2"

_STATUS_LABELS = {
    "SUCCEEDED": "执行完成",
    "COMPLETED": "执行完成",
    "PASS": "通过",
    "PASS_WITH_WARNING": "通过（含注意项）",
    "PASS_WITH_WARNINGS": "通过（含注意项）",
    "PARTIAL": "部分完成",
    "PARTIALLY_EVALUATED": "部分评估",
    "CAUTION": "需注意",
    "WARNING": "需注意",
    "INCONCLUSIVE": "待确认",
    "FAIL": "未通过",
    "FAILED": "执行失败",
    "ERROR": "执行错误",
    "BLOCKED": "已阻塞",
    "SKIPPED": "已跳过",
    "REUSED": "已复用",
    "PENDING": "等待执行",
    "RUNNING": "执行中",
    "AVAILABLE": "可用",
    "UNAVAILABLE": "不可用",
    "NOT_APPLICABLE": "不适用",
    "NOT_EVALUATED": "未评估",
    "END": "结束",
    "WAIT_FOR_USER": "等待输入",
    "RETRY": "可重试",
    "NOT_AVAILABLE": "未提供",
    "UNKNOWN": "未知",
}

_FIELD_LABELS = {
    "assay_id": "任务标识",
    "candidate_id": "结果标识",
    "created_at": "生成时间",
    "decision": "最终判定",
    "duration_ms": "耗时（毫秒）",
    "error_code": "错误代码",
    "evidence_level": "证据级别",
    "execution_status": "执行状态",
    "job_id": "任务 ID",
    "message": "说明",
    "next_action": "后续动作",
    "rank": "排序",
    "reason": "原因",
    "report_tool": "报告入口",
    "retryable": "可重试",
    "reused": "复用已有结果",
    "schema_version": "结构版本",
    "score": "评分",
    "status": "状态",
    "step": "步骤",
    "tool": "工具",
    "verdict": "判定",
    "workflow": "工作流",
    "workflow_version": "工作流版本",
}

_SUCCESS_STATES = {
    "PASS",
    "SUCCEEDED",
    "COMPLETED",
    "AVAILABLE",
    "REUSED",
    "END",
}
_WARNING_STATES = {
    "PASS_WITH_WARNING",
    "PASS_WITH_WARNINGS",
    "PARTIAL",
    "PARTIALLY_EVALUATED",
    "CAUTION",
    "WARNING",
    "INCONCLUSIVE",
    "BLOCKED",
    "SKIPPED",
    "PENDING",
    "RUNNING",
    "WAIT_FOR_USER",
    "RETRY",
}
_DANGER_STATES = {"FAIL", "FAILED", "ERROR", "UNAVAILABLE"}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def _field_label(value: Any) -> str:
    key = _text(value)
    return _FIELD_LABELS.get(key, key.replace("_", " "))


def _display_label(value: Any) -> str:
    raw = _text(value)
    return _STATUS_LABELS.get(raw.upper(), raw or "未提供")


def _format_number(value: Any, *, digits: int = 3) -> str:
    if value is None or value == "":
        return "未提供"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _text(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.{digits}f}".rstrip("0").rstrip(".")


def _format_timestamp(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return "未提供"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    return parsed.strftime("%Y-%m-%d %H:%M:%S %z").rstrip()


def _status_tone(value: Any) -> str:
    normalized = _text(value).upper()
    if normalized in _SUCCESS_STATES:
        return "success"
    if normalized in _WARNING_STATES:
        return "warning"
    if normalized in _DANGER_STATES:
        return "danger"
    return "neutral"


def _badge(value: Any) -> str:
    return (
        f'<span class="badge badge--{_status_tone(value)}">'
        f"{html.escape(_display_label(value))}</span>"
    )


def _fact(label: str, value_html: str) -> str:
    return (
        '<div class="key-item">'
        f"<dt>{html.escape(label)}</dt><dd>{value_html}</dd></div>"
    )


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    )


def _compact_value(value: Any) -> str:
    if value is None or value == "":
        return "未提供"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return _format_number(value, digits=6)
    if isinstance(value, Mapping):
        preferred = (
            value.get("message")
            or value.get("reason")
            or value.get("code")
            or value.get("status")
        )
        if preferred not in (None, ""):
            return _text(preferred)
    if _is_sequence(value):
        return "、".join(_compact_value(item) for item in value)
    return _text(value)


def _json_for_script(value: Any) -> str:
    """Serialize JSON without allowing user data to close the script element."""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return (
        payload.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _flatten_rows(
    value: Any,
    *,
    prefix: str = "",
    limit: int = 180,
) -> tuple[list[tuple[str, Any]], bool]:
    rows: list[tuple[str, Any]] = []
    truncated = False

    def visit(item: Any, path: str) -> None:
        nonlocal truncated
        if len(rows) >= limit:
            truncated = True
            return
        if isinstance(item, Mapping):
            if not item:
                rows.append((path or "value", {}))
                return
            for key in sorted(item, key=lambda entry: _text(entry)):
                child = f"{path}.{key}" if path else _text(key)
                visit(item[key], child)
                if truncated:
                    return
            return
        if _is_sequence(item):
            if not item:
                rows.append((path or "value", []))
                return
            for index, child_value in enumerate(item, start=1):
                child = f"{path}[{index}]" if path else f"[{index}]"
                visit(child_value, child)
                if truncated:
                    return
            return
        rows.append((path or "value", item))

    visit(value, prefix)
    return rows, truncated


def _technical_table(value: Any, *, limit: int = 180) -> str:
    rows, truncated = _flatten_rows(value, limit=limit)
    if not rows:
        return '<p class="empty-state">没有可展示字段。</p>'
    body = "".join(
        "<tr>"
        f'<th scope="row">{html.escape(_field_label(path))}</th>'
        f'<td class="code-value">{html.escape(_compact_value(item))}</td>'
        "</tr>"
        for path, item in rows
    )
    note = (
        '<p class="quiet-note">主视图仅展示前 '
        f"{limit} 个字段；完整结构化记录仍保存在本附件中。</p>"
        if truncated
        else ""
    )
    return (
        '<div class="table-scroll"><table class="sequence-table technical-table">'
        "<thead><tr><th>字段</th><th>值</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>{note}"
    )


def _execution_status(report: Mapping[str, Any]) -> Any:
    return report.get("execution_status") or report.get("status") or "UNKNOWN"


def _final_decision(report: Mapping[str, Any]) -> Any:
    internal = report.get("internal_control")
    internal_decision = (
        internal.get("verdict") if isinstance(internal, Mapping) else None
    )
    return (
        report.get("decision")
        or report.get("verdict")
        or internal_decision
        or _execution_status(report)
    )


def _next_action(report: Mapping[str, Any]) -> Any:
    if report.get("next_action"):
        return report["next_action"]
    internal = report.get("internal_control")
    if isinstance(internal, Mapping):
        fallback = internal.get("fallback")
        if isinstance(fallback, Mapping) and fallback.get("can_reenter_step1"):
            return "WAIT_FOR_USER"
    status = _text(_execution_status(report)).upper()
    return "END" if status in _SUCCESS_STATES else "NOT_AVAILABLE"


def _steps(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw_steps = report.get("steps") or report.get("workflow_trace") or []
    if not _is_sequence(raw_steps):
        return []
    return [item for item in raw_steps if isinstance(item, Mapping)]


def _step_detail(step: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "tool",
        "duration_ms",
        "reused",
        "retryable",
        "error_code",
        "message",
    ):
        if step.get(key) not in (None, ""):
            parts.append(f"{_field_label(key)}：{_compact_value(step[key])}")
    return "；".join(parts) or "步骤记录已写入结构化结果"


def _validation_steps(report: Mapping[str, Any]) -> str:
    steps = _steps(report)
    if not steps:
        return '<ol class="validation-list"><li class="empty-state">没有步骤记录。</li></ol>'
    return '<ol class="validation-list">' + "".join(
        "<li>"
        f'<span class="step-number">{index:02d}</span>'
        '<div class="step-copy">'
        f"<strong>{html.escape(_text(step.get('step')) or f'STEP_{index}')}</strong>"
        f"<span>{html.escape(_step_detail(step))}</span>"
        "</div>"
        f"{_badge(step.get('status'))}"
        "</li>"
        for index, step in enumerate(steps, start=1)
    ) + "</ol>"


def _candidates(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw_candidates = report.get("candidates") or []
    if not _is_sequence(raw_candidates):
        return []
    return [item for item in raw_candidates if isinstance(item, Mapping)]


def _candidate_score(candidate: Mapping[str, Any]) -> Any:
    score = candidate.get("score")
    if isinstance(score, Mapping):
        for key in ("final_score", "total", "score"):
            if score.get(key) is not None:
                return score[key]
        return None
    return score


def _candidate_status(candidate: Mapping[str, Any]) -> Any:
    return candidate.get("verdict") or candidate.get("status") or "UNKNOWN"


def _candidate_id(candidate: Mapping[str, Any], index: int) -> str:
    identifier = _text(candidate.get("candidate_id") or candidate.get("id"))
    return identifier or f"RESULT-{index}"


def _warning_count(candidate: Mapping[str, Any]) -> int:
    warnings = candidate.get("warnings") or []
    return len(warnings) if _is_sequence(warnings) else 1


def _comparison_table(candidates: list[Mapping[str, Any]]) -> str:
    if not candidates:
        return '<p class="empty-state">本次没有可对比的结果项。</p>'
    body = "".join(
        "<tr>"
        f'<th scope="row"><a class="panel-link" href="#result-{index}">结果 {index}</a>'
        '<span class="trace-id">'
        f"{html.escape(_candidate_id(candidate, index))}</span></th>"
        "<td>"
        f"{html.escape(_format_number(candidate.get('rank') or index, digits=0))}"
        "</td>"
        f"<td>{_badge(_candidate_status(candidate))}</td>"
        f"<td>{html.escape(_format_number(_candidate_score(candidate)))}</td>"
        f"<td>{html.escape(_display_label(candidate.get('evidence_level')))}</td>"
        f"<td>{_warning_count(candidate)}</td>"
        "</tr>"
        for index, candidate in enumerate(candidates, start=1)
    )
    return (
        '<div class="table-scroll"><table class="comparison-table">'
        "<thead><tr><th>结果项</th><th>排序</th><th>状态</th>"
        "<th>评分</th><th>证据级别</th><th>注意项</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def _candidate_section(candidate: Mapping[str, Any], index: int) -> str:
    candidate_id = _candidate_id(candidate, index)
    status = _candidate_status(candidate)
    rank = _format_number(candidate.get("rank") or index, digits=0)
    score = _format_number(_candidate_score(candidate))
    evidence = _display_label(candidate.get("evidence_level"))
    warning_count = _warning_count(candidate)
    warnings = candidate.get("warnings") or []
    note = ""
    if _is_sequence(warnings) and warnings:
        note = (
            '<div class="inline-note"><strong>注意事项</strong><ul>'
            + "".join(
                f"<li>{html.escape(_compact_value(item))}</li>" for item in warnings
            )
            + "</ul></div>"
        )
    return (
        f'<section class="member-section" id="result-{index}">'
        '<div class="member-heading"><div>'
        f'<p class="section-kicker">03.{index:02d} · 结果明细</p>'
        f"<h3>结果 {index} {_badge(status)}</h3>"
        f'<span class="trace-id">追踪编号：{html.escape(candidate_id)}</span>'
        "</div>"
        f'<span class="channel-text">评分 {html.escape(score)}</span>'
        "</div>"
        '<dl class="member-key-data">'
        f'{_fact("结果标识", html.escape(candidate_id))}'
        f'{_fact("排序", html.escape(rank))}'
        f'{_fact("最终状态", _badge(status))}'
        f'{_fact("证据级别", html.escape(evidence))}'
        f'{_fact("注意项", str(warning_count))}'
        "</dl>"
        f"{note}"
        '<details class="technical-summary"><summary>查看结果字段</summary>'
        f"{_technical_table(candidate)}</details></section>"
    )


def _issues(report: Mapping[str, Any]) -> list[tuple[str, Any]]:
    result: list[tuple[str, Any]] = []
    for label, key in (
        ("注意项", "warnings"),
        ("错误项", "errors"),
        ("判定原因", "decision_reasons"),
    ):
        value = report.get(key) or []
        if _is_sequence(value):
            result.extend((label, item) for item in value)
        elif value not in (None, ""):
            result.append((label, value))
    return result


def _issue_section(report: Mapping[str, Any]) -> str:
    issues = _issues(report)
    if not issues:
        return '<p class="quiet-note">当前报告没有额外问题项。</p>'
    return '<ul class="issue-list">' + "".join(
        "<li>"
        f"<strong>{html.escape(label)}</strong>"
        f"<span>{html.escape(_compact_value(item))}</span>"
        "</li>"
        for label, item in issues
    ) + "</ul>"


def _technical_metadata(report: Mapping[str, Any]) -> dict[str, Any]:
    excluded = {
        "candidates",
        "decision_reasons",
        "errors",
        "steps",
        "warnings",
        "workflow_trace",
    }
    return {key: value for key, value in report.items() if key not in excluded}


# Frozen visual snapshot of WF-02 readable HTML v2.  Keeping the snapshot in
# this renderer lets WF-02 and WF-03/WF-04 advance their renderer versions
# independently while retaining the same visible design contract.
_WF02_READABLE_STYLE = r"""
:root{--ink:#1d2226;--muted:#697179;--line:#dfe3e6;--soft:#f6f7f8;--accent:#315d73;--success:#2f6b52;--warning:#7a5b1d;--danger:#9c3f3f}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#fff;color:var(--ink);font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",Arial,sans-serif;line-height:1.65}
.report-shell{width:min(1040px,calc(100% - 48px));margin:0 auto 64px}.report-header{padding:52px 0 28px;border-bottom:2px solid var(--ink)}.header-top{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:14px}
.eyebrow,.section-kicker{margin:0;color:var(--accent);font-size:12px;font-weight:700;letter-spacing:.13em;text-transform:uppercase}h1{margin:0;font-size:clamp(30px,4vw,44px);font-weight:650;line-height:1.2;letter-spacing:-.02em}h2{margin:0;font-size:24px;line-height:1.35}h3{margin:0;font-size:18px}.report-intro{max-width:760px;margin:14px 0 0;color:#454c52;font-size:15px}
.meta-row{display:flex;gap:8px 28px;flex-wrap:wrap;margin:22px 0 0;color:var(--muted);font-size:12px}.meta-row b{color:var(--ink);font-weight:600}
.badge{display:inline-flex;align-items:center;gap:7px;width:max-content;color:var(--muted);font-size:12px;font-weight:700;line-height:1.35;vertical-align:middle}.badge::before{width:7px;height:7px;border-radius:50%;background:currentColor;content:""}.badge--success{color:var(--success)}.badge--warning{color:var(--warning)}.badge--danger{color:var(--danger)}.badge--neutral{color:var(--muted)}
.key-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));margin:28px 0 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}.key-metrics .key-item{padding:18px 20px}.key-metrics .key-item+.key-item{border-left:1px solid var(--line)}.key-item{min-width:0}.key-item dt{color:var(--muted);font-size:11px}.key-item dd{margin:4px 0 0;font-size:15px;font-weight:650;overflow-wrap:anywhere}.key-metrics dd{font-size:22px}
.report-section,.panel-section{padding:38px 0;border-bottom:1px solid var(--line)}.section-heading{display:flex;align-items:baseline;gap:14px;margin-bottom:20px}.section-heading>span{color:var(--accent);font:700 12px/1 Consolas,monospace}.context-notes{display:grid;gap:10px}.context-notes p{margin:0;color:#4f565c;font-size:13px}.context-notes strong{color:var(--ink)}
.validation-list{margin:0;padding:0;list-style:none;border-top:1px solid var(--line)}.validation-list li{display:grid;grid-template-columns:48px minmax(0,1fr) auto;gap:14px;align-items:center;padding:15px 0;border-bottom:1px solid var(--line)}.validation-list li.empty-state{display:block}.step-number{color:#98a0a6;font:12px/1 Consolas,monospace}.step-copy{display:grid;grid-template-columns:minmax(120px,.45fr) minmax(220px,1fr);gap:18px;align-items:center}.step-copy strong{font-size:14px}.step-copy span{color:var(--muted);font-size:13px}
.table-scroll{overflow-x:auto}table{border-collapse:collapse;width:100%}.comparison-table{min-width:720px;font-size:13px}th,td{padding:11px 12px;text-align:left;border-bottom:1px solid var(--line);vertical-align:middle}thead th{background:var(--soft);color:#535b62;font-size:11px;font-weight:650}tbody th{font-size:12px}.panel-link{display:block;color:var(--accent);font-weight:700;text-decoration:none}.trace-id{display:block;margin-top:3px;color:#858c92;font:11px/1.45 Consolas,"SFMono-Regular",monospace;overflow-wrap:anywhere}
.panel-section{padding-top:48px;scroll-margin-top:18px;border-bottom:2px solid var(--ink)}.panel-heading,.member-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:24px}.member-heading h3 .badge{margin-left:9px}.panel-key-data,.member-key-data{display:grid;margin:22px 0 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}.member-key-data{grid-template-columns:1.2fr repeat(4,minmax(0,1fr))}.member-key-data .key-item{padding:12px 14px}.member-key-data .key-item+.key-item{border-left:1px solid var(--line)}
.inline-note{margin:18px 0 0;padding-left:14px;border-left:2px solid #aeb5ba;color:#4c5359;font-size:13px}.inline-note strong{color:var(--ink)}.inline-note ul{margin:5px 0 0;padding-left:20px}.quiet-note{margin:15px 0 0;color:var(--muted);font-size:12px}
.member-list{margin-top:22px}.member-section{padding:30px 0 6px;border-top:1px solid var(--line)}.member-section:first-child{border-top:0}.channel-text{color:var(--muted);font-size:12px}.technical-summary{margin-top:20px;padding-top:14px;border-top:1px solid var(--line)}.technical-summary summary{cursor:pointer;color:var(--accent);font-size:13px;font-weight:700}.technical-summary[open] summary{margin-bottom:12px}.sequence-table{min-width:720px;margin-top:14px;font-size:12px}.technical-table th:first-child{width:32%}.code-value{font-family:Consolas,"SFMono-Regular",monospace;overflow-wrap:anywhere;word-break:break-word}.empty-value,.empty-state{color:var(--muted);font-weight:400}.empty-state{padding:18px 0;text-align:center}.issue-list{margin:0;padding:0;list-style:none;border-top:1px solid var(--line)}.issue-list li{display:grid;grid-template-columns:120px minmax(0,1fr);gap:16px;padding:12px 0;border-bottom:1px solid var(--line);font-size:13px}.issue-list span{color:#4f565c}.report-footer{padding:24px 0;color:var(--muted);font-size:11px}.report-footer p{margin:3px 0}
@media(max-width:760px){.report-shell{width:min(100% - 28px,1040px)}.report-header{padding-top:30px}.key-metrics{grid-template-columns:1fr 1fr}.key-metrics .key-item:nth-child(3){border-left:0;border-top:1px solid var(--line)}.key-metrics .key-item:nth-child(4){border-top:1px solid var(--line)}.step-copy{grid-template-columns:1fr;gap:2px}.member-key-data{grid-template-columns:1fr 1fr}.member-key-data .key-item{border-bottom:1px solid var(--line)}.member-key-data .key-item:nth-child(odd){border-left:0}.issue-list li{grid-template-columns:1fr;gap:3px}}
@media(max-width:520px){.header-top,.panel-heading,.member-heading{align-items:flex-start;flex-direction:column}.validation-list li{grid-template-columns:34px minmax(0,1fr)}.validation-list li>.badge{grid-column:2}.meta-row{display:grid;gap:4px}}
@media print{@page{size:A4 portrait;margin:12mm}.report-shell{width:100%;margin:0}.report-header{padding-top:0}.panel-section{break-before:page}.member-section{break-inside:avoid}details:not([open])>*:not(summary){display:block!important}.table-scroll{overflow:visible}}
"""


def render_workflow_report_html(report: dict[str, Any]) -> bytes:
    """Render a deterministic, readable view of one WF-03/WF-04 result."""

    workflow = _text(report.get("workflow")) or "WORKFLOW"
    job_id = _text(report.get("job_id"))
    execution_status = _execution_status(report)
    final_decision = _final_decision(report)
    next_action = _next_action(report)
    candidates = _candidates(report)
    steps = _steps(report)
    version = report.get("workflow_version") or report.get("schema_version")
    created_at = report.get("created_at") or report.get("generated_at")
    narrative = (
        f"本次共返回 {len(candidates)} 个可查看结果项，并记录 {len(steps)} 个执行步骤；"
        "下方按执行摘要、结果对比和技术明细依次展示。"
    )
    candidate_sections = "".join(
        _candidate_section(candidate, index)
        for index, candidate in enumerate(candidates, start=1)
    ) or '<p class="empty-state">本次没有可展示的结果明细。</p>'
    technical_metadata = _technical_metadata(report)
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(workflow)} 结果报告</title>
<style>{_WF02_READABLE_STYLE}</style></head>
<body><main class="report-shell">
<header class="report-header"><div class="header-top"><p class="eyebrow">{html.escape(workflow)} · 结果报告</p>{_badge(execution_status)}</div><h1>工作流终态结果与执行摘要</h1>
<p class="report-intro">{html.escape(narrative)}</p>
<div class="meta-row"><span><b>任务：</b>{html.escape(job_id or '未提供')}</span><span><b>版本：</b>{html.escape(_text(version) or '未提供')}</span><span><b>生成时间：</b>{html.escape(_format_timestamp(created_at))}</span></div></header>
<dl class="key-metrics" aria-label="关键数据">{_fact("执行状态", _badge(execution_status))}{_fact("最终判定", _badge(final_decision))}{_fact("结果数量", str(len(candidates)))}{_fact("后续动作", html.escape(_display_label(next_action)))}</dl>
<section class="report-section"><div class="section-heading"><span>01</span><h2>执行步骤</h2></div>{_validation_steps(report)}</section>
<section class="report-section"><div class="section-heading"><span>02</span><h2>结果对比</h2></div>{_comparison_table(candidates)}</section>
<section class="panel-section"><div class="section-heading"><span>03</span><h2>结果明细</h2></div><div class="member-list">{candidate_sections}</div></section>
<section class="report-section"><div class="section-heading"><span>04</span><h2>问题与提示</h2></div>{_issue_section(report)}</section>
<section class="report-section"><div class="section-heading"><span>05</span><h2>技术记录</h2></div><details class="technical-summary"><summary>查看工作流元数据</summary>{_technical_table(technical_metadata, limit=220)}</details></section>
<footer class="report-footer"><p>页面主视图采用 WF-02 的可读报告版式；完整结构化记录保存在附件内的数据块中。</p><p>渲染器：{WORKFLOW_REPORT_RENDERER_VERSION}</p></footer>
<script type="application/json" id="workflow-report-data">{_json_for_script(report)}</script>
</main></body></html>"""
    return document.encode("utf-8")
