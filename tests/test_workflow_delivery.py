from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentd.errors import InputValidationError, PersistenceError
from agentd.service import AgentDService
from agentd.workflow_delivery import WorkflowTerminalDeliveryStore
from agentd.workflow_report_html import (
    WORKFLOW_REPORT_RENDERER_VERSION,
    render_workflow_report_html,
)


class DisabledPublisher:
    enabled = False

    @staticmethod
    def publish(*_args, **_kwargs):
        return None


class TogglePublisher:
    def __init__(self) -> None:
        self.enabled = False
        self.calls = 0

    def publish(
        self,
        path: Path,
        *,
        batch_id: str,
        media_type: str,
        sha256: str,
        object_name: str,
    ) -> dict | None:
        if not self.enabled:
            return None
        self.calls += 1
        return {
            "provider": "rustfs",
            "file_url": f"https://files.example/{object_name}",
            "sha256": sha256,
            "size_bytes": path.stat().st_size,
            "batch_id": batch_id,
            "media_type": media_type,
        }


def test_terminal_delivery_rejects_non_object_report(tmp_path: Path) -> None:
    store = WorkflowTerminalDeliveryStore(tmp_path, DisabledPublisher())

    with pytest.raises(InputValidationError, match="终态报告必须为对象"):
        store.build_delivery([])  # type: ignore[arg-type]


def test_terminal_delivery_reuses_wf01_output_contract(tmp_path: Path) -> None:
    store = WorkflowTerminalDeliveryStore(tmp_path, DisabledPublisher())
    report = {
        "workflow": "WF-04",
        "job_id": "12345678-1234-4234-9234-123456789abc",
        "execution_status": "SUCCEEDED",
        "decision": "PASS",
        "next_action": "END",
        "warnings": [],
        "errors": [],
    }

    first = store.build_delivery(report)
    second = store.build_delivery(report)

    assert first["batch_code"] == second["batch_code"]
    assert first["ui_message"] == second["ui_message"]
    message = first["ui_message"]
    assert message["type"] == "primer_design_output"
    assert message["data"]["primaryAttachmentId"] == "attachment_html"
    attachment = message["data"]["attachments"][0]
    assert attachment["fileUrl"].startswith(
        "/data/reports/12345678-1234-4234-9234-123456789abc/"
    )
    path = store.get_export_path(report["job_id"], attachment["fileName"])
    assert path.is_file()
    assert "WF-04" in path.read_text(encoding="utf-8")
    metadata = json.loads(
        (path.parent / "metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["renderer_version"] == WORKFLOW_REPORT_RENDERER_VERSION

    schema = json.loads(
        (ROOT / "schemas" / "primer-design-output.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(message)


@pytest.mark.parametrize(
    "report",
    (
        {
            "workflow": "WF-03",
            "job_id": "03030303-1234-4234-9234-123456789abc",
            "status": "COMPLETED",
            "schema_version": "wf03-v0.1",
            "created_at": "2026-08-25T10:20:30+08:00",
            "workflow_trace": [
                {
                    "step": "WF03_STEP1",
                    "status": "PASS",
                    "tool": "P5",
                    "duration_ms": 12,
                }
            ],
            "internal_control": {"verdict": "PASS"},
            "candidates": [
                {
                    "candidate_id": "WF03-RESULT-1",
                    "rank": 1,
                    "verdict": "PASS",
                    "score": 0.91,
                    "evidence_level": "VERIFIED",
                    "parameters": {"alpha": 1, "beta": True},
                    "warnings": [],
                }
            ],
            "warnings": [],
            "errors": [],
        },
        {
            "workflow": "WF-04",
            "job_id": "04040404-1234-4234-9234-123456789abc",
            "execution_status": "SUCCEEDED",
            "decision": "PASS",
            "workflow_version": "wf04-v0.1",
            "created_at": "2026-08-25T10:20:30+08:00",
            "steps": [
                {
                    "step": "WF04_STEP1",
                    "status": "PASS",
                    "tool": "P1",
                    "reused": False,
                }
            ],
            "candidates": [
                {
                    "candidate_id": "WF04-RESULT-1",
                    "rank": 1,
                    "verdict": "PASS",
                    "score": 0.88,
                    "evidence_level": "VERIFIED",
                    "parameters": {"alpha": 2, "beta": False},
                    "warnings": ["review-note"],
                }
            ],
            "warnings": ["workflow-note"],
            "errors": [],
        },
    ),
)
def test_terminal_report_uses_wf02_readable_visual_contract(
    report: dict,
) -> None:
    rendered = render_workflow_report_html(report).decode("utf-8")

    assert 'class="report-shell"' in rendered
    assert 'class="key-metrics"' in rendered
    assert 'class="validation-list"' in rendered
    assert 'class="comparison-table"' in rendered
    assert 'class="member-section"' in rendered
    assert 'class="technical-summary"' in rendered
    assert 'id="workflow-report-data"' in rendered
    assert "body{margin:0;background:#fff" in rendered
    assert "linear-gradient" not in rendered
    assert "box-shadow" not in rendered
    assert "<pre" not in rendered


def test_wf03_terminal_report_translates_internal_records_into_business_language() -> None:
    report = {
        "workflow": "WF-03",
        "job_id": "86c082c9-b71b-41a2-a4e0-63fa140251ca",
        "status": "PARTIAL",
        "created_at": "2026-08-25T03:13:19+00:00",
        "target": {
            "host_species": "pig",
            "host_species_input": "猪",
            "gene_selected": "pig_ACTB",
        },
        "internal_control": {
            "verdict": "CAUTION",
            "validation_summary": {
                "candidate_quantity": {
                    "status": "PASS",
                    "actual": 3,
                    "required": 3,
                },
                "conservation": {"status": "PASS", "score": 0.994444},
                "amplicon_structure": {
                    "status": "PASS",
                    "candidate_count": 3,
                },
                "target_compatibility": {
                    "status": "NOT_EVALUATED",
                    "performed": False,
                    "reason": "未提供existing_targets",
                },
                "channel_assignment": {
                    "status": "SUGGESTION_ONLY",
                    "suggested_fluorophore": "Cy5",
                },
                "expression_stability": {"status": "NOT_EVALUATED"},
            },
        },
        "workflow_trace": [
            {"step": "WF03_INPUT", "status": "PASS"},
            {"step": "P5_DESIGN_INTERNAL_CONTROL", "status": "COMPLETED"},
        ],
        "candidates": [
            {
                "candidate_id": "IC-pig-pig_ACTB-p3-1",
                "rank": 1,
                "score": 0.808836,
                "verdict": "PASS_WITH_WARNINGS",
                "evidence_level": "blast_verified",
                "conservation_score": 0.994444,
                "conservation_sequence_count": 6,
                "forward_primer": {
                    "sequence_5to3": "TTCCAGCAGATGTGGATCA",
                    "length_nt": 19,
                    "tm_celsius": 60.141,
                    "gc_percent": 47.368,
                },
                "reverse_primer": {
                    "sequence_5to3": "GGTGGAATGCAACTAACAGT",
                    "length_nt": 20,
                    "tm_celsius": 59.894,
                    "gc_percent": 45.0,
                },
                "probe": {
                    "sequence_5to3": "CTCCATCGTCCACCGCAAATGCTTC",
                    "length_nt": 25,
                    "tm_celsius": 69.773,
                    "gc_percent": 56.0,
                },
                "amplicon": {
                    "length_bp": 99,
                    "sequence_5to3": "TTCCAGCAGATGTGGATCAGCAAGCAGGAGTAC",
                },
                "amplicon_structure": {"status": "PASS"},
                "metadata": {
                    "gene": "pig_ACTB",
                    "fluorophore": "Cy5",
                    "quencher": "BHQ-2",
                },
                "specificity": {
                    "overall_status": "CAUTION",
                    "host_verdict": "CAUTION",
                    "cross_species_verdict": "PASS",
                    "reason": "AT_LEAST_ONE_P5_P2_MODE_REQUIRES_REVIEW",
                    "primary_match": {
                        "status": "CAUTION",
                        "reason": "P5_PRIMARY_MATCH_MULTIPLE_HITS_REQUIRE_REVIEW",
                        "mode_specific": {
                            "per_query_hit_count": {
                                "forward": 5,
                                "reverse": 1,
                                "probe": 4,
                            }
                        },
                    },
                    "related_background_nt": {"status": "PASS"},
                },
                "rules": [
                    {
                        "code": "R-16",
                        "status": "NOT_EVALUATED",
                        "actual": {"n": 6, "score": 0.994444},
                    },
                    {
                        "code": "R-19",
                        "status": "WARNING",
                        "message": "P2返回需复核结论",
                    },
                    {"code": "R-21", "status": "PASS"},
                ],
                "warnings": ["R-16", "R-19", "R-21"],
            }
        ],
        "warnings": [
            "P2:AT_LEAST_ONE_P5_P2_MODE_REQUIRES_REVIEW",
            "未提供existing_targets，无法声明与靶标引物探针兼容",
        ],
        "errors": [],
    }

    rendered = render_workflow_report_html(report).decode("utf-8")
    visible = rendered.split(
        '<script type="application/json" id="workflow-report-data">', 1
    )[0]

    assert "内标引物探针设计报告" in visible
    assert "流程已经运行至报告输出" in visible
    assert "已针对猪的 ACTB 基因形成" in visible
    assert "正向引物 5 个匹配位点" in visible
    assert "建议至少 20 条" in visible
    assert "TTCCAGCAGATGTGGATCA" in visible
    assert "80.9 / 100" in visible
    assert "2026-08-25 11:13:19（北京时间）" in visible
    for internal_term in (
        "WF03_INPUT",
        "P5_DESIGN_INTERNAL_CONTROL",
        "blast_verified",
        "pig_ACTB",
        "R-16",
        "R-19",
        "R-21",
        "AT_LEAST_ONE_P5_P2_MODE_REQUIRES_REVIEW",
        "existing_targets",
    ):
        assert internal_term not in visible
    assert "WF03_INPUT" in rendered
    assert "R-16" in rendered


def test_wf04_terminal_report_translates_unknown_rule_codes() -> None:
    report = {
        "workflow": "WF-04",
        "job_id": "WF04-report-issues",
        "execution_status": "PARTIAL",
        "decision": "FAIL",
        "candidates": [
            {
                "candidate_id": "candidate-1",
                "verdict": "FAIL",
                "rules": [
                    {"code": "R-HARD-FAIL", "category": "hard", "status": "FAIL"},
                    {
                        "code": "R-HARD-GAP",
                        "category": "hard",
                        "status": "NOT_EVALUATED",
                    },
                    {"code": "R-PASS", "category": "hard", "status": "PASS"},
                ],
            }
        ],
        "warnings": [],
        "errors": [],
    }

    rendered = render_workflow_report_html(report).decode("utf-8")
    visible = rendered.split(
        '<script type="application/json" id="workflow-report-data">', 1
    )[0]

    assert "必须修改" in visible
    assert "其他设计检查未通过" in visible
    assert "其他设计检查尚未完成" in visible
    assert "R-HARD-FAIL" not in visible
    assert "R-HARD-GAP" not in visible
    assert "R-PASS" not in visible
    assert "R-HARD-FAIL" in rendered


def test_wf04_terminal_report_shows_names_and_corresponding_data() -> None:
    report = {
        "workflow": "WF-04",
        "job_id": "04040404-1234-4234-9234-123456789abc",
        "execution_status": "PARTIAL",
        "decision": "INCONCLUSIVE",
        "decision_reasons": ["P2_REQUIRED_EVIDENCE_INCOMPLETE"],
        "created_at": "2026-08-25T03:13:19+00:00",
        "input": {
            "target_id": "target-code",
            "target_species": "目标对象名称",
            "host_species": "pig",
        },
        "target": {"target_id": "target-code", "organism": "目标对象名称"},
        "specificity": {
            "target": {
                "verdict": "PASS",
                "blast_hits": 3,
                "method": "blastn",
            },
            "host": {
                "verdict": "PASS",
                "blast_hits": 0,
                "method": "blastn",
            },
            "cross_species": {
                "verdict": "NOT_EVALUATED",
                "blast_hits": 0,
                "recommendation": "P2_REQUIRED_RESOURCE_NOT_RESOLVED",
            },
        },
        "candidates": [
            {
                "candidate_id": "WF04-INTERNAL-CANDIDATE",
                "verdict": "PARTIAL",
                "evidence_level": "in_silico_only",
                "conservation_score": 0.9725,
                "conservation_sequence_count": 24,
                "pair_heterodimer_dg_kcal": -4.2,
                "three_prime_heterodimer_dg_kcal": -2.8,
                "three_prime_involved": False,
                "forward_primer": {
                    "sequence_5to3": "ACGTACGTACGTACGTACGA",
                    "length_nt": 20,
                    "tm_celsius": 60.1,
                    "gc_percent": 50.0,
                    "position_end": 19,
                    "hairpin_dg_kcal": -1.2,
                    "homodimer_dg_kcal": -2.1,
                    "three_prime_dimer_dg_kcal": -1.1,
                },
                "reverse_primer": {
                    "sequence_5to3": "TCGTACGTACGTACGTACGT",
                    "length_nt": 20,
                    "tm_celsius": 61.4,
                    "gc_percent": 50.0,
                    "position_start": 100,
                    "hairpin_dg_kcal": -1.4,
                    "homodimer_dg_kcal": -2.3,
                    "three_prime_dimer_dg_kcal": -1.3,
                },
                "probe": {
                    "sequence_5to3": "CACGTACGTACGTACGTACG",
                    "length_nt": 20,
                    "tm_celsius": 69.8,
                    "gc_percent": 55.0,
                    "position_start": 40,
                    "position_end": 59,
                    "hairpin_dg_kcal": -1.6,
                },
                "amplicon": {
                    "sequence_5to3": "ACGTACGTACGTACGTACGT",
                    "length_bp": 120,
                },
                "metadata": {
                    "software": "Primer3",
                    "software_version": "2.6.1",
                    "tm_algorithm": "nearest-neighbor",
                    "mv_conc_mm": 50,
                    "dv_conc_mm": 1.5,
                    "oligo_conc_nm": 250,
                },
                "rules": [
                    {
                        "code": "R-01",
                        "status": "PASS",
                        "actual": {
                            "forward_tm": 60.1,
                            "reverse_tm": 61.4,
                            "delta_tm": 1.3,
                        },
                    },
                    {"code": "R-04", "status": "PASS"},
                    {"code": "R-05", "status": "PASS", "actual": "C"},
                    {
                        "code": "R-06",
                        "status": "PASS",
                        "actual": {"left_gap_bp": 21, "right_gap_bp": 41},
                    },
                    {"code": "R-07", "status": "PASS", "actual": 9.05},
                    {"code": "R-09", "status": "PASS"},
                    {
                        "code": "R-11",
                        "status": "PASS",
                        "actual": {
                            "forward": -1.2,
                            "reverse": -1.4,
                            "probe": -1.6,
                        },
                    },
                    {
                        "code": "R-16",
                        "status": "PASS",
                        "actual": {"score": 0.9725, "n": 24},
                    },
                    {"code": "R-18", "status": "PASS", "actual": {"missing": []}},
                    {
                        "code": "R-19",
                        "status": "NOT_EVALUATED",
                        "message": "P2必需模式未完整执行",
                    },
                    {
                        "code": "R-20",
                        "status": "PASS",
                        "actual": "in_silico_only",
                    },
                    {
                        "code": "R-21",
                        "status": "NOT_EVALUATED",
                        "actual": {
                            "forward": True,
                            "reverse": True,
                            "probe": True,
                            "amplicon": True,
                            "tm": True,
                            "dg": True,
                            "specificity": False,
                            "conservation": True,
                        },
                    },
                ],
            }
        ],
        "steps": [{"step": "WF04_STEP2_P2_A", "status": "COMPLETED"}],
        "warnings": ["P2_REQUIRED_EVIDENCE_INCOMPLETE", "R-19", "R-21"],
        "errors": [],
    }

    rendered = render_workflow_report_html(report).decode("utf-8")
    visible = rendered.split(
        '<script type="application/json" id="workflow-report-data">', 1
    )[0]

    assert "逐项验证数据" in visible
    assert "引物退火温度" in visible
    assert "正向引物 60.1 °C；反向引物 61.4 °C；差值 1.3 °C" in visible
    assert "探针与引物间距" in visible
    assert "左侧间距 21 bp；右侧间距 41 bp" in visible
    assert "代表性序列 24 条；保守性结果 97.25%" in visible
    assert "计算软件 Primer3；软件版本 2.6.1" in visible
    assert "目标序列核验：通过，匹配记录 3 条" in visible
    assert "宿主" in visible and "猪" in visible
    assert "仅完成计算验证，尚未完成实验验证" in visible
    assert "未记录：数据库检查" in visible
    for internal_term in (
        "WF04_STEP2_P2_A",
        "WF04-INTERNAL-CANDIDATE",
        "P2_REQUIRED_EVIDENCE_INCOMPLETE",
        "P2_REQUIRED_RESOURCE_NOT_RESOLVED",
        "in_silico_only",
        "R-01",
        "R-19",
        "R-21",
    ):
        assert internal_term not in visible
    assert "WF04_STEP2_P2_A" in rendered
    assert "R-19" in rendered


def test_terminal_report_embeds_full_json_without_script_injection() -> None:
    marker = "</script><script>alert('unsafe')</script>"
    report = {
        "workflow": "WF-03",
        "job_id": "03111111-1234-4234-9234-123456789abc",
        "status": "COMPLETED",
        "candidates": [
            {
                "candidate_id": marker,
                "rank": 1,
                "verdict": "PASS",
                "score": 1,
            }
        ],
        "warnings": [marker],
        "full_only_marker": "persisted-full-result",
    }

    rendered = render_workflow_report_html(report).decode("utf-8")
    match = re.search(
        r'<script type="application/json" id="workflow-report-data">(.*?)</script>',
        rendered,
        flags=re.DOTALL,
    )

    assert match is not None
    assert marker not in rendered
    assert "\\u003c/script\\u003e" in match.group(1)
    assert json.loads(match.group(1)) == report
    assert "&lt;/script&gt;&lt;script&gt;" in rendered


def test_terminal_delivery_rejects_corrupt_metadata_filename(
    tmp_path: Path,
) -> None:
    store = WorkflowTerminalDeliveryStore(tmp_path, DisabledPublisher())
    report = {
        "workflow": "WF-03",
        "job_id": "abcdefab-1234-4234-9234-123456789abc",
        "status": "SUCCEEDED",
    }
    store.build_delivery(report)
    metadata_path = (
        tmp_path
        / "workflow-exports"
        / report["job_id"]
        / "metadata.json"
    )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["filename"] = "../outside.html"
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(PersistenceError, match="非法文件名"):
        store.build_delivery(report)


def test_terminal_delivery_rejects_non_object_metadata(tmp_path: Path) -> None:
    store = WorkflowTerminalDeliveryStore(tmp_path, DisabledPublisher())
    report = {
        "workflow": "WF-03",
        "job_id": "fedcba98-1234-4234-9234-123456789abc",
        "status": "SUCCEEDED",
    }
    store.build_delivery(report)
    metadata_path = (
        tmp_path
        / "workflow-exports"
        / report["job_id"]
        / "metadata.json"
    )
    metadata_path.write_text("[]", encoding="utf-8")

    with pytest.raises(PersistenceError, match="必须为对象"):
        store.build_delivery(report)


def test_terminal_delivery_backfills_publication_without_rerender(
    tmp_path: Path,
) -> None:
    publisher = TogglePublisher()
    store = WorkflowTerminalDeliveryStore(tmp_path, publisher)
    report = {
        "workflow": "WF-04",
        "job_id": "deadbeef-1234-4234-9234-123456789abc",
        "execution_status": "SUCCEEDED",
        "decision": "PASS",
    }

    local = store.build_delivery(report)
    local_attachment = local["ui_message"]["data"]["attachments"][0]
    assert local_attachment["fileUrl"].startswith("/data/reports/")
    local_path = store.get_export_path(
        report["job_id"],
        local_attachment["fileName"],
    )
    original_mtime = local_path.stat().st_mtime_ns

    publisher.enabled = True
    published = store.build_delivery(report)
    repeated = store.build_delivery(report)

    assert publisher.calls == 1
    assert local_path.stat().st_mtime_ns == original_mtime
    assert published["ui_message"]["data"]["attachments"][0][
        "fileUrl"
    ].startswith("https://files.example/")
    assert repeated["ui_message"] == published["ui_message"]


@pytest.mark.parametrize(
    (
        "workflow",
        "job_id",
        "result_method",
        "bounded_method",
        "delivery_method",
    ),
    (
        (
            "WF-03",
            "33333333-1234-4234-9234-123456789abc",
            "get_wf03_result",
            "get_wf03_report",
            "get_wf03_delivery",
        ),
        (
            "WF-04",
            "44444444-1234-4234-9234-123456789abc",
            "get_wf04_result",
            "get_wf04_report",
            "get_wf04_delivery",
        ),
    ),
)
def test_service_terminal_attachment_uses_full_persisted_result(
    tmp_path: Path,
    workflow: str,
    job_id: str,
    result_method: str,
    bounded_method: str,
    delivery_method: str,
) -> None:
    service = object.__new__(AgentDService)
    service.workflow_delivery = WorkflowTerminalDeliveryStore(
        tmp_path,
        DisabledPublisher(),
    )
    report = {
        "workflow": workflow,
        "job_id": job_id,
        "status": "SUCCEEDED",
        "full_only_marker": "persisted-full-result",
    }
    setattr(service, result_method, lambda _job_id: report)
    setattr(
        service,
        bounded_method,
        lambda _job_id: pytest.fail("bounded report must not render attachment"),
    )

    delivery = getattr(service, delivery_method)(job_id)
    attachment = delivery["ui_message"]["data"]["attachments"][0]
    path = service.get_workflow_terminal_export_path(
        job_id,
        attachment["fileName"],
    )

    assert "persisted-full-result" in path.read_text(encoding="utf-8")


def test_wf03_delivery_preserves_wait_control_from_full_result(
    tmp_path: Path,
) -> None:
    service = object.__new__(AgentDService)
    service.workflow_delivery = WorkflowTerminalDeliveryStore(
        tmp_path,
        DisabledPublisher(),
    )
    report = {
        "workflow": "WF-03",
        "job_id": "77777777-1234-4234-9234-123456789abc",
        "status": "FAILED",
        "internal_control": {
            "fallback": {"can_reenter_step1": True},
        },
    }
    service.get_wf03_result = lambda _job_id: report

    delivery = service.get_wf03_delivery(report["job_id"])

    assert delivery["next_action"] == "WAIT_FOR_USER"
    assert delivery["ui_message"]["type"] == "primer_design_output"


def test_terminal_delivery_is_concurrently_idempotent(tmp_path: Path) -> None:
    store = WorkflowTerminalDeliveryStore(tmp_path, DisabledPublisher())
    report = {
        "workflow": "WF-03",
        "job_id": "55555555-1234-4234-9234-123456789abc",
        "status": "SUCCEEDED",
        "trace": [{"step": index} for index in range(100)],
    }

    with ThreadPoolExecutor(max_workers=8) as executor:
        deliveries = list(
            executor.map(lambda _index: store.build_delivery(report), range(32))
        )

    messages = [delivery["ui_message"] for delivery in deliveries]
    assert all(message == messages[0] for message in messages)
    export_directory = tmp_path / "workflow-exports" / report["job_id"]
    assert not list(export_directory.glob(".*.tmp"))


def test_shared_data_route_serves_terminal_attachment(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    import agentd.api as api_module

    service = AgentDService(state_dir=tmp_path)
    report = {
        "workflow": "WF-04",
        "job_id": "66666666-1234-4234-9234-123456789abc",
        "execution_status": "SUCCEEDED",
        "decision": "PASS",
        "route_marker": "shared-data-route",
    }
    delivery = service.workflow_delivery.build_delivery(report)
    file_url = delivery["ui_message"]["data"]["attachments"][0]["fileUrl"]

    with patch.object(api_module, "service", service):
        response = TestClient(api_module.app).get(file_url)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "shared-data-route" in response.text
