"""C29 structured comparison and C30 deterministic interpretation layer."""

from __future__ import annotations

from .models import Candidate, EvidenceLevel, WorkflowResult


def _fmt(value, digits: int = 1) -> str:
    if value is None:
        return "未评估"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _candidate_table(candidates: list[Candidate]) -> str:
    lines = [
        "| 排名 | 候选 | 正向引物 | 反向引物 | 探针 | 类型 | 引物Tm (°C) | 探针Tm (°C) | 扩增子(bp) | P6 | 特异性 | 得分 | 判定 |",
        "|---:|---|---|---|---|---|---|---:|---:|---|---|---:|---|",
    ]
    for candidate in candidates:
        probe = candidate.probe
        probe_type = candidate.metadata.get("probe_type", "TaqMan_BHQ" if probe else "none")
        probe_tm = (
            candidate.mgb_assessment.get("corrected_tm_celsius")
            if candidate.mgb_assessment
            else (probe.tm_celsius if probe else None)
        )
        structure = candidate.amplicon_structure or {}
        specificity = candidate.specificity.rating if candidate.specificity else "未评估"
        lines.append(
            "| {rank} | {identifier} | `{forward}` | `{reverse}` | `{probe}` | {probe_type} | {ftm}/{rtm} | {ptm} | {length} | {structure} | {specificity} | {score} | {verdict} |".format(
                rank=_fmt(candidate.rank, 0),
                identifier=candidate.candidate_id,
                forward=candidate.forward_primer.sequence_5to3,
                reverse=candidate.reverse_primer.sequence_5to3,
                probe=probe.sequence_5to3 if probe else "—",
                probe_type=probe_type,
                ftm=_fmt(candidate.forward_primer.tm_celsius),
                rtm=_fmt(candidate.reverse_primer.tm_celsius),
                ptm=_fmt(probe_tm),
                length=candidate.length_bp,
                structure=structure.get("status", "未评估"),
                specificity=specificity,
                score=_fmt(candidate.score, 3),
                verdict=candidate.verdict,
            )
        )
    return "\n".join(lines)


def _variation_table(variation: dict) -> str:
    lines = [
        "| 风险 | 寡核苷酸 | 位置 | 参考→变异 | 频率 | ΔTm (°C) | 可能漏检 |",
        "|---|---|---:|---|---:|---:|---|",
    ]
    for site in variation.get("high_risk_sites", []):
        lines.append(
            f"| {site['risk_level']} | {site['oligo']} | {site['position']} | {site['ref']}→{site['alt']} | {site['frequency']:.1%} | {_fmt(site.get('tm_shift_celsius'))} | {'是' if site.get('possible_dropout') else '否'} |"
        )
    if len(lines) == 2:
        lines.append("| — | — | — | 未发现达到观测门槛的变异 | — | — | 否 |")
    return "\n".join(lines)


def candidate_comparison_report(result: WorkflowResult) -> str:
    title = f"# {result.assay_id} — {result.workflow} 结果对比"
    sections = [title]
    if result.candidates:
        sections.extend(["## 候选方案", _candidate_table(result.candidates)])
    else:
        sections.extend(["## 候选方案", "没有通过筛选的候选方案。"])
    if result.multiplex:
        multiplex = result.multiplex
        sections.extend(
            [
                "## 多重面板",
                f"面板判定：{multiplex.get('verdict', '未评估')}；已设计靶标 {multiplex.get('designed_targets', 0)}/{multiplex.get('requested_targets', 0)}；返回面板 {len(multiplex.get('panels', []))} 套。",
            ]
        )
        if multiplex.get("channel_assignment"):
            channels = "、".join(f"{target}→{dye}" for target, dye in multiplex["channel_assignment"].items())
            sections.append(f"通道分配：{channels}。")
    if result.internal_control:
        internal = result.internal_control
        sections.extend(
            [
                "## 内标",
                f"状态：{internal.get('status', '未评估')}；基因：{internal.get('gene_selected', '未选择')}；判定：{internal.get('verdict', '未评估')}。",
            ]
        )
    if result.variation and "sequences_analyzed" in result.variation:
        variation = result.variation
        sections.extend(
            [
                "## 变异耐受性",
                (
                    f"分析 {variation['sequences_analyzed']} 条序列；完全匹配覆盖率 "
                    f"{variation['coverage_100pct_match']:.1%}，允许1个非3′错配 "
                    f"{variation['coverage_1_mismatch']:.1%}，允许2个错配 "
                    f"{variation['coverage_2_mismatches']:.1%}。评级：{variation['verdict']}。"
                ),
                _variation_table(variation),
            ]
        )
    elif result.variation and "targets" in result.variation:
        sections.extend(
            [
                "## 变异耐受性",
                f"整体判定：{result.variation.get('verdict')}；已评估 {result.variation.get('evaluated_targets', 0)}/{result.variation.get('requested_targets', 0)} 个靶标。",
            ]
        )
    if result.diagnosis:
        attribution = result.diagnosis.get("attribution", {})
        sections.extend(
            [
                "## 问题诊断",
                f"问题类型：{result.diagnosis.get('problem_type')}；归因：{attribution.get('cause')}；置信度：{attribution.get('confidence')}。",
            ]
        )
    if result.degenerate_strategy:
        strategy = result.degenerate_strategy
        sections.extend(
            [
                "## 简并策略",
                f"可行性：{strategy.get('feasibility', '未评估')}；组合数：{strategy.get('combinations_tested', '未评估')}；最大Tm极差：{_fmt(strategy.get('tm_spread_celsius'))}°C。",
            ]
        )
    return "\n\n".join(sections)


def result_interpretation(result: WorkflowResult) -> dict:
    recommendations: list[str] = []
    if result.status.value == "FAILED":
        summary = f"{result.workflow} 未获得可交付方案。"
        recommendations.append("按失败记录优先更换靶区或修复红线规则后重试")
    elif result.workflow == "WF-05" and result.variation:
        variation = result.variation
        summary = (
            f"共分析{variation['sequences_analyzed']}条序列，完全匹配覆盖率为"
            f"{variation['coverage_100pct_match']:.1%}，综合评级为{variation['verdict']}。"
        )
        if variation["verdict"] == "REDESIGN_RECOMMENDED":
            recommendations.append("排除高频变异热点后重新运行WF-01")
        elif variation["high_risk_sites"]:
            recommendations.append("优先湿实验验证3′端和探针区变异株")
    elif result.workflow == "WF-02" and result.multiplex:
        summary = f"{result.workflow}多重面板判定为{result.multiplex.get('verdict')}，返回{len(result.multiplex.get('panels', []))}套面板。"
        if not result.multiplex.get("panels"):
            recommendations.append("减少靶标数量、替换候选或采用分管检测")
    elif result.workflow == "WF-03" and result.internal_control:
        summary = f"内标设计状态为{result.internal_control.get('status')}，当前判定{result.internal_control.get('verdict')}。"
        if result.internal_control.get("recommendation"):
            recommendations.append(result.internal_control["recommendation"])
    else:
        summary = f"{result.workflow} 生成{len(result.candidates)}套候选，流程状态为{result.status.value}。"
        if result.candidates:
            top = result.candidates[0]
            recommendations.append(f"首选{top.candidate_id}，当前得分{top.score:.3f}，判定{top.verdict}")
    if result.evidence_level == EvidenceLevel.IN_SILICO_ONLY:
        recommendations.append("当前为in_silico_only证据，需完成正式BLAST与湿实验验证")
    recommendations.extend(result.warnings[:3])
    return {
        "method": "deterministic_scientific_template",
        "llm_used": False,
        "summary": summary,
        "recommendations": list(dict.fromkeys(recommendations)),
        "disclaimer": result.disclaimer,
    }


def generate_reports(result: WorkflowResult) -> dict:
    return {
        "C29_candidate_comparison_markdown": candidate_comparison_report(result),
        "C30_interpretation": result_interpretation(result),
    }
