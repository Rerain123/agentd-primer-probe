from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentd_workflow_snapshots.wf04_v1.agentd.models import Candidate, Oligo
from agentd_workflow_snapshots.wf04_v1.agentd.primer3_tool import _kcal
from agentd_workflow_snapshots.wf04_v1.agentd.rules import RuleEngine
from agentd_workflow_snapshots.wf04_v1.agentd.workflow_report_html import (
    _wf04_validation_checks,
)


def _oligo(sequence: str, *, three_prime_dg: float) -> Oligo:
    return Oligo(
        sequence_5to3=sequence,
        length_nt=len(sequence),
        tm_celsius=58.0,
        gc_percent=50.0,
        hairpin_dg_kcal=0.0,
        homodimer_dg_kcal=-1.0,
        three_prime_dimer_dg_kcal=three_prime_dg,
    )


def test_wf04_primer3_dg_is_always_converted_from_cal_to_kcal() -> None:
    assert _kcal(-90.24) == -0.09024
    assert _kcal(-517.495) == -0.517495
    assert _kcal(0.0) == 0.0
    assert _kcal(None) is None


def test_wf04_original_case_corrected_values_pass_r09_without_rule_change() -> None:
    candidate = Candidate(
        candidate_id="WF04-DG-UNIT-REGRESSION",
        forward_primer=_oligo(
            "ATTTCTATGAAGGGAATTAAAGAC",
            three_prime_dg=_kcal(-517.495),
        ),
        reverse_primer=_oligo(
            "CCTGTTGTTCTATGTTACTGAAG",
            three_prime_dg=_kcal(-90.24),
        ),
        probe=None,
        amplicon_sequence_5to3="",
        amplicon_start=0,
        amplicon_end=-1,
        pair_heterodimer_dg_kcal=-1.777007,
        three_prime_heterodimer_dg_kcal=-0.581831,
        three_prime_involved=True,
    )

    r09 = next(
        rule for rule in RuleEngine().evaluate(candidate) if rule.code == "R-09"
    )

    assert r09.threshold == "3'端自二聚体ΔG≥-6.0 kcal/mol"
    assert r09.status.value == "PASS"
    assert r09.actual == {"forward": -0.517495, "reverse": -0.09024}


def test_wf04_zero_mfe_remains_a_nonblocking_p6_pass() -> None:
    candidate = Candidate(
        candidate_id="WF04-P6-ZERO-MFE-REGRESSION",
        forward_primer=_oligo("ATTTCTATGAAGGGAATTAAAGAC", three_prime_dg=-0.5),
        reverse_primer=_oligo("CCTGTTGTTCTATGTTACTGAAG", three_prime_dg=-0.1),
        probe=None,
        amplicon_sequence_5to3="A" * 115,
        amplicon_start=0,
        amplicon_end=114,
        amplicon_structure={"status": "PASS", "mfe_dg": 0.0},
    )

    supplement = next(
        rule
        for rule in RuleEngine().evaluate(candidate)
        if rule.code == "R-SUPPLEMENT"
    )

    assert supplement.status.value == "PASS"
    assert supplement.actual == {"status": "PASS", "mfe_dg": 0.0}


def test_wf04_key_check_includes_r09_failure() -> None:
    report = {
        "workflow": "WF-04",
        "input": {},
        "target": {},
        "specificity": {},
        "candidates": [
            {
                "candidate_id": "WF04-R09-REPORT-REGRESSION",
                "pair_heterodimer_dg_kcal": -1.0,
                "forward_primer": {"three_prime_dimer_dg_kcal": -7.0},
                "reverse_primer": {"three_prime_dimer_dg_kcal": -1.0},
                "rules": [
                    {
                        "code": "R-09",
                        "status": "FAIL",
                        "actual": {"forward": -7.0, "reverse": -1.0},
                    },
                    {"code": "R-10", "status": "PASS"},
                    {"code": "R-11", "status": "PASS"},
                    {"code": "R-12", "status": "PASS"},
                ],
            }
        ],
    }

    structure_check = next(
        check
        for check in _wf04_validation_checks(report)
        if check[0] == "引物探针自身结构与二聚体"
    )

    assert structure_check[2] == "FAIL"
    assert "存在需要处理的问题" in structure_check[1]
