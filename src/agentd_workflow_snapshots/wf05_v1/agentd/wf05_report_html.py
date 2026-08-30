"""Readable deterministic HTML report owned by the isolated WF-05 runtime.

The workflow owns every scientific value and decision. This module only
translates the persisted result into a reader-first view while preserving the
complete machine-readable payload in a non-visible JSON block.
"""

from __future__ import annotations

import html
import json
import math
from datetime import datetime
from typing import Any, Mapping

from .errors import InputValidationError


WORKFLOW_REPORT_RENDERER_VERSION = "wf05-readable-html-v3"

_STATUS_LABELS = {
    "SUCCEEDED": "分析已完成",
    "COMPLETED": "已完成",
    "PASS": "通过",
    "PASS_WITH_WARNING": "通过（含注意项）",
    "PASS_WITH_WARNINGS": "通过（含注意项）",
    "PARTIAL": "部分完成",
    "ACCEPTABLE": "可继续使用，建议复核",
    "ROBUST": "耐受性良好",
    "REDESIGN_RECOMMENDED": "建议重新设计",
    "INCONCLUSIVE": "暂无法判断",
    "FAIL": "未通过",
    "FAILED": "分析未完成",
    "ERROR": "未完成",
    "NOT_RUN": "未运行",
    "NOT_EVALUATED": "未评估",
    "NOT_APPLICABLE": "不适用",
    "WARNING": "需注意",
    "RETRY": "修复后重试",
    "HANDOFF_WF01": "新建设计任务",
    "END": "无需调整",
    "RECOMMENDED": "建议执行",
    "HIGH": "高风险",
    "MEDIUM": "中风险",
    "LOW": "低风险",
}

_STEP_LABELS = {
    "WF05_STEP1_SEQUENCE_SET": "输入序列检查",
    "WF05_STEP2_ALIGNMENT": "多序列比对",
    "WF05_STEP3_VARIANT_SCAN": "结合位点变异扫描",
    "WF05_STEP4_TM_IMPACT": "温度影响评估",
    "WF05_STEP5_COVERAGE": "覆盖表现统计",
    "WF05_STEP6_DECISION": "综合结论生成",
}

_ROLE_LABELS = {
    "forward": "正向引物",
    "forward_primer": "正向引物",
    "reverse": "反向引物",
    "reverse_primer": "反向引物",
    "probe": "探针",
}

_REASON_MESSAGES = {
    "EXACT_COVERAGE_BELOW_ACCEPTABLE_THRESHOLD": (
        "完全匹配比例低于当前可接受要求。"
    ),
    "HIGH_FREQUENCY_THREE_PRIME_VARIANT": (
        "引物 3′ 端关键区域发现达到关注频率的变化。"
    ),
    "REDESIGN_POLICY_TRIGGERED": "综合评估达到重新设计条件。",
    "ACCEPTABLE_WITH_REVIEW": (
        "当前覆盖表现可以继续使用，但重点位点仍需复核。"
    ),
    "ROBUST_POLICY_MET": "覆盖表现和重点位点均达到当前耐受性要求。",
    "INCONCLUSIVE": "现有运行证据不足，暂时无法形成可靠结论。",
    "TOOL_UNAVAILABLE": "运行所需的计算资源暂不可用。",
    "DEPENDENCY_UNAVAILABLE": "运行所需的计算资源暂不可用。",
}

_WARNING_MESSAGES = {
    "当前结果建议创建独立WF-01新任务；WF-05未自动执行跨工作流调用": (
        "建议基于本次风险位置新建独立设计任务；系统没有自动发起后续流程。"
    ),
    "已有输入未通过基础规则检查；该结果不改变WF-05执行状态": (
        "当前引物或探针存在基础设计检查未通过项；"
        "这不代表本次变异分析执行失败。"
    ),
    "MAFFT不可用；将输入视为已比对等长序列": (
        "标准比对工具暂不可用；本次使用兼容方式处理等长序列，"
        "建议在标准计算条件恢复后复核。"
    ),
}

_RULE_LABELS = {
    "R-01": "引物退火温度",
    "R-01b": "低温引物风险",
    "R-02": "扩增片段长度",
    "R-03": "引物 3′ 端组成",
    "R-03b": "引物 3′ 端稳定性",
    "R-04": "引物长度",
    "R-05": "探针 5′ 端组成",
    "R-06": "探针与引物间距",
    "R-07": "探针与引物温度差",
    "R-08": "探针长度",
    "R-09": "3′ 端自二聚体",
    "R-10": "3′ 端引物间二聚体",
    "R-11": "发卡结构",
    "R-12": "引物二聚体",
    "R-13": "探针修饰适配性",
    "R-SUPPLEMENT": "扩增片段二级结构",
    "R-14": "多重通道兼容性",
    "R-15": "辅助通道设置",
    "R-16": "代表性序列覆盖",
    "R-17": "多扩增片段区分度",
    "R-18": "计算参数完整性",
    "R-19": "数据库特异性",
    "R-20": "证据可追溯性",
    "R-21": "结果记录完整性",
}

_RULE_REQUIREMENTS = {
    "R-01": "两条引物的温度范围及相互差值应满足当前设计要求。",
    "R-01b": "温度偏低的引物需要在实验条件中重点复核。",
    "R-02": "扩增片段长度应处于当前建议范围。",
    "R-03": "引物 3′ 端碱基组成应避免过强结合和连续重复。",
    "R-03b": "引物 3′ 端稳定性存在需要关注的组成特征。",
    "R-04": "正向和反向引物长度应处于当前建议范围。",
    "R-05": "探针 5′ 端首个碱基应满足当前设计要求。",
    "R-06": "探针与两侧引物之间应保留足够间距。",
    "R-07": "探针温度应高于两条引物的平均温度并处于建议差值范围。",
    "R-08": "探针长度应与当前修饰类型相匹配。",
    "R-09": "两条引物 3′ 端自二聚体风险应处于可接受范围。",
    "R-10": "涉及 3′ 端的引物间二聚体风险应处于可接受范围。",
    "R-11": "发卡结构稳定性存在需要关注的结果。",
    "R-12": "引物二聚体稳定性存在需要关注的结果。",
    "R-13": "采用修饰探针时，其有效温度应处于建议范围。",
    "R-SUPPLEMENT": "扩增片段二级结构尚未完成或需要复核。",
    "R-16": "代表性序列数量和覆盖结果应达到当前建议要求。",
    "R-18": "计算软件、方法和浓度参数应记录完整。",
    "R-19": "数据库特异性证据尚未完成或需要复核。",
    "R-20": "报告应明确计算证据范围及实验验证状态。",
    "R-21": "序列、温度、结构、数据库和覆盖证据应记录完整。",
}

_EVIDENCE_LABELS = {
    "in_silico_only": "仅完成计算评估，尚未完成实验验证",
    "blast_verified": "已完成数据库核验，尚未完成实验验证",
    "experimentally_validated": "已记录实验验证证据",
}

_SUCCESS_STATES = {
    "SUCCEEDED",
    "COMPLETED",
    "PASS",
    "ROBUST",
    "END",
}
_WARNING_STATES = {
    "PARTIAL",
    "ACCEPTABLE",
    "WARNING",
    "PASS_WITH_WARNING",
    "PASS_WITH_WARNINGS",
    "NOT_EVALUATED",
    "RETRY",
}
_DANGER_STATES = {
    "FAILED",
    "ERROR",
    "FAIL",
    "REDESIGN_RECOMMENDED",
}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _json_for_script(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _number(value: Any, *, digits: int = 2, suffix: str = "") -> str:
    if isinstance(value, bool) or value in (None, ""):
        return "未提供"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _text(value)
    if not math.isfinite(number):
        return "未提供"
    visible = (
        f"{number:.0f}"
        if digits == 0
        else f"{number:.{digits}f}".rstrip("0").rstrip(".")
    )
    return f"{visible}{suffix}"


def _integer(value: Any) -> str:
    return _number(value, digits=0)


def _percent(value: Any, *, digits: int = 1) -> str:
    if isinstance(value, bool) or value in (None, ""):
        return "未提供"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _text(value)
    if not math.isfinite(number):
        return "未提供"
    if -1 <= number <= 1:
        number *= 100
    return f"{number:.{digits}f}%"


def _format_timestamp(value: Any) -> str:
    visible = _text(value)
    if not visible:
        return "未提供"
    try:
        parsed = datetime.fromisoformat(visible.replace("Z", "+00:00"))
    except ValueError:
        return visible
    zone = parsed.strftime("%z")
    if zone == "+0000":
        zone_text = " UTC"
    elif zone:
        zone_text = f" UTC{zone[:3]}:{zone[3:]}"
    else:
        zone_text = ""
    return parsed.strftime("%Y-%m-%d %H:%M:%S") + zone_text


def _status_label(value: Any, *, default: str = "待确认") -> str:
    normalized = _text(value).upper()
    if not normalized:
        return default
    if normalized in _STATUS_LABELS:
        return _STATUS_LABELS[normalized]
    if any("\u4e00" <= char <= "\u9fff" for char in normalized):
        return normalized
    return default


def _status_tone(value: Any) -> str:
    normalized = _text(value).upper()
    if normalized in _DANGER_STATES:
        return "danger"
    if normalized in _WARNING_STATES:
        return "warning"
    if normalized in _SUCCESS_STATES:
        return "success"
    return "neutral"


def _badge(
    value: Any,
    *,
    label: str | None = None,
    tone: str | None = None,
) -> str:
    visible = label or _status_label(value)
    selected_tone = tone or _status_tone(value)
    return (
        f'<span class="badge badge--{html.escape(selected_tone)}">'
        f"{html.escape(visible)}</span>"
    )


def _fact(label: str, value_html: str, *, note: str | None = None) -> str:
    note_html = (
        f'<span class="business-subline">{html.escape(note)}</span>'
        if note
        else ""
    )
    return (
        '<div class="key-item">'
        f"<dt>{html.escape(label)}</dt><dd>{value_html}{note_html}</dd>"
        "</div>"
    )


def _sequence_html(value: Any) -> str:
    sequence = _text(value).upper()
    if not sequence:
        return '<span class="empty-value">未提供</span>'
    chunks = [
        html.escape(sequence[index : index + 5])
        for index in range(0, len(sequence), 5)
    ]
    return '<span class="sequence-code">' + "<wbr>".join(chunks) + "</span>"


def _decision_state(
    report: Mapping[str, Any],
) -> tuple[str, str, str, str]:
    variation = _mapping(report.get("variation"))
    execution = _text(
        report.get("execution_status") or report.get("status")
    ).upper()
    decision = _text(
        report.get("decision")
        or variation.get("verdict")
        or "INCONCLUSIVE"
    ).upper()
    if execution == "FAILED":
        return (
            "本次分析未完成",
            "danger",
            "运行过程中出现阻断，当前证据不足以判断现有方案的变异耐受性。",
            "“分析未完成”表示运行证据不完整；修复所需条件并重试后，"
            "才能形成最终评估结论。",
        )
    outcomes = {
        "ROBUST": (
            "当前方案耐受性良好",
            "success",
            "完全匹配比例和重点位点均满足当前评估要求，"
            "现有方案可继续进入实验验证。",
            "执行状态说明分析步骤是否完成；耐受性结论说明现有方案面对"
            "本批代表性序列时的覆盖表现。",
        ),
        "ACCEPTABLE": (
            "当前方案可继续使用，建议复核",
            "warning",
            "整体覆盖表现可以接受，但仍有重点位点需要在定稿前复核。",
            "建议结合样本构成和实验结果确认风险位点是否影响实际检出。",
        ),
        "REDESIGN_RECOMMENDED": (
            "建议重新设计",
            "danger",
            "当前完全匹配比例或引物 3′ 端重点位点达到重新设计条件。",
            "本次分析已经运行完成；“建议重新设计”是评估结论，"
            "不代表程序执行失败。",
        ),
        "INCONCLUSIVE": (
            "暂无法形成可靠结论",
            "warning",
            "现有证据不足以判断当前方案的变异耐受性。",
            "请先补齐输入数据或恢复计算条件，再使用原任务重试。",
        ),
    }
    title, tone, summary, clarification = outcomes.get(
        decision, outcomes["INCONCLUSIVE"]
    )
    if execution == "PARTIAL" and tone == "success":
        tone = "warning"
        summary = "部分计算采用了不完整证据；当前结果只能作为复核参考。"
    return title, tone, summary, clarification


def _issue_message(value: Any) -> str:
    if isinstance(value, Mapping):
        code = _text(
            value.get("code")
            or value.get("reason_code")
            or value.get("error_code")
        )
        if code in _REASON_MESSAGES:
            return _REASON_MESSAGES[code]
        message = _text(value.get("message") or value.get("reason"))
        if code:
            return "运行过程中记录了一项需要修复的问题。"
        value = message
    raw = _text(value)
    if not raw:
        return ""
    if raw in _REASON_MESSAGES:
        return _REASON_MESSAGES[raw]
    if raw in _WARNING_MESSAGES:
        return _WARNING_MESSAGES[raw]
    if raw.startswith("序列数不足"):
        return raw.replace(
            "覆盖率统计置信度低",
            "覆盖统计的代表性可能不足",
        )
    if "fallback" in raw.lower():
        return "本次使用了兼容计算模式，建议在标准计算条件恢复后复核。"
    if raw.isascii() and (
        "_" in raw or raw.upper() == raw or raw.startswith("WF05")
    ):
        return "报告记录了一项需要结合完整证据复核的提示。"
    return raw.replace("WF-05", "本流程").replace(
        "WF-01", "后续设计流程"
    )


def _decision_card(report: Mapping[str, Any]) -> str:
    title, tone, summary, clarification = _decision_state(report)
    messages: list[str] = []
    for key in ("decision_reasons", "warnings", "errors"):
        for value in _items(report.get(key)):
            message = _issue_message(value)
            if message and message not in messages:
                messages.append(message)
    if not messages:
        messages.append("本次没有额外记录需要单独说明。")
    issue_html = "".join(
        f"<li>{html.escape(message)}</li>" for message in messages
    )
    return (
        f'<div class="decision-card decision-card--{html.escape(tone)}">'
        '<p class="decision-label">本次结论</p>'
        f"<h3>{html.escape(title)}</h3>"
        f"<p>{html.escape(summary)}</p>"
        f'<ul class="reason-list">{issue_html}</ul>'
        f'<p class="decision-clarification">{html.escape(clarification)}</p>'
        "</div>"
    )


def _coverage_count(
    counts: Mapping[str, Any],
    key: str,
    rate: Any,
    total: Any,
) -> str:
    direct = counts.get(key)
    if not isinstance(direct, bool) and direct not in (None, ""):
        return _integer(direct)
    try:
        return str(round(float(rate) * int(total)))
    except (TypeError, ValueError):
        return "未提供"


def _coverage_row(
    *,
    label: str,
    explanation: str,
    rate: Any,
    count: str,
    total: Any,
    badge_html: str,
) -> str:
    try:
        width = max(0.0, min(100.0, float(rate) * 100.0))
    except (TypeError, ValueError):
        width = 0.0
    count_text = (
        f"{count} / {_integer(total)} 条"
        if count != "未提供" and _integer(total) != "未提供"
        else "计数未提供"
    )
    return (
        '<div class="coverage-row">'
        f'<div class="coverage-label"><strong>{html.escape(label)}</strong>'
        f"<span>{html.escape(explanation)}</span></div>"
        '<div class="coverage-track" aria-hidden="true">'
        f'<span style="width:{width:.3f}%"></span></div>'
        f'<div class="coverage-value"><strong>{html.escape(_percent(rate))}'
        "</strong>"
        f"<span>{html.escape(count_text)}</span></div>"
        f'<div class="coverage-status">{badge_html}</div>'
        "</div>"
    )


def _coverage_section(report: Mapping[str, Any]) -> str:
    variation = _mapping(report.get("variation"))
    counts = _mapping(variation.get("coverage_counts"))
    total = variation.get("sequences_analyzed")
    exact = variation.get("coverage_100pct_match")
    one = variation.get("coverage_1_mismatch")
    two = variation.get("coverage_2_mismatches")
    thresholds = _mapping(variation.get("thresholds"))
    acceptable = thresholds.get("acceptable_coverage")
    robust = thresholds.get("robust_coverage")
    try:
        exact_number = float(exact)
        acceptable_number = float(acceptable)
        robust_number = float(robust)
    except (TypeError, ValueError):
        exact_badge = _badge("", label="尚未完成", tone="neutral")
    else:
        if exact_number >= robust_number:
            exact_badge = _badge(
                "", label="达到稳健要求", tone="success"
            )
        elif exact_number >= acceptable_number:
            exact_badge = _badge(
                "", label="达到可接受要求", tone="warning"
            )
        else:
            exact_badge = _badge(
                "", label="低于可接受要求", tone="danger"
            )
    rows = [
        _coverage_row(
            label="完全匹配",
            explanation="三条寡核苷酸均未发现差异",
            rate=exact,
            count=_coverage_count(counts, "exact", exact, total),
            total=total,
            badge_html=exact_badge,
        ),
        _coverage_row(
            label="不超过 1 处差异",
            explanation="且引物 3′ 端关键区域没有差异",
            rate=one,
            count=_coverage_count(
                counts, "one_non_3prime", one, total
            ),
            total=total,
            badge_html=_badge("", label="趋势参考", tone="neutral"),
        ),
        _coverage_row(
            label="不超过 2 处差异",
            explanation="用于观察放宽条件下的覆盖变化",
            rate=two,
            count=_coverage_count(counts, "two", two, total),
            total=total,
            badge_html=_badge("", label="趋势参考", tone="neutral"),
        ),
    ]
    threshold_note = (
        f"当前判定参考：完全匹配达到 {_percent(acceptable)} 视为可接受，"
        f"达到 {_percent(robust)} 视为稳健。"
        if acceptable not in (None, "") and robust not in (None, "")
        else "当前判定阈值未完整记录。"
    )
    return (
        '<div class="coverage-list">'
        + "".join(rows)
        + f'<p class="section-note">{html.escape(threshold_note)}</p></div>'
    )


def _risk_location(site: Mapping[str, Any]) -> tuple[str, str]:
    role = _ROLE_LABELS.get(
        _text(site.get("oligo")).lower(),
        "寡核苷酸",
    )
    position = _integer(site.get("position"))
    reference_position = _integer(
        site.get("reference_position_1based")
    )
    raw_location = _text(site.get("location")).lower()
    if "3prime" in raw_location:
        region = "3′ 端关键区域"
    elif raw_location == "probe":
        region = "探针结合区域"
    else:
        region = "5′ 端或中段"
    primary = (
        f"{role}第 {position} 位"
        if position != "未提供"
        else role
    )
    secondary_parts = [region]
    if reference_position != "未提供":
        secondary_parts.append(f"参考位置 {reference_position}")
    return primary, " · ".join(secondary_parts)


def _risk_assessment(site: Mapping[str, Any]) -> str:
    if site.get("possible_dropout"):
        return "温度变化较大，可能影响实际检出，建议优先避开。"
    level = _text(site.get("risk_level")).upper()
    if level == "HIGH":
        return "位于引物关键区域，建议优先复核或避开。"
    if level == "MEDIUM":
        return "位于探针结合区域，建议结合样本构成复核。"
    return "已记录该位置变化，当前作为一般关注项。"


def _risk_section(report: Mapping[str, Any]) -> str:
    variation = _mapping(report.get("variation"))
    sites = [
        item
        for item in _items(variation.get("high_risk_sites"))
        if isinstance(item, Mapping)
    ]
    if not sites:
        execution = _text(
            report.get("execution_status") or report.get("status")
        ).upper()
        message = (
            "本次分析未完成，尚未形成可展示的重点位点结果。"
            if execution == "FAILED"
            else "本次没有发现达到当前报告阈值的重点位点。"
        )
        return f'<p class="empty-state">{html.escape(message)}</p>'
    rows: list[str] = []
    for site in sites:
        primary, secondary = _risk_location(site)
        ref = _text(site.get("ref")).upper()
        alt = _text(site.get("alt")).upper()
        change = f"{ref} → {alt}" if ref and alt else "未提供"
        observations = _integer(site.get("observations"))
        frequency = _percent(site.get("frequency"))
        frequency_text = (
            f"{frequency}（{observations} 条）"
            if observations != "未提供"
            else frequency
        )
        tm_shift = _number(
            site.get("tm_shift_celsius"),
            digits=2,
            suffix=" °C",
        )
        level = _text(site.get("risk_level")).upper()
        rows.append(
            "<tr>"
            f"<th><strong>{html.escape(primary)}</strong>"
            f'<span class="table-subline">{html.escape(secondary)}</span></th>'
            f'<td><span class="sequence-code">{html.escape(change)}</span></td>'
            f"<td>{html.escape(frequency_text)}</td>"
            f"<td>{html.escape(tm_shift)}</td>"
            f"<td>{_badge(level)}</td>"
            f"<td>{html.escape(_risk_assessment(site))}</td>"
            "</tr>"
        )
    high_count = sum(
        _text(site.get("risk_level")).upper() == "HIGH"
        for site in sites
    )
    dropout_count = sum(
        bool(site.get("possible_dropout")) for site in sites
    )
    summary = (
        f"共记录 {len(sites)} 个重点位点，其中高风险 {high_count} 个，"
        f"温度变化较大 {dropout_count} 个。"
    )
    return (
        f'<p class="section-note">{html.escape(summary)}</p>'
        '<div class="table-scroll"><table class="risk-table"><thead><tr>'
        "<th>所在位置</th><th>变化</th><th>出现比例</th>"
        "<th>预计温度变化</th><th>风险</th><th>解释</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _candidate(report: Mapping[str, Any]) -> Mapping[str, Any]:
    for item in _items(report.get("candidates")):
        if isinstance(item, Mapping):
            return item
    return {}


def _oligo_data(
    report: Mapping[str, Any],
    candidate: Mapping[str, Any],
    field: str,
) -> Mapping[str, Any]:
    value = candidate.get(field)
    if isinstance(value, Mapping):
        return value
    input_payload = _mapping(report.get("input"))
    sequence = input_payload.get(field)
    return {"sequence_5to3": sequence} if sequence else {}


def _scheme_status(candidate: Mapping[str, Any]) -> tuple[str, str]:
    verdict = _text(candidate.get("verdict")).upper()
    labels = {
        "PASS": ("基础设计检查通过", "success"),
        "PASS_WITH_WARNINGS": (
            "基础设计检查通过（含注意项）",
            "warning",
        ),
        "PARTIAL": ("基础设计检查尚未完成", "warning"),
        "FAIL": ("基础设计检查未通过", "danger"),
    }
    return labels.get(
        verdict,
        ("基础设计检查结果未提供", "neutral"),
    )


def _scheme_section(report: Mapping[str, Any]) -> str:
    candidate = _candidate(report)
    rows: list[str] = []
    for field, label in (
        ("forward_primer", "正向引物"),
        ("reverse_primer", "反向引物"),
        ("probe", "探针"),
    ):
        oligo = _oligo_data(report, candidate, field)
        if not oligo:
            continue
        length = oligo.get("length_nt")
        length_text = (
            f"{_integer(length)} nt"
            if length not in (None, "")
            else "未提供"
        )
        rows.append(
            "<tr>"
            f"<th>{html.escape(label)}</th>"
            f"<td>{_sequence_html(oligo.get('sequence_5to3'))}</td>"
            f"<td>{html.escape(length_text)}</td>"
            f"<td>{html.escape(_number(oligo.get('tm_celsius'), digits=2, suffix=' °C'))}</td>"
            f"<td>{html.escape(_number(oligo.get('gc_percent'), digits=1, suffix='%'))}</td>"
            "</tr>"
        )
    if not rows:
        oligo_table = (
            '<p class="empty-state">'
            "当前没有可展示的引物或探针参数。</p>"
        )
    else:
        oligo_table = (
            '<div class="table-scroll"><table class="oligo-table">'
            "<thead><tr><th>组成</th><th>序列（5′→3′）</th>"
            "<th>长度</th><th>Tm</th><th>GC</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table></div>"
        )
    scheme_label, scheme_tone = _scheme_status(candidate)
    rules = [
        item
        for item in _items(candidate.get("rules"))
        if isinstance(item, Mapping)
        and _text(item.get("status")).upper()
        in {"FAIL", "WARNING", "NOT_EVALUATED", "ERROR"}
    ]
    attention_items: list[str] = []
    for rule in rules:
        code = _text(rule.get("code"))
        title = _RULE_LABELS.get(code, "其他基础检查")
        requirement = _RULE_REQUIREMENTS.get(
            code,
            "该项需要结合完整设计证据复核。",
        )
        attention_items.append(
            "<li>"
            f'<div><strong>{html.escape(title)}</strong>'
            f"<span>{html.escape(requirement)}</span></div>"
            f"{_badge(rule.get('status'))}"
            "</li>"
        )
    attention_html = (
        '<div class="scheme-attention"><p class="section-note">'
        "以下基础设计检查与本次变异耐受性结论相互独立：</p>"
        f'<ul class="attention-list">{"".join(attention_items)}</ul></div>'
        if attention_items
        else ""
    )
    return (
        '<div class="scheme-heading"><p>'
        "这里展示本次被评估的引物和探针，"
        "不展示对象名称或内部候选编号。</p>"
        f'{_badge("", label=scheme_label, tone=scheme_tone)}</div>'
        + oligo_table
        + attention_html
    )


def _step_detail(
    step: Mapping[str, Any],
    report: Mapping[str, Any],
) -> str:
    code = _text(step.get("step"))
    status = _text(step.get("status")).upper()
    if status == "ERROR":
        return "该步骤未完成；恢复所需计算条件后可以重试。"
    if status == "NOT_RUN":
        return "前序步骤未完成，因此本步骤没有运行。"
    variation = _mapping(report.get("variation"))
    if code == "WF05_STEP1_SEQUENCE_SET":
        count = (
            step.get("sequence_count")
            or variation.get("sequences_analyzed")
        )
        minimum = step.get("minimum_recommended")
        detail = f"已载入 {_integer(count)} 条代表性序列。"
        if minimum not in (None, ""):
            detail += f" 当前建议不少于 {_integer(minimum)} 条。"
        return detail
    if code == "WF05_STEP2_ALIGNMENT":
        width = step.get("alignment_width")
        return (
            f"已完成多序列对齐，共形成 {_integer(width)} 个比对列。"
            if width not in (None, "")
            else "已完成多序列对齐。"
        )
    if code == "WF05_STEP3_VARIANT_SCAN":
        count = step.get("risk_site_count")
        if count in (None, ""):
            count = len(_items(variation.get("high_risk_sites")))
        return (
            "已扫描三条寡核苷酸的结合区域，"
            f"记录 {_integer(count)} 个重点位点。"
        )
    if code == "WF05_STEP4_TM_IMPACT":
        count = step.get("evaluated_site_count")
        return (
            f"已评估 {_integer(count)} 个重点位点对结合温度的影响。"
            if count not in (None, "")
            else "已评估重点位点对结合温度的影响。"
        )
    if code == "WF05_STEP5_COVERAGE":
        value = step.get("coverage_100pct_match")
        if value in (None, ""):
            value = variation.get("coverage_100pct_match")
        return f"已完成覆盖统计；完全匹配比例为 {_percent(value)}。"
    if code == "WF05_STEP6_DECISION":
        title, _tone, _summary, _clarification = _decision_state(
            report
        )
        return f"已根据覆盖表现和重点位点生成结论：{title}。"
    return "该分析步骤已记录完成状态。"


def _steps_section(report: Mapping[str, Any]) -> str:
    steps = [
        item
        for item in _items(
            report.get("steps") or report.get("workflow_trace")
        )
        if isinstance(item, Mapping)
    ]
    if not steps:
        return '<p class="empty-state">本次没有可展示的分析步骤。</p>'
    rows: list[str] = []
    for index, step in enumerate(steps, start=1):
        code = _text(step.get("step"))
        label = _STEP_LABELS.get(code, "其他分析步骤")
        rows.append(
            "<li>"
            f'<span class="step-number">{index:02d}</span>'
            '<div class="step-copy">'
            f"<strong>{html.escape(label)}</strong>"
            f"<span>{html.escape(_step_detail(step, report))}</span>"
            "</div>"
            f"{_badge(step.get('status'))}"
            "</li>"
        )
    return f'<ol class="validation-list">{"".join(rows)}</ol>'


def _position_summary(values: Any, *, limit: int = 12) -> str:
    positions: list[int] = []
    for value in _items(values):
        try:
            positions.append(int(value))
        except (TypeError, ValueError):
            continue
    positions = sorted(set(positions))
    if not positions:
        return ""
    visible = "、".join(str(value) for value in positions[:limit])
    if len(positions) > limit:
        visible += f" 等 {len(positions)} 处"
    return visible


def _actions_section(report: Mapping[str, Any]) -> str:
    variation = _mapping(report.get("variation"))
    next_action = _text(report.get("next_action")).upper()
    candidate = _candidate(report)
    actions: list[str] = []
    if next_action == "HANDOFF_WF01":
        handoff = _mapping(variation.get("redesign_handoff"))
        positions = _position_summary(
            handoff.get("exclude_reference_positions_1based")
        )
        action = (
            "新建独立的设计任务，并将本次重点位点作为优先避开区域。"
        )
        if positions:
            action += f" 建议优先避开参考位置：{positions}。"
        actions.append(action)
    elif next_action == "RETRY":
        actions.append(
            "检查输入数据和运行所需的计算资源，"
            "条件恢复后使用原任务重试。"
        )
    else:
        actions.append(
            "保留当前方案，结合预期样本范围进入小规模实验验证。"
        )
    if _text(candidate.get("verdict")).upper() in {
        "FAIL",
        "PARTIAL",
        "PASS_WITH_WARNINGS",
    }:
        actions.append(
            "先处理“当前方案”中的基础设计注意项，"
            "再决定是否进入正式实验。"
        )
    if variation.get("sequences_warning"):
        actions.append(
            "增加具有代表性的序列记录后重新评估，"
            "以提高覆盖统计的代表性。"
        )
    if any(
        bool(_mapping(site).get("possible_dropout"))
        for site in _items(variation.get("high_risk_sites"))
    ):
        actions.append(
            "优先复核温度变化较大的重点位点，"
            "并在重新设计时避免这些位置。"
        )
    actions.append(
        "定稿前使用覆盖主要变异类型的代表性样本完成实验验证。"
    )
    unique_actions = list(dict.fromkeys(actions))
    rows = "".join(
        "<li>"
        f"<span>{index:02d}</span><p>{html.escape(action)}</p>"
        "</li>"
        for index, action in enumerate(unique_actions, start=1)
    )
    return f'<ol class="action-list">{rows}</ol>'


def _scope_section(report: Mapping[str, Any]) -> str:
    variation = _mapping(report.get("variation"))
    evidence = _text(report.get("evidence_level")).lower()
    evidence_label = _EVIDENCE_LABELS.get(
        evidence,
        "当前证据范围未完整记录",
    )
    minimum_frequency = _percent(
        variation.get("minimum_detectable_frequency")
    )
    alignment_method = _text(
        variation.get("alignment_method")
    ).lower()
    method_note = (
        "本次采用兼容模式完成多序列对齐，"
        "建议标准计算条件恢复后复核。"
        if alignment_method.endswith("fallback")
        else "基于多序列比对、结合位点扫描、"
        "温度变化估算和覆盖统计形成结论。"
    )
    handoff = _mapping(variation.get("redesign_handoff"))
    automatic = handoff.get("automatic_execution")
    follow_up = (
        "没有自动创建后续任务，所有后续操作均需由用户确认后单独发起。"
        if automatic is False
        else "本报告未记录自动后续处理。"
    )
    disclaimer = _text(report.get("disclaimer")) or (
        "计算结果用于方案复核，不能替代真实样本和实际仪器条件下的"
        "实验验证。"
    )
    return (
        '<div class="scope-grid">'
        '<div class="scope-item"><strong>证据范围</strong>'
        f"<p>{html.escape(evidence_label)}</p></div>"
        '<div class="scope-item"><strong>分析方法</strong>'
        f"<p>{html.escape(method_note)}</p></div>"
        '<div class="scope-item"><strong>统计分辨率</strong>'
        "<p>本批次可稳定统计的最低出现比例约为 "
        f"{html.escape(minimum_frequency)}。</p></div>"
        '<div class="scope-item"><strong>后续任务</strong>'
        f"<p>{html.escape(follow_up)}</p></div>"
        '<div class="scope-item scope-item--wide">'
        "<strong>使用边界</strong>"
        f"<p>{html.escape(disclaimer)}</p></div>"
        "</div>"
    )


_READABLE_STYLE = r"""
:root{--ink:#1d2226;--muted:#697179;--line:#dfe3e6;--soft:#f6f7f8;--accent:#315d73;--success:#2f6b52;--warning:#7a5b1d;--danger:#9c3f3f}
*{box-sizing:border-box;letter-spacing:0}html{scroll-behavior:smooth}body{margin:0;background:#fff;color:var(--ink);font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",Arial,sans-serif;line-height:1.65}
.report-shell{width:min(1040px,calc(100% - 48px));margin:0 auto 64px}.report-header{padding:52px 0 28px;border-bottom:2px solid var(--ink)}.header-top{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:14px}
.eyebrow{margin:0;color:var(--accent);font-size:12px;font-weight:700}h1{margin:0;font-size:42px;font-weight:650;line-height:1.2}h2{margin:0;font-size:24px;line-height:1.35}h3{margin:0;font-size:22px}.report-intro{max-width:780px;margin:14px 0 0;color:#454c52;font-size:15px}
.meta-row{display:flex;gap:8px 28px;flex-wrap:wrap;margin:22px 0 0;color:var(--muted);font-size:12px}.meta-row b{color:var(--ink);font-weight:600}
.badge{display:inline-flex;align-items:center;gap:7px;width:max-content;color:var(--muted);font-size:12px;font-weight:700;line-height:1.35;vertical-align:middle}.badge::before{width:7px;height:7px;border-radius:50%;background:currentColor;content:""}.badge--success{color:var(--success)}.badge--warning{color:var(--warning)}.badge--danger{color:var(--danger)}.badge--neutral{color:var(--muted)}
.key-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));margin:28px 0 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}.key-item{min-width:0;padding:18px 20px}.key-item+.key-item{border-left:1px solid var(--line)}.key-item dt{color:var(--muted);font-size:11px}.key-item dd{margin:4px 0 0;font-size:22px;font-weight:650;overflow-wrap:anywhere}.business-subline{display:block;margin-top:3px;color:var(--muted);font-size:11px;font-weight:400}
.report-section{padding:38px 0;border-bottom:1px solid var(--line)}.section-heading{display:flex;align-items:baseline;gap:14px;margin-bottom:20px}.section-heading>span{color:var(--accent);font:700 12px/1 Consolas,monospace}.section-note{margin:0 0 14px;color:var(--muted);font-size:12px}.empty-state{padding:18px 0;color:var(--muted);text-align:center}
.decision-card{padding:24px 26px;border-left:4px solid var(--muted);background:var(--soft)}.decision-card--success{border-color:var(--success)}.decision-card--warning{border-color:var(--warning)}.decision-card--danger{border-color:var(--danger)}.decision-label{margin:0 0 5px;color:var(--muted);font-size:11px;font-weight:700}.decision-card p{margin:10px 0 0;color:#4c5359;font-size:14px}.reason-list{margin:15px 0 0;padding-left:20px}.reason-list li{margin:6px 0;color:#454c52;font-size:13px}.decision-card .decision-clarification{padding-top:10px;border-top:1px solid var(--line);color:var(--ink);font-weight:600}
.coverage-list{border-top:1px solid var(--line)}.coverage-row{display:grid;grid-template-columns:200px minmax(180px,1fr) 120px 128px;gap:18px;align-items:center;padding:16px 0;border-bottom:1px solid var(--line)}.coverage-label,.coverage-value{display:flex;flex-direction:column}.coverage-label strong,.coverage-value strong{font-size:14px}.coverage-label span,.coverage-value span{color:var(--muted);font-size:11px}.coverage-track{height:8px;background:#e6eaec}.coverage-track span{display:block;height:100%;background:var(--accent)}.coverage-status{justify-self:end}
.table-scroll{overflow-x:auto}table{border-collapse:collapse;width:100%}th,td{padding:11px 12px;text-align:left;border-bottom:1px solid var(--line);vertical-align:middle}thead th{background:var(--soft);color:#535b62;font-size:11px;font-weight:650}.risk-table{min-width:940px;font-size:12px}.risk-table tbody th{width:190px;font-size:12px}.risk-table td:last-child{min-width:220px;color:#4f565c}.table-subline{display:block;margin-top:3px;color:var(--muted);font-size:10px;font-weight:400}.sequence-code{font-family:Consolas,"SFMono-Regular",monospace;font-weight:650;overflow-wrap:anywhere;word-break:break-all}.empty-value{color:var(--muted);font-weight:400}
.scheme-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:24px;margin-bottom:16px}.scheme-heading p{margin:0;color:var(--muted);font-size:13px}.oligo-table{min-width:720px;font-size:12px}.oligo-table th:first-child{width:110px}.oligo-table td:nth-child(2){min-width:300px}.scheme-attention{margin-top:24px}.attention-list{margin:0;padding:0;list-style:none;border-top:1px solid var(--line)}.attention-list li{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:16px;align-items:center;padding:13px 0;border-bottom:1px solid var(--line)}.attention-list div{display:flex;gap:18px}.attention-list strong{min-width:150px;font-size:13px}.attention-list span{color:var(--muted);font-size:12px}
.validation-list{margin:0;padding:0;list-style:none;border-top:1px solid var(--line)}.validation-list li{display:grid;grid-template-columns:48px minmax(0,1fr) auto;gap:14px;align-items:center;padding:15px 0;border-bottom:1px solid var(--line)}.step-number{color:#98a0a6;font:12px/1 Consolas,monospace}.step-copy{display:grid;grid-template-columns:minmax(150px,.42fr) minmax(260px,1fr);gap:18px;align-items:center}.step-copy strong{font-size:14px}.step-copy span{color:var(--muted);font-size:13px}
.action-list{margin:0;padding:0;list-style:none;border-top:1px solid var(--line)}.action-list li{display:grid;grid-template-columns:48px minmax(0,1fr);gap:14px;padding:15px 0;border-bottom:1px solid var(--line)}.action-list li>span{color:var(--accent);font:700 12px/1.8 Consolas,monospace}.action-list p{margin:0;color:#454c52;font-size:14px}
.scope-grid{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--line);border:1px solid var(--line)}.scope-item{padding:18px 20px;background:#fff}.scope-item--wide{grid-column:1/-1}.scope-item strong{font-size:13px}.scope-item p{margin:5px 0 0;color:#4f565c;font-size:13px}.report-footer{padding:24px 0;color:var(--muted);font-size:11px}.report-footer p{margin:3px 0}
@media(max-width:760px){.report-shell{width:min(100% - 28px,1040px)}.report-header{padding-top:30px}h1{font-size:32px}.key-metrics{grid-template-columns:1fr 1fr}.key-metrics .key-item:nth-child(3){border-left:0;border-top:1px solid var(--line)}.key-metrics .key-item:nth-child(4){border-top:1px solid var(--line)}.coverage-row{grid-template-columns:1fr 110px;gap:10px}.coverage-track{grid-column:1/-1;grid-row:2}.coverage-value{grid-column:1}.coverage-status{grid-column:2;justify-self:end}.step-copy{grid-template-columns:1fr;gap:2px}.attention-list div{flex-direction:column;gap:2px}.attention-list strong{min-width:0}.scope-grid{grid-template-columns:1fr}.scope-item--wide{grid-column:auto}}
@media(max-width:520px){.header-top,.scheme-heading{align-items:flex-start;flex-direction:column}.key-metrics{grid-template-columns:1fr}.key-metrics .key-item+.key-item{border-left:0;border-top:1px solid var(--line)}.validation-list li{grid-template-columns:34px minmax(0,1fr)}.validation-list li>.badge{grid-column:2}.meta-row{display:grid;gap:4px}.coverage-row{grid-template-columns:1fr}.coverage-track,.coverage-value,.coverage-status{grid-column:1}.coverage-status{justify-self:start}}
@media print{@page{size:A4 portrait;margin:12mm}.report-shell{width:100%;margin:0}.report-header{padding-top:0}.report-section{break-inside:auto}.table-scroll{overflow:visible}}
"""


def render_workflow_report_html(report: dict[str, Any]) -> bytes:
    if not isinstance(report, dict):
        raise InputValidationError("WF-05终态报告必须为对象")
    if report.get("workflow") != "WF-05":
        raise InputValidationError(
            "WF-05渲染器不能处理其他工作流",
            details={"workflow": report.get("workflow")},
        )
    variation = _mapping(report.get("variation"))
    title, tone, _summary, _clarification = _decision_state(report)
    execution_status = (
        report.get("execution_status") or report.get("status")
    )
    job_id = _text(report.get("job_id"))
    created_at = report.get("created_at") or report.get("generated_at")
    sequence_count = variation.get("sequences_analyzed")
    risk_count = len(_items(variation.get("high_risk_sites")))
    exact_coverage = variation.get("coverage_100pct_match")
    intro = (
        f"本次基于 {_integer(sequence_count)} 条代表性序列评估现有"
        f"引物和探针的变异耐受性。当前结论：{title}。"
    )
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,"/><title>WF-05 变异耐受性评估报告</title>
<style>{_READABLE_STYLE}</style></head>
<body><main class="report-shell">
<header class="report-header"><div class="header-top"><p class="eyebrow">WF-05 · 变异耐受性评估</p>{_badge(execution_status)}</div><h1>变异耐受性评估报告</h1>
<p class="report-intro">{html.escape(intro)}</p>
<div class="meta-row"><span><b>报告编号：</b>{html.escape(job_id or '未提供')}</span><span><b>生成时间：</b>{html.escape(_format_timestamp(created_at))}</span></div></header>
<dl class="key-metrics" aria-label="关键结果">{_fact("分析记录", html.escape(f"{_integer(sequence_count)} 条"))}{_fact("完全匹配", html.escape(_percent(exact_coverage)))}{_fact("重点位点", html.escape(f"{risk_count} 个"))}{_fact("当前建议", _badge("", label=title, tone=tone))}</dl>
<section class="report-section"><div class="section-heading"><span>01</span><h2>本次结论</h2></div>{_decision_card(report)}</section>
<section class="report-section"><div class="section-heading"><span>02</span><h2>覆盖表现</h2></div>{_coverage_section(report)}</section>
<section class="report-section"><div class="section-heading"><span>03</span><h2>重点变异位点</h2></div>{_risk_section(report)}</section>
<section class="report-section"><div class="section-heading"><span>04</span><h2>当前方案</h2></div>{_scheme_section(report)}</section>
<section class="report-section"><div class="section-heading"><span>05</span><h2>分析步骤</h2></div>{_steps_section(report)}</section>
<section class="report-section"><div class="section-heading"><span>06</span><h2>建议的处理顺序</h2></div>{_actions_section(report)}</section>
<section class="report-section"><div class="section-heading"><span>07</span><h2>方法与使用边界</h2></div>{_scope_section(report)}</section>
<footer class="report-footer"><p>主视图仅展示适合阅读和决策的信息，不显示对象名称、内部候选编号或流程字段。</p><p>完整机器可读记录随本附件保存，用于审计和追溯。</p></footer>
<script type="application/json" id="workflow-report-data">{_json_for_script(report)}</script>
</main></body></html>"""
    return document.encode("utf-8")
