"""Readable WF-03/WF-04 HTML reports using the WF-02 visual language.

This module is deliberately presentation-only.  It accepts the persisted
workflow result and never derives or mutates workflow execution state.
"""

from __future__ import annotations

import html
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any


WORKFLOW_REPORT_RENDERER_VERSION = "wf03-wf04-readable-html-v6"

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

_WF03_HOST_LABELS = {
    "pig": "猪",
    "swine": "猪",
    "sus_scrofa": "猪",
    "cattle": "牛",
    "bovine": "牛",
    "bos_taurus": "牛",
    "chicken": "鸡",
    "gallus_gallus": "鸡",
    "human": "人",
    "homo_sapiens": "人",
    "mouse": "小鼠",
    "mus_musculus": "小鼠",
    "rat": "大鼠",
    "rattus_norvegicus": "大鼠",
}

_RULE_BUSINESS_TITLES = {
    "R-01": "引物退火温度",
    "R-01b": "低温引物风险",
    "R-02": "扩增片段长度",
    "R-03": "引物 3′ 端组成",
    "R-03b": "引物 3′ 端稳定性",
    "R-04": "引物长度",
    "R-05": "探针 5′ 端组成",
    "R-06": "探针与引物间距",
    "R-07": "探针与引物 Tm 差",
    "R-08": "探针长度",
    "R-09": "3′ 端自二聚体",
    "R-10": "3′ 端引物间二聚体",
    "R-11": "发卡结构",
    "R-12": "引物二聚体",
    "R-13": "MGB 修饰适配性",
    "R-SUPPLEMENT": "扩增片段二级结构",
    "R-14": "多重通道兼容性",
    "R-15": "内标通道设置",
    "R-16": "广谱保守性",
    "R-17": "多重扩增片段区分度",
    "R-18": "计算参数完整性",
    "R-19": "数据库特异性",
    "R-20": "证据可追溯性",
    "R-21": "结果记录完整性",
}

_WF03_REASON_MESSAGES = {
    "AT_LEAST_ONE_P5_P2_MODE_REQUIRES_REVIEW": (
        "数据库比对已完成，但至少一项结果需要人工复核。"
    ),
    "P5_PRIMARY_MATCH_MULTIPLE_HITS_REQUIRE_REVIEW": (
        "在宿主参考基因组中发现多个匹配位点，需确认这些位点是否会形成非预期扩增。"
    ),
    "P2_RELATED_BACKGROUND_NT_PASS": (
        "背景数据库比对未发现达到当前风险阈值的跨物种匹配。"
    ),
    "AT_LEAST_ONE_REQUIRED_P2_MODE_NOT_EVALUATED": (
        "至少一项必需的数据库比对尚未完成。"
    ),
    "P2_REQUIRED_RESOURCE_NOT_RESOLVED": (
        "数据库资源未就绪，因此特异性检查尚未完成。"
    ),
    "P2_CONTRACT_ERROR": "数据库比对结果不完整，需要修复依赖后重新运行。",
    "P5_TARGET_CROSS_DIMER": (
        "与待组合靶标之间存在潜在二聚体风险，需要重新检查组合方案。"
    ),
}

_WF04_REASON_MESSAGES = {
    "LOCAL_RULE_FAILURE": "引物探针的本地设计参数存在阻断问题。",
    "LOCAL_RULE_EVIDENCE_INCOMPLETE": "部分本地设计检查缺少必要输入。",
    "P2_REQUIRED_EVIDENCE_INCOMPLETE": "目标、宿主和背景特异性证据尚未全部完成。",
    "P2_DECISION_FAILURE": "数据库特异性检查发现不可接受的风险。",
    "P2_HOST_NOT_CHECKED": "宿主基因组排除检查尚未执行。",
    "P3_DECISION_FAILURE": "探针修饰适配性未通过。",
    "P3_EXECUTION_FAILED": "探针修饰评估未能完成。",
    "P1_REQUIRED_EVIDENCE_INCOMPLETE": "基础引物探针参数检查缺少必要证据。",
    "P1_REQUIRED_EVIDENCE_DEGRADED": "基础引物探针参数检查使用了降级证据。",
    "P1_EXECUTION_FAILED": "基础参数计算未能完成。",
    "P2_REQUIRED_RESOURCE_NOT_RESOLVED": "数据库资源未就绪，因此相关检查尚未完成。",
    "P2_CONTRACT_ERROR": "数据库检查结果不完整，需要修复依赖后重新运行。",
    "P2_EXECUTION_ERROR": "数据库检查未能完成。",
    "P3_EXECUTION_ERROR": "探针修饰适配性检查未能完成。",
    "P6_UNAVAILABLE": "扩增片段结构计算资源当前不可用。",
}

_WF04_ROLE_LABELS = {
    "forward": "正向引物",
    "reverse": "反向引物",
    "probe": "探针",
}

_WF04_RULE_REQUIREMENTS = {
    "R-01": "两条引物 Tm 均为 50–65 °C，且差值不超过 2 °C",
    "R-01b": "任一引物 Tm 低于 55 °C 时标记为注意项",
    "R-02": "推荐长度 70–150 bp；可接受范围 50–200 bp",
    "R-03": "每条引物 3′ 端 5 nt 中 G/C 不超过 2 个，并避免连续 4 个 G",
    "R-03b": "3′ 端宜保留适量 G/C，并避免连续 3 个相同碱基",
    "R-04": "正向、反向引物长度均为 18–28 nt",
    "R-05": "探针 5′ 端首个碱基不为 G",
    "R-06": "探针与两侧引物的间距均不少于 5 bp",
    "R-07": "普通探针 Tm 比两条引物平均 Tm 高 8–10 °C",
    "R-08": "普通探针长度 18–30 nt；MGB 探针长度 13–20 nt",
    "R-09": "两条引物 3′ 端自二聚体 ΔG 均不低于 -6.0 kcal/mol",
    "R-10": "涉及 3′ 端的引物间二聚体 ΔG 不低于 -9.0 kcal/mol",
    "R-11": "发卡结构 ΔG 低于 -3.0 kcal/mol 时标记为注意项",
    "R-12": "非 3′ 区域二聚体 ΔG 位于 [-9, -6) kcal/mol 时标记为注意项",
    "R-13": "采用 MGB 修饰时，有效 Tm 为 65–72 °C",
    "R-SUPPLEMENT": "扩增片段结构 ΔG 不低于 -20 kcal/mol 时通过；低于 -30 kcal/mol 时需重点关注",
    "R-14": "仅多重检测方案需要核验通道兼容性",
    "R-15": "仅内标方案需要核验荧光通道和限量浓度",
    "R-16": "代表性序列不少于 20 条，保守性结果不低于 95%",
    "R-17": "仅多重检测方案需要核验不同扩增片段的长度差",
    "R-18": "软件、版本、计算方法和浓度参数记录完整",
    "R-19": "目标、宿主和非目标背景三项数据库检查均完成并保留结果",
    "R-20": "明确标注计算证据范围以及尚未完成的实验验证",
    "R-21": "序列、Tm、ΔG、长度、数据库检查和保守性记录完整",
}

_WF04_METADATA_LABELS = {
    "software": "计算软件",
    "software_version": "软件版本",
    "tm_algorithm": "Tm 计算方法",
    "mv_conc_mm": "单价盐浓度",
    "dv_conc_mm": "二价盐浓度",
    "oligo_conc_nm": "寡核苷酸浓度",
}

_WF04_COMPLETENESS_LABELS = {
    "forward": "正向引物序列",
    "reverse": "反向引物序列",
    "probe": "探针序列",
    "amplicon": "扩增片段",
    "tm": "Tm 数据",
    "dg": "ΔG 数据",
    "specificity": "数据库检查",
    "conservation": "保守性数据",
}

_WF04_EVIDENCE_LABELS = {
    "in_silico_only": "仅完成计算验证，尚未完成实验验证",
    "blast_verified": "已完成数据库比对，尚未完成实验验证",
    "experiment_verified": "已记录实验验证证据",
    "experimentally_validated": "已记录实验验证证据",
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


def _business_format_timestamp(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return "未提供"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone(timedelta(hours=8)))
        return parsed.strftime("%Y-%m-%d %H:%M:%S") + "（北京时间）"
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _wf03_format_timestamp(value: Any) -> str:
    return _business_format_timestamp(value)


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
    candidates = report.get("candidates") or []
    if _is_sequence(candidates):
        candidate_items = [item for item in candidates if isinstance(item, Mapping)]
        multiple_candidates = len(candidate_items) > 1
        seen_rule_issues: set[tuple[str, str]] = set()
        for index, candidate in enumerate(candidate_items, start=1):
            rules = candidate.get("rules") or []
            if not _is_sequence(rules):
                continue
            candidate_id = _candidate_id(candidate, index)
            for rule in rules:
                if not isinstance(rule, Mapping):
                    continue
                status = str(rule.get("status") or "").upper()
                category = str(rule.get("category") or "").lower()
                if status == "FAIL":
                    label = "失败项"
                elif status == "NOT_EVALUATED" and category == "hard":
                    label = "未完成项"
                else:
                    continue
                code = str(rule.get("code") or "UNKNOWN_RULE")
                value = f"{candidate_id} · {code}" if multiple_candidates else code
                issue = (label, value)
                if issue not in seen_rule_issues:
                    seen_rule_issues.add(issue)
                    result.append(issue)
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


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _business_badge(label: str, tone: str) -> str:
    return (
        f'<span class="badge badge--{html.escape(tone)}">'
        f"{html.escape(label)}</span>"
    )


def _wf03_business_badge(label: str, tone: str) -> str:
    return _business_badge(label, tone)


def _wf03_gene_display(gene: Any, host_code: str = "") -> str:
    raw = _text(gene).strip()
    if not raw:
        return "未提供"
    prefix = f"{host_code}_" if host_code else ""
    if prefix and raw.casefold().startswith(prefix.casefold()):
        return raw[len(prefix) :]
    if "_" in raw:
        possible_host, remainder = raw.split("_", 1)
        if possible_host.casefold() in _WF03_HOST_LABELS and remainder:
            return remainder
    return raw


def _wf03_host_and_gene(report: Mapping[str, Any]) -> tuple[str, str, str]:
    target = _mapping(report.get("target"))
    payload = _mapping(report.get("input"))
    internal = _mapping(report.get("internal_control"))
    host_code = _text(
        target.get("host_species") or payload.get("host_species")
    ).strip()
    host_input = _text(
        target.get("host_species_input")
        or payload.get("host_species_input")
    ).strip()
    host = (
        host_input
        or _WF03_HOST_LABELS.get(host_code.casefold())
        or host_code
        or "未提供"
    )
    gene = _text(
        target.get("gene_selected") or internal.get("gene_selected")
    ).strip()
    if not gene:
        candidates = _candidates(report)
        if candidates:
            gene = _text(
                _mapping(candidates[0].get("metadata")).get("gene")
            ).strip()
    display_gene = _wf03_gene_display(gene, host_code)
    return host, display_gene or "未提供", gene or "未提供"


def _wf03_score(value: Any) -> str:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return "未提供"
    if 0 <= score <= 1:
        score *= 100
    return f"{score:.1f} / 100"


def _wf03_percent(value: Any, *, digits: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "未提供"
    if -1 <= number <= 1:
        number *= 100
    return f"{number:.{digits}f}%"


def _wf03_check_badge(status: Any) -> str:
    normalized = _text(status).upper()
    if normalized in {"PASS", "SUCCEEDED", "COMPLETED", "AVAILABLE"}:
        return _wf03_business_badge("符合当前要求", "success")
    if normalized in {"FAIL", "FAILED", "ERROR", "UNAVAILABLE"}:
        return _wf03_business_badge("未通过", "danger")
    if normalized in {"NOT_EVALUATED", "NOT_AVAILABLE", "SKIPPED"}:
        return _wf03_business_badge("尚未评估", "warning")
    if normalized in {"SUGGESTION_ONLY", "NOT_APPLICABLE"}:
        return _wf03_business_badge("仅为建议", "neutral")
    return _wf03_business_badge("需要复核", "warning")


def _wf03_outcome(report: Mapping[str, Any]) -> tuple[str, str]:
    status = _text(_execution_status(report)).upper()
    candidate_count = len(_candidates(report))
    if status in {"FAIL", "FAILED", "ERROR", "BLOCKED"}:
        return "本次未形成可定稿方案", "danger"
    if status in {"PARTIAL", "PARTIALLY_EVALUATED", "CAUTION", "WARNING"}:
        if candidate_count:
            return "已产出候选，仍需复核", "warning"
        return "信息不足，暂不能定稿", "warning"
    if status in {"PASS", "SUCCEEDED", "COMPLETED"}:
        return "设计与计算检查已完成", "success"
    return "结果状态待确认", "neutral"


def _wf03_narrative(report: Mapping[str, Any]) -> str:
    host, gene, _ = _wf03_host_and_gene(report)
    candidate_count = len(_candidates(report))
    status = _text(_execution_status(report)).upper()
    subject = (
        f"{host}的 {gene} 基因"
        if host != "未提供" and gene != "未提供"
        else "本次输入对象"
    )
    if status in {"PARTIAL", "PARTIALLY_EVALUATED", "CAUTION", "WARNING"}:
        return (
            f"已针对{subject}形成 {candidate_count} 套内标引物探针候选。"
            "流程已经运行至报告输出；当前标记为“仍需复核”，表示部分科学证据尚未闭环，"
            "不代表程序在中途停止。"
        )
    if status in {"FAIL", "FAILED", "ERROR", "BLOCKED"}:
        return (
            f"已完成对{subject}的本轮评估，但没有形成可定稿的候选方案。"
            "请根据下方建议补充材料或调整设计后重新运行。"
        )
    return (
        f"已针对{subject}形成 {candidate_count} 套内标引物探针候选，"
        "并完成当前流程要求的计算检查。最终使用前仍需进行实验验证。"
    )


def _candidate_rule(
    candidate: Mapping[str, Any],
    code: str,
) -> Mapping[str, Any]:
    rules = candidate.get("rules") or []
    if not _is_sequence(rules):
        return {}
    for item in rules:
        if isinstance(item, Mapping) and _text(item.get("code")) == code:
            return item
    return {}


def _wf03_candidate_rule(
    candidate: Mapping[str, Any],
    code: str,
) -> Mapping[str, Any]:
    return _candidate_rule(candidate, code)


def _wf03_reason_message(value: Any) -> str:
    raw = _text(value).strip()
    if not raw:
        return ""
    reason = raw[3:] if raw.startswith("P2:") else raw
    if reason in _WF03_REASON_MESSAGES:
        return _WF03_REASON_MESSAGES[reason]
    if "existing_targets" in raw:
        return (
            "未提供待组合使用的靶标引物探针，因此本报告不声明多重体系兼容性。"
        )
    if "evidence_level" in raw or "in_silico_only" in raw:
        return "总体证据尚未形成完整闭环，候选需复核并完成实验验证。"
    if "P6" in raw and ("未完整" in raw or "NOT_EVALUATED" in raw):
        return "扩增片段二级结构尚未完整评估。"
    if raw.replace("P2:", "").replace("-", "_").isupper() and "_" in raw:
        return "一项计算证据需要进一步复核，具体记录保存在报告数据中。"
    return raw


def _wf03_candidate_specificity(
    candidate: Mapping[str, Any],
) -> Mapping[str, Any]:
    specificity = candidate.get("specificity")
    return specificity if isinstance(specificity, Mapping) else {}


def _wf03_primary_match_detail(candidate: Mapping[str, Any]) -> tuple[str, str]:
    specificity = _wf03_candidate_specificity(candidate)
    primary = _mapping(specificity.get("primary_match"))
    status = _text(
        primary.get("status")
        or specificity.get("host_verdict")
        or specificity.get("overall_status")
    ).upper() or "NOT_EVALUATED"
    mode = _mapping(primary.get("mode_specific"))
    hit_counts = _mapping(mode.get("per_query_hit_count"))
    role_labels = {
        "forward": "正向引物",
        "reverse": "反向引物",
        "probe": "探针",
    }
    count_parts: list[str] = []
    for role in ("forward", "reverse", "probe"):
        if hit_counts.get(role) is not None:
            count_parts.append(
                f"{role_labels[role]} {html.escape(_format_number(hit_counts[role], digits=0))} 个匹配位点"
            )
    if status == "PASS":
        return status, "宿主参考基因组中的匹配符合当前判定阈值。"
    if status in {"CAUTION", "WARNING", "PASS_WITH_WARNINGS"}:
        counts = "、".join(count_parts)
        if counts:
            return (
                status,
                f"宿主基因组比对已完成；该方案检出{counts}，需确认其他位点是否会形成非预期扩增。",
            )
        reason = _wf03_reason_message(primary.get("reason") or specificity.get("reason"))
        return status, reason or "宿主基因组比对结果需要人工复核。"
    if status == "FAIL":
        return status, "宿主基因组匹配未通过，不建议直接使用当前候选。"
    return status, "宿主基因组匹配尚未完成，当前不能确认靶向表现。"


def _wf03_background_detail(candidate: Mapping[str, Any]) -> tuple[str, str]:
    specificity = _wf03_candidate_specificity(candidate)
    background = _mapping(specificity.get("related_background_nt"))
    status = _text(
        background.get("status") or specificity.get("cross_species_verdict")
    ).upper() or "NOT_EVALUATED"
    if status == "PASS":
        return status, "背景数据库比对未发现达到当前风险阈值的跨物种匹配。"
    if status == "FAIL":
        return status, "发现达到当前风险阈值的跨物种匹配，需要重新设计或进一步排查。"
    if status in {"CAUTION", "WARNING", "PASS_WITH_WARNINGS"}:
        return status, "跨物种背景比对存在需要人工复核的匹配。"
    return status, "跨物种背景比对尚未完成。"


def _wf03_validation_checks(
    report: Mapping[str, Any],
) -> list[tuple[str, str, str]]:
    candidates = _candidates(report)
    first = candidates[0] if candidates else {}
    internal = _mapping(report.get("internal_control"))
    summary = _mapping(internal.get("validation_summary"))
    checks: list[tuple[str, str, str]] = []

    quantity = _mapping(summary.get("candidate_quantity"))
    actual = quantity.get("actual", len(candidates))
    required = quantity.get("required", 3)
    quantity_status = _text(quantity.get("status")).upper()
    if not quantity_status:
        try:
            quantity_status = "PASS" if int(actual) >= int(required) else "CAUTION"
        except (TypeError, ValueError):
            quantity_status = "NOT_EVALUATED"
    checks.append(
        (
            "候选方案数量",
            f"已形成 {_format_number(actual, digits=0)} 套，当前流程要求至少 {_format_number(required, digits=0)} 套。",
            quantity_status,
        )
    )

    conservation = _mapping(summary.get("conservation"))
    conservation_score = conservation.get("score")
    if conservation_score is None and first:
        conservation_score = first.get("conservation_score")
    sequence_count = first.get("conservation_sequence_count") if first else None
    conservation_rule = _wf03_candidate_rule(first, "R-16") if first else {}
    conservation_status = _text(
        conservation_rule.get("status") or conservation.get("status")
    ).upper() or "NOT_EVALUATED"
    conservation_parts: list[str] = []
    if conservation_score is not None:
        conservation_parts.append(f"序列保守性为 {_wf03_percent(conservation_score)}")
    if sequence_count is not None:
        conservation_parts.append(
            f"当前依据 {_format_number(sequence_count, digits=0)} 条同源序列"
        )
    if conservation_status == "NOT_EVALUATED" and sequence_count is not None:
        conservation_parts.append("代表性序列数尚未达到广谱评估建议的 20 条")
    conservation_badge_status = (
        "CAUTION"
        if conservation_status == "NOT_EVALUATED"
        and conservation_score is not None
        and sequence_count is not None
        else conservation_status
    )
    checks.append(
        (
            "序列保守性与覆盖广度",
            "；".join(conservation_parts) + "。"
            if conservation_parts
            else "尚未获得可用于判断保守性的完整数据。",
            conservation_badge_status,
        )
    )

    if first:
        primary_status, primary_detail = _wf03_primary_match_detail(first)
        primary_detail = primary_detail.replace("该方案检出", "优先方案检出", 1)
        background_status, background_detail = _wf03_background_detail(first)
    else:
        specificity = _mapping(summary.get("specificity"))
        primary_status = _text(specificity.get("status")).upper() or "NOT_EVALUATED"
        primary_detail = "数据库特异性检查尚未形成可展示的候选级结果。"
        background_status = "NOT_EVALUATED"
        background_detail = "跨物种背景比对尚未形成可展示的候选级结果。"
    checks.extend(
        (
            ("宿主基因组匹配", primary_detail, primary_status),
            ("跨物种背景排查", background_detail, background_status),
        )
    )

    structure = _mapping(summary.get("amplicon_structure"))
    structure_status = _text(structure.get("status")).upper()
    structure_count = structure.get("candidate_count")
    if not structure_status and first:
        structure_status = _text(
            _mapping(first.get("amplicon_structure")).get("status")
        ).upper()
    structure_status = structure_status or "NOT_EVALUATED"
    structure_detail = (
        f"{_format_number(structure_count, digits=0)} 套候选均已完成扩增片段二级结构检查。"
        if structure_status == "PASS" and structure_count is not None
        else (
            "扩增片段二级结构检查已完成，但有结果需要复核。"
            if structure_status in {"CAUTION", "WARNING", "PARTIALLY_EVALUATED"}
            else "扩增片段二级结构尚未完成评估。"
        )
    )
    checks.append(("扩增片段二级结构", structure_detail, structure_status))

    compatibility = _mapping(summary.get("target_compatibility"))
    compatibility_status = _text(compatibility.get("status")).upper() or "NOT_EVALUATED"
    if compatibility_status == "PASS":
        compatibility_detail = "已完成与待组合靶标引物探针的兼容性检查。"
    elif compatibility_status == "FAIL":
        compatibility_detail = "与待组合靶标存在兼容性风险，需要调整方案。"
    else:
        compatibility_detail = (
            "未提供待组合使用的靶标引物探针，因此本报告不声明多重体系兼容性。"
        )
    checks.append(("与靶标方案的组合兼容性", compatibility_detail, compatibility_status))

    channel = _mapping(summary.get("channel_assignment"))
    channel_status = _text(channel.get("status")).upper() or "SUGGESTION_ONLY"
    fluorophore = _text(channel.get("suggested_fluorophore")) or "Cy5"
    quencher = _text(_mapping(first.get("metadata")).get("quencher")) if first else ""
    label_pair = f"{fluorophore}/{quencher}" if quencher else fluorophore
    checks.append(
        (
            "探针标记与通道",
            f"当前给出 {label_pair} 作为建议；最终通道需与完整多重方案一起验证。",
            channel_status,
        )
    )

    expression = _mapping(summary.get("expression_stability"))
    expression_status = _text(expression.get("status")).upper() or "NOT_EVALUATED"
    checks.append(
        (
            "内标表达稳定性",
            "本流程只评估序列层面；表达稳定性需使用 geNorm/NormFinder 并结合湿实验确认。",
            expression_status,
        )
    )
    return checks


def _wf03_validation_list(report: Mapping[str, Any]) -> str:
    checks = _wf03_validation_checks(report)
    return '<ol class="validation-list">' + "".join(
        "<li>"
        f'<span class="step-number">{index:02d}</span>'
        '<div class="step-copy">'
        f"<strong>{html.escape(title)}</strong>"
        f"<span>{html.escape(detail)}</span>"
        "</div>"
        f"{_wf03_check_badge(status)}"
        "</li>"
        for index, (title, detail, status) in enumerate(checks, start=1)
    ) + "</ol>"


def _wf03_candidate_status(candidate: Mapping[str, Any]) -> tuple[str, str]:
    verdict = _text(_candidate_status(candidate)).upper()
    if verdict in {"FAIL", "FAILED", "ERROR"}:
        return "不建议使用", "danger"
    if verdict in {
        "PASS_WITH_WARNING",
        "PASS_WITH_WARNINGS",
        "CAUTION",
        "WARNING",
        "PARTIAL",
        "PARTIALLY_EVALUATED",
    }:
        return "可作为候选，需复核", "warning"
    if verdict in {"PASS", "SUCCEEDED", "COMPLETED"}:
        return "满足当前计算要求", "success"
    return "信息尚不完整", "warning"


def _wf03_database_label(candidate: Mapping[str, Any]) -> str:
    specificity = _wf03_candidate_specificity(candidate)
    status = _text(
        specificity.get("overall_status") or specificity.get("host_verdict")
    ).upper()
    if status == "PASS":
        return "当前阈值通过"
    if status in {"CAUTION", "WARNING", "PASS_WITH_WARNINGS"}:
        return "已完成，需复核"
    if status == "FAIL":
        return "未通过"
    evidence = _text(candidate.get("evidence_level")).casefold()
    if evidence == "blast_verified":
        return "已完成数据库比对"
    return "尚未完整评估"


def _wf03_comparison_table(candidates: list[Mapping[str, Any]]) -> str:
    if not candidates:
        return '<p class="empty-state">本次没有形成可展示的候选方案。</p>'
    body_parts: list[str] = []
    for index, candidate in enumerate(candidates, start=1):
        forward = _mapping(candidate.get("forward_primer"))
        reverse = _mapping(candidate.get("reverse_primer"))
        probe = _mapping(candidate.get("probe"))
        amplicon = _mapping(candidate.get("amplicon"))
        priority = "优先复核" if index == 1 else "备选"
        primer_tm = (
            f"{_format_number(forward.get('tm_celsius'), digits=1)} / "
            f"{_format_number(reverse.get('tm_celsius'), digits=1)} °C"
        )
        body_parts.append(
            "<tr>"
            f'<th scope="row"><a class="panel-link" href="#result-{index}">方案 {index}</a>'
            f'<span class="business-subline">{priority}</span></th>'
            f"<td>{html.escape(_format_number(amplicon.get('length_bp'), digits=0))} bp</td>"
            f"<td>{html.escape(primer_tm)}</td>"
            f"<td>{html.escape(_format_number(probe.get('tm_celsius'), digits=1))} °C</td>"
            f"<td>{html.escape(_wf03_percent(candidate.get('conservation_score')))}</td>"
            f"<td>{html.escape(_wf03_database_label(candidate))}</td>"
            f"<td>{html.escape(_wf03_score(_candidate_score(candidate)))}</td>"
            "</tr>"
        )
    return (
        '<div class="table-scroll"><table class="comparison-table">'
        "<thead><tr><th>候选方案</th><th>扩增片段</th><th>引物 Tm</th>"
        "<th>探针 Tm</th><th>序列保守性</th><th>数据库核验</th><th>综合评分</th></tr></thead>"
        f"<tbody>{''.join(body_parts)}</tbody></table></div>"
    )


def _wf03_oligo_row(label: str, value: Any) -> str:
    oligo = _mapping(value)
    sequence = _text(oligo.get("sequence_5to3"))
    if not sequence:
        return ""
    return (
        "<tr>"
        f'<th scope="row">{html.escape(label)}</th>'
        f'<td class="sequence-code">{html.escape(sequence)}</td>'
        f"<td>{html.escape(_format_number(oligo.get('length_nt'), digits=0))} nt</td>"
        f"<td>{html.escape(_format_number(oligo.get('tm_celsius'), digits=1))} °C</td>"
        f"<td>{html.escape(_format_number(oligo.get('gc_percent'), digits=1))}%</td>"
        "</tr>"
    )


def _wf03_candidate_notes(candidate: Mapping[str, Any]) -> list[str]:
    notes: list[str] = []
    current_rule_codes: set[str] = set()
    rules = candidate.get("rules") or []
    if _is_sequence(rules):
        for item in rules:
            if not isinstance(item, Mapping):
                continue
            code = _text(item.get("code"))
            current_rule_codes.add(code)
            status = _text(item.get("status")).upper()
            if status not in {"FAIL", "WARNING", "CAUTION", "NOT_EVALUATED"}:
                continue
            if code == "R-16":
                actual = _mapping(item.get("actual"))
                sequence_count = actual.get(
                    "n", candidate.get("conservation_sequence_count")
                )
                notes.append(
                    "广谱保守性评估目前只依据 "
                    f"{_format_number(sequence_count, digits=0)} 条同源序列，"
                    "建议补充至至少 20 条代表性序列后复核。"
                )
                continue
            if code == "R-19":
                _, detail = _wf03_primary_match_detail(candidate)
                notes.append(detail)
                continue
            title = _RULE_BUSINESS_TITLES.get(code, "一项设计检查")
            message = _wf03_reason_message(item.get("message"))
            threshold = _text(item.get("threshold"))
            detail = message or threshold or "结果需要进一步复核"
            notes.append(f"{title}：{detail}。".replace("。。", "。"))
    warnings = candidate.get("warnings") or []
    if _is_sequence(warnings):
        for warning in warnings:
            raw = _text(warning)
            if raw in current_rule_codes:
                continue
            message = _wf03_reason_message(raw)
            if message:
                notes.append(message)
    return list(dict.fromkeys(note for note in notes if note))


def _wf03_candidate_section(
    candidate: Mapping[str, Any],
    index: int,
    fallback_gene: str,
) -> str:
    status_label, status_tone = _wf03_candidate_status(candidate)
    metadata = _mapping(candidate.get("metadata"))
    amplicon = _mapping(candidate.get("amplicon"))
    structure = _mapping(candidate.get("amplicon_structure"))
    gene = _text(metadata.get("gene")) or fallback_gene
    display_gene = _wf03_gene_display(gene)
    fluorophore = _text(metadata.get("fluorophore")) or "未提供"
    quencher = _text(metadata.get("quencher")) or "未提供"
    notes = _wf03_candidate_notes(candidate)
    note_html = (
        '<div class="inline-note"><strong>该方案还需确认</strong><ul>'
        + "".join(f"<li>{html.escape(note)}</li>" for note in notes)
        + "</ul></div>"
        if notes
        else '<p class="quiet-note">当前候选没有额外的计算复核项。</p>'
    )
    oligo_rows = "".join(
        (
            _wf03_oligo_row("正向引物", candidate.get("forward_primer")),
            _wf03_oligo_row("反向引物", candidate.get("reverse_primer")),
            _wf03_oligo_row("探针", candidate.get("probe")),
        )
    )
    amplicon_sequence = _text(amplicon.get("sequence_5to3"))
    amplicon_detail = (
        '<details class="technical-summary"><summary>查看扩增片段序列</summary>'
        f'<p class="sequence-block">{html.escape(amplicon_sequence)}</p></details>'
        if amplicon_sequence
        else ""
    )
    return (
        f'<section class="member-section" id="result-{index}">'
        '<div class="member-heading"><div>'
        f'<p class="section-kicker">04.{index:02d} · 候选方案</p>'
        f"<h3>方案 {index} {_wf03_business_badge(status_label, status_tone)}</h3>"
        f'<span class="business-subline">{html.escape("优先复核" if index == 1 else "备选方案")}</span>'
        "</div>"
        f'<span class="channel-text">综合评分 {html.escape(_wf03_score(_candidate_score(candidate)))}</span>'
        "</div>"
        '<dl class="member-key-data">'
        f'{_fact("候选基因", html.escape(display_gene))}'
        f'{_fact("扩增片段", html.escape(_format_number(amplicon.get("length_bp"), digits=0) + " bp"))}'
        f'{_fact("序列保守性", html.escape(_wf03_percent(candidate.get("conservation_score"))))}'
        f'{_fact("片段结构", _wf03_check_badge(structure.get("status")))}'
        f'{_fact("探针标记建议", html.escape(f"{fluorophore}/{quencher}"))}'
        "</dl>"
        '<div class="table-scroll"><table class="sequence-table oligo-table">'
        "<thead><tr><th>组成</th><th>序列（5′→3′）</th><th>长度</th><th>Tm</th><th>GC</th></tr></thead>"
        f"<tbody>{oligo_rows}</tbody></table></div>"
        f"{note_html}{amplicon_detail}</section>"
    )


def _wf03_actions(report: Mapping[str, Any]) -> list[str]:
    candidates = _candidates(report)
    first = candidates[0] if candidates else {}
    summary = _mapping(
        _mapping(report.get("internal_control")).get("validation_summary")
    )
    actions: list[str] = []

    if first:
        primary_status, _ = _wf03_primary_match_detail(first)
        if primary_status in {
            "CAUTION",
            "WARNING",
            "PASS_WITH_WARNINGS",
            "FAIL",
        }:
            actions.append(
                "优先复核方案 1 的宿主多位点命中：确认正向引物、反向引物和探针的其他匹配位点不会组成非预期扩增片段；必要时重新设计。"
            )
        conservation_rule = _wf03_candidate_rule(first, "R-16")
        if _text(conservation_rule.get("status")).upper() == "NOT_EVALUATED":
            actual = _mapping(conservation_rule.get("actual"))
            sequence_count = actual.get(
                "n", first.get("conservation_sequence_count")
            )
            actions.append(
                "补充具有代表性的同源序列后重新评估广谱保守性：当前使用 "
                f"{_format_number(sequence_count, digits=0)} 条，建议至少 20 条。"
            )

    compatibility = _mapping(summary.get("target_compatibility"))
    if _text(compatibility.get("status")).upper() in {
        "",
        "NOT_EVALUATED",
        "CAUTION",
        "WARNING",
        "FAIL",
    }:
        actions.append(
            "若内标需要与已有靶标同管使用，请提供所有靶标的正向引物、反向引物和探针序列，再检查交叉二聚体与荧光通道兼容性。"
        )

    fallback = _mapping(
        _mapping(report.get("internal_control")).get("fallback")
    )
    options = fallback.get("options") or []
    if _is_sequence(options):
        for option in options:
            if not isinstance(option, Mapping):
                continue
            description = _wf03_reason_message(option.get("description"))
            if description:
                actions.append(description)

    warnings = report.get("warnings") or []
    if _is_sequence(warnings):
        for warning in warnings:
            raw_warning = _text(warning)
            if (
                "existing_targets" in raw_warning
                or "evidence_level" in raw_warning
                or "in_silico_only" in raw_warning
                or raw_warning.startswith("P2:")
            ):
                continue
            message = _wf03_reason_message(warning)
            if message:
                actions.append(message)
    errors = report.get("errors") or []
    if _is_sequence(errors):
        for error in errors:
            if isinstance(error, Mapping):
                message = _wf03_reason_message(
                    error.get("message") or error.get("code")
                )
            else:
                message = _wf03_reason_message(error)
            if message:
                actions.append(message)

    if candidates:
        actions.append(
            "完成小规模 qPCR 实验验证，包括退火温度梯度、扩增效率、线性范围、检出限、重复性和无模板对照；计算结果不能替代实验放行。"
        )
        actions.append(
            "用 geNorm/NormFinder 或同等方法并结合目标样本验证内标表达稳定性。"
        )
    return list(dict.fromkeys(action for action in actions if action))


def _wf03_action_list(report: Mapping[str, Any]) -> str:
    actions = _wf03_actions(report)
    if not actions:
        return '<p class="quiet-note">当前没有额外的处理建议。</p>'
    return '<ol class="action-list">' + "".join(
        f'<li><span>{index:02d}</span><p>{html.escape(action)}</p></li>'
        for index, action in enumerate(actions, start=1)
    ) + "</ol>"


def _wf03_database_summary(report: Mapping[str, Any]) -> str:
    candidates = _candidates(report)
    if not candidates:
        return "未形成结果"
    specificity = _wf03_candidate_specificity(candidates[0])
    status = _text(
        specificity.get("overall_status") or specificity.get("host_verdict")
    ).upper()
    if status == "PASS":
        return "当前阈值通过"
    if status in {"CAUTION", "WARNING", "PASS_WITH_WARNINGS"}:
        return "已完成，需复核"
    if status == "FAIL":
        return "未通过"
    return "尚未完整评估"


def _wf03_decision_card(report: Mapping[str, Any]) -> str:
    label, tone = _wf03_outcome(report)
    review_items = [
        title
        for title, _detail, status in _wf03_validation_checks(report)
        if _text(status).upper()
        not in {"PASS", "SUCCEEDED", "COMPLETED", "AVAILABLE", "SUGGESTION_ONLY"}
    ]
    if tone == "success":
        explanation = (
            "当前要求的计算检查已经完成，可进入实验验证；报告结论不等同于实验性能确认。"
        )
    elif tone == "danger":
        explanation = "本轮没有形成可定稿方案，请按下方建议补充材料或调整设计后重试。"
    elif review_items:
        explanation = "尚未闭环的事项包括：" + "、".join(review_items) + "。"
    else:
        explanation = "部分科学证据尚未闭环，建议完成复核后再定稿。"
    return (
        f'<div class="decision-card decision-card--{html.escape(tone)}">'
        '<p class="decision-label">本次结论</p>'
        f"<h3>{html.escape(label)}</h3>"
        f"<p>{html.escape(explanation)}</p>"
        '<p class="decision-clarification">流程已经运行至报告输出；“需复核”描述的是证据完整度，不是程序执行中断。</p>'
        "</div>"
    )


def _wf03_scope_section(report: Mapping[str, Any]) -> str:
    disclaimer = _text(report.get("disclaimer")) or (
        "本报告为计算设计与数据库证据汇总，仅供科研设计参考；"
        "未经实验验证，不得直接用于临床或生产决策。"
    )
    payload = _mapping(report.get("input"))
    artifact = _mapping(payload.get("input_artifact"))
    provenance = _mapping(report.get("provenance"))
    records: dict[str, Any] = {
        "报告编号": report.get("job_id") or "未提供",
        "生成时间": _wf03_format_timestamp(
            report.get("created_at") or report.get("generated_at")
        ),
    }
    if artifact.get("file_name"):
        records["输入文件"] = artifact["file_name"]
    resolutions = provenance.get("database_resolution") or []
    if _is_sequence(resolutions):
        for item in resolutions:
            if not isinstance(item, Mapping) or not item.get("version"):
                continue
            field = _text(item.get("field"))
            if field == "host_db":
                records["宿主参考数据库版本"] = item["version"]
            elif field == "cross_db":
                records["背景数据库版本"] = item["version"]
    return (
        '<div class="scope-grid">'
        '<div class="scope-item"><strong>本次已覆盖</strong><p>引物探针序列设计、基础热力学、序列保守性、扩增片段结构和数据库比对。</p></div>'
        '<div class="scope-item"><strong>本次不替代</strong><p>表达稳定性评价、完整多重体系验证和任何湿实验性能确认。</p></div>'
        f'<div class="scope-item scope-item--wide"><strong>使用边界</strong><p>{html.escape(disclaimer)}</p></div>'
        "</div>"
        '<details class="technical-summary"><summary>查看报告追溯信息</summary>'
        f"{_technical_table(records, limit=20)}</details>"
    )


def _render_wf03_report_html(report: Mapping[str, Any]) -> bytes:
    candidates = _candidates(report)
    host, gene, _raw_gene = _wf03_host_and_gene(report)
    outcome_label, outcome_tone = _wf03_outcome(report)
    job_id = _text(report.get("job_id"))
    created_at = report.get("created_at") or report.get("generated_at")
    candidate_sections = "".join(
        _wf03_candidate_section(candidate, index, gene)
        for index, candidate in enumerate(candidates, start=1)
    ) or '<p class="empty-state">本次没有形成可展示的候选方案。</p>'
    design_object = (
        f"{host} / {gene}"
        if host != "未提供" or gene != "未提供"
        else "未提供"
    )
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,"/><title>WF-03 内标引物探针设计报告</title>
<style>{_WF02_READABLE_STYLE}</style></head>
<body><main class="report-shell">
<header class="report-header"><div class="header-top"><p class="eyebrow">WF-03 · 内标引物探针设计</p>{_wf03_business_badge(outcome_label, outcome_tone)}</div><h1>内标引物探针设计报告</h1>
<p class="report-intro">{html.escape(_wf03_narrative(report))}</p>
<div class="meta-row"><span><b>报告编号：</b>{html.escape(job_id or '未提供')}</span><span><b>生成时间：</b>{html.escape(_wf03_format_timestamp(created_at))}</span></div></header>
<dl class="key-metrics" aria-label="关键结果">{_fact("设计对象", html.escape(design_object))}{_fact("候选方案", html.escape(f"{len(candidates)} 套"))}{_fact("数据库核验", html.escape(_wf03_database_summary(report)))}{_fact("当前结论", _wf03_business_badge(outcome_label, outcome_tone))}</dl>
<section class="report-section"><div class="section-heading"><span>01</span><h2>本次结论</h2></div>{_wf03_decision_card(report)}</section>
<section class="report-section"><div class="section-heading"><span>02</span><h2>关键检查结果</h2></div>{_wf03_validation_list(report)}</section>
<section class="report-section"><div class="section-heading"><span>03</span><h2>候选方案对比</h2></div>{_wf03_comparison_table(candidates)}</section>
<section class="panel-section"><div class="section-heading"><span>04</span><h2>候选方案明细</h2></div><div class="member-list">{candidate_sections}</div></section>
<section class="report-section"><div class="section-heading"><span>05</span><h2>定稿前需要完成的事项</h2></div>{_wf03_action_list(report)}</section>
<section class="report-section"><div class="section-heading"><span>06</span><h2>方法与使用边界</h2></div>{_wf03_scope_section(report)}</section>
<footer class="report-footer"><p>主视图按业务决策顺序展示；完整机器可读记录随本附件保存，用于审计和追溯。</p></footer>
<script type="application/json" id="workflow-report-data">{_json_for_script(report)}</script>
</main></body></html>"""
    return document.encode("utf-8")


def _wf04_candidate(report: Mapping[str, Any]) -> Mapping[str, Any]:
    candidates = _candidates(report)
    if candidates:
        return candidates[0]
    candidate = report.get("candidate")
    return candidate if isinstance(candidate, Mapping) else {}


def _wf04_species_label(value: Any) -> str:
    raw = _text(value).strip()
    if not raw:
        return "未提供"
    normalized = raw.lower().replace(" ", "_").replace("-", "_")
    return _WF03_HOST_LABELS.get(normalized, raw.replace("_", " "))


def _wf04_reason_message(value: Any) -> str:
    raw = _text(value).strip()
    if not raw:
        return ""
    direct = _WF04_REASON_MESSAGES.get(raw)
    if direct:
        return direct
    if ":" in raw:
        tail = raw.rsplit(":", 1)[-1]
        direct = _WF04_REASON_MESSAGES.get(tail)
        if direct:
            return direct
    replacements = {
        "P2返回失败结论": "数据库检查返回未通过结论。",
        "P2返回需复核结论": "数据库检查已完成，但结果需要人工复核。",
        "P2必需模式未完整执行": "必需的数据库检查尚未全部完成。",
        "P2执行失败": "数据库检查未能完成。",
        "P6_UNAVAILABLE": "扩增片段结构计算资源当前不可用。",
        "TOOL_UNAVAILABLE": "本项所需的计算资源当前不可用。",
        "P1_DEGRADED": "基础参数采用了降级计算结果。",
        "MGB探针改由R-13判定": "本方案采用 MGB 修饰，结果在“MGB 修饰适配性”项目中展示。",
        "WF-01/WF-04不适用": "当前为单方案验证，本项不适用。",
        "非内标候选不适用": "当前不是内标方案，本项不适用。",
        "单靶标不适用": "当前为单对象方案，本项不适用。",
    }
    if raw in replacements:
        return replacements[raw]
    if "R-13" in raw and "MGB" in raw:
        return replacements["MGB探针改由R-13判定"]
    if raw.startswith("P1_"):
        return "基础参数检查尚未获得完整结果。"
    if raw.startswith("P2_"):
        return "数据库检查尚未获得完整结果。"
    if raw.startswith("P3_"):
        return "探针修饰适配性检查尚未获得完整结果。"
    if raw.startswith("P6_"):
        return "扩增片段结构检查尚未获得完整结果。"
    if raw.startswith("WF04_"):
        return "验证流程中的一项检查尚未完成。"
    if raw.isascii() and (
        "_" in raw
        or raw.startswith("R-")
        or (raw.upper() == raw and "-" in raw)
    ):
        return "该项尚未获得可展示的完整结果。"
    return raw


def _wf04_number(value: Any, *, digits: int = 2) -> str:
    if isinstance(value, bool) or value in (None, ""):
        return "未提供"
    return _format_number(value, digits=digits)


def _wf04_measure(value: Any, unit: str, *, digits: int = 2) -> str:
    formatted = _wf04_number(value, digits=digits)
    return formatted if formatted == "未提供" else f"{formatted} {unit}"


def _wf04_oligo(candidate: Mapping[str, Any], role: str) -> Mapping[str, Any]:
    field = {
        "forward": "forward_primer",
        "reverse": "reverse_primer",
        "probe": "probe",
    }.get(role, role)
    return _mapping(candidate.get(field))


def _wf04_end_composition(candidate: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for role in ("forward", "reverse"):
        sequence = _text(_wf04_oligo(candidate, role).get("sequence_5to3")).upper()
        if not sequence:
            continue
        end = sequence[-5:]
        gc_count = end.count("G") + end.count("C")
        repeated = any(base * 3 in end for base in "ACGT")
        parts.append(
            f"{_WF04_ROLE_LABELS[role]}末端 {end}，G/C {gc_count} 个，"
            f"连续 3 个相同碱基：{'是' if repeated else '否'}"
        )
    return "；".join(parts) or "末端序列数据未提供"


def _wf04_specificity_actual(
    report: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> str:
    rows: list[str] = []
    for name, label in (
        ("target", "目标序列核验"),
        ("host", "宿主背景排除"),
        ("cross_species", "非目标背景排查"),
    ):
        branch = _wf04_branch(report, candidate, name)
        status = _wf04_branch_status(branch)
        hit_count = branch.get("blast_hits")
        count_text = (
            f"，匹配记录 {_wf04_number(hit_count, digits=0)} 条"
            if not isinstance(hit_count, bool) and hit_count not in (None, "")
            else "，匹配记录数未提供"
        )
        rows.append(f"{label}：{_display_label(status)}{count_text}")
    return "；".join(rows)


def _wf04_rule_requirement(rule: Mapping[str, Any]) -> str:
    code = _text(rule.get("code"))
    if code in _WF04_RULE_REQUIREMENTS:
        return _WF04_RULE_REQUIREMENTS[code]
    threshold = _wf04_reason_message(rule.get("threshold"))
    return threshold or "按当前任务配置进行检查"


def _wf04_rule_actual(
    rule: Mapping[str, Any],
    candidate: Mapping[str, Any],
    report: Mapping[str, Any],
) -> str:
    code = _text(rule.get("code"))
    status = _text(rule.get("status")).upper()
    actual = rule.get("actual")
    values = _mapping(actual)
    forward = _wf04_oligo(candidate, "forward")
    reverse = _wf04_oligo(candidate, "reverse")
    probe = _wf04_oligo(candidate, "probe")

    if code == "R-01":
        forward_tm = values.get("forward_tm", forward.get("tm_celsius"))
        reverse_tm = values.get("reverse_tm", reverse.get("tm_celsius"))
        delta_tm = values.get("delta_tm")
        if isinstance(delta_tm, bool) or delta_tm in (None, ""):
            try:
                delta_tm = abs(float(forward_tm) - float(reverse_tm))
            except (TypeError, ValueError):
                delta_tm = None
        return (
            f"正向引物 {_wf04_measure(forward_tm, '°C', digits=1)}；"
            f"反向引物 {_wf04_measure(reverse_tm, '°C', digits=1)}；"
            f"差值 {_wf04_measure(delta_tm, '°C', digits=2)}"
        )
    if code == "R-01b":
        return (
            f"正向引物 {_wf04_measure(forward.get('tm_celsius'), '°C', digits=1)}；"
            f"反向引物 {_wf04_measure(reverse.get('tm_celsius'), '°C', digits=1)}"
        )
    if code == "R-02":
        amplicon = _mapping(candidate.get("amplicon"))
        length = actual if not isinstance(actual, (bool, Mapping, list, tuple)) else None
        if length in (None, ""):
            length = amplicon.get("length_bp")
        return f"扩增片段长度 {_wf04_measure(length, 'bp', digits=0)}"
    if code in {"R-03", "R-03b"}:
        return _wf04_end_composition(candidate)
    if code == "R-04":
        forward_length = values.get("forward", forward.get("length_nt"))
        reverse_length = values.get("reverse", reverse.get("length_nt"))
        return (
            f"正向引物 {_wf04_measure(forward_length, 'nt', digits=0)}；"
            f"反向引物 {_wf04_measure(reverse_length, 'nt', digits=0)}"
        )
    if code == "R-05":
        first_base = actual if isinstance(actual, str) and len(actual) == 1 else None
        if not first_base:
            sequence = _text(probe.get("sequence_5to3")).upper()
            first_base = sequence[:1] or None
        return f"探针 5′ 端首个碱基：{first_base or '未提供'}"
    if code == "R-06":
        left_gap = values.get("left_gap_bp")
        right_gap = values.get("right_gap_bp")
        if left_gap in (None, ""):
            try:
                left_gap = int(probe["position_start"]) - int(forward["position_end"])
            except (KeyError, TypeError, ValueError):
                left_gap = None
        if right_gap in (None, ""):
            try:
                right_gap = int(reverse["position_start"]) - int(probe["position_end"])
            except (KeyError, TypeError, ValueError):
                right_gap = None
        return (
            f"左侧间距 {_wf04_measure(left_gap, 'bp', digits=0)}；"
            f"右侧间距 {_wf04_measure(right_gap, 'bp', digits=0)}"
        )
    if code == "R-07":
        delta = actual if not isinstance(actual, (bool, Mapping, list, tuple)) else None
        if delta in (None, ""):
            try:
                mean_primer_tm = (
                    float(forward["tm_celsius"]) + float(reverse["tm_celsius"])
                ) / 2
                delta = float(probe["tm_celsius"]) - mean_primer_tm
            except (KeyError, TypeError, ValueError):
                delta = None
        return (
            f"探针 {_wf04_measure(probe.get('tm_celsius'), '°C', digits=1)}；"
            f"比引物平均 Tm 高 {_wf04_measure(delta, '°C', digits=2)}"
        )
    if code == "R-08":
        length = actual if not isinstance(actual, (bool, Mapping, list, tuple)) else None
        if length in (None, ""):
            length = probe.get("length_nt")
        return f"探针长度 {_wf04_measure(length, 'nt', digits=0)}"
    if code == "R-09":
        return (
            f"正向引物 {_wf04_measure(values.get('forward', forward.get('three_prime_dimer_dg_kcal')), 'kcal/mol')}；"
            f"反向引物 {_wf04_measure(values.get('reverse', reverse.get('three_prime_dimer_dg_kcal')), 'kcal/mol')}"
        )
    if code == "R-10":
        dg_value = values.get("dg_kcal", candidate.get("three_prime_heterodimer_dg_kcal"))
        involved = values.get("three_prime_involved", candidate.get("three_prime_involved"))
        involved_text = "未提供" if involved is None else ("是" if bool(involved) else "否")
        return (
            f"引物间 ΔG {_wf04_measure(dg_value, 'kcal/mol')}；"
            f"涉及 3′ 端：{involved_text}"
        )
    if code == "R-11":
        return "；".join(
            f"{_WF04_ROLE_LABELS[role]} {_wf04_measure(values.get(role, _wf04_oligo(candidate, role).get('hairpin_dg_kcal')), 'kcal/mol')}"
            for role in ("forward", "reverse", "probe")
            if _wf04_oligo(candidate, role)
        ) or "发卡结构数据未提供"
    if code == "R-12":
        return (
            f"引物对 {_wf04_measure(candidate.get('pair_heterodimer_dg_kcal'), 'kcal/mol')}；"
            f"正向引物自二聚体 {_wf04_measure(forward.get('homodimer_dg_kcal'), 'kcal/mol')}；"
            f"反向引物自二聚体 {_wf04_measure(reverse.get('homodimer_dg_kcal'), 'kcal/mol')}"
        )
    if code == "R-13":
        assessment = values or _mapping(candidate.get("mgb_assessment"))
        corrected_tm = assessment.get("corrected_tm_celsius")
        if corrected_tm not in (None, ""):
            return f"修正后 Tm {_wf04_measure(corrected_tm, '°C', digits=1)}"
        if not assessment or status == "NOT_APPLICABLE":
            return "本方案未采用 MGB 修饰"
        return _wf04_reason_message(rule.get("message")) or "修饰适配数据未提供"
    if code == "R-SUPPLEMENT":
        structure = values or _mapping(candidate.get("amplicon_structure"))
        return (
            f"计算结果 {_display_label(structure.get('status') or status)}；"
            f"最低自由能 {_wf04_measure(structure.get('mfe_dg'), 'kcal/mol')}"
        )
    if code == "R-14":
        return "当前为单方案验证，不涉及多重通道组合"
    if code == "R-15":
        if status == "NOT_APPLICABLE":
            return "当前不是内标方案"
        return (
            f"对象 {values.get('gene') or '未提供'}；通道 {values.get('fluorophore') or '未提供'}；"
            f"引物浓度 {_wf04_measure(values.get('primer_concentration_nm'), 'nM', digits=0)}"
        )
    if code == "R-16":
        score = values.get("score", candidate.get("conservation_score"))
        count = values.get("n", candidate.get("conservation_sequence_count"))
        score_text = "未提供"
        if not isinstance(score, bool) and score not in (None, ""):
            try:
                numeric_score = float(score)
                if numeric_score <= 1:
                    numeric_score *= 100
                score_text = f"{_format_number(numeric_score, digits=2)}%"
            except (TypeError, ValueError):
                score_text = _text(score)
        return f"代表性序列 {_wf04_number(count, digits=0)} 条；保守性结果 {score_text}"
    if code == "R-17":
        return "当前为单对象方案，不涉及多扩增片段对比"
    if code == "R-18":
        metadata = _mapping(candidate.get("metadata"))
        missing = values.get("missing") or []
        parts: list[str] = []
        units = {"mv_conc_mm": "mM", "dv_conc_mm": "mM", "oligo_conc_nm": "nM"}
        for key, label in _WF04_METADATA_LABELS.items():
            value = metadata.get(key)
            if value in (None, ""):
                continue
            unit = units.get(key, "")
            parts.append(f"{label} {value}{(' ' + unit) if unit else ''}")
        if missing:
            missing_labels = [
                _WF04_METADATA_LABELS.get(_text(item), _text(item).replace("_", " "))
                for item in missing
            ]
            parts.append("缺少：" + "、".join(missing_labels))
        return "；".join(parts) or "计算参数记录未提供"
    if code == "R-19":
        return _wf04_specificity_actual(report, candidate)
    if code == "R-20":
        evidence = (
            _text(actual)
            if isinstance(actual, str)
            else _text(candidate.get("evidence_level"))
        )
        return _WF04_EVIDENCE_LABELS.get(evidence.lower(), _display_label(evidence))
    if code == "R-21":
        if not values:
            specificity = _wf04_specificity(report, candidate)
            branch_statuses = [
                _wf04_branch_status(_mapping(specificity.get(name)))
                for name in ("target", "host", "cross_species")
            ]
            values = {
                "forward": bool(forward.get("sequence_5to3")),
                "reverse": bool(reverse.get("sequence_5to3")),
                "probe": bool(probe.get("sequence_5to3")),
                "amplicon": bool(
                    _mapping(candidate.get("amplicon")).get("sequence_5to3")
                ),
                "tm": all(
                    item.get("tm_celsius") not in (None, "")
                    for item in (forward, reverse, probe)
                    if item
                ),
                "dg": all(
                    item.get("hairpin_dg_kcal") not in (None, "")
                    for item in (forward, reverse, probe)
                    if item
                ),
                "specificity": bool(branch_statuses)
                and all(
                    item not in {"NOT_EVALUATED", "NOT_CHECKED", "DEGRADED"}
                    for item in branch_statuses
                ),
                "conservation": candidate.get("conservation_score")
                not in (None, ""),
            }
        complete = [
            label
            for key, label in _WF04_COMPLETENESS_LABELS.items()
            if values.get(key) is True
        ]
        missing = [
            label
            for key, label in _WF04_COMPLETENESS_LABELS.items()
            if values.get(key) is False
        ]
        parts = ["已记录：" + "、".join(complete)] if complete else []
        if missing:
            parts.append("未记录：" + "、".join(missing))
        return "；".join(parts) or "完整性明细未提供"
    if status == "NOT_APPLICABLE":
        return _wf04_reason_message(rule.get("message")) or "本次不适用"
    if isinstance(actual, (str, int, float)) and not isinstance(actual, bool):
        return _compact_value(actual)
    return _wf04_reason_message(rule.get("message")) or "本次数据未提供"


def _wf04_context(report: Mapping[str, Any]) -> tuple[str, str, str]:
    payload = _mapping(report.get("input"))
    target = _mapping(report.get("target"))
    target_id = _text(target.get("target_id") or payload.get("target_id")).strip()
    organism = _text(
        target.get("organism_display_name")
        or payload.get("target_species_input")
        or target.get("organism")
        or payload.get("target_species")
        or target_id
    ).strip()
    host = _text(
        payload.get("host_species_input") or payload.get("host_species")
    ).strip()
    return (
        target_id or "未提供",
        _wf04_species_label(organism),
        _wf04_species_label(host),
    )


def _wf04_decision(report: Mapping[str, Any]) -> str:
    candidate = _wf04_candidate(report)
    self_check = _mapping(report.get("self_check"))
    return _text(
        report.get("decision")
        or self_check.get("verdict")
        or candidate.get("verdict")
        or "INCONCLUSIVE"
    ).upper()


def _wf04_outcome(report: Mapping[str, Any]) -> tuple[str, str]:
    decision = _wf04_decision(report)
    if decision == "FAIL":
        return "当前方案不建议使用", "danger"
    if decision in {"INCONCLUSIVE", "PARTIAL", "NOT_EVALUATED"}:
        return "证据不足，暂不能判断", "warning"
    if decision in {"PASS_WITH_WARNING", "PASS_WITH_WARNINGS", "CAUTION"}:
        return "基本满足要求，仍需关注风险", "warning"
    if decision == "PASS":
        return "满足当前计算要求", "success"
    return "验证结论待确认", "neutral"


def _wf04_specificity(
    report: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> Mapping[str, Any]:
    specificity = report.get("specificity")
    if isinstance(specificity, Mapping):
        return specificity
    candidate_specificity = candidate.get("specificity")
    return candidate_specificity if isinstance(candidate_specificity, Mapping) else {}


def _wf04_branch(
    report: Mapping[str, Any],
    candidate: Mapping[str, Any],
    name: str,
) -> Mapping[str, Any]:
    return _mapping(_wf04_specificity(report, candidate).get(name))


def _wf04_branch_status(branch: Mapping[str, Any]) -> str:
    return _text(
        branch.get("verdict") or branch.get("status") or "NOT_EVALUATED"
    ).upper()


def _wf04_aggregate_status(statuses: Sequence[Any]) -> str:
    normalized = [_text(value).upper() for value in statuses if _text(value)]
    if not normalized:
        return "NOT_EVALUATED"
    if any(value in {"FAIL", "FAILED", "ERROR"} for value in normalized):
        return "FAIL"
    if any(
        value in {"WARNING", "CAUTION", "PASS_WITH_WARNINGS", "PARTIAL"}
        for value in normalized
    ):
        return "CAUTION"
    if any(value in {"NOT_EVALUATED", "NOT_CHECKED", "DEGRADED"} for value in normalized):
        return "NOT_EVALUATED"
    return "PASS"


def _wf04_rule_detail(
    rule: Mapping[str, Any],
    candidate: Mapping[str, Any] | None = None,
    report: Mapping[str, Any] | None = None,
) -> str:
    selected_candidate = candidate or {}
    selected_report = report or {}
    code = _text(rule.get("code"))
    title = _RULE_BUSINESS_TITLES.get(code, "其他设计检查")
    status = _text(rule.get("status")).upper() or "NOT_EVALUATED"
    actual = _wf04_rule_actual(rule, selected_candidate, selected_report)
    requirement = _wf04_rule_requirement(rule)
    message = _wf04_reason_message(rule.get("message")).rstrip("。")
    if status == "NOT_APPLICABLE":
        return f"{title}：{actual}。"
    if status in {"NOT_EVALUATED", "NOT_CHECKED", "DEGRADED", "SKIPPED"}:
        reason = message or "本项缺少必要输入或计算结果"
        return f"{title}尚未完成：{reason}；本次数据：{actual}；判定要求：{requirement}。"
    if status in {"FAIL", "FAILED", "ERROR"}:
        return f"{title}未通过；本次数据：{actual}；判定要求：{requirement}。"
    if status in {"WARNING", "CAUTION", "PASS_WITH_WARNINGS"}:
        return f"{title}需要确认；本次数据：{actual}；判定要求：{requirement}。"
    return f"{title}：本次数据为 {actual}；判定要求为 {requirement}。"


def _wf04_target_detail(branch: Mapping[str, Any]) -> tuple[str, str]:
    status = _wf04_branch_status(branch)
    mode = _mapping(branch.get("mode_specific"))
    perfect = mode.get("perfect_query_ids") or []
    matched = [
        _WF04_ROLE_LABELS.get(_text(role), _text(role)) for role in perfect
    ]
    hit_count = branch.get("blast_hits")
    count_text = (
        f"，共记录 {_wf04_number(hit_count, digits=0)} 条匹配结果"
        if not isinstance(hit_count, bool) and hit_count not in (None, "")
        else ""
    )
    if status == "PASS":
        subject = "、".join(matched) if matched else "输入的引物探针"
        return status, f"{subject}均在目标参考序列中获得完整匹配{count_text}。"
    if status == "FAIL":
        return status, f"至少一条引物或探针未能在目标参考序列中获得合格匹配{count_text}。"
    return status, f"目标序列匹配尚未完整评估{count_text}。"


def _wf04_host_detail(branch: Mapping[str, Any], host: str) -> tuple[str, str]:
    status = _wf04_branch_status(branch)
    host_label = host if host != "未提供" else "所配置的宿主"
    hit_count = branch.get("blast_hits")
    count_text = (
        f"共记录 {_wf04_number(hit_count, digits=0)} 条匹配结果，"
        if not isinstance(hit_count, bool) and hit_count not in (None, "")
        else ""
    )
    if status == "PASS":
        return status, f"在{host_label}参考基因组中{count_text}未发现达到当前风险阈值的非预期匹配。"
    if status == "FAIL":
        return status, f"在{host_label}参考基因组中{count_text}发现达到当前风险阈值的非预期匹配。"
    recommendation = _wf04_reason_message(branch.get("recommendation"))
    return (
        status,
        recommendation
        or f"尚未完成{host_label}参考基因组排除检查，当前不能判断宿主背景干扰。",
    )


def _wf04_background_detail(branch: Mapping[str, Any]) -> tuple[str, str]:
    status = _wf04_branch_status(branch)
    hit_count = branch.get("blast_hits")
    mode = _mapping(branch.get("mode_specific"))
    cross_species = mode.get("cross_species") or []
    same_genus = mode.get("same_genus_species") or []
    count_text = _wf04_number(hit_count, digits=0)
    if status == "PASS":
        if hit_count not in (None, "") and not isinstance(hit_count, bool):
            return status, f"背景数据库共记录 {count_text} 条匹配结果，未发现达到当前风险阈值的非目标匹配。"
        return status, "背景数据库中未发现达到当前风险阈值的非目标匹配。"
    if status == "FAIL":
        if hit_count not in (None, "") and not isinstance(hit_count, bool):
            return status, f"背景数据库共记录 {count_text} 条匹配结果，其中存在达到当前风险阈值的非目标匹配。"
        return status, "背景数据库中发现达到当前风险阈值的非目标匹配。"
    if status in {"CAUTION", "WARNING", "PASS_WITH_WARNINGS"}:
        if not cross_species and not same_genus:
            return (
                status,
                f"背景数据库检出 {count_text} 条匹配，但未归类为明确的跨物种或同属物种命中；"
                "需核对命中对象以及背景库是否包含目标参考序列。",
            )
        named: list[str] = []
        for item in list(cross_species) + list(same_genus):
            if isinstance(item, Mapping):
                name = next(
                    (
                        _text(item.get(field)).strip()
                        for field in (
                            "organism_name",
                            "scientific_name",
                            "species",
                            "organism",
                            "subject_title",
                            "accession",
                        )
                        if _text(item.get(field)).strip()
                    ),
                    "未命名匹配记录",
                )
            else:
                name = _text(item).strip() or "未命名匹配记录"
            named.append(name)
        return status, "需要复核的背景匹配包括：" + "、".join(named) + "。"
    recommendation = _wf04_reason_message(branch.get("recommendation"))
    return status, recommendation or "背景数据库排查尚未完成。"


def _wf04_check_badge(status: Any) -> str:
    normalized = _text(status).upper()
    if normalized in {"PASS", "SUCCEEDED", "COMPLETED", "AVAILABLE"}:
        return _business_badge("通过", "success")
    if normalized in {"FAIL", "FAILED", "ERROR", "UNAVAILABLE"}:
        return _business_badge("未通过", "danger")
    if normalized in {"NOT_EVALUATED", "NOT_CHECKED", "DEGRADED", "SKIPPED"}:
        return _business_badge("未完成", "warning")
    if normalized in {"NOT_APPLICABLE", "SUGGESTION_ONLY"}:
        return _business_badge("不适用", "neutral")
    return _business_badge("需复核", "warning")


def _wf04_validation_checks(
    report: Mapping[str, Any],
) -> list[tuple[str, str, str]]:
    candidate = _wf04_candidate(report)
    _target_id, _organism, host = _wf04_context(report)
    checks: list[tuple[str, str, str]] = []

    tm_rule = _candidate_rule(candidate, "R-01")
    tm_status = _text(tm_rule.get("status")).upper() or "NOT_EVALUATED"
    tm_detail = (
        _wf04_rule_detail(tm_rule, candidate, report)
        if tm_rule
        else "尚未获得正反引物退火温度的完整计算结果。"
    )
    checks.append(("正反引物温度匹配", tm_detail, tm_status))

    end_rule = _candidate_rule(candidate, "R-03")
    end_warning = _candidate_rule(candidate, "R-03b")
    end_status = _wf04_aggregate_status(
        [end_rule.get("status"), end_warning.get("status")]
    )
    end_details = [
        _wf04_rule_detail(rule, candidate, report)
        for rule in (end_rule, end_warning)
        if rule and _text(rule.get("status")).upper() != "PASS"
    ]
    checks.append(
        (
            "引物 3′ 端组成",
            " ".join(end_details)
            if end_details
            else "两条引物的 3′ 端组成符合当前规则。",
            end_status,
        )
    )

    probe = _mapping(candidate.get("probe"))
    if probe:
        probe_tm_rule = _candidate_rule(candidate, "R-07")
        probe_tm_status = _text(probe_tm_rule.get("status")).upper() or "NOT_EVALUATED"
        checks.append(
            (
                "探针与引物的温差",
                _wf04_rule_detail(probe_tm_rule, candidate, report)
                if probe_tm_rule
                else "尚未完成探针与引物温差核验。",
                probe_tm_status,
            )
        )

    structure_rules = [
        _candidate_rule(candidate, code) for code in ("R-10", "R-11", "R-12")
    ]
    structure_status = _wf04_aggregate_status(
        [rule.get("status") for rule in structure_rules if rule]
    )
    if structure_status == "PASS":
        structure_detail = (
            "当前计算未触发 3′ 端二聚体、发卡或引物二聚体的阻断阈值；"
            f"引物对异源二聚体 ΔG 为 {_format_number(candidate.get('pair_heterodimer_dg_kcal'), digits=2)} kcal/mol。"
        )
    elif structure_status == "NOT_EVALUATED":
        structure_detail = "引物探针自身结构与二聚体风险尚未完整评估。"
    else:
        structure_detail = "引物探针自身结构或二聚体风险存在需要处理的问题。"
    checks.append(("引物探针自身结构与二聚体", structure_detail, structure_status))

    target_branch = _wf04_branch(report, candidate, "target")
    host_branch = _wf04_branch(report, candidate, "host")
    background_branch = _wf04_branch(report, candidate, "cross_species")
    target_status, target_detail = _wf04_target_detail(target_branch)
    host_status, host_detail = _wf04_host_detail(host_branch, host)
    background_status, background_detail = _wf04_background_detail(
        background_branch
    )
    checks.extend(
        (
            ("目标序列匹配", target_detail, target_status),
            ("宿主基因组排除", host_detail, host_status),
            ("非目标背景排查", background_detail, background_status),
        )
    )

    template_rules = [
        _candidate_rule(candidate, code)
        for code in ("R-02", "R-06", "R-SUPPLEMENT")
    ]
    template_status = _wf04_aggregate_status(
        [rule.get("status") for rule in template_rules if rule]
    )
    if template_status == "PASS":
        amplicon = _mapping(candidate.get("amplicon"))
        template_detail = (
            "已定位扩增片段并完成长度、探针位置和片段结构检查；"
            f"扩增片段长度为 {_format_number(amplicon.get('length_bp'), digits=0)} bp。"
        )
    elif template_status == "NOT_EVALUATED":
        template_detail = (
            "未提供或未定位模板序列，因此扩增片段长度、探针位置和片段二级结构无法完整评估。"
        )
    else:
        template_detail = " ".join(
            _wf04_rule_detail(rule, candidate, report)
            for rule in template_rules
            if rule and _text(rule.get("status")).upper() != "PASS"
        )
    checks.append(("模板与扩增片段", template_detail, template_status))

    conservation_rule = _candidate_rule(candidate, "R-16")
    conservation_status = _text(
        conservation_rule.get("status")
    ).upper() or "NOT_EVALUATED"
    checks.append(
        (
            "目标序列保守性",
            _wf04_rule_detail(conservation_rule, candidate, report)
            if conservation_rule
            else "尚未提供可用于广谱保守性评估的代表性序列集合。",
            conservation_status,
        )
    )
    return checks


def _wf04_validation_list(report: Mapping[str, Any]) -> str:
    checks = _wf04_validation_checks(report)
    return '<ol class="validation-list">' + "".join(
        "<li>"
        f'<span class="step-number">{index:02d}</span>'
        '<div class="step-copy">'
        f"<strong>{html.escape(title)}</strong>"
        f"<span>{html.escape(detail)}</span>"
        "</div>"
        f"{_wf04_check_badge(status)}"
        "</li>"
        for index, (title, detail, status) in enumerate(checks, start=1)
    ) + "</ol>"


def _wf04_rule_data_table(report: Mapping[str, Any]) -> str:
    candidate = _wf04_candidate(report)
    rules = candidate.get("rules") or []
    if not _is_sequence(rules):
        return (
            '<div class="table-scroll rule-data-table"><table class="comparison-table">'
            "<thead><tr><th>检查项目</th><th>本次数据</th><th>判定要求</th><th>结果</th></tr></thead>"
            '<tbody><tr><td colspan="4" class="empty-state">本次没有可展示的逐项检查数据。</td></tr></tbody>'
            "</table></div>"
        )
    rows: list[str] = []
    for rule in rules:
        if not isinstance(rule, Mapping):
            continue
        code = _text(rule.get("code"))
        title = _RULE_BUSINESS_TITLES.get(code, "其他设计检查")
        actual = _wf04_rule_actual(rule, candidate, report)
        requirement = _wf04_rule_requirement(rule)
        status = _text(rule.get("status")).upper() or "NOT_EVALUATED"
        rows.append(
            "<tr>"
            f'<th scope="row">{html.escape(title)}</th>'
            f"<td>{html.escape(actual)}</td>"
            f"<td>{html.escape(requirement)}</td>"
            f"<td>{_wf04_check_badge(status)}</td>"
            "</tr>"
        )
    if not rows:
        return (
            '<div class="table-scroll rule-data-table"><table class="comparison-table">'
            "<thead><tr><th>检查项目</th><th>本次数据</th><th>判定要求</th><th>结果</th></tr></thead>"
            '<tbody><tr><td colspan="4" class="empty-state">本次没有可展示的逐项检查数据。</td></tr></tbody>'
            "</table></div>"
        )
    return (
        '<p class="section-note">每一项均展示具体名称、本次实测或计算数据、判定要求和结果；内部规则编号不在正文中显示。</p>'
        '<div class="table-scroll rule-data-table"><table class="comparison-table">'
        "<thead><tr><th>检查项目</th><th>本次数据</th><th>判定要求</th><th>结果</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _wf04_issue_groups(report: Mapping[str, Any]) -> dict[str, list[str]]:
    candidate = _wf04_candidate(report)
    _target_id, _organism, host = _wf04_context(report)
    blockers: list[str] = []
    gaps: list[str] = []
    cautions: list[str] = []
    rules = candidate.get("rules") or []
    if _is_sequence(rules):
        for rule in rules:
            if not isinstance(rule, Mapping):
                continue
            code = _text(rule.get("code"))
            status = _text(rule.get("status")).upper()
            if status == "FAIL":
                blockers.append(_wf04_rule_detail(rule, candidate, report))
            elif status in {"WARNING", "CAUTION"}:
                cautions.append(_wf04_rule_detail(rule, candidate, report))
            elif status == "NOT_EVALUATED" and code not in {
                "R-02",
                "R-06",
                "R-16",
                "R-SUPPLEMENT",
            }:
                gaps.append(_wf04_rule_detail(rule, candidate, report))

    template_rules = [
        _candidate_rule(candidate, code)
        for code in ("R-02", "R-06", "R-SUPPLEMENT")
    ]
    if any(
        _text(rule.get("status")).upper() == "NOT_EVALUATED"
        for rule in template_rules
        if rule
    ):
        gaps.append(
            "未提供或未定位模板序列，扩增片段长度、探针位置和片段结构尚未完成检查。"
        )

    conservation = _candidate_rule(candidate, "R-16")
    if _text(conservation.get("status")).upper() == "NOT_EVALUATED":
        gaps.append(_wf04_rule_detail(conservation, candidate, report))

    target_branch = _wf04_branch(report, candidate, "target")
    target_status, target_detail = _wf04_target_detail(target_branch)
    if target_status in {"FAIL", "FAILED"}:
        blockers.append(target_detail)
    elif target_status not in {"PASS", "NOT_APPLICABLE"}:
        gaps.append(target_detail)

    host_branch = _wf04_branch(report, candidate, "host")
    host_status, host_detail = _wf04_host_detail(host_branch, host)
    if host_status in {"FAIL", "FAILED"}:
        blockers.append(host_detail)
    elif host_status not in {"PASS", "NOT_APPLICABLE"}:
        gaps.append(host_detail)

    background_branch = _wf04_branch(report, candidate, "cross_species")
    background_status, background_detail = _wf04_background_detail(
        background_branch
    )
    if background_status in {"FAIL", "FAILED"}:
        blockers.append(background_detail)
    elif background_status in {"CAUTION", "WARNING", "PASS_WITH_WARNINGS"}:
        cautions.append(background_detail)
    elif background_status not in {"PASS", "NOT_APPLICABLE"}:
        gaps.append(background_detail)

    self_check = _mapping(report.get("self_check"))
    reason_codes = report.get("decision_reasons") or self_check.get("reason_codes") or []
    if _is_sequence(reason_codes):
        for reason in reason_codes:
            code = _text(reason)
            message = _wf04_reason_message(code)
            if not message:
                continue
            if code in {
                "LOCAL_RULE_FAILURE",
                "P2_DECISION_FAILURE",
                "P3_DECISION_FAILURE",
            }:
                if not blockers:
                    blockers.append(message)
            elif code in _WF04_REASON_MESSAGES and not gaps:
                gaps.append(message)

    errors = report.get("errors") or []
    if _is_sequence(errors):
        for error in errors:
            if not isinstance(error, Mapping):
                continue
            message = _wf04_reason_message(error.get("code")) or _wf04_reason_message(
                error.get("message")
            )
            if message:
                gaps.append(message)

    return {
        "blockers": list(dict.fromkeys(item for item in blockers if item)),
        "gaps": list(dict.fromkeys(item for item in gaps if item)),
        "cautions": list(dict.fromkeys(item for item in cautions if item)),
    }


def _wf04_issue_groups_html(report: Mapping[str, Any]) -> str:
    groups = _wf04_issue_groups(report)
    definitions = (
        ("blockers", "必须修改", "danger", "这些问题会直接阻止当前方案通过。"),
        ("gaps", "需要补充", "warning", "补齐输入或数据库后才能完成判断。"),
        ("cautions", "需要确认", "neutral", "这些提示需要结合检测边界进行人工确认。"),
    )
    sections: list[str] = []
    for key, title, tone, intro in definitions:
        items = groups[key]
        if not items:
            continue
        sections.append(
            f'<section class="issue-group issue-group--{tone}">'
            f"<h3>{html.escape(title)}</h3><p>{html.escape(intro)}</p><ul>"
            + "".join(f"<li>{html.escape(item)}</li>" for item in items)
            + "</ul></section>"
        )
    return (
        '<div class="issue-groups">' + "".join(sections) + "</div>"
        if sections
        else '<p class="quiet-note">当前报告没有额外问题项。</p>'
    )


def _wf04_sequence_table(candidate: Mapping[str, Any]) -> str:
    role_values = (
        ("正向引物", _mapping(candidate.get("forward_primer"))),
        ("反向引物", _mapping(candidate.get("reverse_primer"))),
        ("探针", _mapping(candidate.get("probe"))),
    )
    rows: list[str] = []
    for label, oligo in role_values:
        sequence = _text(oligo.get("sequence_5to3"))
        if not sequence:
            continue
        rows.append(
            "<tr>"
            f'<th scope="row">{html.escape(label)}</th>'
            f'<td class="sequence-code">{html.escape(sequence)}</td>'
            f"<td>{html.escape(_format_number(oligo.get('length_nt'), digits=0))} nt</td>"
            f"<td>{html.escape(_format_number(oligo.get('tm_celsius'), digits=1))} °C</td>"
            f"<td>{html.escape(_format_number(oligo.get('gc_percent'), digits=1))}%</td>"
            "</tr>"
        )
    if not rows:
        return '<p class="empty-state">没有可展示的输入序列。</p>'
    return (
        '<div class="table-scroll"><table class="comparison-table">'
        "<thead><tr><th>组成</th><th>序列（5′→3′）</th><th>长度</th><th>Tm</th><th>GC</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _wf04_input_section(report: Mapping[str, Any]) -> str:
    candidate = _wf04_candidate(report)
    target_id, organism, host = _wf04_context(report)
    outcome_label, outcome_tone = _wf04_outcome(report)
    tm_rule = _candidate_rule(candidate, "R-01")
    tm_actual = _mapping(tm_rule.get("actual"))
    probe_tm_rule = _candidate_rule(candidate, "R-07")
    forward = _wf04_oligo(candidate, "forward")
    reverse = _wf04_oligo(candidate, "reverse")
    probe = _wf04_oligo(candidate, "probe")
    tm_delta = tm_actual.get("delta_tm")
    if isinstance(tm_delta, bool) or tm_delta in (None, ""):
        try:
            tm_delta = abs(float(forward["tm_celsius"]) - float(reverse["tm_celsius"]))
        except (KeyError, TypeError, ValueError):
            tm_delta = None
    probe_delta = probe_tm_rule.get("actual")
    if isinstance(probe_delta, (bool, Mapping, list, tuple)) or probe_delta in (None, ""):
        try:
            probe_delta = float(probe["tm_celsius"]) - (
                float(forward["tm_celsius"]) + float(reverse["tm_celsius"])
            ) / 2
        except (KeyError, TypeError, ValueError):
            probe_delta = None
    amplicon = _mapping(candidate.get("amplicon"))
    amplicon_length = amplicon.get("length_bp")
    amplicon_label = (
        f"{_format_number(amplicon_length, digits=0)} bp"
        if amplicon_length not in (None, 0, "")
        else "未定位"
    )
    target_label = organism if organism != "未提供" else target_id
    return (
        '<section class="member-section">'
        '<div class="member-heading"><div>'
        '<p class="section-kicker">04.01 · 当前输入</p>'
        f"<h3>原始方案 {_business_badge(outcome_label, outcome_tone)}</h3>"
        '<span class="business-subline">本报告只验证输入方案，不自动改写序列。</span>'
        "</div></div>"
        '<dl class="member-key-data">'
        f'{_fact("检测对象", html.escape(target_label))}'
        f'{_fact("宿主", html.escape(host))}'
        f'{_fact("引物 Tm 差", html.escape(_wf04_measure(tm_delta, "°C", digits=2)))}'
        f'{_fact("探针温差", html.escape(_wf04_measure(probe_delta, "°C", digits=2)))}'
        f'{_fact("扩增片段", html.escape(amplicon_label))}'
        "</dl>"
        f"{_wf04_sequence_table(candidate)}</section>"
    )


def _wf04_decision_card(report: Mapping[str, Any]) -> str:
    label, tone = _wf04_outcome(report)
    decision = _wf04_decision(report)
    groups = _wf04_issue_groups(report)
    blocker_count = len(groups["blockers"])
    retry = _mapping(report.get("retry"))
    if tone == "danger":
        explanation = (
            f"当前输入方案有 {blocker_count} 项必须修改的问题。"
            if blocker_count
            else "当前输入方案存在阻断问题。"
        )
    elif decision in {"INCONCLUSIVE", "PARTIAL", "NOT_EVALUATED"}:
        explanation = "当前没有形成完整证据，补齐缺失检查后才能定稿。"
    elif tone == "warning":
        explanation = "当前方案基本满足计算要求，但仍有需要确认的注意项。"
    else:
        explanation = "当前计算结果可以进入实验验证阶段。"
    if retry.get("allowed") and blocker_count:
        clarification = (
            "报告中的“可补充检查”仅用于补齐数据库或模板证据，不会自动修复输入序列；"
            "修改序列后应创建新的验证任务。"
        )
    elif retry.get("allowed"):
        clarification = "可以补齐缺失输入后重新运行未完成的检查。"
    else:
        clarification = "最终使用前仍需完成实验验证。"
    return (
        f'<div class="decision-card decision-card--{html.escape(tone)}">'
        '<p class="decision-label">本次结论</p>'
        f"<h3>{html.escape(label)}</h3>"
        f"<p>{html.escape(explanation)}</p>"
        f'<p class="decision-clarification">{html.escape(clarification)}</p>'
        "</div>"
    )


def _wf04_narrative(report: Mapping[str, Any]) -> str:
    _target_id, organism, _host = _wf04_context(report)
    candidate = _wf04_candidate(report)
    target_status, _detail = _wf04_target_detail(
        _wf04_branch(report, candidate, "target")
    )
    outcome_label, _tone = _wf04_outcome(report)
    target_phrase = "目标匹配已通过" if target_status == "PASS" else "目标匹配尚未通过"
    return (
        f"已完成对 {organism} 现有引物探针方案的计算验证。"
        f"{target_phrase}；当前结论为“{outcome_label}”。"
        "下方按检查结果、问题分级和处理顺序展示。"
    )


def _wf04_actions(report: Mapping[str, Any]) -> list[str]:
    candidate = _wf04_candidate(report)
    actions: list[str] = []
    failed_codes = {
        _text(rule.get("code"))
        for rule in candidate.get("rules") or []
        if isinstance(rule, Mapping) and _text(rule.get("status")).upper() == "FAIL"
    }
    if "R-01" in failed_codes:
        actions.append("调整正反向引物，使两者 Tm 差不超过 2 °C。")
    if "R-03" in failed_codes:
        actions.append("调整不合格引物的 3′ 端组成，使末端 5 nt 中 G/C 不超过 2 个。")
    if "R-07" in failed_codes:
        actions.append("调整探针或引物组合，使探针 Tm 比两条引物平均值高 8–10 °C。")
    if failed_codes:
        actions.append("修改引物或探针序列后，新建验证任务并重新提交完整方案。")

    template_rules = [
        _candidate_rule(candidate, code)
        for code in ("R-02", "R-06", "R-SUPPLEMENT")
    ]
    if any(
        _text(rule.get("status")).upper() == "NOT_EVALUATED"
        for rule in template_rules
        if rule
    ):
        actions.append("补充目标模板序列，以完成扩增片段定位和模板相关检查。")

    host_branch = _wf04_branch(report, candidate, "host")
    if _wf04_branch_status(host_branch) not in {"PASS", "NOT_APPLICABLE"}:
        actions.append("配置正确的宿主参考数据库后，重新运行宿主背景排除检查。")

    background_branch = _wf04_branch(report, candidate, "cross_species")
    if _wf04_branch_status(background_branch) in {
        "CAUTION",
        "WARNING",
        "PASS_WITH_WARNINGS",
        "FAIL",
    }:
        actions.append("复核背景数据库命中对象，并确认背景库是否包含目标参考序列。")

    specificity_rule = _candidate_rule(candidate, "R-19")
    if _text(specificity_rule.get("status")).upper() in {
        "NOT_EVALUATED",
        "NOT_CHECKED",
        "DEGRADED",
    }:
        actions.append("确认目标、宿主和非目标背景三项数据库检查均使用正式资源并保存完整记录。")

    completeness_rule = _candidate_rule(candidate, "R-21")
    if _text(completeness_rule.get("status")).upper() == "NOT_EVALUATED":
        actions.append("补齐逐项验证数据中标记为“未记录”的内容后再定稿。")

    actions.append("完成实验验证后再确定方案是否可用于正式检测。")
    return list(dict.fromkeys(action for action in actions if action))


def _wf04_action_list(report: Mapping[str, Any]) -> str:
    actions = _wf04_actions(report)
    return '<ol class="action-list">' + "".join(
        f'<li><span>{index:02d}</span><p>{html.escape(action)}</p></li>'
        for index, action in enumerate(actions, start=1)
    ) + "</ol>"


def _wf04_local_result_label(report: Mapping[str, Any]) -> str:
    groups = _wf04_issue_groups(report)
    blocker_count = len(groups["blockers"])
    if blocker_count:
        return f"{blocker_count} 项未通过"
    if groups["gaps"]:
        return "有检查未完成"
    if groups["cautions"]:
        return "通过，需关注提示"
    return "通过"


def _wf04_evidence_label(report: Mapping[str, Any]) -> str:
    candidate = _wf04_candidate(report)
    statuses = [
        _wf04_branch_status(_wf04_branch(report, candidate, name))
        for name in ("target", "host", "cross_species")
    ]
    if all(status == "PASS" for status in statuses):
        return "三项检查已完成"
    if any(status in {"FAIL", "FAILED"} for status in statuses):
        return "存在未通过项"
    if any(status in {"NOT_CHECKED", "NOT_EVALUATED", "DEGRADED"} for status in statuses):
        return "有检查未完成"
    return "已完成，需复核"


def _wf04_scope_section(report: Mapping[str, Any]) -> str:
    candidate = _wf04_candidate(report)
    disclaimer = _text(report.get("disclaimer")) or (
        "本报告为计算验证结果，仅供科研设计参考；未经实验验证，"
        "不得直接用于临床或生产决策。"
    )
    groups = _wf04_issue_groups(report)
    completed_items: list[str] = []
    if _wf04_oligo(candidate, "forward") and _wf04_oligo(candidate, "reverse"):
        completed_items.append("输入序列参数计算")
    for name, label in (
        ("target", "目标序列匹配"),
        ("host", "宿主背景排除"),
        ("cross_species", "非目标背景排查"),
    ):
        status = _wf04_branch_status(_wf04_branch(report, candidate, name))
        if status not in {"NOT_EVALUATED", "NOT_CHECKED", "DEGRADED", "SKIPPED"}:
            completed_items.append(label)
    completed = (
        "、".join(completed_items) + "。"
        if completed_items
        else "当前尚无可确认的已完成检查。"
    )
    missing_items: list[str] = []
    if groups["blockers"]:
        missing_items.append("输入方案修改")
    if groups["gaps"]:
        missing_items.append("缺失证据补充")
    missing_items.append("实验性能确认")
    missing = "、".join(dict.fromkeys(missing_items)) + "。"

    provenance = _mapping(report.get("provenance"))
    records: dict[str, Any] = {
        "报告编号": report.get("job_id") or "未提供",
        "生成时间": _business_format_timestamp(
            report.get("created_at") or report.get("generated_at")
        ),
    }
    resolutions = provenance.get("database_resolution") or []
    if _is_sequence(resolutions):
        for item in resolutions:
            if not isinstance(item, Mapping) or not item.get("version"):
                continue
            field = _text(item.get("field"))
            if field == "target_db":
                records["目标参考数据库版本"] = item["version"]
            elif field == "host_db":
                records["宿主参考数据库版本"] = item["version"]
            elif field == "cross_db":
                records["背景数据库版本"] = item["version"]
    target_branch = _wf04_branch(report, candidate, "target")
    if target_branch.get("method"):
        records["目标匹配方式"] = (
            "数据库序列比对"
            if _text(target_branch.get("method")).lower() == "blastn"
            else "计算检查"
        )
    return (
        '<div class="scope-grid">'
        f'<div class="scope-item"><strong>本次已覆盖</strong><p>{html.escape(completed)}</p></div>'
        f'<div class="scope-item"><strong>仍需完成</strong><p>{html.escape(missing)}</p></div>'
        f'<div class="scope-item scope-item--wide"><strong>使用边界</strong><p>{html.escape(disclaimer)}</p></div>'
        "</div>"
        '<details class="technical-summary"><summary>查看报告追溯信息</summary>'
        f"{_technical_table(records, limit=20)}</details>"
    )


def _render_wf04_report_html(report: Mapping[str, Any]) -> bytes:
    target_id, organism, _host = _wf04_context(report)
    outcome_label, outcome_tone = _wf04_outcome(report)
    job_id = _text(report.get("job_id"))
    created_at = report.get("created_at") or report.get("generated_at")
    target_label = organism if organism != "未提供" else target_id
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,"/><title>已有引物探针验证报告</title>
<style>{_WF02_READABLE_STYLE}</style></head>
<body><main class="report-shell">
<header class="report-header"><div class="header-top"><p class="eyebrow">已有方案 · 引物探针验证</p>{_business_badge(outcome_label, outcome_tone)}</div><h1>已有引物探针验证报告</h1>
<p class="report-intro">{html.escape(_wf04_narrative(report))}</p>
<div class="meta-row"><span><b>报告编号：</b>{html.escape(job_id or '未提供')}</span><span><b>生成时间：</b>{html.escape(_business_format_timestamp(created_at))}</span></div></header>
<dl class="key-metrics" aria-label="关键结果">{_fact("验证对象", html.escape(target_label))}{_fact("输入方案", html.escape(_wf04_local_result_label(report)))}{_fact("特异性证据", html.escape(_wf04_evidence_label(report)))}{_fact("当前结论", _business_badge(outcome_label, outcome_tone))}</dl>
<section class="report-section"><div class="section-heading"><span>01</span><h2>本次结论</h2></div>{_wf04_decision_card(report)}</section>
<section class="report-section"><div class="section-heading"><span>02</span><h2>关键检查结果</h2></div>{_wf04_validation_list(report)}</section>
<section class="report-section"><div class="section-heading"><span>03</span><h2>逐项验证数据</h2></div>{_wf04_rule_data_table(report)}</section>
<section class="panel-section"><div class="section-heading"><span>04</span><h2>输入方案与参数</h2></div>{_wf04_input_section(report)}</section>
<section class="report-section"><div class="section-heading"><span>05</span><h2>问题分级</h2></div>{_wf04_issue_groups_html(report)}</section>
<section class="report-section"><div class="section-heading"><span>06</span><h2>建议的处理顺序</h2></div>{_wf04_action_list(report)}</section>
<section class="report-section"><div class="section-heading"><span>07</span><h2>方法与使用边界</h2></div>{_wf04_scope_section(report)}</section>
<footer class="report-footer"><p>主视图按用户决策顺序展示；完整机器可读记录随本附件保存，用于审计和追溯。</p></footer>
<script type="application/json" id="workflow-report-data">{_json_for_script(report)}</script>
</main></body></html>"""
    return document.encode("utf-8")


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
.decision-card{padding:24px 26px;border-left:4px solid var(--muted);background:var(--soft)}.decision-card--success{border-color:var(--success)}.decision-card--warning{border-color:var(--warning)}.decision-card--danger{border-color:var(--danger)}.decision-label{margin:0 0 5px;color:var(--muted);font-size:11px;font-weight:700;letter-spacing:.08em}.decision-card h3{font-size:23px}.decision-card p{margin:10px 0 0;color:#4c5359;font-size:14px}.decision-card .decision-clarification{padding-top:10px;border-top:1px solid var(--line);color:var(--ink);font-weight:600}
.table-scroll{overflow-x:auto}table{border-collapse:collapse;width:100%}.comparison-table{min-width:720px;font-size:13px}th,td{padding:11px 12px;text-align:left;border-bottom:1px solid var(--line);vertical-align:middle}thead th{background:var(--soft);color:#535b62;font-size:11px;font-weight:650}tbody th{font-size:12px}.section-note{margin:0 0 14px;color:var(--muted);font-size:12px}.rule-data-table .comparison-table{min-width:940px}.rule-data-table th:first-child{width:18%}.rule-data-table td:nth-child(2){width:34%;color:var(--ink)}.rule-data-table td:nth-child(3){width:34%;color:var(--muted)}.rule-data-table td:last-child{white-space:nowrap}.panel-link{display:block;color:var(--accent);font-weight:700;text-decoration:none}.trace-id{display:block;margin-top:3px;color:#858c92;font:11px/1.45 Consolas,"SFMono-Regular",monospace;overflow-wrap:anywhere}
.business-subline{display:block;margin-top:3px;color:var(--muted);font-size:11px;font-weight:400}.oligo-table{min-width:700px}.sequence-code{font-family:Consolas,"SFMono-Regular",monospace;font-weight:650;letter-spacing:.035em;overflow-wrap:anywhere;word-break:break-all}.sequence-block{margin:12px 0 0;padding:12px 14px;background:var(--soft);font:12px/1.7 Consolas,"SFMono-Regular",monospace;overflow-wrap:anywhere;word-break:break-all}
.panel-section{padding-top:48px;scroll-margin-top:18px;border-bottom:2px solid var(--ink)}.panel-heading,.member-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:24px}.member-heading h3 .badge{margin-left:9px}.panel-key-data,.member-key-data{display:grid;margin:22px 0 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}.member-key-data{grid-template-columns:1.2fr repeat(4,minmax(0,1fr))}.member-key-data .key-item{padding:12px 14px}.member-key-data .key-item+.key-item{border-left:1px solid var(--line)}
.inline-note{margin:18px 0 0;padding-left:14px;border-left:2px solid #aeb5ba;color:#4c5359;font-size:13px}.inline-note strong{color:var(--ink)}.inline-note ul{margin:5px 0 0;padding-left:20px}.quiet-note{margin:15px 0 0;color:var(--muted);font-size:12px}
.action-list{margin:0;padding:0;list-style:none;border-top:1px solid var(--line)}.action-list li{display:grid;grid-template-columns:48px minmax(0,1fr);gap:14px;padding:15px 0;border-bottom:1px solid var(--line)}.action-list li>span{color:var(--accent);font:700 12px/1.8 Consolas,monospace}.action-list p{margin:0;color:#454c52;font-size:14px}.scope-grid{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--line);border:1px solid var(--line)}.scope-item{padding:18px 20px;background:#fff}.scope-item--wide{grid-column:1/-1}.scope-item strong{font-size:13px}.scope-item p{margin:5px 0 0;color:#4f565c;font-size:13px}
.issue-groups{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.issue-group{padding:18px 20px;border-top:3px solid var(--muted);background:var(--soft)}.issue-group--danger{border-color:var(--danger)}.issue-group--warning{border-color:var(--warning)}.issue-group--neutral{border-color:var(--accent)}.issue-group h3{font-size:16px}.issue-group>p{margin:5px 0 0;color:var(--muted);font-size:12px}.issue-group ul{margin:13px 0 0;padding-left:19px}.issue-group li{margin:7px 0;color:#454c52;font-size:13px}
.member-list{margin-top:22px}.member-section{padding:30px 0 6px;border-top:1px solid var(--line)}.member-section:first-child{border-top:0}.channel-text{color:var(--muted);font-size:12px}.technical-summary{margin-top:20px;padding-top:14px;border-top:1px solid var(--line)}.technical-summary summary{cursor:pointer;color:var(--accent);font-size:13px;font-weight:700}.technical-summary[open] summary{margin-bottom:12px}.sequence-table{min-width:720px;margin-top:14px;font-size:12px}.technical-table th:first-child{width:32%}.code-value{font-family:Consolas,"SFMono-Regular",monospace;overflow-wrap:anywhere;word-break:break-word}.empty-value,.empty-state{color:var(--muted);font-weight:400}.empty-state{padding:18px 0;text-align:center}.issue-list{margin:0;padding:0;list-style:none;border-top:1px solid var(--line)}.issue-list li{display:grid;grid-template-columns:120px minmax(0,1fr);gap:16px;padding:12px 0;border-bottom:1px solid var(--line);font-size:13px}.issue-list span{color:#4f565c}.report-footer{padding:24px 0;color:var(--muted);font-size:11px}.report-footer p{margin:3px 0}
@media(max-width:760px){.report-shell{width:min(100% - 28px,1040px)}.report-header{padding-top:30px}.key-metrics{grid-template-columns:1fr 1fr}.key-metrics .key-item:nth-child(3){border-left:0;border-top:1px solid var(--line)}.key-metrics .key-item:nth-child(4){border-top:1px solid var(--line)}.step-copy{grid-template-columns:1fr;gap:2px}.member-key-data{grid-template-columns:1fr 1fr}.member-key-data .key-item{border-bottom:1px solid var(--line)}.member-key-data .key-item:nth-child(odd){border-left:0}.issue-list li{grid-template-columns:1fr;gap:3px}.scope-grid,.issue-groups{grid-template-columns:1fr}.scope-item--wide{grid-column:auto}.rule-data-table{overflow:visible}.rule-data-table .comparison-table,.rule-data-table tbody,.rule-data-table tr,.rule-data-table th,.rule-data-table td{display:block;width:100%;min-width:0}.rule-data-table thead{display:none}.rule-data-table tr{padding:15px 0;border-bottom:1px solid var(--line)}.rule-data-table th,.rule-data-table td{padding:4px 0;border:0}.rule-data-table th:first-child{width:auto;font-size:15px}.rule-data-table td:nth-child(2),.rule-data-table td:nth-child(3){width:auto;color:var(--ink)}.rule-data-table td:last-child{white-space:normal}.rule-data-table td::before{display:block;margin-bottom:1px;color:var(--muted);font-size:10px;font-weight:650}.rule-data-table td:nth-child(2)::before{content:"本次数据"}.rule-data-table td:nth-child(3)::before{content:"判定要求"}.rule-data-table td:nth-child(4)::before{content:"结果"}.rule-data-table td.empty-state::before{content:none}}
@media(max-width:520px){.header-top,.panel-heading,.member-heading{align-items:flex-start;flex-direction:column}.validation-list li{grid-template-columns:34px minmax(0,1fr)}.validation-list li>.badge{grid-column:2}.meta-row{display:grid;gap:4px}}
@media print{@page{size:A4 portrait;margin:12mm}.report-shell{width:100%;margin:0}.report-header{padding-top:0}.panel-section{break-before:page}.member-section{break-inside:avoid}details:not([open])>*:not(summary){display:block!important}.table-scroll{overflow:visible}}
"""


def render_workflow_report_html(report: dict[str, Any]) -> bytes:
    """Render a deterministic, readable view of one WF-03/WF-04 result."""

    workflow = _text(report.get("workflow")) or "WORKFLOW"
    if workflow == "WF-03":
        return _render_wf03_report_html(report)
    if workflow == "WF-04":
        return _render_wf04_report_html(report)
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
