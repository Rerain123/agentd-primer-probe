from __future__ import annotations

import csv
import copy
import io
import sys
import unittest
from pathlib import Path

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.wf02_v01_report import (
    render_wf02_csv,
    render_wf02_html,
    render_wf02_pdf,
)


def _report() -> dict:
    specificity = {
        "overall_status": "PASS",
        "target_hit": {
            "status": "PASS",
            "hits": [
                {
                    "raw_tabular_fields": {"private_field": "do-not-render"},
                    "raw_output": "X" * 200_000,
                }
            ],
        },
        "related_background_nt": {"status": "PASS", "hits": []},
        "host_exclusion": {
            "status": "NOT_EVALUATED",
            "database": {"path": "/data/private/database"},
        },
    }
    panel = {
        "rank": 1,
        "panel_id": "wf02-panel-fixture",
        "tier": "RISK",
        "classification": "RISK",
        "score": {
            "final_score": 1.784652,
            "target_quality_sum": 1.983456,
            "compatibility_coefficient": 0.9,
            "tm_balance_coefficient": 1.0,
        },
        "recommended_annealing_temp_celsius": 58.123456,
        "warnings": ["INSTRUMENT_PROFILE_UNVERIFIED"],
        "balance": {
            "primer_tm_spread_celsius": 1.2345,
            "probe_tm_spread_celsius": 1.6789,
            "gc_spread_percent": 4.567,
        },
        "p4_result": {
            "status": "COMPLETED",
            "verdict": "MARGINAL",
            "relaxed": False,
            "thresholds": {"cross_dimer_dg_kcal": -6.0},
            "heterodimer": {
                "total_pairs": 3,
                "evaluated_pairs": 3,
                "worst_pair": ["target-A:fwd", "target-B:rev"],
                "worst_dg_kcal": -5.6789,
                "raw_pairs": ["must", "not", "render"],
            },
            "issues": ["P4_MARGINAL"],
        },
        "members": [
            {
                "target_id": "target-<script>alert(1)</script>",
                "role": "target",
                "candidate_id": "candidate-001",
                "fluorophore": "FAM",
                "quencher": "BHQ-1",
                "forward_primer": {
                    "sequence_5to3": "ACGTACGTACGTACGTACGT",
                    "tm_celsius": 58.123456,
                    "hairpin_dg_kcal": -1.234,
                    "homodimer_dg_kcal": -2.345,
                    "three_prime_dimer_dg_kcal": -0.456,
                },
                "reverse_primer": {
                    "sequence_5to3": "TGCATGCATGCATGCATGCA",
                    "tm_celsius": 58.456789,
                },
                "probe": {
                    "sequence_5to3": "AACCGGTTAACCGGTTAACCGGTT",
                    "tm_celsius": 68.123456,
                },
                "amplicon": {"length_bp": 123},
                "specificity": specificity,
                "conservation_score": 0.9712,
                "evidence_level": "blast_verified",
                "warnings": [],
            }
        ],
    }
    return {
        "source": {"batch_code": "WF02-TEST", "task_version": 2},
        "generated_at": "2026-08-22T06:00:00+00:00",
        "result_summary": {"status": "PASS_WITH_WARNING", "top3_count": 1},
        "internal_control_summary": {"statement": "本次辅助项已完成处理。"},
        "experimental_validation_statement": "请结合实际运行条件复核本报告。",
        "selection_mode": "GLOBAL_SCORE_TOP3",
        "multiplex": {
            "coverage": {
                "status": "COMPLETE",
                "covered_targets": 1,
                "requested_targets": 1,
            },
            "coverage_gaps": [],
        },
        "outcome": {"reason_codes": []},
        "candidate_comparison": {
            "rows": [
                {
                    "rank": 1,
                    "panel_id": panel["panel_id"],
                    "tier": panel["tier"],
                    "final_score": panel["score"]["final_score"],
                    "member_count": 1,
                    "warning_count": 1,
                }
            ],
            "markdown": "| Rank | machine-only table |",
        },
        "top3_panels": [panel],
        "partial_panels": [],
        "failure_guidance": [],
    }


def _multi_panel_report() -> dict:
    report = _report()
    base_panel = report["top3_panels"][0]
    second_member = copy.deepcopy(base_panel["members"][0])
    second_member.update(
        {
            "target_id": "target-B",
            "candidate_id": "candidate-002",
            "fluorophore": "VIC",
        }
    )
    base_panel["members"].append(second_member)
    panels = []
    rows = []
    for rank in range(1, 4):
        panel = copy.deepcopy(base_panel)
        panel["rank"] = rank
        panel["panel_id"] = f"wf02-panel-fixture-{rank}"
        panel["score"]["final_score"] = 1.8 - rank / 100
        panels.append(panel)
        rows.append(
            {
                "rank": rank,
                "panel_id": panel["panel_id"],
                "tier": panel["tier"],
                "final_score": panel["score"]["final_score"],
                "member_count": 2,
                "warning_count": 1,
            }
        )
    report["top3_panels"] = panels
    report["candidate_comparison"]["rows"] = rows
    report["result_summary"]["top3_count"] = 3
    return report


class WF02HTMLReportTests(unittest.TestCase):
    def test_html_uses_minimal_reading_layout_without_raw_structures(self) -> None:
        rendered = render_wf02_html(_report()).decode("utf-8")

        self.assertIn('class="key-metrics"', rendered)
        self.assertIn('class="validation-list"', rendered)
        self.assertIn('class="comparison-table"', rendered)
        self.assertIn('class="member-section"', rendered)
        self.assertIn('class="sequence-table"', rendered)
        self.assertIn("检查步骤", rendered)
        self.assertIn("数据库核验", rendered)
        self.assertIn("已完成（含注意项）", rendered)
        self.assertIn("备选方案（含注意项）", rendered)
        self.assertIn("设备配置尚未完成验证", rendered)
        self.assertIn("已完成数据库核验", rendered)
        self.assertIn("97.1%", rendered)
        self.assertIn("58.12 °C", rendered)
        self.assertIn("交叉二聚体（cross-dimer）检查", rendered)
        self.assertIn("已评估", rendered)
        self.assertIn("-5.679 kcal/mol", rendered)
        self.assertIn("target-A:fwd ↔ target-B:rev", rendered)
        self.assertIn("3 / 3", rendered)
        self.assertIn("-6 kcal/mol", rendered)
        self.assertIn("标准模式", rendered)
        self.assertIn("ACGTA<wbr>CGTAC<wbr>GTACG<wbr>TACGT", rendered)
        self.assertIn("target-&lt;script&gt;alert(1)&lt;/script&gt;", rendered)

        self.assertNotIn("raw_tabular_fields", rendered)
        self.assertNotIn("raw_output", rendered)
        self.assertNotIn("raw_pairs", rendered)
        self.assertNotIn("do-not-render", rendered)
        self.assertNotIn("/data/private/database", rendered)
        self.assertNotIn("| Rank |", rendered)
        self.assertNotIn("<pre", rendered)
        self.assertNotIn('class="summary-card"', rendered)
        self.assertNotIn('class="panel-card"', rendered)
        self.assertNotIn('class="oligo-card"', rendered)
        self.assertNotIn("linear-gradient", rendered)
        self.assertNotIn("box-shadow", rendered)
        self.assertIn("body{margin:0;background:#fff", rendered)
        self.assertLess(len(rendered), 50_000)

    def test_failure_reason_is_readable_but_remains_machine_traceable(self) -> None:
        report = _report()
        report["top3_panels"] = []
        report["candidate_comparison"]["rows"] = []
        report["result_summary"] = {"status": "FAIL", "top3_count": 0}
        report["selection_mode"] = "INCOMPLETE_TARGET_COVERAGE"
        report["outcome"] = {
            "reason_codes": ["PRIMER3_GENERATED_NO_CANDIDATES"]
        }

        rendered = render_wf02_html(report).decode("utf-8")

        self.assertIn("候选生成步骤未返回结果", rendered)
        self.assertIn(
            'data-reason-code="PRIMER3_GENERATED_NO_CANDIDATES"', rendered
        )
        self.assertNotIn(">PRIMER3_GENERATED_NO_CANDIDATES<", rendered)

    def test_cross_dimer_missing_result_is_explicitly_not_evaluated(self) -> None:
        report = _report()
        p4_result = report["top3_panels"][0]["p4_result"]
        p4_result["status"] = "NOT_EVALUATED"
        p4_result["verdict"] = None
        p4_result["heterodimer"] = {
            "total_pairs": 3,
            "evaluated_pairs": 0,
            "worst_pair": [],
            "worst_dg_kcal": None,
        }

        rendered = render_wf02_html(report).decode("utf-8")

        self.assertIn("交叉二聚体（cross-dimer）检查", rendered)
        self.assertIn("未评估", rendered)
        self.assertIn("0 / 3", rendered)
        self.assertIn("最不利组合</dt><dd>未提供", rendered)

    def test_csv_contains_panel_cross_dimer_summary(self) -> None:
        rendered = render_wf02_csv(_report()).decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(rendered)))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cross_dimer_evaluation_status"], "COMPLETED")
        self.assertEqual(rows[0]["cross_dimer_worst_dg_kcal"], "-5.6789")
        self.assertEqual(
            rows[0]["cross_dimer_worst_pair"],
            "target-A:fwd ↔ target-B:rev",
        )
        self.assertEqual(rows[0]["cross_dimer_evaluated_pairs"], "3")
        self.assertEqual(rows[0]["cross_dimer_total_pairs"], "3")
        self.assertEqual(rows[0]["cross_dimer_threshold_dg_kcal"], "-6.0")
        self.assertEqual(rows[0]["cross_dimer_mode"], "STANDARD")

    def test_pdf_uses_readable_summary_without_nested_raw_records(self) -> None:
        content = render_wf02_pdf(_multi_panel_report())
        reader = PdfReader(io.BytesIO(content))
        extracted = "\n".join(
            page.extract_text() or "" for page in reader.pages
        )
        page_streams = b"\n".join(
            page.get_contents().get_data() for page in reader.pages
        )

        self.assertTrue(content.startswith(b"%PDF"))
        self.assertGreaterEqual(len(reader.pages), 1)
        self.assertIn("wf02-panel-fixture-1", extracted)
        self.assertIn("-5.679 kcal/mol", extracted)
        self.assertIn("target-A:fwd", extracted)
        self.assertIn("target-B:rev", extracted)
        self.assertNotIn("raw_tabular_fields", extracted)
        self.assertNotIn("raw_output", extracted)
        self.assertNotIn(b"raw_tabular_fields", page_streams)
        self.assertNotIn(b"raw_output", page_streams)
        self.assertLess(len(content), 100_000)


if __name__ == "__main__":
    unittest.main()
