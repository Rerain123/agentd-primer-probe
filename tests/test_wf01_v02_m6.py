from __future__ import annotations

import copy
import csv
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4
from unittest.mock import patch
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentd.service import AgentDService
from agentd.errors import PersistenceError
from agentd.interactions import build_candidate_shortfall_interaction
from agentd.wf01_v02 import WF01V02BatchService
from agentd.wf01_v02_m6 import (
    RULE_CODES,
    WF01V02CanonicalReportAssembler,
    render_html,
    render_process_detail_html,
    render_rules_validation_html,
    render_pdf,
    render_all_exports,
)


class RecordingArtifactPublisher:
    enabled = True

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.uploads: list[dict] = []

    def publish(self, path: Path, *, batch_id: str, media_type: str, sha256: str):
        if self.fail:
            raise PersistenceError("simulated RustFS failure")
        upload = {
            "provider": "rustfs",
            "endpoint_url": "https://rustfs.ne.scqcyz.cn",
            "bucket": "wens",
            "object_key": f"data/{path.name}",
            "etag": "fixture-etag",
            "version_id": None,
            "size_bytes": path.stat().st_size,
            "sha256": sha256,
            "file_url": (
                "https://rustfs.ne.scqcyz.cn/wens/data/"
                + quote(path.name, safe="")
            ),
            "published_at": "2026-08-08T00:00:00+00:00",
        }
        self.uploads.append(
            {
                "path": path,
                "batch_id": batch_id,
                "media_type": media_type,
                **upload,
            }
        )
        return upload

    def status(self):
        return {"available": True, "enabled": True, "configured": True}


def oligo(sequence: str, tm: float) -> dict:
    return {
        "sequence_5to3": sequence,
        "length_nt": len(sequence),
        "tm_celsius": tm,
        "gc_percent": 50.0,
        "hairpin_dg_kcal": -1.1,
        "homodimer_dg_kcal": -2.1,
        "three_prime_dimer_dg_kcal": -1.5,
        "thermo_method": "primer3_santalucia_1998",
    }


def rule(code: str, status: str = "PASS") -> dict:
    return {
        "code": code,
        "category": "hard" if int(code[1:]) <= 12 else "metadata",
        "status": status,
        "actual": {"fixture": True},
        "threshold": f"{code} fixture threshold",
        "source": "fixture",
        "message": "fixture evaluated result",
    }


def specificity(status: str = "PASS", *, with_hit: bool = True) -> dict:
    hit = {
        "query_id": "c0001_forward",
        "subject_id": "NC_FIXTURE.1",
        "identity_percent": 100.0,
        "coverage_percent": 100.0,
        "evalue": 1e-20,
        "query_start": 1,
        "query_end": 20,
        "subject_start": 100,
        "subject_end": 119,
        "three_prime_mismatches": 0,
        "title": "Synthetic test virus",
        "organism": "Synthetic test virus",
    }

    def mode(name: str, mode_status: str) -> dict:
        return {
            "mode": name,
            "status": mode_status,
            "objects": [
                {
                    "object_id": "target_001:target:1",
                    "original_input": {"name": "Synthetic test virus"},
                    "resolution_status": "NORMALIZED",
                }
            ],
            "database": {
                "resource_key": f"{name.lower()}:fixture:blast",
                "version": "fixture-2026.07",
                "path": "C:/fixture/database",
            },
            "thresholds": {"evalue_lt": 1e-5},
            "method": "blastn",
            "queries": {"c0001_forward": "GACCACTTTGTCAAGCTCAT"},
            "hits": [copy.deepcopy(hit)] if with_hit else [],
            "reason": None,
            "error": None,
        }

    return {
        "overall_status": status,
        "target_hit": mode("TARGET_HIT", status),
        "host_exclusion": {
            "mode": "HOST_EXCLUSION",
            "status": "PASS",
            "object_results": [mode("HOST_EXCLUSION", "PASS")],
        },
        "related_background_nt": {
            **mode("RELATED_BACKGROUND_NT", status),
            "objects": [],
            "hits": [],
            "reason": "NO_RELEVANT_NON_TARGET_HIT",
        },
    }


def candidate(
    index: int,
    *,
    verdict: str = "PASS",
    specificity_status: str = "PASS",
) -> dict:
    suffix = "A" * index
    amplicon = ("ACGT" * 35)[: 100 + index]
    return {
        "candidate_id": f"target_001-candidate-{index + 1}",
        "dedupe_key": f"dedupe-{index:03d}",
        "forward_primer": oligo(f"GACCACTTTGTCAAGCTCAT{suffix}", 60.0),
        "reverse_primer": oligo(f"TCTCTTCCTCTTGTGCTCTT{suffix}", 60.2),
        "probe": oligo(f"ACATGGCCTCCAAGGAGTAAGACCC{suffix}", 69.1),
        "amplicon": {
            "sequence_5to3": amplicon,
            "length_bp": len(amplicon),
            "start": 10,
            "end": 10 + len(amplicon) - 1,
        },
        "primer3_pair_penalty": float(index),
        "pair_heterodimer_dg_kcal": -4.8,
        "three_prime_heterodimer_dg_kcal": -3.9,
        "three_prime_involved": False,
        "design_method": "primer3",
        "evidence_level": (
            "blast_verified"
            if specificity_status != "NOT_EVALUATED"
            else "in_silico_only"
        ),
        "verdict": verdict,
        "score": 92.5 - index if verdict == "PASS" else None,
        "rank": index + 1 if verdict == "PASS" else None,
        "rules": [
            *[rule(f"R{number:02d}") for number in range(1, 13)],
            rule("R18"),
            rule(
                "R19",
                {
                    "PASS": "PASS",
                    "FAIL": "FAIL",
                    "NOT_EVALUATED": "NOT_EVALUATED",
                }[specificity_status],
            ),
            rule("R20"),
        ],
        "specificity": specificity(
            specificity_status,
            with_hit=specificity_status != "NOT_EVALUATED",
        ),
        "metadata": {
            "target_id": "target_001",
            "exploration_unit_id": "target_001-unit-001",
            "parameter_run_id": "default",
            "pre_specificity_rank": index + 1,
            "primer3_version": "2.6.1",
            "thermo_method": "primer3_santalucia_1998",
            "salt_monovalent_mm": 50.0,
            "salt_divalent_mm": 1.5,
            "dntp_mm": 0.6,
            "dna_conc_nm": 50.0,
            "oligo_conc_nm": 250.0,
            "wf01_v02_scoring": {
                "status": "FINAL" if verdict == "PASS" else "NOT_EVALUATED",
                "weights": {
                    "thermodynamics": 0.357,
                    "specificity": 0.357,
                    "amplicon_features": 0.286,
                },
                "dimensions": {
                    "thermodynamics": {
                        "weight": 0.357,
                        "raw_score": 95.0,
                    },
                    "specificity": {
                        "weight": 0.357,
                        "raw_score": 100.0,
                    },
                    "amplicon_features": {
                        "weight": 0.286,
                        "raw_score": 90.0,
                    },
                },
                "base_score": 95.0,
                "penalties": [],
                "total_penalty_percent": 0.0,
                "final_score": 92.5 - index if verdict == "PASS" else None,
            },
        },
    }


def target(
    target_id: str = "target_001",
    *,
    include_candidates: bool = True,
) -> dict:
    if not include_candidates:
        return {
            "target_id": target_id,
            "ordinal": 2,
            "status": "FAILED",
            "template": {
                "record_name": "empty_target",
                "sequence": "ACGT" * 30,
                "length_nt": 120,
                "validation_status": "VALID",
            },
            "target_object": {"original_input": {"name": "Synthetic virus"}},
            "host_objects": [{"original_input": {"name": "pig"}}],
            "related_objects": [],
            "background_objects": [],
            "parameters": {"source": "batch_default"},
            "issues": [],
            "m2_execution": {
                "parameter_runs": [],
                "exploration_manifest": {"units": []},
                "exploration_units": [],
                "hard_rule_pass_candidates": [],
                "hard_rule_rejected_candidates": [],
                "hard_rule_incomplete_candidates": [],
            },
            "m3_execution": {
                "all_hard_rule_pass_candidates": [],
                "all_hard_rule_rejected_candidates": [],
                "all_hard_rule_incomplete_candidates": [],
            },
            "m4_execution": {
                "all_qualified_candidates": [],
                "all_specificity_failed_candidates": [],
                "all_specificity_incomplete_candidates": [],
                "hard_rule_rejected_candidates": [],
                "hard_rule_incomplete_candidates": [],
            },
            "m5_execution": {
                "status": "FAILED",
                "status_reason": "NO_DELIVERABLE_CANDIDATES",
                "top10": [],
                "all_deliverable_candidates": [],
                "completed_scope": [],
                "incomplete_scope": [],
                "incomplete_reasons": [],
                "recommendations": [],
            },
            "candidate_counts": {
                "deliverable": 0,
                "qualified": 0,
                "incomplete_evidence": 0,
                "top10": 0,
                "hard_rule_rejected": 0,
                "hard_rule_incomplete": 0,
                "specificity_failed": 0,
            },
            "search_complete": True,
            "technical_search_complete": True,
            "result_summary": {"status": "FAILED"},
            "incomplete_reasons": [],
            "recommendations": [],
        }

    qualified = candidate(0)
    specificity_failed = candidate(
        1, verdict="REJECTED_SPECIFICITY", specificity_status="FAIL"
    )
    hard_rejected = candidate(
        2, verdict="REJECTED_HARD_RULE", specificity_status="NOT_EVALUATED"
    )
    hard_rejected.pop("specificity")
    m5_qualified = copy.deepcopy(qualified)
    m5_qualified["display_rank"] = 1
    m5_qualified.setdefault("metadata", {})["m5_delivery"] = {
        "status": "QUALIFIED",
        "selection_basis": "M4_FINAL_SCORE",
        "display_rank": 1,
        "formal_rank": 1,
        "score": qualified["score"],
    }
    return {
        "target_id": target_id,
        "ordinal": 1,
        "status": "PARTIAL",
        "template": {
            "record_name": "fixture_target",
            "original_record_name": "fixture_target",
            "sequence": "ACGT" * 40,
            "length_nt": 160,
            "source_file": "fixture.fasta",
            "validation_status": "VALID",
            "checksum_sha256": "a" * 64,
        },
        "target_object": {
            "object_id": f"{target_id}:target:1",
            "original_input": {"name": "Synthetic test virus"},
            "normalized_name": "Synthetic test virus",
            "resolution_status": "NORMALIZED",
        },
        "host_objects": [
            {
                "object_id": f"{target_id}:host:1",
                "original_input": {"name": "pig"},
                "scientific_name": "Sus scrofa",
                "resolution_status": "NORMALIZED",
            }
        ],
        "related_objects": [],
        "background_objects": [],
        "parameters": {"source": "batch_default"},
        "issues": [],
        "m2_execution": {
            "parameter_runs": [{"run_id": "default", "profile": "default"}],
            "exploration_manifest": {
                "manifest_id": f"{target_id}-manifest",
                "units": [
                    {
                        "exploration_unit_id": f"{target_id}-unit-001",
                        "status": "COMPLETED",
                    }
                ],
            },
            "exploration_units": [
                {
                    "exploration_unit_id": f"{target_id}-unit-001",
                    "status": "COMPLETED",
                }
            ],
            "hard_rule_pass_candidates": [
                copy.deepcopy(qualified),
                copy.deepcopy(specificity_failed),
            ],
            "hard_rule_rejected_candidates": [copy.deepcopy(hard_rejected)],
            "hard_rule_incomplete_candidates": [],
        },
        "m3_execution": {
            "manifest_validation": {"status": "COMPLETE"},
            "deduplication": {"duplicates_collapsed": 0},
            "all_hard_rule_pass_candidates": [
                copy.deepcopy(qualified),
                copy.deepcopy(specificity_failed),
            ],
            "all_hard_rule_rejected_candidates": [copy.deepcopy(hard_rejected)],
            "all_hard_rule_incomplete_candidates": [],
        },
        "m4_execution": {
            "provider_status": {"blast": {"available": True}},
            "resource_resolution": {
                "target": {
                    "resource_key": "target:fixture:blast",
                    "version": "fixture-2026.07",
                }
            },
            "policy_version": "fixture-policy-v1",
            "policy_status": "APPROVED_FIXTURE",
            "ranking_status": "FINAL",
            "search_incomplete_reason": None,
            "all_qualified_candidates": [copy.deepcopy(qualified)],
            "all_specificity_failed_candidates": [
                copy.deepcopy(specificity_failed)
            ],
            "all_specificity_incomplete_candidates": [],
            "hard_rule_rejected_candidates": [copy.deepcopy(hard_rejected)],
            "hard_rule_incomplete_candidates": [],
        },
        "m5_execution": {
            "status": "PARTIAL",
            "status_reason": "COMPLETE_SEARCH_WITH_ONE_TO_NINE_CANDIDATES",
            "top10": [copy.deepcopy(m5_qualified)],
            "all_deliverable_candidates": [copy.deepcopy(m5_qualified)],
            "completed_scope": [{"scope_type": "CANDIDATE_DELIVERY"}],
            "incomplete_scope": [],
            "incomplete_reasons": [],
            "recommendations": [],
            "result_summary": {"status": "PARTIAL"},
        },
        "candidate_counts": {
            "deliverable": 1,
            "qualified": 1,
            "incomplete_evidence": 0,
            "top10": 1,
            "hard_rule_rejected": 1,
            "hard_rule_incomplete": 0,
            "specificity_failed": 1,
        },
        "top10": [copy.deepcopy(m5_qualified)],
        "search_complete": True,
        "technical_search_complete": True,
        "result_summary": {"status": "PARTIAL"},
        "incomplete_reasons": [],
        "recommendations": [],
    }


def batch(*, two_targets: bool = False) -> dict:
    targets = [target()]
    if two_targets:
        targets.append(target("target_002", include_candidates=False))
    return {
        "batch_id": str(uuid4()),
        "task_version": 1,
        "source_batch_id": None,
        "source_version": None,
        "workflow": "WF-01",
        "prd_version": "0.2",
        "status": "PARTIALLY_SUCCEEDED",
        "created_at": "2026-07-31T00:00:00+00:00",
        "updated_at": "2026-07-31T00:01:00+00:00",
        "preflight": {
            "normalised_input": {"task_name": "M6 fixture batch"}
        },
        "targets": targets,
        "target_total": len(targets),
        "target_counts": {
            "total": len(targets),
            "partial": 1,
            "failed": int(two_targets),
            "deliverable": 1,
        },
        "candidate_counts": {
            "deliverable": 1,
            "qualified": 1,
            "incomplete_evidence": 0,
        },
        "events": [
            {
                "event_seq": 1,
                "event_type": "BATCH_CREATED",
                "created_at": "2026-07-31T00:00:00+00:00",
                "status": "QUEUED",
            }
        ],
        "m2_outcome": "COMPLETED",
        "m3_outcome": "COMPLETED",
        "m4_outcome": "COMPLETED",
        "m5_outcome": "COMPLETED",
        "m5_execution": {
            "status": "PARTIALLY_SUCCEEDED",
            "status_reason": "AT_LEAST_ONE_TARGET_NOT_FULLY_SUCCEEDED",
            "summarized_at": "2026-07-31T00:01:00+00:00",
            "failed_target_ids": ["target_002"] if two_targets else [],
            "incomplete_target_ids": [],
            "recommendations": [],
        },
        "search_complete": True,
        "technical_search_complete": True,
        "report_id": None,
    }


def canonical_from_xlsx(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        namespace = {
            "x": (
                "http://schemas.openxmlformats.org/"
                "spreadsheetml/2006/main"
            )
        }
        names = [
            item.attrib["name"]
            for item in workbook.findall("x:sheets/x:sheet", namespace)
        ]
        sheet_number = names.index("Canonical JSON") + 1
        sheet = ET.fromstring(
            archive.read(f"xl/worksheets/sheet{sheet_number}.xml")
        )
        texts = [
            item.text or ""
            for item in sheet.findall(".//x:t", namespace)
        ]
    return json.loads("".join(texts[2:]))


class WF01V02M6CanonicalReportTests(unittest.TestCase):
    def test_ac_rpt_01_rules_thermo_structure_and_disclaimer_are_complete(
        self,
    ) -> None:
        report = WF01V02CanonicalReportAssembler().assemble(batch())
        self.assertEqual(report["completeness"]["status"], "COMPLETE")
        self.assertEqual(report["completeness"]["issue_count"], 0)
        selected = report["targets"][0]["all_primer3_candidates"][0]
        self.assertEqual(
            {item["code"] for item in selected["rules"]},
            set(RULE_CODES),
        )
        rule_map = {item["code"]: item for item in selected["rules"]}
        self.assertEqual(rule_map["R21"]["status"], "PASS")
        r21_actual = rule_map["R21"]["actual"]
        self.assertTrue(all(r21_actual["required_fields"].values()))
        self.assertEqual(r21_actual["missing_fields"], [])
        self.assertEqual(
            r21_actual["conservation_scoring"]["status"],
            "DEFERRED",
        )
        self.assertFalse(
            r21_actual["conservation_scoring"][
                "included_in_r21_pass_condition"
            ]
        )
        self.assertIn(
            selected["thermodynamic_metadata"]["status"],
            {"EVALUATED", "NOT_EVALUATED"},
        )
        self.assertEqual(
            selected["amplicon_secondary_structure"]["status"],
            "NOT_EVALUATED",
        )
        self.assertFalse(report["disclaimer"]["experimental_validation"])
        self.assertIn("不得直接作为临床诊断", report["disclaimer"]["text"])

    def test_ac_rpt_03_preserves_all_candidate_groups_search_and_hits(
        self,
    ) -> None:
        report = WF01V02CanonicalReportAssembler().assemble(batch())
        target_report = report["targets"][0]
        self.assertEqual(len(target_report["top10"]), 1)
        self.assertEqual(len(target_report["all_qualified_candidates"]), 1)
        self.assertEqual(len(target_report["all_excluded_candidates"]), 2)
        self.assertEqual(len(target_report["all_primer3_candidates"]), 3)
        self.assertEqual(
            len(target_report["primer3_candidate_occurrences"]), 3
        )
        self.assertTrue(
            target_report["specificity_evidence"]["hit_evidence"]
        )
        self.assertTrue(
            target_report["search_records"]["exploration_manifest"]
        )

    def test_process_report_merges_stage_versions_before_numbering(self) -> None:
        payload = batch()
        target_payload = payload["targets"][0]
        dedupe_key = "stage-dedupe-key"
        for candidate_value in (
            target_payload["m3_execution"]["all_hard_rule_rejected_candidates"],
            target_payload["m4_execution"]["hard_rule_rejected_candidates"],
        ):
            candidate_value[0]["dedupe_key"] = dedupe_key
            candidate_value[0]["score"] = 0.0

        report = WF01V02CanonicalReportAssembler().assemble(payload)
        target_report = report["targets"][0]
        candidate_ids = [
            item["candidate_id"]
            for item in target_report["all_primer3_candidates"]
        ]

        self.assertEqual(len(candidate_ids), 3)
        self.assertEqual(len(set(candidate_ids)), 3)
        self.assertEqual(
            candidate_ids.count("target_001-candidate-3"),
            1,
        )
        self.assertEqual(len(target_report["primer3_candidate_occurrences"]), 3)

        content = render_process_detail_html(report).decode("utf-8")
        self.assertIn("全部生成候选</span><strong>3</strong>", content)
        self.assertIn("<td>候选 001（最终第 1 名）</td>", content)
        self.assertIn("<td>候选 002</td>", content)
        self.assertIn("<td>候选 003</td>", content)
        self.assertNotIn("<td>候选 004</td>", content)

    def test_r21_fails_when_a_required_delta_g_value_is_missing(self) -> None:
        payload = batch()

        def remove_pair_delta_g(value: object) -> None:
            if isinstance(value, dict):
                if value.get("candidate_id") == "target_001-candidate-1":
                    value.pop("pair_heterodimer_dg_kcal", None)
                for child in value.values():
                    remove_pair_delta_g(child)
            elif isinstance(value, list):
                for child in value:
                    remove_pair_delta_g(child)

        remove_pair_delta_g(payload)
        report = WF01V02CanonicalReportAssembler().assemble(payload)
        selected = report["targets"][0]["all_primer3_candidates"][0]
        r21 = next(
            item for item in selected["rules"] if item["code"] == "R21"
        )

        self.assertEqual(r21["status"], "FAIL")
        self.assertIn("pair_heterodimer_dg", r21["actual"]["missing_fields"])
        self.assertEqual(report["completeness"]["status"], "INCOMPLETE")

    def test_r21_accepts_explicit_not_evaluated_specificity_for_design_only(self) -> None:
        payload = batch()
        selected = payload["targets"][0]
        selected["execution_path"] = "DESIGN_ONLY"
        design_only = candidate(
            0,
            verdict="SPECIFICITY_NOT_EVALUATED",
            specificity_status="NOT_EVALUATED",
        )
        design_only["specificity"]["reason"] = "SKIPPED_MISSING_CONTEXT"
        design_only["specificity"]["target_hit"]["reason"] = "TARGET_OBJECT_MISSING"
        for item in design_only["specificity"]["host_exclusion"]["object_results"]:
            item["status"] = "NOT_EVALUATED"
            item["reason"] = "HOST_OBJECT_MISSING"
        design_only["specificity"]["related_background_nt"]["status"] = "NOT_EVALUATED"
        design_only["specificity"]["related_background_nt"]["reason"] = (
            "TARGET_OBJECT_MISSING"
        )
        selected["m2_execution"]["hard_rule_pass_candidates"] = [
            copy.deepcopy(design_only)
        ]
        selected["m2_execution"]["hard_rule_rejected_candidates"] = []
        selected["m3_execution"]["all_hard_rule_pass_candidates"] = [
            copy.deepcopy(design_only)
        ]
        selected["m3_execution"]["all_hard_rule_rejected_candidates"] = []
        selected["m4_execution"]["all_qualified_candidates"] = []
        selected["m4_execution"]["all_specificity_failed_candidates"] = []
        selected["m4_execution"]["all_specificity_incomplete_candidates"] = [
            copy.deepcopy(design_only)
        ]
        selected["m4_execution"]["hard_rule_rejected_candidates"] = []
        delivered = copy.deepcopy(design_only)
        delivered["display_rank"] = 1
        selected["m5_execution"]["top10"] = [copy.deepcopy(delivered)]
        selected["m5_execution"]["all_deliverable_candidates"] = [
            copy.deepcopy(delivered)
        ]
        selected["top10"] = [copy.deepcopy(delivered)]

        report = WF01V02CanonicalReportAssembler().assemble(payload)
        reported = report["targets"][0]["all_primer3_candidates"][0]
        r21 = next(item for item in reported["rules"] if item["code"] == "R21")

        self.assertEqual(r21["status"], "PASS")
        self.assertTrue(
            r21["actual"]["required_fields"][
                "blast_summary_or_explicit_exclusion"
            ]
        )
        self.assertEqual(report["completeness"]["status"], "COMPLETE")

    def test_ac_rpt_04_scope_states_are_explicit(self) -> None:
        report = WF01V02CanonicalReportAssembler().assemble(batch())
        scope = report["out_of_scope"]
        self.assertEqual(scope["mgb"]["status"], "NOT_APPLICABLE")
        self.assertEqual(scope["multiplex"]["status"], "NOT_APPLICABLE")
        self.assertEqual(
            scope["internal_control"]["status"], "NOT_APPLICABLE"
        )
        self.assertEqual(
            scope["conservation"]["status"], "NOT_EVALUATED"
        )
        self.assertEqual(
            scope["amplicon_secondary_structure"]["status"],
            "NOT_EVALUATED",
        )

    @unittest.skipUnless(
        importlib.util.find_spec("jsonschema") is not None,
        "jsonschema dev dependency not installed",
    )
    def test_canonical_report_matches_published_json_schema(self) -> None:
        import jsonschema

        report = WF01V02CanonicalReportAssembler().assemble(batch())
        schema = json.loads(
            (
                ROOT
                / "schemas"
                / "wf01-v02-canonical-report.schema.json"
            ).read_text(encoding="utf-8")
        )
        jsonschema.validate(report, schema)


@unittest.skipUnless(
    importlib.util.find_spec("reportlab") is not None,
    "reportlab api dependency not installed",
)
class WF01V02M6ExportTests(unittest.TestCase):
    def test_process_report_is_human_readable_and_contains_no_json_dump(self) -> None:
        report = WF01V02CanonicalReportAssembler().assemble(batch())
        content = render_process_detail_html(report).decode("utf-8")

        self.assertIn("实验过程与科学工具", content)
        self.assertIn("Primer3 候选生成过程", content)
        self.assertIn("逐项检测结果", content)
        self.assertIn("特异性检测汇总", content)
        self.assertNotIn("<pre>", content)
        self.assertNotIn("agentd-canonical-report", content)
        self.assertNotIn('"target_id"', content)
        self.assertNotRegex(content, r">R\d{2}<")
        self.assertNotIn("数据大小（字节）", content)
        self.assertNotIn("数据校验值", content)
        self.assertNotIn("序列校验值", content)

    def test_process_report_section_headings_use_fixed_left_aligned_grid(self) -> None:
        report = WF01V02CanonicalReportAssembler().assemble(batch())
        content = render_process_detail_html(report).decode("utf-8")

        self.assertIn(
            ".section-heading{display:grid;grid-template-columns:22px minmax(0,1fr) auto;",
            content,
        )
        self.assertIn("list-style:none;text-align:left", content)
        self.assertNotIn(
            ".section-heading{display:flex;justify-content:space-between;",
            content,
        )

    def test_process_report_groups_candidates_and_rules_with_readable_names(self) -> None:
        payload = batch()
        first_target = payload["targets"][0]
        first_target["template"]["record_name"] = (
            "NC_002657.1|CSFV|5UTR|1-373"
        )
        first_target["target_object"]["original_input"]["name"] = (
            "猪瘟病毒（CSFV"
        )
        report = WF01V02CanonicalReportAssembler().assemble(payload)
        content = render_process_detail_html(report).decode("utf-8")

        self.assertIn('<details class="report-section" id="candidates" open>', content)
        self.assertIn('<details class="report-section" id="rules">', content)
        self.assertIn("猪瘟病毒（CSFV） 5′UTR 引物探针设计", content)
        self.assertIn("CSFV · 5′UTR · NC_002657.1（1–373 nt）", content)
        self.assertIn("原始输入序列", content)
        self.assertIn("fixture.fasta", content)
        self.assertIn("ACGTACGTACGTACGT", content)
        self.assertIn("候选筛选结果", content)
        self.assertIn("全部生成候选", content)
        self.assertIn("进入特异性检测", content)
        self.assertIn("硬性检测未通过", content)
        self.assertIn("主要结果", content)
        self.assertIn("已通过全部必需检测", content)
        self.assertIn("候选 001（最终第 1 名）", content)
        self.assertIn("Primer3 配对罚分", content)
        self.assertIn("熔解温度（Tm）", content)
        self.assertIn("关键 ΔG", content)
        self.assertIn("正反向引物 3′端异源二聚体 ΔG", content)
        self.assertIn("BLAST 特异性检测", content)
        self.assertIn("适用范围命中摘要", content)
        self.assertIn("靶标覆盖检查", content)
        self.assertIn("宿主非特异性检查", content)
        self.assertIn("近缘种与背景序列检查", content)
        self.assertNotIn("数据库版本", content)
        self.assertNotIn("fixture-2026.07", content)
        self.assertIn("Synthetic test virus", content)
        self.assertNotIn("候选记录 1", content)
        self.assertNotIn("Primer3 原始罚分", content)

    def test_process_report_highlights_data_for_failed_checks(self) -> None:
        report = WF01V02CanonicalReportAssembler().assemble(batch())
        candidate_report = report["targets"][0]["all_primer3_candidates"][0]
        failed_codes = {
            "R01",
            "R02",
            "R03",
            "R04",
            "R05",
            "R06",
            "R07",
            "R08",
            "R09",
            "R10",
            "R11",
            "R12",
            "R19",
        }
        for rule_result in candidate_report["rules"]:
            if rule_result["code"] in failed_codes:
                rule_result["status"] = "FAIL"
        candidate_report["specificity"]["target_hit"]["status"] = "FAIL"

        content = render_process_detail_html(report).decode("utf-8")

        self.assertIn("红色数据", content)
        self.assertIn("表示对应的硬性检测项目未通过", content)
        self.assertIn(
            '<span class="sequence failed-data">GACCACTTTGTCAAGCTCAT</span>',
            content,
        )
        self.assertIn('<span class="failed-data">20 nt</span>', content)
        self.assertIn('<span class="failed-data">60 ℃</span>', content)
        self.assertIn('<span class="failed-data">10–109</span>', content)
        self.assertIn('<span class="failed-data">100 bp</span>', content)
        self.assertIn(
            '<span class="failed-data">-1.1 kcal/mol</span>', content
        )
        self.assertIn(
            '<span class="failed-data">-4.8 kcal/mol</span>', content
        )
        self.assertIn(
            '<span class="failed-data">-3.9 kcal/mol</span>', content
        )
        self.assertIn(
            '<td><span class="failed-data">1</span></td>', content
        )

        delivery_content = render_html(report).decode("utf-8")
        self.assertNotIn('class="failed-data"', delivery_content)

    def test_process_report_only_shows_relaxed_search_when_previous_round_fails(self) -> None:
        report = WF01V02CanonicalReportAssembler().assemble(batch())
        target_report = report["targets"][0]
        target_report["search_records"]["exploration_units"] = [
            {
                "exploration_unit_id": "target_001-unit-001",
                "parameter_profile_labels": ["default"],
                "region": {"start": 0, "end": 159, "length_nt": 160},
                "status": "COMPLETED",
                "counts": {
                    "generated": 50,
                    "hard_rule_pass": 8,
                    "hard_rule_rejected": 42,
                    "hard_rule_not_evaluated": 0,
                },
            },
            {
                "exploration_unit_id": "target_001-unit-002",
                "parameter_profile_labels": ["L1"],
                "region": {"start": 0, "end": 159, "length_nt": 160},
                "status": "COMPLETED",
                "counts": {
                    "generated": 50,
                    "hard_rule_pass": 10,
                    "hard_rule_rejected": 40,
                    "hard_rule_not_evaluated": 0,
                },
            },
            {
                "exploration_unit_id": "target_001-unit-003",
                "parameter_profile_labels": ["L2"],
                "region": {"start": 0, "end": 159, "length_nt": 160},
                "status": "COMPLETED",
                "counts": {
                    "generated": 50,
                    "hard_rule_pass": 16,
                    "hard_rule_rejected": 34,
                    "hard_rule_not_evaluated": 0,
                },
            },
        ]

        standard_passed = render_process_detail_html(report).decode("utf-8")
        self.assertIn("为何使用放宽规则", standard_passed)
        self.assertNotIn("实际参数", standard_passed)
        self.assertNotIn("放宽参数一级", standard_passed)
        self.assertNotIn("放宽参数二级", standard_passed)

        first_unit = target_report["search_records"]["exploration_units"][0]
        first_unit["counts"]["hard_rule_pass"] = 0
        first_candidate = target_report["all_primer3_candidates"][0]
        first_candidate["metadata"]["exploration_unit_id"] = first_unit[
            "exploration_unit_id"
        ]
        first_candidate["rules"][0]["status"] = "FAIL"
        relaxed = render_process_detail_html(report).decode("utf-8")

        self.assertIn("放宽参数一级", relaxed)
        self.assertNotIn("放宽参数二级", relaxed)
        self.assertIn("上一轮未满足：引物熔解温度及两条引物温差", relaxed)

    def test_rules_report_marks_all_21_rules_and_explains_every_problem(self) -> None:
        report = WF01V02CanonicalReportAssembler().assemble(batch())
        content = render_rules_validation_html(report).decode("utf-8")

        self.assertIn("21条规则验证报告", content)
        self.assertIn("✓ 无问题", content)
        self.assertIn("✗ 有问题", content)
        self.assertIn("R-01", content)
        self.assertIn("R-21", content)
        self.assertIn("热力学计算元数据完整性", content)
        self.assertIn("靶序列保守性", content)
        self.assertIn("未执行、未完成或证据不足", content)
        self.assertEqual(content.count(">R-16</td>"), 2)
        self.assertNotIn("agentd-canonical-report", content)

    def test_rules_report_marks_all_rules_failed_when_no_candidate_exists(self) -> None:
        report = WF01V02CanonicalReportAssembler().assemble(
            batch(two_targets=True)
        )
        content = render_rules_validation_html(report).decode("utf-8")

        self.assertIn("没有可交付候选，无法验证该条规则", content)
        self.assertIn("无法对具体方案执行 21 条规则验证", content)

    def test_rules_report_marks_soft_rule_penalty_as_problem(self) -> None:
        payload = batch()
        for collection in (
            payload["targets"][0]["m5_execution"]["top10"],
            payload["targets"][0]["m5_execution"][
                "all_deliverable_candidates"
            ],
        ):
            collection[0]["metadata"]["wf01_v02_scoring"]["penalties"] = [
                {
                    "rule_code": "R01_SOFT",
                    "message": "引物 Tm 低于 55℃",
                    "penalty_percent": 5,
                }
            ]
        report = WF01V02CanonicalReportAssembler().assemble(payload)
        content = render_rules_validation_html(report).decode("utf-8")

        self.assertIn("硬性门槛已通过，但触发R01_SOFT", content)
        self.assertIn("检测到需关注项", content)

    def test_report_exports_use_human_readable_china_time_filename(self) -> None:
        report = {
            "generated_at": "2026-08-07T11:50:15.818257+00:00",
            "batch_overview": {"task_name": "M6 fixture batch"},
        }

        self.assertEqual(
            WF01V02BatchService._export_filename(report, "html"),
            "2026-08-07_19-50-15_引物探针设计报告.html",
        )
        self.assertEqual(
            WF01V02BatchService._export_filename(report, "pdf"),
            "2026-08-07_19-50-15_引物探针设计报告.pdf",
        )
        self.assertEqual(
            WF01V02BatchService._export_filename(report, "csv"),
            "M6 fixture batch_引物探针候选结果.csv",
        )
        self.assertEqual(
            WF01V02BatchService._export_filename(report, "process_html"),
            "2026-08-07_19-50-15_科学过程详情报告.html",
        )
        self.assertEqual(
            WF01V02BatchService._export_filename(report, "rules_html"),
            "2026-08-07_19-50-15_21条规则验证报告.html",
        )
        self.assertEqual(
            WF01V02BatchService._export_filename(report, "scientific_data"),
            "2026-08-07_19-50-15_科学工具原始数据.zip",
        )

    def test_flat_attachment_url_serves_newest_duplicate_filename(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="agentd-m6-flat-attachment-"
        ) as state_dir:
            service = AgentDService(state_dir=state_dir)
            filename = "wf01_batch_引物探针设计报告.html"
            older_id = str(uuid4())
            newer_id = str(uuid4())
            older_path = service.wf01_v02.store.save_export(
                older_id,
                "html",
                b"older report",
                filename=filename,
            )
            newer_path = service.wf01_v02.store.save_export(
                newer_id,
                "html",
                b"newer report",
                filename=filename,
            )
            os.utime(older_path, ns=(1_000_000_000, 1_000_000_000))
            os.utime(newer_path, ns=(2_000_000_000, 2_000_000_000))
            for batch_id in (older_id, newer_id):
                service.wf01_v02.store.save(
                    {
                        "batch_id": batch_id,
                        "m6_execution": {
                            "exports": {
                                "html": {"filename": filename}
                            }
                        },
                    }
                )

            resolved = service.get_wf01_v02_export_path_by_filename(
                filename
            )

            self.assertEqual(resolved, newer_path)
            self.assertEqual(resolved.read_bytes(), b"newer report")

    def test_m6_packages_prior_scientific_artifacts_for_download(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="agentd-m6-scientific-archive-"
        ) as state_dir:
            service = AgentDService(state_dir=state_dir)
            value = batch()
            service.wf01_v02.store.save_scientific_artifact(
                value["batch_id"],
                artifact_id="m4-target-001-test",
                stage="M4",
                tools=["BLASTN"],
                raw_data={"stdout": "raw blast evidence"},
                target_id="target_001",
            )
            service.wf01_v02.store.save(value)

            completed = service.execute_wf01_v02_m6(value["batch_id"])
            self.assertEqual(
                set(completed["m6_execution"]["exports"]),
                {"html", "process_html", "rules_html", "scientific_data"},
            )
            self.assertEqual(
                [
                    item["fileType"]
                    for item in completed["ui_message"]["data"][
                        "attachments"
                    ]
                ],
                ["HTML", "HTML", "HTML", "ZIP"],
            )
            archive_path = service.get_wf01_v02_export_path(
                value["batch_id"], "scientific_data"
            )
            refreshed = service.get_wf01_v02_batch(value["batch_id"])
            metadata = refreshed["m6_execution"]["exports"][
                "scientific_data"
            ]
            self.assertEqual(metadata["media_type"], "application/zip")
            self.assertEqual(
                metadata["file_url"],
                f'/data/{metadata["filename"]}',
            )
            with zipfile.ZipFile(archive_path) as archive:
                names = archive.namelist()
                self.assertIn("使用说明.txt", names)
                self.assertIn("科学过程详情报告.html", names)
                self.assertIn("21条规则验证报告.html", names)
                self.assertIn("01_科学工具与数据清单.csv", names)
                self.assertIn("04_全部候选原始结果.csv", names)
                self.assertFalse(any(name.lower().endswith(".json") for name in names))
                content = b"".join(archive.read(name) for name in names)
                self.assertNotIn(b"raw blast evidence", content)

    def test_m6_publishes_html_to_rustfs_before_returning_attachment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-m6-rustfs-") as state_dir:
            publisher = RecordingArtifactPublisher()
            service = AgentDService(
                state_dir=state_dir,
                artifact_publisher=publisher,
            )
            value = batch()
            service.wf01_v02.store.save(value)

            completed = service.execute_wf01_v02_m6(value["batch_id"])

            self.assertEqual(len(publisher.uploads), 4)
            upload = next(
                item
                for item in publisher.uploads
                if item["path"].name.endswith("_引物探针设计报告.html")
            )
            self.assertEqual(upload["batch_id"], value["batch_id"])
            self.assertEqual(upload["bucket"], "wens")
            metadata = completed["m6_execution"]["exports"]["html"]
            self.assertEqual(
                metadata["object_storage"]["object_key"],
                f'data/{metadata["filename"]}',
            )
            self.assertEqual(
                completed["ui_message"]["data"]["attachments"][0]["fileUrl"],
                metadata["file_url"],
            )
            self.assertTrue(
                metadata["file_url"].startswith(
                    "https://rustfs.ne.scqcyz.cn/wens/data/"
                )
            )

    def test_m6_does_not_complete_attachment_when_rustfs_upload_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-m6-rustfs-fail-") as state_dir:
            service = AgentDService(
                state_dir=state_dir,
                artifact_publisher=RecordingArtifactPublisher(fail=True),
            )
            value = batch()
            service.wf01_v02.store.save(value)

            with self.assertRaises(PersistenceError):
                service.execute_wf01_v02_m6(value["batch_id"])

            persisted = service.wf01_v02.store.get(value["batch_id"])
            self.assertNotEqual(persisted.get("m6_outcome"), "COMPLETED")
            self.assertIsNone(persisted.get("m6_execution"))

    def test_m6_does_not_render_optional_pdf_by_default(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-m6-partial-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            value = batch()
            service.wf01_v02.store.save(value)

            with patch(
                "agentd.wf01_v02_m6.render_pdf",
                side_effect=RuntimeError("simulated PDF table LayoutError"),
            ):
                completed = service.execute_wf01_v02_m6(value["batch_id"])

            self.assertEqual(completed["m6_outcome"], "COMPLETED")
            m6 = completed["m6_execution"]
            self.assertEqual(m6["export_status"], "COMPLETE")
            self.assertEqual(
                set(m6["exports"]),
                {"html", "process_html", "rules_html", "scientific_data"},
            )
            self.assertEqual(m6["export_failures"], {})
            self.assertEqual(
                completed["ui_message"]["data"]["primaryAttachmentId"],
                "attachment_html",
            )
            self.assertEqual(
                [
                    item["fileType"]
                    for item in completed["ui_message"]["data"]["attachments"]
                ],
                ["HTML", "HTML", "HTML", "ZIP"],
            )
            self.assertTrue(service.get_wf01_v02_report(value["batch_id"]))

    def test_pdf_caps_oversized_visible_cells_and_keeps_canonical_attachment(
        self,
    ) -> None:
        report = WF01V02CanonicalReportAssembler().assemble(batch())
        report["targets"][0]["search_records"]["oversized_fixture"] = (
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 3000
        )
        pdf_content = render_pdf(report)
        self.assertTrue(pdf_content.startswith(b"%PDF-"))

    def test_m6_waits_for_delivery_decision_then_accepts_current_result(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-m6-route-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            value = batch()
            interaction = build_candidate_shortfall_interaction(
                value["batch_id"],
                target_id="target_001",
                candidate_count=1,
            )
            value["agent_interactions"] = [interaction]
            service.wf01_v02.store.save(value)

            with self.assertRaisesRegex(Exception, "仍有待处理交互"):
                service.execute_wf01_v02_m6(value["batch_id"])

            routed = service.submit_wf01_v02_interaction_response(
                value["batch_id"],
                {
                    "type": "agent_interaction_response",
                    "task": "PRIMER_DESIGN",
                    "interactionId": interaction["data"]["interactionId"],
                    "answer": "EXPORT_CURRENT_REPORT",
                },
            )
            self.assertFalse(routed["new_version_created"])
            self.assertEqual(routed["batch_id"], value["batch_id"])
            self.assertEqual(routed["next_action"], "RUN_M6")

            completed = service.execute_wf01_v02_m6(value["batch_id"])
            self.assertEqual(completed["m6_outcome"], "COMPLETED")
            self.assertEqual(
                completed["ui_message"]["data"]["primaryAttachmentId"],
                "attachment_html",
            )

    def test_m6_export_runtime_dependencies_are_packaged(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        dockerfile = (
            ROOT / "deploy" / "docker" / "Dockerfile"
        ).read_text(encoding="utf-8")
        self.assertIn('"reportlab>=4.2,<5"', pyproject)
        self.assertIn('"pypdf>=5.0,<7"', pyproject)
        self.assertIn("fonts-noto-cjk", dockerfile)

    def test_ac_rpt_02_delivery_html_is_top10_and_other_exports_keep_canonical_data(self) -> None:
        report = WF01V02CanonicalReportAssembler().assemble(
            batch(two_targets=True)
        )
        exports = render_all_exports(report)
        self.assertEqual(
            set(exports),
            {
                "html",
                "process_html",
                "rules_html",
                "scientific_data",
                "csv",
                "pdf",
            },
        )
        html_text = exports["html"]["content"].decode("utf-8")
        expected_candidate_cards = sum(
            min(10, len(target["top10"]))
            for target in report["targets"]
        )
        self.assertEqual(
            html_text.count('<article class="candidate-card">'),
            expected_candidate_cards,
        )
        self.assertEqual(
            html_text.count('class="candidate-summary-row"'),
            expected_candidate_cards,
        )
        self.assertIn("优先候选方案列表", html_text)
        self.assertIn("候选方案明细", html_text)
        self.assertLess(
            html_text.index("优先候选方案列表"),
            html_text.index("候选方案明细"),
        )
        self.assertLess(
            html_text.index("候选方案明细"),
            html_text.index('<article class="candidate-card">'),
        )
        self.assertIn("综合评分", html_text)
        self.assertIn("热力学评分", html_text)
        self.assertIn("特异性评分", html_text)
        self.assertIn("扩增子特征评分", html_text)
        self.assertIn("BLAST 查询覆盖率", html_text)
        self.assertIn("三维基础分与软规则扣分", html_text)
        self.assertIn("加权基础分", html_text)
        self.assertIn("最终综合评分", html_text)
        self.assertIn("权重 35.7%", html_text)
        self.assertIn("权重 28.6%", html_text)
        self.assertIn("保守性", html_text)
        self.assertIn("保守性评分", html_text)
        self.assertIn("本版本暂缓", html_text)
        self.assertNotIn("退火温度", html_text)
        self.assertIn("原始模板序列", html_text)
        self.assertIn(
            'aria-label="Synthetic test virus原始模板序列"',
            html_text,
        )
        self.assertIn("ACGTACGTACGTACGT", html_text)
        self.assertLess(
            html_text.index("原始模板序列"),
            html_text.index("本报告仅展示"),
        )
        self.assertIn(
            '<span>输入文件</span><strong class="input-file-name">fixture.fasta</strong>',
            html_text,
        )
        self.assertIn(
            "<span>FASTA 序列记录</span><strong>fixture_target</strong>",
            html_text,
        )
        self.assertIn("<span>输入序列长度</span><strong>160 nt</strong>", html_text)
        self.assertNotIn("<span>参考序列</span>", html_text)
        self.assertNotIn("<span>参考序列长度</span>", html_text)
        self.assertIn("正向引物", html_text)
        self.assertIn("反向引物", html_text)
        self.assertIn("TaqMan 探针", html_text)
        self.assertIn("扩增子序列", html_text)
        first_candidate_card = html_text.split(
            '<article class="candidate-card">', 1
        )[1].split("</article>", 1)[0]
        self.assertLess(
            first_candidate_card.index('class="candidate-parameter-detail"'),
            first_candidate_card.index('<div class="metrics">'),
        )
        self.assertIn("关键 ΔG", html_text)
        self.assertIn("正反向引物异源二聚体 ΔG", html_text)
        self.assertIn("BLAST 特异性检测", html_text)
        self.assertIn("适用范围命中摘要", html_text)
        self.assertIn("靶标覆盖检查", html_text)
        self.assertIn("宿主非特异性检查", html_text)
        self.assertIn("近缘种与背景序列检查", html_text)
        self.assertNotIn("数据库版本", html_text)
        self.assertNotIn("fixture-2026.07", html_text)
        self.assertIn("Synthetic test virus", html_text)
        self.assertIn("检测结果", html_text)
        self.assertIn("13 项已执行核心检测全部通过", html_text)
        self.assertIn("已通过的检测", html_text)
        self.assertIn("引物熔解温度及两条引物温差", html_text)
        self.assertIn("靶标命中、背景序列及可选宿主排除特异性", html_text)
        self.assertNotIn("agentd-canonical-report", html_text)
        self.assertNotIn("全部合格候选", html_text)
        self.assertNotIn("全部排除候选", html_text)
        self.assertNotIn("R01-R21", html_text)
        self.assertNotIn("PASS_WITH_WARNINGS", html_text)
        self.assertNotIn("blast_verified", html_text)
        self.assertNotIn("all_primer3_candidates", html_text)
        self.assertIn(
            f'content="{report["content_sha256"]}"',
            html_text,
        )
        csv_rows = list(
            csv.DictReader(
                io.StringIO(exports["csv"]["content"].decode("utf-8-sig"))
            )
        )
        self.assertTrue(csv_rows)
        self.assertEqual(
            {row["数据摘要 SHA256"] for row in csv_rows},
            {report["content_sha256"]},
        )
        self.assertEqual(
            {item["靶标 ID"] for item in csv_rows},
            {"target_001", "target_002"},
        )
        self.assertTrue(exports["pdf"]["content"].startswith(b"%PDF-"))
        from pypdf import PdfReader

        pdf = PdfReader(io.BytesIO(exports["pdf"]["content"]))
        self.assertIn("canonical-report.json", pdf.attachments)
        attached = pdf.attachments["canonical-report.json"]
        if isinstance(attached, list):
            attached = attached[0]
        pdf_report = json.loads(attached)
        self.assertEqual(
            pdf_report["content_sha256"], report["content_sha256"]
        )
        self.assertEqual(len(pdf_report["targets"]), 2)
        for metadata in exports.values():
            self.assertEqual(
                metadata["canonical_content_sha256"],
                report["content_sha256"],
            )

    def test_design_only_delivery_report_labels_and_displays_design_score(self) -> None:
        report = WF01V02CanonicalReportAssembler().assemble(batch())
        selected = report["targets"][0]["top10"][0]
        selected["score"] = 88.25
        selected["rank"] = 1
        scoring = selected["scoring_detail"]
        scoring.update(
            {
                "status": "FINAL_DESIGN_ONLY",
                "score_type": "DESIGN_SCORE",
                "score_label": "设计评分",
                "score_scope": ["thermodynamics", "amplicon_features"],
                "effective_weights": {
                    "thermodynamics": 0.55521,
                    "specificity": 0.0,
                    "amplicon_features": 0.44479,
                },
                "base_score": 88.25,
                "final_score": 88.25,
            }
        )
        scoring["dimensions"]["thermodynamics"].update(
            {"effective_weight": 0.55521, "participates_in_score": True}
        )
        scoring["dimensions"]["specificity"].update(
            {
                "raw_score": None,
                "effective_weight": 0.0,
                "participates_in_score": False,
            }
        )
        scoring["dimensions"]["amplicon_features"].update(
            {"effective_weight": 0.44479, "participates_in_score": True}
        )

        html_text = render_html(report).decode("utf-8")

        self.assertIn("按设计评分排序", html_text)
        self.assertIn("<th>设计评分</th>", html_text)
        self.assertIn("引物对设计评分", html_text)
        self.assertIn("二维设计评分与软规则扣分", html_text)
        self.assertIn("最终设计评分", html_text)
        self.assertIn("特异性未评估，不参与设计评分", html_text)
        self.assertIn("特异性 · 本路线不参与", html_text)
        self.assertIn("热力学</span><strong>权重 55.5%", html_text)
        self.assertIn("扩增子特征</span><strong>权重 44.5%", html_text)
        self.assertIn("仅适用于本次仅序列设计路线", html_text)
        self.assertIn("88.2/100", html_text)

    def test_delivery_html_lists_top10_oligos_and_scores_before_details(self) -> None:
        report = WF01V02CanonicalReportAssembler().assemble(batch())
        target_report = report["targets"][0]
        base_candidate = target_report["top10"][0]
        candidates = []
        for ordinal in range(1, 13):
            value = copy.deepcopy(base_candidate)
            value["candidate_id"] = f"summary-candidate-{ordinal:02d}"
            value["forward_primer"]["sequence_5to3"] = (
                f"FORWARD-SEQUENCE-{ordinal:02d}"
            )
            value["score"] = 100.0 - ordinal
            candidates.append(value)
        target_report["top10"] = candidates

        html_text = render_html(report).decode("utf-8")

        self.assertEqual(html_text.count('class="candidate-summary-row"'), 10)
        self.assertEqual(html_text.count('<article class="candidate-card">'), 10)
        summary_table = html_text.split(
            '<div class="candidate-summary-table">', 1
        )[1].split("</table>", 1)[0]
        for header in (
            "候选方案",
            "正向引物（5′→3′）",
            "反向引物（5′→3′）",
            "TaqMan 探针（5′→3′）",
            "综合评分",
            "结论",
        ):
            self.assertIn(f"<th>{header}</th>", summary_table)
        self.assertNotIn("目标基因 ID", summary_table)
        self.assertNotIn("引物对综合评分", summary_table)

        first_candidate_card = html_text.split(
            '<article class="candidate-card">', 1
        )[1].split("</article>", 1)[0]
        for detail_field in (
            "目标基因 ID",
            "正向引物",
            "反向引物",
            "探针",
            "扩增子长度",
            "正向引物 Tm 值",
            "正向引物 GC 含量",
            "正向引物长度",
            "反向引物 Tm 值",
            "反向引物 GC 含量",
            "反向引物长度",
            "探针 Tm 值",
            "探针 GC 含量",
            "探针长度",
            "引物对综合评分",
        ):
            self.assertIn(f"<th>{detail_field}</th>", first_candidate_card)
        self.assertIn("fixture_target", first_candidate_card)
        self.assertIn('class="candidate-parameter-detail"', first_candidate_card)
        self.assertIn('<strong class="detail-score">99.0/100</strong>', first_candidate_card)
        self.assertIn("FORWARD-SEQUENCE-10", html_text)
        self.assertNotIn("FORWARD-SEQUENCE-11", html_text)
        self.assertIn("100 bp", first_candidate_card)
        self.assertIn("60.0°C", first_candidate_card)
        self.assertIn("50.0%", first_candidate_card)
        self.assertIn("20 nt", first_candidate_card)
        self.assertNotIn("需梯度实验确定", first_candidate_card)
        self.assertIn('<strong class="summary-score">99.0/100</strong>', html_text)
        self.assertLess(
            html_text.index('class="candidate-summary-table"'),
            html_text.index("候选方案明细"),
        )
        self.assertLess(
            html_text.index("候选方案明细"),
            html_text.index('<article class="candidate-card">'),
        )

    def test_delivery_html_explains_warning_without_exposing_rule_code(self) -> None:
        report = WF01V02CanonicalReportAssembler().assemble(batch())
        value = report["targets"][0]["top10"][0]
        value["verdict"] = "PASS_WITH_WARNINGS"
        value["warnings"] = ["R03_SOFT"]
        value["forward_primer"]["sequence_5to3"] = "CACAGTAGGACTAGCAAACG"
        scoring = value["scoring_detail"]
        scoring["penalties"] = [
            {
                "rule_code": "R03_SOFT",
                "penalty_percent": 2.0,
            }
        ]
        scoring["total_penalty_percent"] = 2.0
        scoring["applied_penalty_percent"] = 2.0

        html_text = render_html(report).decode("utf-8")

        self.assertIn(report["batch_code"], html_text)
        self.assertIn("任务编号", html_text)
        self.assertIn("全部通过 0 套 · 需关注 1 套", html_text)
        self.assertIn(
            "13 项已执行核心检测通过，1 项软规则需要关注",
            html_text,
        )
        self.assertIn("引物 3′ 端序列构成需要复核", html_text)
        self.assertIn("-2.0%", html_text)
        self.assertIn(
            "正向引物末 5 个碱基 AAACG，含连续 3 个 A",
            html_text,
        )
        self.assertNotIn("R03_SOFT", html_text)

    def test_delivery_html_distinguishes_uploaded_file_from_fasta_record(self) -> None:
        payload = batch()
        template = payload["targets"][0]["template"]
        template["source_file"] = r"C:\uploads\CSFV_NC_002657.1.fasta"
        template["record_name"] = "NC_002657.1"
        template["length_nt"] = 12301

        report = WF01V02CanonicalReportAssembler().assemble(payload)
        html_text = render_html(report).decode("utf-8")

        self.assertIn(
            '<span>输入文件</span><strong class="input-file-name">CSFV_NC_002657.1.fasta</strong>',
            html_text,
        )
        self.assertIn(
            "<span>FASTA 序列记录</span><strong>NC_002657.1</strong>",
            html_text,
        )
        self.assertIn(
            "<span>输入序列长度</span><strong>12301 nt</strong>",
            html_text,
        )
        self.assertNotIn(r"C:\uploads", html_text)

    def test_m6_service_persists_report_exports_and_filtered_view_is_read_only(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="agentd-m6-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            value = batch(two_targets=True)
            service.wf01_v02.store.save(value)
            completed = service.execute_wf01_v02_m6(value["batch_id"])
            self.assertEqual(completed["m6_outcome"], "COMPLETED")
            self.assertEqual(
                completed["m6_execution"]["completeness"]["status"],
                "COMPLETE",
            )
            self.assertEqual(
                set(completed["m6_execution"]["exports"]),
                {"html", "process_html", "rules_html", "scientific_data"},
            )
            ui_message = completed["ui_message"]
            self.assertEqual(list(ui_message), ["data", "type"])
            self.assertEqual(ui_message["type"], "primer_design_output")
            self.assertEqual(
                ui_message["data"]["primaryAttachmentId"],
                "attachment_html",
            )
            self.assertEqual(
                [item["fileType"] for item in ui_message["data"]["attachments"]],
                ["HTML", "HTML", "HTML", "ZIP"],
            )
            self.assertNotIn(
                "CSV",
                [item["fileType"] for item in ui_message["data"]["attachments"]],
            )
            self.assertIn("科学过程详情", ui_message["data"]["summaryText"])
            self.assertIn(
                completed["batch_code"],
                ui_message["data"]["summaryText"],
            )
            html_export = completed["m6_execution"]["exports"]["html"]
            self.assertRegex(
                html_export["filename"],
                r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_引物探针设计报告\.html$",
            )
            self.assertEqual(
                html_export["preview_endpoint"],
                f'/data/{html_export["filename"]}',
            )
            self.assertEqual(
                html_export["file_url"],
                f'/data/{html_export["filename"]}',
            )
            self.assertNotIn("pdf", completed["m6_execution"]["exports"])
            view = service.get_wf01_v02_report(
                value["batch_id"],
                target_id="target_001",
                candidate_scope="top10",
                page=1,
                page_size=1,
            )
            self.assertEqual(view["canonical_target_total"], 2)
            self.assertEqual(view["export_data_scope"], "COMPLETE_BATCH")
            html_path = service.get_wf01_v02_export_path(
                value["batch_id"], "html"
            )
            html_text = html_path.read_text(encoding="utf-8")
            self.assertIn("Synthetic test virus", html_text)
            self.assertIn("Synthetic virus", html_text)
            self.assertNotIn("agentd-canonical-report", html_text)
            csv_path = service.get_wf01_v02_export_path(
                value["batch_id"], "csv"
            )
            self.assertEqual(
                csv_path.name,
                "M6 fixture batch_引物探针候选结果.csv",
            )
            refreshed = service.get_wf01_v02_batch(value["batch_id"])
            self.assertEqual(
                set(refreshed["m6_execution"]["exports"]),
                {
                    "html",
                    "process_html",
                    "rules_html",
                    "scientific_data",
                    "csv",
                },
            )
            persisted = service.get_wf01_v02_report(value["batch_id"])
            self.assertEqual(len(persisted["targets"]), 2)
            with self.assertRaisesRegex(Exception, "已经执行过 M6"):
                service.execute_wf01_v02_m6(value["batch_id"])


@unittest.skipUnless(
    importlib.util.find_spec("fastapi") is not None
    and importlib.util.find_spec("reportlab") is not None,
    "FastAPI/reportlab dependencies not installed",
)
class WF01V02M6ApiTests(unittest.TestCase):
    def test_api_defaults_to_html_and_generates_csv_pdf_on_demand(self) -> None:
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        from agentd import api

        with tempfile.TemporaryDirectory(prefix="agentd-m6-api-") as state_dir:
            service = AgentDService(state_dir=state_dir)
            value = batch(two_targets=True)
            service.wf01_v02.store.save(value)
            with patch.object(api, "service", service):
                client = TestClient(api.app)
                response = client.post(
                    f"/v1/wf01/tasks/{value['batch_id']}/run-m6",
                    json={},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(
                    [
                        item["fileType"]
                        for item in response.json()["ui_message"]["data"][
                            "attachments"
                        ]
                    ],
                    ["HTML", "HTML", "HTML", "ZIP"],
                )
                view = client.get(
                    f"/v1/wf01/tasks/{value['batch_id']}/report",
                    params={
                        "target_id": "target_001",
                        "candidate_scope": "all",
                        "page": 1,
                        "page_size": 1,
                    },
                )
                self.assertEqual(view.status_code, 200, view.text)
                self.assertEqual(view.json()["canonical_target_total"], 2)
                for format_name in (
                    "html",
                    "scientific_data",
                    "csv",
                    "pdf",
                ):
                    download = client.get(
                        f"/v1/wf01/tasks/{value['batch_id']}/"
                        f"exports/{format_name}"
                    )
                    self.assertEqual(download.status_code, 200)
                    self.assertGreater(len(download.content), 100)
                preview = client.get(
                    f"/v1/wf01/tasks/{value['batch_id']}/exports/html/preview"
                )
                self.assertEqual(preview.status_code, 200)
                self.assertEqual(preview.headers["content-type"].split(";", 1)[0], "text/html")
                self.assertEqual(preview.headers["content-disposition"], "inline")
                self.assertIn("<!doctype html>", preview.text.lower())
                file_url = client.get(
                    response.json()["ui_message"]["data"]["attachments"][0][
                        "fileUrl"
                    ]
                )
                self.assertEqual(file_url.status_code, 200)
                self.assertEqual(
                    file_url.headers["content-disposition"], "inline"
                )
                self.assertIn("<!doctype html>", file_url.text.lower())
                capabilities = client.get("/v1/capabilities").json()[
                    "wf01_prd_v0_2"
                ]
                self.assertEqual(
                    capabilities["status"],
                    "M7_VERSIONED_REDESIGN_AND_REUSE",
                )
                self.assertEqual(
                    capabilities["user_export_formats"],
                    [
                        "html",
                        "process_html",
                        "rules_html",
                        "scientific_data",
                        "csv",
                        "pdf",
                    ],
                )
                self.assertFalse(capabilities["user_json_download"])


if __name__ == "__main__":
    unittest.main()
