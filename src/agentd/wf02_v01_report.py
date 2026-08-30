"""Deterministic WF-02 canonical-report renderers.

The workflow builds the scientific JSON.  This module only renders that same
snapshot as HTML, CSV, and PDF; it does not make scientific decisions.
"""

from __future__ import annotations

import csv
import html
import io
import json
from datetime import datetime
from typing import Any

from .errors import PersistenceError


WF02_EXPORT_RENDERER_VERSIONS = {
    "html": "wf02-readable-html-v3",
    "csv": "wf02-csv-v2",
    "pdf": "wf02-readable-pdf-v2",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _pdf_text(value: Any, *, max_chars: int = 1200) -> str:
    """Return bounded, escaped text for a visible PDF block.

    The canonical JSON and HTML exports retain the complete value.  Bounding
    only the PDF representation prevents a single verbose nested record from
    creating a table row taller than ReportLab's page frame.
    """
    visible = _text(value)
    if len(visible) > max_chars:
        visible = (
            visible[:max_chars]
            + "\n[PDF 可见内容已截断；完整数据保留在 HTML 和 JSON 报告中]"
        )
    return html.escape(visible).replace("\n", "<br/>")


def _dg_text(oligo: dict[str, Any]) -> str:
    values = [
        ("发卡", oligo.get("hairpin_dg_kcal")),
        ("同源二聚体", oligo.get("homodimer_dg_kcal")),
        ("3′二聚体", oligo.get("three_prime_dimer_dg_kcal")),
    ]
    available = [f"{label} {value}" for label, value in values if value is not None]
    return "；".join(available) if available else "未提供"


def _delivery_panels(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Return complete or partial deliverables without mixing alternatives."""
    return list(report.get("top3_panels") or []) + list(
        report.get("partial_panels") or []
    )


_DISPLAY_LABELS = {
    "PASS": "通过",
    "PASS_WITH_WARNING": "已完成（含注意项）",
    "COMPLETED": "已完成",
    "COMPLETE": "完整",
    "COMPLETE_PANEL_AVAILABLE": "已生成完整方案",
    "PARTIAL": "部分完成",
    "PARTIAL_ONLY": "仅有部分方案",
    "PARTIAL_PANEL_AVAILABLE": "已生成部分方案",
    "PARTIAL_TARGET_COVERAGE": "部分覆盖",
    "FAILED": "失败",
    "FAIL": "未通过",
    "BLOCKED": "已阻塞",
    "NOT_EVALUATED": "未评估",
    "NOT_APPLICABLE": "不适用",
    "NOT_PROVIDED": "未提供",
    "WARNING": "需注意",
    "CAUTION": "建议复核",
    "MARGINAL": "接近阈值",
    "STRICT": "推荐方案",
    "STRICT_COMPATIBLE": "推荐方案",
    "RISK": "备选方案（含注意项）",
    "RISK_COMPLETE": "完整备选方案",
    "RISK_PANEL": "备选方案",
    "GLOBAL_SCORE_TOP3": "按综合评分排序",
    "INCOMPLETE_TARGET_COVERAGE": "覆盖未完成",
    "NO_COMPATIBLE_PANEL": "未生成兼容方案",
    "NO_COMPATIBLE_PANEL_FOR_AVAILABLE_TARGETS": "现有成员未组成兼容方案",
    "PRIMER3_GENERATED_NO_CANDIDATES": "候选生成步骤未返回结果",
    "TARGET_DESIGN_NO_CANDIDATES": "一个或多个目标未返回候选",
    "NO_BLAST_VERIFIED_CANDIDATES": "上游没有返回可用候选（历史原因码）",
    "NO_WF01_QUALIFIED_CANDIDATES": "上游权威合格候选集合为空",
    "TARGET_EVALUATION_BLOCKED_RESOURCE": "运行所需资源尚未就绪",
    "TARGET_DATABASE_OR_VERSION_NOT_RESOLVED": "目标数据库或版本未解析",
    "NT_DATABASE_OR_VERSION_NOT_RESOLVED": "背景数据库或版本未解析",
    "HOST_DATABASE_OR_VERSION_NOT_RESOLVED": "排除项数据库或版本未解析",
    "PARTIAL_PANEL": "部分方案",
    "target": "目标成员",
    "internal_control": "辅助成员",
    "blast_verified": "已完成数据库核验",
    "computed": "程序计算",
    "INSTRUMENT_PROFILE_UNVERIFIED": "设备配置尚未完成验证",
    "P4_MARGINAL": "方案兼容性接近阈值，建议优先复核",
    "P4_NOT_EVALUATED": "方案兼容性尚未评估",
    "TM_SPREAD_LIMIT_RELAXED_TO_4C": "温度一致性采用了放宽条件",
    "CROSS_DIMER_DG_GT_NEGATIVE_7": "交叉作用采用了放宽条件",
    "PRIMER_AND_PROBE_TM_SPREAD_LE_4C": "温度一致性采用了放宽条件",
    "NONE": "无",
}


def _display_label(value: Any, *, default: str = "未提供") -> str:
    visible = _text(value).strip()
    if not visible:
        return default
    translated = _DISPLAY_LABELS.get(visible)
    if translated:
        return translated
    if any("\u4e00" <= char <= "\u9fff" for char in visible):
        return visible
    if visible.upper() == visible and "_" in visible:
        return visible.replace("_", " ").title()
    return visible


def _format_number(
    value: Any,
    *,
    digits: int = 2,
    suffix: str = "",
) -> str:
    if value is None or value == "":
        return "未提供"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return f"{_text(value)}{suffix}"
    visible = f"{number:.{digits}f}".rstrip("0").rstrip(".")
    return f"{visible}{suffix}"


def _format_conservation(value: Any) -> str:
    if value is None or value == "":
        return "未提供"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _text(value)
    if 0.0 <= number <= 1.0:
        number *= 100.0
    return f"{number:.1f}%"


def _format_timestamp(value: Any) -> str:
    visible = _text(value).strip()
    if not visible:
        return "未提供"
    try:
        parsed = datetime.fromisoformat(visible.replace("Z", "+00:00"))
    except ValueError:
        return visible
    zone = parsed.strftime("%z")
    zone_text = " UTC" if zone == "+0000" else (f" UTC{zone[:3]}:{zone[3:]}" if zone else "")
    return parsed.strftime("%Y-%m-%d %H:%M:%S") + zone_text


def _status_tone(value: Any) -> str:
    normalized = _text(value).upper()
    if any(
        token in normalized
        for token in ("FAIL", "BLOCK", "ERROR", "INCOMPATIBLE")
    ):
        return "danger"
    if any(
        token in normalized
        for token in (
            "WARNING",
            "CAUTION",
            "RISK",
            "PARTIAL",
            "MARGINAL",
            "NOT_EVALUATED",
        )
    ):
        return "warning"
    if any(
        token in normalized
        for token in ("PASS", "COMPLETE", "RESOLVED", "VERIFIED")
    ):
        return "success"
    return "neutral"


def _badge(value: Any, *, label: str | None = None) -> str:
    visible = label or _display_label(value)
    return (
        f'<span class="badge badge--{_status_tone(value)}">'
        f"{html.escape(visible)}</span>"
    )


def _sequence_html(value: Any) -> str:
    sequence = _text(value).strip()
    if not sequence:
        return '<span class="empty-value">未提供</span>'
    chunks = [
        html.escape(sequence[index : index + 5])
        for index in range(0, len(sequence), 5)
    ]
    return "<wbr>".join(chunks)


def _readable_item(value: Any) -> str:
    if isinstance(value, dict):
        for key in (
            "message",
            "statement",
            "recommendation",
            "suggestion",
            "reason",
            "reason_code",
            "code",
        ):
            if value.get(key):
                return _display_label(value[key])
        return "存在一项结构化记录"
    if isinstance(value, list):
        visible = [_readable_item(item) for item in value]
        return "；".join(item for item in visible if item) or "无"
    return _display_label(value)


def _oligo_row(label: str, oligo: dict[str, Any]) -> str:
    return (
        "<tr>"
        f"<th scope=\"row\">{html.escape(label)}</th>"
        f'<td class="sequence">{_sequence_html(oligo.get("sequence_5to3"))}</td>'
        f'<td>{html.escape(_format_number(oligo.get("tm_celsius"), suffix=" °C"))}</td>'
        f'<td>{html.escape(_format_number(oligo.get("hairpin_dg_kcal"), suffix=" kcal/mol"))}</td>'
        f'<td>{html.escape(_format_number(oligo.get("homodimer_dg_kcal"), suffix=" kcal/mol"))}</td>'
        f'<td>{html.escape(_format_number(oligo.get("three_prime_dimer_dg_kcal"), suffix=" kcal/mol"))}</td>'
        "</tr>"
    )


def _specificity_checks(member: dict[str, Any]) -> list[tuple[str, Any]]:
    specificity = member.get("specificity") or member.get("blast_summary") or {}
    if not isinstance(specificity, dict):
        return []
    checks: list[tuple[str, Any]] = []
    overall = specificity.get("overall_status") or specificity.get("status")
    if overall:
        checks.append(("综合状态", overall))
    for key, label in (
        ("target_hit", "目标匹配"),
        ("related_background_nt", "背景检索"),
        ("host_exclusion", "排除项检索"),
    ):
        item = specificity.get(key)
        if isinstance(item, dict) and item.get("status"):
            checks.append((label, item.get("status")))
    return checks


def _specificity_summary(member: dict[str, Any]) -> str:
    checks = _specificity_checks(member)
    if not checks:
        return '<p class="empty-value">未提供核验摘要</p>'
    return '<ul class="check-results">' + "".join(
        "<li>"
        f"<span>{html.escape(label)}</span>{_badge(status)}"
        "</li>"
        for label, status in checks
    ) + "</ul>"


def _fact(label: str, value_html: str) -> str:
    return (
        '<div class="key-item">'
        f"<dt>{html.escape(label)}</dt><dd>{value_html}</dd>"
        "</div>"
    )


def _optional_nonnegative_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, parsed)


def _cross_dimer_data(panel: dict[str, Any]) -> dict[str, Any]:
    """Normalize existing P4 cross-dimer evidence for report rendering only."""
    p4_result = panel.get("p4_result") or {}
    heterodimer = p4_result.get("heterodimer") or panel.get(
        "cross_dimer_evidence"
    ) or {}
    thresholds = p4_result.get("thresholds") or {}

    worst_dg_kcal = heterodimer.get("worst_dg_kcal")
    if (
        worst_dg_kcal is None
        and heterodimer.get("worst_dg_cal_per_mol") is not None
    ):
        try:
            worst_dg_kcal = float(heterodimer["worst_dg_cal_per_mol"]) / 1000.0
        except (TypeError, ValueError):
            worst_dg_kcal = None

    evaluated_pairs = _optional_nonnegative_int(
        heterodimer.get("evaluated_pairs")
    )
    total_pairs = _optional_nonnegative_int(heterodimer.get("total_pairs"))
    if evaluated_pairs is not None:
        if evaluated_pairs == 0:
            evaluation_status = "NOT_EVALUATED"
        elif total_pairs is not None and evaluated_pairs < total_pairs:
            evaluation_status = "PARTIAL"
        else:
            evaluation_status = "COMPLETED"
    elif worst_dg_kcal is not None:
        evaluation_status = "COMPLETED"
    else:
        evaluation_status = "NOT_EVALUATED"

    worst_pair = heterodimer.get("worst_pair")
    if isinstance(worst_pair, (list, tuple)):
        pair_items = [
            _text(item).strip() for item in worst_pair if _text(item).strip()
        ]
        worst_pair_text = " ↔ ".join(pair_items)
    else:
        worst_pair_text = _text(worst_pair).strip()

    relaxed = p4_result.get("relaxed")
    if relaxed is True:
        mode = "放宽模式"
        mode_code = "RELAXED"
    elif relaxed is False:
        mode = "标准模式"
        mode_code = "STANDARD"
    else:
        mode = "未提供"
        mode_code = "NOT_PROVIDED"

    return {
        "status": evaluation_status,
        "worst_dg_kcal": worst_dg_kcal,
        "worst_pair": worst_pair_text or None,
        "evaluated_pairs": evaluated_pairs,
        "total_pairs": total_pairs,
        "threshold_dg_kcal": thresholds.get("cross_dimer_dg_kcal"),
        "mode": mode,
        "mode_code": mode_code,
    }


def _cross_dimer_progress(data: dict[str, Any]) -> str:
    evaluated_pairs = data.get("evaluated_pairs")
    total_pairs = data.get("total_pairs")
    if evaluated_pairs is None and total_pairs is None:
        return "未提供"
    if evaluated_pairs is None:
        return f"未提供 / {total_pairs}"
    if total_pairs is None:
        return str(evaluated_pairs)
    return f"{evaluated_pairs} / {total_pairs}"


def _cross_dimer_status_label(status: Any) -> str:
    return {
        "COMPLETED": "已评估",
        "PARTIAL": "部分评估",
        "NOT_EVALUATED": "未评估",
    }.get(_text(status).upper(), _display_label(status))


def _cross_dimer_summary_text(panel: dict[str, Any]) -> str:
    data = _cross_dimer_data(panel)
    return (
        "交叉二聚体（cross-dimer）检查："
        f"状态 {_cross_dimer_status_label(data['status'])}；"
        f"最不利 ΔG {_format_number(data['worst_dg_kcal'], digits=3, suffix=' kcal/mol')}；"
        f"最不利组合 {_text(data['worst_pair']) or '未提供'}；"
        f"已检查／总组合 {_cross_dimer_progress(data)}；"
        f"判定阈值 {_format_number(data['threshold_dg_kcal'], digits=3, suffix=' kcal/mol')}；"
        f"{data['mode']}。"
    )


def _cross_dimer_summary_html(panel: dict[str, Any]) -> str:
    data = _cross_dimer_data(panel)
    return (
        '<section class="cross-dimer-summary" aria-label="交叉二聚体检查">'
        '<div class="cross-dimer-heading">'
        "<h3>交叉二聚体（cross-dimer）检查</h3>"
        "<span>面板级 P4 汇总</span>"
        "</div>"
        '<dl class="cross-dimer-data">'
        f'{_fact("检查状态", _badge(data["status"], label=_cross_dimer_status_label(data["status"])))}'
        f'{_fact("最不利 ΔG", html.escape(_format_number(data["worst_dg_kcal"], digits=3, suffix=" kcal/mol")))}'
        f'{_fact("最不利组合", html.escape(_text(data["worst_pair"]) or "未提供"))}'
        f'{_fact("已检查／总组合", html.escape(_cross_dimer_progress(data)))}'
        f'{_fact("判定阈值", html.escape(_format_number(data["threshold_dg_kcal"], digits=3, suffix=" kcal/mol")))}'
        f'{_fact("检查模式", html.escape(_text(data["mode"])))}'
        "</dl></section>"
    )


def _member_card(member: dict[str, Any]) -> str:
    forward = member.get("forward_primer") or {}
    reverse = member.get("reverse_primer") or {}
    probe = member.get("probe") or {}
    amplicon = member.get("amplicon") or {}
    channel = " / ".join(
        item
        for item in (
            _text(member.get("fluorophore")).strip(),
            _text(member.get("quencher")).strip(),
        )
        if item
    ) or "未提供"
    specificity = member.get("specificity") or {}
    specificity_status = None
    if isinstance(specificity, dict):
        specificity_status = specificity.get(
            "overall_status"
        ) or specificity.get("status")
    warning_items = [
        f'<li>{html.escape(_readable_item(item))}</li>'
        for item in member.get("warnings") or []
    ]
    warning_html = (
        '<div class="inline-note"><strong>成员注意项</strong><ul>'
        + "".join(warning_items)
        + "</ul></div>"
        if warning_items
        else ""
    )
    return (
        '<section class="member-section">'
        '<div class="member-heading">'
        '<div>'
        f'<p class="section-kicker">{html.escape(_display_label(member.get("role")))}</p>'
        f'<h3>{html.escape(_text(member.get("target_id")) or "未命名成员")}</h3>'
        "</div>"
        f'<span class="channel-text">通道：{html.escape(channel)}</span>'
        "</div>"
        '<dl class="member-key-data">'
        f'{_fact("候选编号", html.escape(_text(member.get("candidate_id")) or "未提供"))}'
        f'{_fact("扩增子长度", html.escape(_format_number(amplicon.get("length_bp"), digits=0, suffix=" bp")))}'
        f'{_fact("综合核验", _badge(specificity_status) if specificity_status else html.escape("未提供"))}'
        f'{_fact("保守性", html.escape(_format_conservation(member.get("conservation_score"))))}'
        f'{_fact("证据来源", html.escape(_display_label(member.get("evidence_level"))))}'
        "</dl>"
        '<div class="table-scroll"><table class="sequence-table">'
        "<thead><tr><th>类型</th><th>序列（5′ → 3′）</th><th>Tm</th>"
        "<th>发卡 ΔG</th><th>同源二聚体 ΔG</th><th>3′ 二聚体 ΔG</th></tr></thead>"
        "<tbody>"
        f'{_oligo_row("正向引物", forward)}'
        f'{_oligo_row("反向引物", reverse)}'
        f'{_oligo_row("探针", probe)}'
        "</tbody></table></div>"
        '<div class="member-checks"><h4>检查结果</h4>'
        f"{_specificity_summary(member)}</div>"
        f"{warning_html}</section>"
    )


def _panel_technical_summary(panel: dict[str, Any]) -> str:
    p4_result = panel.get("p4_result") or {}
    balance = panel.get("balance") or {}
    score = panel.get("score") or {}
    items = [
        ("兼容性结论", _badge(p4_result.get("verdict") or p4_result.get("status"))),
        (
            "成员质量合计",
            html.escape(_format_number(score.get("target_quality_sum"), digits=3)),
        ),
        (
            "兼容性系数",
            html.escape(_format_number(score.get("compatibility_coefficient"), digits=2)),
        ),
        (
            "温度均衡系数",
            html.escape(_format_number(score.get("tm_balance_coefficient"), digits=2)),
        ),
        (
            "引物 Tm 极差",
            html.escape(
                _format_number(
                    balance.get("primer_tm_spread_celsius"), suffix=" °C"
                )
            ),
        ),
        (
            "探针 Tm 极差",
            html.escape(
                _format_number(
                    balance.get("probe_tm_spread_celsius"), suffix=" °C"
                )
            ),
        ),
        (
            "GC 极差",
            html.escape(
                _format_number(balance.get("gc_spread_percent"), suffix="%")
            ),
        ),
    ]
    issue_values = list(p4_result.get("issues") or [])
    issue_html = (
        '<div class="inline-note"><strong>附加说明</strong><ul>'
        + "".join(
            f"<li>{html.escape(_readable_item(item))}</li>"
            for item in issue_values
        )
        + "</ul></div>"
        if issue_values
        else ""
    )
    return (
        '<details class="technical-summary">'
        "<summary>展开次要技术参数</summary>"
        '<dl class="technical-list">'
        + "".join(
            "<div>"
            f"<dt>{html.escape(label)}</dt><dd>{value}</dd>"
            "</div>"
            for label, value in items
        )
        + "</dl>"
        + issue_html
        + "</details>"
    )


def _comparison_table(
    report: dict[str, Any], panels: list[dict[str, Any]]
) -> str:
    rows = list((report.get("candidate_comparison") or {}).get("rows") or [])
    if not rows:
        rows = [
            {
                "rank": panel.get("rank"),
                "panel_id": panel.get("panel_id"),
                "tier": panel.get("tier"),
                "final_score": (panel.get("score") or {}).get("final_score"),
                "member_count": len(panel.get("members") or []),
                "warning_count": len(panel.get("warnings") or []),
            }
            for panel in panels
        ]
    if not rows:
        return '<p class="empty-state">当前没有可对比的方案。</p>'
    body_rows: list[str] = []
    for row in rows:
        rank = _text(row.get("rank")) or "-"
        anchor_rank = _text(row.get("rank")) or "0"
        purpose = "首选" if rank == "1" else "备选"
        body_rows.append(
            "<tr>"
            f"<td><strong>#{html.escape(rank)}</strong></td>"
            f'<td><a class="panel-link" href="#panel-{html.escape(anchor_rank)}">'
            f"方案 {html.escape(rank)}</a>"
            f'<span class="trace-id">{html.escape(_text(row.get("panel_id")) or "未提供")}</span></td>'
            f'<td>{_badge(row.get("tier"))}</td>'
            f'<td>{html.escape(_format_number(row.get("final_score"), digits=3))}</td>'
            f'<td>{html.escape(_format_number(row.get("member_count"), digits=0))}</td>'
            f'<td>{html.escape(_format_number(row.get("warning_count"), digits=0))}</td>'
            f"<td>{purpose}</td>"
            "</tr>"
        )
    body = "".join(body_rows)
    return (
        '<div class="table-scroll"><table class="comparison-table">'
        "<thead><tr><th>排名</th><th>方案</th><th>类型</th><th>综合评分</th>"
        "<th>成员数</th><th>注意项</th><th>用途</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def _aggregate_check_status(values: list[Any]) -> str:
    normalized = [_text(value).upper() for value in values if _text(value)]
    if any(
        token in value
        for value in normalized
        for token in ("FAIL", "BLOCK", "ERROR", "INCOMPATIBLE")
    ):
        return "FAIL"
    if any("NOT_EVALUATED" in value for value in normalized):
        return "NOT_EVALUATED"
    if any(
        token in value
        for value in normalized
        for token in ("WARNING", "CAUTION", "RISK", "MARGINAL", "PARTIAL")
    ):
        return "WARNING"
    if normalized:
        return "PASS"
    return "NOT_EVALUATED"


def _validation_steps(
    report: dict[str, Any], panels: list[dict[str, Any]]
) -> str:
    multiplex = report.get("multiplex") or {}
    outcome = report.get("outcome") or {}
    coverage = multiplex.get("coverage") or outcome.get("coverage_status") or {}
    primary = panels[0] if panels else {}
    members = list(primary.get("members") or [])
    covered = coverage.get("covered_targets")
    requested = coverage.get("requested_targets")
    coverage_detail = (
        f"{covered} / {requested}"
        if covered is not None and requested is not None
        else _display_label(coverage.get("status"))
    )

    member_statuses: list[Any] = []
    for member in members:
        checks = _specificity_checks(member)
        overall = next(
            (status for label, status in checks if label == "综合状态"),
            None,
        )
        if overall:
            member_statuses.append(overall)

    p4_result = primary.get("p4_result") or {}
    balance = primary.get("balance") or {}
    comparison = report.get("candidate_comparison") or {}
    final_score = (primary.get("score") or {}).get("final_score")
    steps = [
        (
            "数据覆盖",
            coverage.get("status") or outcome.get("workflow_status"),
            f"覆盖进度 {coverage_detail}",
        ),
        (
            "候选形成",
            "PASS" if panels else outcome.get("workflow_status"),
            f"{len(panels)} 个方案进入结果页",
        ),
        (
            "数据库核验",
            _aggregate_check_status(member_statuses),
            f"首选方案包含 {len(members)} 个成员",
        ),
        (
            "组合检查",
            p4_result.get("verdict") or p4_result.get("status"),
            (
                f"附加说明 {len(p4_result.get('issues') or [])} 项"
                if primary
                else "未形成可检查方案"
            ),
        ),
        (
            "均衡检查",
            balance.get("status") or ("PASS" if balance else None),
            (
                "Tm 极差：引物 {primer}；探针 {probe}".format(
                    primer=_format_number(
                        balance.get("primer_tm_spread_celsius"),
                        suffix=" °C",
                    ),
                    probe=_format_number(
                        balance.get("probe_tm_spread_celsius"),
                        suffix=" °C",
                    ),
                )
                if balance
                else "未提供均衡摘要"
            ),
        ),
        (
            "评分与排序",
            comparison.get("status") or ("PASS" if panels else None),
            f"首选评分 {_format_number(final_score, digits=3)}",
        ),
    ]
    return '<ol class="validation-list">' + "".join(
        "<li>"
        f'<span class="step-number">{index:02d}</span>'
        '<div class="step-copy">'
        f"<strong>{html.escape(label)}</strong>"
        f"<span>{html.escape(detail)}</span>"
        "</div>"
        f"{_badge(status)}"
        "</li>"
        for index, (label, status, detail) in enumerate(steps, start=1)
    ) + "</ol>"


def _terminal_follow_up(report: dict[str, Any]) -> str:
    multiplex = report.get("multiplex") or {}
    outcome = report.get("outcome") or {}
    items = list(multiplex.get("coverage_gaps") or [])
    items.extend(report.get("failure_guidance") or [])
    items.extend(outcome.get("reason_codes") or [])
    if not items:
        return ""
    unique: list[tuple[str, str]] = []
    for item in items:
        visible = _readable_item(item)
        raw_code = ""
        if isinstance(item, dict):
            raw_code = _text(item.get("reason_code") or item.get("code"))
        elif isinstance(item, str) and item.upper() == item:
            raw_code = item
        entry = (visible, raw_code)
        if visible and entry not in unique:
            unique.append(entry)
    if not unique:
        return ""
    return (
        '<section class="report-section follow-up">'
        '<div class="section-heading"><span>05</span><h2>未完成项与建议</h2></div><ul>'
        + "".join(
            "<li{}>{}</li>".format(
                (
                    f' data-reason-code="{html.escape(raw_code)}"'
                    if raw_code
                    else ""
                ),
                html.escape(visible),
            )
            for visible, raw_code in unique
        )
        + "</ul></section>"
    )


def render_wf02_html(report: dict[str, Any]) -> bytes:
    panels = _delivery_panels(report)
    source = report.get("source") or {}
    result = report.get("result_summary") or {}
    internal = report.get("internal_control_summary") or {}
    multiplex = report.get("multiplex") or {}
    coverage = multiplex.get("coverage") or (
        (report.get("outcome") or {}).get("coverage_status") or {}
    )
    delivered_count = len(panels)
    covered = coverage.get("covered_targets")
    requested = coverage.get("requested_targets")
    coverage_value = (
        f"{covered} / {requested}"
        if covered is not None and requested is not None
        else _display_label(coverage.get("status"))
    )
    warning_count = sum(len(panel.get("warnings") or []) for panel in panels)
    primary = panels[0] if panels else {}
    primary_score = (primary.get("score") or {}).get("final_score")
    narrative = (
        f"本次共返回 {delivered_count} 个可查看方案，并按综合评分排列；"
        "下方先列出关键结论与检查步骤，再展开详细参数。"
        if panels
        else "本次没有生成可交付方案，请查看下方未完成项与建议。"
    )
    panel_sections: list[str] = []
    for index, panel in enumerate(panels, start=1):
        rank = panel.get("rank") or index
        purpose = "首选方案" if _text(rank) == "1" else "备选方案"
        warning_values = list(panel.get("warnings") or [])
        warning_html = (
            '<div class="inline-note"><strong>注意事项</strong><ul>'
            + "".join(
                f"<li>{html.escape(_readable_item(item))}</li>"
                for item in warning_values
            )
            + "</ul></div>"
            if warning_values
            else '<p class="quiet-note">当前方案没有额外注意项。</p>'
        )
        members = "".join(
            _member_card(member) for member in panel.get("members") or []
        ) or '<p class="empty-state">该方案没有可展示的成员。</p>'
        panel_sections.append(
            f'<section class="panel-section" id="panel-{html.escape(_text(rank))}">'
            '<div class="panel-heading">'
            '<div><p class="section-kicker">'
            f'03.{html.escape(_text(rank))} · {purpose}</p>'
            f'<h2>方案 {html.escape(_text(rank))} {_badge(panel.get("tier"))}</h2>'
            f'<p class="trace-id">追踪编号：{html.escape(_text(panel.get("panel_id")) or "未提供")}</p>'
            "</div>"
            '<div class="score-focus"><span>综合评分</span>'
            f'<strong>{html.escape(_format_number((panel.get("score") or {}).get("final_score"), digits=3))}</strong></div>'
            "</div>"
            '<dl class="panel-key-data">'
            f'{_fact("建议温度", html.escape(_format_number(panel.get("recommended_annealing_temp_celsius"), suffix=" °C")))}'
            f'{_fact("成员数量", html.escape(_format_number(len(panel.get("members") or []), digits=0)))}'
            f'{_fact("注意项", html.escape(_format_number(len(warning_values), digits=0)))}'
            f'{_fact("组合检查", _badge((panel.get("p4_result") or {}).get("verdict") or (panel.get("p4_result") or {}).get("status")))}'
            "</dl>"
            f"{_cross_dimer_summary_html(panel)}"
            f"{warning_html}"
            '<div class="member-list">'
            f"{members}</div>{_panel_technical_summary(panel)}</section>"
        )
    panel_body = "".join(panel_sections)
    result_status = result.get("status") or result.get("workflow_status")
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>WF-02 方案报告</title>
<style>
:root{{--ink:#1d2226;--muted:#697179;--line:#dfe3e6;--soft:#f6f7f8;--accent:#315d73;--success:#2f6b52;--warning:#7a5b1d;--danger:#9c3f3f}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:#fff;color:var(--ink);font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",Arial,sans-serif;line-height:1.65}}
.report-shell{{width:min(1040px,calc(100% - 48px));margin:0 auto 64px}}.report-header{{padding:52px 0 28px;border-bottom:2px solid var(--ink)}}.header-top{{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:14px}}
.eyebrow,.section-kicker{{margin:0;color:var(--accent);font-size:12px;font-weight:700;letter-spacing:.13em;text-transform:uppercase}}h1{{margin:0;font-size:clamp(30px,4vw,44px);font-weight:650;line-height:1.2;letter-spacing:-.02em}}h2{{margin:0;font-size:24px;line-height:1.35}}h3{{margin:0;font-size:18px}}h4{{margin:0;font-size:14px}}.report-intro{{max-width:760px;margin:14px 0 0;color:#454c52;font-size:15px}}
.meta-row{{display:flex;gap:8px 28px;flex-wrap:wrap;margin:22px 0 0;color:var(--muted);font-size:12px}}.meta-row b{{color:var(--ink);font-weight:600}}
.badge{{display:inline-flex;align-items:center;gap:7px;width:max-content;color:var(--muted);font-size:12px;font-weight:700;line-height:1.35;vertical-align:middle}}.badge::before{{width:7px;height:7px;border-radius:50%;background:currentColor;content:""}}.badge--success{{color:var(--success)}}.badge--warning{{color:var(--warning)}}.badge--danger{{color:var(--danger)}}.badge--neutral{{color:var(--muted)}}
.key-metrics{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));margin:28px 0 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}}.key-metrics .key-item{{padding:18px 20px}}.key-metrics .key-item+.key-item{{border-left:1px solid var(--line)}}.key-item{{min-width:0}}.key-item dt{{color:var(--muted);font-size:11px}}.key-item dd{{margin:4px 0 0;font-size:15px;font-weight:650;overflow-wrap:anywhere}}.key-metrics dd{{font-size:22px}}
.report-section,.panel-section{{padding:38px 0;border-bottom:1px solid var(--line)}}.section-heading{{display:flex;align-items:baseline;gap:14px;margin-bottom:20px}}.section-heading>span{{color:var(--accent);font:700 12px/1 Consolas,monospace}}.context-notes{{display:grid;gap:10px}}.context-notes p{{margin:0;color:#4f565c;font-size:13px}}.context-notes strong{{color:var(--ink)}}
.validation-list{{margin:0;padding:0;list-style:none;border-top:1px solid var(--line)}}.validation-list li{{display:grid;grid-template-columns:48px minmax(0,1fr) auto;gap:14px;align-items:center;padding:15px 0;border-bottom:1px solid var(--line)}}.step-number{{color:#98a0a6;font:12px/1 Consolas,monospace}}.step-copy{{display:grid;grid-template-columns:minmax(120px,.45fr) minmax(220px,1fr);gap:18px;align-items:center}}.step-copy strong{{font-size:14px}}.step-copy span{{color:var(--muted);font-size:13px}}
.table-scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%}}.comparison-table{{min-width:720px;font-size:13px}}th,td{{padding:11px 12px;text-align:left;border-bottom:1px solid var(--line);vertical-align:middle}}thead th{{background:var(--soft);color:#535b62;font-size:11px;font-weight:650}}tbody th{{font-size:12px}}.panel-link{{display:block;color:var(--accent);font-weight:700;text-decoration:none}}.trace-id{{display:block;margin-top:3px;color:#858c92;font:11px/1.45 Consolas,"SFMono-Regular",monospace;overflow-wrap:anywhere}}
.panel-section{{padding-top:48px;scroll-margin-top:18px;border-bottom:2px solid var(--ink)}}.panel-heading,.member-heading{{display:flex;align-items:flex-start;justify-content:space-between;gap:24px}}.panel-heading h2 .badge{{margin-left:9px}}.score-focus{{min-width:150px;padding-left:24px;border-left:1px solid var(--line);text-align:right}}.score-focus span{{display:block;color:var(--muted);font-size:11px}}.score-focus strong{{display:block;margin-top:2px;font-size:30px;line-height:1.15}}.panel-key-data,.member-key-data{{display:grid;margin:22px 0 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}}.panel-key-data{{grid-template-columns:repeat(4,minmax(0,1fr))}}.panel-key-data .key-item,.member-key-data .key-item{{padding:12px 14px}}.panel-key-data .key-item+.key-item,.member-key-data .key-item+.key-item{{border-left:1px solid var(--line)}}
.cross-dimer-summary{{margin-top:24px;padding:16px 0 0;border-top:2px solid var(--accent)}}.cross-dimer-heading{{display:flex;align-items:baseline;justify-content:space-between;gap:18px}}.cross-dimer-heading h3{{font-size:16px}}.cross-dimer-heading span{{color:var(--muted);font-size:11px}}.cross-dimer-data{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));margin:12px 0 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}}.cross-dimer-data .key-item{{padding:11px 12px}}.cross-dimer-data .key-item:not(:nth-child(3n+1)){{border-left:1px solid var(--line)}}.cross-dimer-data .key-item:nth-child(n+4){{border-top:1px solid var(--line)}}
.inline-note{{margin:18px 0 0;padding-left:14px;border-left:2px solid #aeb5ba;color:#4c5359;font-size:13px}}.inline-note strong{{color:var(--ink)}}.inline-note ul,.follow-up ul{{margin:5px 0 0;padding-left:20px}}.quiet-note{{margin:15px 0 0;color:var(--muted);font-size:12px}}
.member-list{{margin-top:22px}}.member-section{{padding:30px 0 6px;border-top:1px solid var(--line)}}.member-section:first-child{{border-top:0}}.channel-text{{color:var(--muted);font-size:12px}}.member-key-data{{grid-template-columns:1.2fr repeat(4,minmax(0,1fr));margin-top:14px}}.sequence-table{{min-width:900px;margin-top:18px;font-size:12px}}.sequence-table th:first-child{{width:92px}}.sequence-table .sequence{{min-width:230px;font:700 12px/1.8 Consolas,"SFMono-Regular",monospace;letter-spacing:.06em;white-space:nowrap}}.member-checks{{display:grid;grid-template-columns:130px minmax(0,1fr);gap:14px;align-items:start;margin-top:16px}}.check-results{{display:flex;gap:10px 24px;flex-wrap:wrap;margin:0;padding:0;list-style:none}}.check-results li{{display:flex;gap:9px;align-items:center;font-size:12px}}.check-results li>span:first-child{{color:var(--muted)}}
.technical-summary{{margin-top:20px;padding-top:14px;border-top:1px solid var(--line)}}.technical-summary summary{{cursor:pointer;color:var(--accent);font-size:13px;font-weight:700}}.technical-summary[open] summary{{margin-bottom:12px}}.technical-list{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));margin:0;border-top:1px solid var(--line)}}.technical-list>div{{padding:10px 12px;border-bottom:1px solid var(--line)}}.technical-list dt{{color:var(--muted);font-size:11px}}.technical-list dd{{margin:3px 0 0;font-size:12px;font-weight:600}}.empty-value,.empty-state{{color:var(--muted);font-weight:400}}.empty-state{{padding:18px 0;text-align:center}}.report-footer{{padding:24px 0;color:var(--muted);font-size:11px}}.report-footer p{{margin:3px 0}}
@media(max-width:760px){{.report-shell{{width:min(100% - 28px,1040px)}}.report-header{{padding-top:30px}}.key-metrics{{grid-template-columns:1fr 1fr}}.key-metrics .key-item:nth-child(3){{border-left:0;border-top:1px solid var(--line)}}.key-metrics .key-item:nth-child(4){{border-top:1px solid var(--line)}}.step-copy{{grid-template-columns:1fr;gap:2px}}.panel-key-data{{grid-template-columns:1fr 1fr}}.panel-key-data .key-item:nth-child(3){{border-left:0;border-top:1px solid var(--line)}}.panel-key-data .key-item:nth-child(4){{border-top:1px solid var(--line)}}.cross-dimer-data{{grid-template-columns:1fr 1fr}}.cross-dimer-data .key-item:not(:nth-child(3n+1)){{border-left:0}}.cross-dimer-data .key-item:nth-child(even){{border-left:1px solid var(--line)}}.cross-dimer-data .key-item:nth-child(n+3){{border-top:1px solid var(--line)}}.member-key-data{{grid-template-columns:1fr 1fr}}.member-key-data .key-item{{border-bottom:1px solid var(--line)}}.member-key-data .key-item:nth-child(odd){{border-left:0}}.technical-list{{grid-template-columns:1fr 1fr}}}}
@media(max-width:520px){{.header-top,.panel-heading,.member-heading,.cross-dimer-heading{{align-items:flex-start;flex-direction:column}}.cross-dimer-heading{{gap:2px}}.score-focus{{width:100%;padding:10px 0 0;border-top:1px solid var(--line);border-left:0;text-align:left}}.validation-list li{{grid-template-columns:34px minmax(0,1fr)}}.validation-list li>.badge{{grid-column:2}}.cross-dimer-data{{grid-template-columns:1fr}}.cross-dimer-data .key-item:nth-child(even){{border-left:0}}.cross-dimer-data .key-item:nth-child(n+2){{border-top:1px solid var(--line)}}.member-checks{{grid-template-columns:1fr}}.meta-row{{display:grid;gap:4px}}}}
@media print{{@page{{size:A4 portrait;margin:12mm}}.report-shell{{width:100%;margin:0}}.report-header{{padding-top:0}}.panel-section{{break-before:page}}.member-section{{break-inside:avoid}}details:not([open])>*:not(summary){{display:block!important}}.table-scroll{{overflow:visible}}}}
</style></head>
<body><main class="report-shell">
<header class="report-header"><div class="header-top"><p class="eyebrow">WF-02 · 结果报告</p>{_badge(result_status)}</div><h1>多方案结果与参数摘要</h1>
<p class="report-intro">{html.escape(narrative)}</p>
<div class="meta-row"><span><b>任务：</b>{html.escape(_text(source.get('batch_code')) or '未提供')}</span><span><b>版本：</b>{html.escape(_text(source.get('task_version')) or '未提供')}</span><span><b>生成时间：</b>{html.escape(_format_timestamp(report.get('generated_at')))}</span></div></header>
<dl class="key-metrics" aria-label="关键数据">{_fact("执行状态", html.escape(_display_label(result_status)))}{_fact("首选评分", html.escape(_format_number(primary_score, digits=3)))}{_fact("方案数量", html.escape(_format_number(delivered_count, digits=0)))}{_fact("覆盖进度", html.escape(_text(coverage_value)))}</dl>
<section class="report-section"><div class="section-heading"><span>01</span><h2>检查步骤</h2></div>{_validation_steps(report, panels)}</section>
<section class="report-section"><div class="section-heading"><span>02</span><h2>方案对比</h2></div>{_comparison_table(report, panels)}</section>
{panel_body}
<section class="report-section"><div class="section-heading"><span>04</span><h2>报告说明</h2></div><div class="context-notes"><p><strong>辅助项：</strong>{html.escape(_text(internal.get('statement')) or '未提供')}</p><p><strong>使用说明：</strong>{html.escape(_text(report.get('experimental_validation_statement')) or '请结合实际运行条件复核本报告。')}</p><p><strong>注意项总数：</strong>{warning_count}</p></div></section>
{_terminal_follow_up(report)}
<footer class="report-footer"><p>页面仅展示适合阅读的摘要字段；完整结构化记录由系统接口保留。</p><p>首选方案：{html.escape(_text(primary.get('panel_id')) or '无')}</p></footer>
</main></body></html>"""
    return document.encode("utf-8")


def render_wf02_csv(report: dict[str, Any]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    header = [
            "panel_rank",
            "panel_id",
            "tier",
            "panel_score",
            "cross_dimer_evaluation_status",
            "cross_dimer_worst_dg_kcal",
            "cross_dimer_worst_pair",
            "cross_dimer_evaluated_pairs",
            "cross_dimer_total_pairs",
            "cross_dimer_threshold_dg_kcal",
            "cross_dimer_mode",
            "target_id",
            "role",
            "candidate_id",
            "fluorophore",
            "quencher",
            "forward_sequence_5to3",
            "forward_tm_celsius",
            "forward_hairpin_dg_kcal",
            "forward_homodimer_dg_kcal",
            "forward_three_prime_dimer_dg_kcal",
            "reverse_sequence_5to3",
            "reverse_tm_celsius",
            "reverse_hairpin_dg_kcal",
            "reverse_homodimer_dg_kcal",
            "reverse_three_prime_dimer_dg_kcal",
            "probe_sequence_5to3",
            "probe_tm_celsius",
            "probe_hairpin_dg_kcal",
            "probe_homodimer_dg_kcal",
            "probe_three_prime_dimer_dg_kcal",
            "amplicon_sequence_5to3",
            "amplicon_length_bp",
            "blast_status",
            "conservation_score",
            "evidence_level",
            "recommended_annealing_temp_celsius",
            "internal_control_statement",
            "warnings",
            "workflow_status",
            "primary_failure_stage",
            "reason_codes",
            "coverage_status",
            "failure_guidance",
        ]
    writer.writerow(header)
    outcome = report.get("outcome") or {}
    coverage = (report.get("multiplex") or {}).get("coverage") or {}
    terminal_columns = [
        outcome.get("workflow_status"),
        outcome.get("primary_failure_stage"),
        " | ".join(str(item) for item in outcome.get("reason_codes") or []),
        _text(coverage),
        " | ".join(str(item) for item in report.get("failure_guidance") or []),
    ]
    delivered_row_count = 0
    for panel in _delivery_panels(report):
        cross_dimer = _cross_dimer_data(panel)
        for member in panel.get("members") or []:
            specificity = member.get("specificity") or {}
            forward = member.get("forward_primer") or {}
            reverse = member.get("reverse_primer") or {}
            probe = member.get("probe") or {}
            writer.writerow(
                [
                    panel.get("rank"),
                    panel.get("panel_id"),
                    panel.get("tier"),
                    (panel.get("score") or {}).get("final_score"),
                    cross_dimer.get("status"),
                    cross_dimer.get("worst_dg_kcal"),
                    cross_dimer.get("worst_pair"),
                    cross_dimer.get("evaluated_pairs"),
                    cross_dimer.get("total_pairs"),
                    cross_dimer.get("threshold_dg_kcal"),
                    cross_dimer.get("mode_code"),
                    member.get("target_id"),
                    member.get("role"),
                    member.get("candidate_id"),
                    member.get("fluorophore"),
                    member.get("quencher"),
                    forward.get("sequence_5to3"),
                    forward.get("tm_celsius"),
                    forward.get("hairpin_dg_kcal"),
                    forward.get("homodimer_dg_kcal"),
                    forward.get("three_prime_dimer_dg_kcal"),
                    reverse.get("sequence_5to3"),
                    reverse.get("tm_celsius"),
                    reverse.get("hairpin_dg_kcal"),
                    reverse.get("homodimer_dg_kcal"),
                    reverse.get("three_prime_dimer_dg_kcal"),
                    probe.get("sequence_5to3"),
                    probe.get("tm_celsius"),
                    probe.get("hairpin_dg_kcal"),
                    probe.get("homodimer_dg_kcal"),
                    probe.get("three_prime_dimer_dg_kcal"),
                    (member.get("amplicon") or {}).get("sequence_5to3"),
                    (member.get("amplicon") or {}).get("length_bp"),
                    specificity.get("overall_status") or specificity.get("status"),
                    member.get("conservation_score"),
                    member.get("evidence_level"),
                    panel.get("recommended_annealing_temp_celsius"),
                    (report.get("internal_control_summary") or {}).get("statement"),
                    " | ".join(_text(item) for item in panel.get("warnings") or []),
                    *terminal_columns,
                ]
            )
            delivered_row_count += 1
    if delivered_row_count == 0:
        summary_row = [""] * len(header)
        for name, value in zip(
            (
                "workflow_status",
                "primary_failure_stage",
                "reason_codes",
                "coverage_status",
                "failure_guidance",
            ),
            terminal_columns,
        ):
            summary_row[header.index(name)] = value
        writer.writerow(summary_row)
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def render_wf02_pdf(report: dict[str, Any]) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.platypus import LongTable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, TableStyle
        from reportlab.platypus.doctemplate import LayoutError
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise PersistenceError("WF-02 PDF 导出依赖 reportlab") from exc

    try:
        pdfmetrics.getFont("STSong-Light")
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=24,
        leftMargin=24,
        topMargin=24,
        bottomMargin=24,
    )
    styles = getSampleStyleSheet()
    body_style = ParagraphStyle(
        "WF02Body",
        parent=styles["BodyText"],
        fontName="STSong-Light",
        fontSize=8,
        leading=11,
        wordWrap="CJK",
    )
    title_style = ParagraphStyle(
        "WF02Title", parent=styles["Title"], fontName="STSong-Light", fontSize=18, leading=22
    )

    def draw_page_footer(canvas: Any, active_document: Any) -> None:
        canvas.saveState()
        canvas.setFont("STSong-Light", 7)
        canvas.setFillColor(colors.HexColor("#657672"))
        canvas.drawString(
            active_document.leftMargin,
            12,
            "WF-02 方案报告",
        )
        canvas.drawRightString(
            active_document.pagesize[0] - active_document.rightMargin,
            12,
            f"第 {canvas.getPageNumber()} 页",
        )
        canvas.restoreState()

    panels = _delivery_panels(report)
    source = report.get("source") or {}
    result = report.get("result_summary") or {}
    outcome = report.get("outcome") or {}
    coverage = (report.get("multiplex") or {}).get("coverage") or (
        outcome.get("coverage_status") or {}
    )
    covered = coverage.get("covered_targets")
    requested = coverage.get("requested_targets")
    coverage_summary = (
        f"{covered}/{requested}"
        if covered is not None and requested is not None
        else _display_label(coverage.get("status"))
    )
    comparison_rows = list(
        (report.get("candidate_comparison") or {}).get("rows") or []
    )
    if not comparison_rows:
        comparison_rows = [
            {
                "rank": panel.get("rank"),
                "tier": panel.get("tier"),
                "final_score": (panel.get("score") or {}).get("final_score"),
                "member_count": len(panel.get("members") or []),
                "warning_count": len(panel.get("warnings") or []),
            }
            for panel in panels
        ]
    follow_up_items = list((report.get("multiplex") or {}).get("coverage_gaps") or [])
    follow_up_items.extend(report.get("failure_guidance") or [])
    follow_up_items.extend(outcome.get("reason_codes") or [])
    story: list[Any] = [
        Paragraph("WF-02 多方案结果与参数摘要", title_style),
        Paragraph(
            _pdf_text(
                f"任务 {source.get('batch_code', '')}；"
                f"版本 {source.get('task_version', '')}；"
                f"生成时间 {_format_timestamp(report.get('generated_at'))}",
                max_chars=500,
            ),
            body_style,
        ),
        Spacer(1, 10),
        Paragraph(
            _pdf_text(
                f"执行状态：{_display_label(result.get('status') or result.get('workflow_status'))}；"
                f"可查看方案：{len(panels)}；覆盖进度：{coverage_summary}。",
                max_chars=800,
            ),
            body_style,
        ),
        Paragraph(
            _pdf_text(
                (
                    f"本次共返回 {len(panels)} 个可查看方案，并按综合评分排列。"
                    if panels
                    else "本次没有生成可交付方案，请查看未完成项与建议。"
                ),
                max_chars=800,
            ),
            body_style,
        ),
        Paragraph("候选方案对比", body_style),
        Paragraph(
            _pdf_text(
                "；".join(
                    "方案 {rank}：{tier}，评分 {score}，成员 {members}，注意项 {warnings}".format(
                        rank=row.get("rank") or "-",
                        tier=_display_label(row.get("tier")),
                        score=_format_number(row.get("final_score"), digits=3),
                        members=_format_number(row.get("member_count"), digits=0),
                        warnings=_format_number(row.get("warning_count"), digits=0),
                    )
                    for row in comparison_rows
                )
                or "当前没有可对比的方案。",
                max_chars=2000,
            ),
            body_style,
        ),
        Paragraph(
            _pdf_text(
                "辅助项说明："
                + _text(
                    (report.get("internal_control_summary") or {}).get(
                        "statement"
                    )
                ),
                max_chars=1200,
            ),
            body_style,
        ),
        Paragraph(
            _pdf_text(
                report.get("experimental_validation_statement"),
                max_chars=1200,
            ),
            body_style,
        ),
        Spacer(1, 12),
    ]
    if follow_up_items:
        story.append(Paragraph("未完成项与建议", body_style))
        for item in follow_up_items:
            story.append(
                Paragraph(
                    _pdf_text(f"• {_readable_item(item)}", max_chars=800),
                    body_style,
                )
            )
        story.append(Spacer(1, 8))
    for panel_index, panel in enumerate(panels):
        if panel_index:
            story.append(PageBreak())
        story.append(
            Paragraph(
                _pdf_text(
                    f"方案 {panel.get('rank')}（{_display_label(panel.get('tier'))}） | "
                    f"综合评分 {_format_number((panel.get('score') or {}).get('final_score'), digits=3)} | "
                    f"建议温度 {_format_number(panel.get('recommended_annealing_temp_celsius'), suffix='°C')} | "
                    f"追踪编号 {panel.get('panel_id')}",
                    max_chars=600,
                ),
                body_style,
            )
        )
        story.append(
            Paragraph(
                _pdf_text(_cross_dimer_summary_text(panel), max_chars=1000),
                body_style,
            )
        )
        for warning in panel.get("warnings") or []:
            story.append(
                Paragraph(
                    _pdf_text(
                        f"注意事项：{_readable_item(warning)}",
                        max_chars=600,
                    ),
                    body_style,
                )
            )
        data: list[list[Any]] = [
            [
                Paragraph(item, body_style)
                for item in [
                    "成员",
                    "通道",
                    "正向引物 5′→3′ / Tm / ΔG",
                    "反向引物 5′→3′ / Tm / ΔG",
                    "探针 5′→3′ / Tm / ΔG",
                    "扩增子",
                    "核验摘要",
                ]
            ]
        ]
        for member in panel.get("members") or []:
            forward = member.get("forward_primer") or {}
            reverse = member.get("reverse_primer") or {}
            probe = member.get("probe") or {}
            checks = _specificity_checks(member)
            check_summary = "；".join(
                f"{label}：{_display_label(status)}"
                for label, status in checks
            ) or "未提供核验摘要"
            evidence_summary = (
                f"{check_summary}；"
                f"保守性：{_format_conservation(member.get('conservation_score'))}；"
                f"证据来源：{_display_label(member.get('evidence_level'))}"
            )
            data.append(
                [
                    Paragraph(
                        _pdf_text(member.get("target_id"), max_chars=120),
                        body_style,
                    ),
                    Paragraph(
                        _pdf_text(
                            f"{member.get('fluorophore', '')}/{member.get('quencher', '')}",
                            max_chars=120,
                        ),
                        body_style,
                    ),
                    Paragraph(
                        _pdf_text(
                            f"{forward.get('sequence_5to3', '')} / "
                            f"{_format_number(forward.get('tm_celsius'), suffix='°C')} / "
                            f"{_dg_text(forward)}",
                            max_chars=300,
                        ),
                        body_style,
                    ),
                    Paragraph(
                        _pdf_text(
                            f"{reverse.get('sequence_5to3', '')} / "
                            f"{_format_number(reverse.get('tm_celsius'), suffix='°C')} / "
                            f"{_dg_text(reverse)}",
                            max_chars=300,
                        ),
                        body_style,
                    ),
                    Paragraph(
                        _pdf_text(
                            f"{probe.get('sequence_5to3', '')} / "
                            f"{_format_number(probe.get('tm_celsius'), suffix='°C')} / "
                            f"{_dg_text(probe)}",
                            max_chars=300,
                        ),
                        body_style,
                    ),
                    Paragraph(
                        _pdf_text(
                            _format_number(
                                (member.get("amplicon") or {}).get(
                                    "length_bp"
                                ),
                                digits=0,
                                suffix=" bp",
                            ),
                            max_chars=80,
                        ),
                        body_style,
                    ),
                    Paragraph(
                        _pdf_text(evidence_summary, max_chars=500),
                        body_style,
                    ),
                ]
            )
        table = LongTable(
            data,
            repeatRows=1,
            colWidths=[54, 54, 150, 150, 150, 55, 75],
            splitByRow=1,
            splitInRow=1,
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dfeeea")),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#8da8a2")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.extend([table, Spacer(1, 12)])

    try:
        document.build(
            story,
            onFirstPage=draw_page_footer,
            onLaterPages=draw_page_footer,
        )
        return buffer.getvalue()
    except LayoutError as primary_error:
        # An extreme ReportLab layout must not roll an otherwise completed
        # workflow back to READY_FOR_SCORING.  Retry with table-free flowables
        # whose visible values are strictly bounded and page-splittable.
        compact_buffer = io.BytesIO()
        compact_document = SimpleDocTemplate(
            compact_buffer,
            pagesize=landscape(A4),
            rightMargin=24,
            leftMargin=24,
            topMargin=24,
            bottomMargin=24,
        )
        compact_story: list[Any] = [
            Paragraph("WF-02 多方案结果与参数摘要（紧凑版）", title_style),
            Paragraph(
                "PDF 布局已自动切换为紧凑分页模式；完整结构化记录由系统接口保留。",
                body_style,
            ),
            Paragraph(
                _pdf_text(
                    f"任务 {source.get('batch_code', '')}；"
                    f"版本 {source.get('task_version', '')}；"
                    f"生成时间 {_format_timestamp(report.get('generated_at'))}；"
                    f"执行状态 {_display_label(result.get('status') or result.get('workflow_status'))}；"
                    f"可查看方案 {len(panels)}；覆盖进度 {coverage_summary}。",
                    max_chars=1200,
                ),
                body_style,
            ),
            Spacer(1, 10),
        ]
        for panel in _delivery_panels(report):
            compact_story.append(
                Paragraph(
                    _pdf_text(
                        f"方案 {panel.get('rank')}（{_display_label(panel.get('tier'))}）；"
                        f"综合评分 {_format_number((panel.get('score') or {}).get('final_score'), digits=3)}；"
                        f"建议温度 {_format_number(panel.get('recommended_annealing_temp_celsius'), suffix='°C')}；"
                        f"追踪编号 {panel.get('panel_id')}。",
                        max_chars=800,
                    ),
                    body_style,
                )
            )
            compact_story.append(
                Paragraph(
                    _pdf_text(
                        _cross_dimer_summary_text(panel), max_chars=1000
                    ),
                    body_style,
                )
            )
            for member in panel.get("members") or []:
                checks = _specificity_checks(member)
                check_summary = "；".join(
                    f"{label}：{_display_label(status)}"
                    for label, status in checks
                ) or "未提供核验摘要"
                forward = member.get("forward_primer") or {}
                reverse = member.get("reverse_primer") or {}
                probe = member.get("probe") or {}
                compact_story.append(
                    Paragraph(
                        _pdf_text(
                            f"成员 {member.get('target_id')}；候选编号 {member.get('candidate_id')}；"
                            f"通道 {member.get('fluorophore') or '未提供'} / {member.get('quencher') or '未提供'}。\n"
                            f"正向引物：{forward.get('sequence_5to3') or '未提供'}；"
                            f"反向引物：{reverse.get('sequence_5to3') or '未提供'}；"
                            f"探针：{probe.get('sequence_5to3') or '未提供'}。\n"
                            f"核验摘要：{check_summary}；"
                            f"保守性：{_format_conservation(member.get('conservation_score'))}；"
                            f"证据来源：{_display_label(member.get('evidence_level'))}。",
                            max_chars=1800,
                        ),
                        body_style,
                    )
                )
            compact_story.append(Spacer(1, 8))
        try:
            compact_document.build(
                compact_story,
                onFirstPage=draw_page_footer,
                onLaterPages=draw_page_footer,
            )
        except LayoutError as compact_error:
            raise PersistenceError(
                "WF-02 PDF 紧凑分页仍然失败",
                details={
                    "primary_layout_error": str(primary_error),
                    "compact_layout_error": str(compact_error),
                },
            ) from compact_error
        return compact_buffer.getvalue()
