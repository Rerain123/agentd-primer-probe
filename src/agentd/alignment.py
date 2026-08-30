"""C5 MAFFT adapter and conservative-region selection."""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from .errors import InputValidationError, ToolUnavailableError
from .models import SequenceRecord
from .sequence import format_fasta, normalize_sequence, read_fasta


class MafftAdapter:
    def __init__(self, executable: str = "mafft", *, allow_fallback: bool = False) -> None:
        self.executable = executable
        self.allow_fallback = allow_fallback
        self.last_evidence: dict = {}

    @property
    def available(self) -> bool:
        return shutil.which(self.executable) is not None

    def status(self) -> dict:
        return {
            "tool": "C5_MAFFT",
            "available": self.available,
            "executable": shutil.which(self.executable),
            "fallback_available": True,
            "fallback_enabled": self.allow_fallback,
        }

    @staticmethod
    def _record_evidence(records: list[SequenceRecord]) -> list[dict[str, Any]]:
        return [
            {
                "id": record.id,
                "description": record.description,
                "source": record.source,
                "length_nt": len(record.sequence.replace("-", "")),
                "aligned_length": len(record.sequence),
                "sequence": record.sequence,
            }
            for record in records
        ]

    @staticmethod
    def _validate_alignment(
        input_records: list[SequenceRecord],
        aligned_records: list[SequenceRecord],
    ) -> list[SequenceRecord]:
        if len(aligned_records) != len(input_records):
            raise InputValidationError(
                "MAFFT 输出序列数量与输入不一致",
                details={
                    "input_count": len(input_records),
                    "output_count": len(aligned_records),
                },
            )
        input_ids = [record.id for record in input_records]
        output_by_id = {record.id: record for record in aligned_records}
        if len(output_by_id) != len(aligned_records) or set(output_by_id) != set(
            input_ids
        ):
            raise InputValidationError(
                "MAFFT 输出记录标识无法与输入逐条映射",
                details={
                    "input_ids": input_ids,
                    "output_ids": [record.id for record in aligned_records],
                },
            )
        ordered = [output_by_id[record_id] for record_id in input_ids]
        widths = {len(record.sequence) for record in ordered}
        if len(widths) != 1 or not widths or next(iter(widths)) == 0:
            raise InputValidationError(
                "MAFFT 输出不是有效的等长多序列比对",
                details={"aligned_lengths": sorted(widths)},
            )
        for original, aligned in zip(input_records, ordered):
            if aligned.sequence.replace("-", "") != original.sequence:
                raise InputValidationError(
                    "MAFFT 输出序列与输入序列不一致",
                    details={"record_id": original.id},
                )
        return ordered

    def align(self, records: list[SequenceRecord]) -> tuple[list[SequenceRecord], str, list[str]]:
        if not records:
            self.last_evidence = {
                "tool": "MAFFT",
                "status": "NOT_RUN",
                "input_fasta": "",
                "output_fasta": "",
                "input_records": [],
                "aligned_records": [],
            }
            return [], "not_run", ["没有可比对序列"]
        if len(records) == 1:
            fasta = format_fasta(records)
            self.last_evidence = {
                "tool": "MAFFT",
                "status": "SKIPPED_SINGLE_SEQUENCE",
                "input_fasta": fasta,
                "output_fasta": fasta,
                "input_records": self._record_evidence(records),
                "aligned_records": self._record_evidence(records),
                "alignment_width": len(records[0].sequence),
            }
            return records, "single_sequence", ["仅提供1条序列，无法评估群体保守性"]
        if self.available:
            with tempfile.TemporaryDirectory(prefix="agentd-mafft-") as temp_dir:
                input_path = Path(temp_dir) / "input.fasta"
                input_path.write_text(format_fasta(records), encoding="utf-8")
                command = [self.executable, "--auto", "--quiet", str(input_path)]
                try:
                    completed = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=600,
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    self.last_evidence = {
                        "tool": "MAFFT",
                        "status": (
                            "TIMED_OUT"
                            if isinstance(exc, subprocess.TimeoutExpired)
                            else "FAILED"
                        ),
                        "command": command,
                        "returncode": None,
                        "input_fasta": input_path.read_text(encoding="utf-8"),
                        "input_records": self._record_evidence(records),
                        "stdout": getattr(exc, "stdout", None),
                        "stderr": getattr(exc, "stderr", None),
                        "error": {
                            "type": type(exc).__name__,
                            "message": str(exc),
                        },
                    }
                    raise ToolUnavailableError(
                        "MAFFT执行异常",
                        details=self.last_evidence["error"],
                    ) from exc
                self.last_evidence = {
                    "tool": "MAFFT",
                    "status": "COMPLETED" if completed.returncode == 0 else "FAILED",
                    "command": command,
                    "returncode": completed.returncode,
                    "input_fasta": input_path.read_text(encoding="utf-8"),
                    "input_records": self._record_evidence(records),
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                }
                if completed.returncode != 0:
                    raise ToolUnavailableError(
                        "MAFFT执行失败",
                        details={"returncode": completed.returncode, "stderr": completed.stderr[-2000:]},
                    )
                output_path = Path(temp_dir) / "aligned.fasta"
                output_path.write_text(completed.stdout, encoding="utf-8")
                try:
                    aligned = self._validate_alignment(
                        records,
                        read_fasta(output_path, allow_gaps=True),
                    )
                except InputValidationError as exc:
                    self.last_evidence.update(
                        {
                            "status": "FAILED",
                            "output_fasta": completed.stdout,
                            "error": exc.to_dict(),
                        }
                    )
                    raise ToolUnavailableError(
                        "MAFFT 输出校验失败",
                        details=exc.to_dict(),
                    ) from exc
                self.last_evidence.update(
                    {
                        "output_fasta": completed.stdout,
                        "input_records": self._record_evidence(records),
                        "aligned_records": self._record_evidence(aligned),
                        "alignment_width": len(aligned[0].sequence),
                    }
                )
                return aligned, "mafft_shannon", []
        equal_lengths = len({len(record.sequence) for record in records}) == 1
        if self.allow_fallback and equal_lengths:
            fasta = format_fasta(records)
            self.last_evidence = {
                "tool": "MAFFT",
                "status": "DEVELOPMENT_FALLBACK",
                "method": "prealigned_shannon_fallback",
                "input_fasta": fasta,
                "output_fasta": fasta,
                "input_records": self._record_evidence(records),
                "aligned_records": self._record_evidence(records),
                "alignment_width": len(records[0].sequence),
            }
            return records, "prealigned_shannon_fallback", ["MAFFT不可用；将输入视为已比对等长序列"]
        if self.allow_fallback:
            input_fasta = format_fasta(records)
            output_fasta = format_fasta([records[0]])
            self.last_evidence = {
                "tool": "MAFFT",
                "status": "DEVELOPMENT_FALLBACK",
                "method": "full_length_fallback",
                "input_fasta": input_fasta,
                "output_fasta": output_fasta,
                "input_records": self._record_evidence(records),
                "aligned_records": self._record_evidence([records[0]]),
                "alignment_width": len(records[0].sequence),
            }
            return [records[0]], "full_length_fallback", ["MAFFT不可用且序列不等长；仅使用首条参考序列"]
        self.last_evidence = {
            "tool": "MAFFT",
            "status": "UNAVAILABLE",
            "input_fasta": format_fasta(records),
            "output_fasta": "",
            "input_records": self._record_evidence(records),
            "aligned_records": [],
        }
        raise ToolUnavailableError("MAFFT不可用", details={"executable": self.executable})


def _column_entropy(column: list[str]) -> float:
    bases = [base for base in column if base in "ACGT"]
    if not bases:
        return 2.0
    counts = Counter(bases)
    total = len(bases)
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


def _entropy_conservation_score(entropy: float) -> float:
    """Map Shannon entropy onto the WF-01 documented conservation scale.

    The piecewise anchors are the PRD values: H=0 -> 1.00, H=0.12 -> 0.95,
    H=0.50 -> 0.90 and the four-base maximum H=2 -> 0.00.
    """

    entropy = max(0.0, min(2.0, float(entropy)))
    if entropy <= 0.12:
        return 1.0 - (entropy / 0.12) * 0.05
    if entropy <= 0.5:
        return 0.95 - ((entropy - 0.12) / 0.38) * 0.05
    return 0.90 - ((entropy - 0.5) / 1.5) * 0.90


def build_conservation_profile(
    aligned_records: list[SequenceRecord],
) -> dict[str, Any]:
    """Build gap-aware, reference-coordinate MAFFT/Shannon evidence."""

    if len(aligned_records) < 2:
        raise ValueError("population conservation requires at least two sequences")
    widths = {len(record.sequence) for record in aligned_records}
    if len(widths) != 1:
        raise ValueError("aligned records must have equal length")
    width = next(iter(widths))
    if width == 0:
        raise ValueError("aligned records must not be empty")

    aligned = [record.sequence.upper() for record in aligned_records]
    reference_aligned = aligned[0]
    reference_columns: list[int] = []
    reference_sequence_parts: list[str] = []
    columns: list[dict[str, Any]] = []
    reference_position = -1
    for alignment_index in range(width):
        values = [sequence[alignment_index] for sequence in aligned]
        reference_base = reference_aligned[alignment_index]
        if reference_base != "-":
            reference_position += 1
            reference_columns.append(alignment_index)
            reference_sequence_parts.append(reference_base)
            mapped_position: int | None = reference_position
        else:
            mapped_position = None
        entropy = _column_entropy(values)
        canonical = [base for base in values if base in "ACGT"]
        coverage = len(canonical) / len(values)
        counts = Counter(values)
        consensus = (
            sorted(
                Counter(canonical).items(),
                key=lambda item: (-item[1], item[0]),
            )[0][0]
            if canonical
            else None
        )
        conservation_score = _entropy_conservation_score(entropy) * coverage
        observed = sorted(counts)
        columns.append(
            {
                "alignment_index": alignment_index,
                "reference_position": mapped_position,
                "reference_base": (
                    reference_base if reference_base != "-" else None
                ),
                "consensus_base": consensus,
                "counts": dict(sorted(counts.items())),
                "entropy_bits": round(entropy, 6),
                "coverage": round(coverage, 6),
                "conservation_score": round(
                    max(0.0, min(1.0, conservation_score)),
                    6,
                ),
                "is_variable": len(observed) > 1,
            }
        )

    reference_sequence = "".join(reference_sequence_parts)
    if not reference_sequence:
        raise ValueError("reference sequence contains only alignment gaps")
    variant_positions = [
        {
            "position": column["reference_position"],
            "alignment_index": column["alignment_index"],
            "reference_base": column["reference_base"],
            "consensus_base": column["consensus_base"],
            "counts": copy_counts,
            "entropy_bits": column["entropy_bits"],
            "coverage": column["coverage"],
            "conservation_score": column["conservation_score"],
        }
        for column in columns
        if column["reference_position"] is not None
        and column["is_variable"]
        for copy_counts in [dict(column["counts"])]
    ]
    reference_scores = [
        columns[index]["conservation_score"] for index in reference_columns
    ]
    return {
        "method": "mafft_shannon",
        "sequence_count": len(aligned_records),
        "sequence_ids": [record.id for record in aligned_records],
        "reference_id": aligned_records[0].id,
        "alignment_width": width,
        "reference_length": len(reference_sequence),
        "reference_sequence": reference_sequence,
        "mean_conservation_score": round(
            sum(reference_scores) / len(reference_scores),
            6,
        ),
        "variant_position_count": len(variant_positions),
        "variant_positions": variant_positions,
        "columns": columns,
        "reference_columns": reference_columns,
        "_aligned_sequences": aligned,
    }


def public_conservation_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Return the immutable scientific profile without internal helpers."""

    return {
        key: copy_value
        for key, copy_value in profile.items()
        if not key.startswith("_")
    }


def _range_metrics(
    profile: dict[str, Any],
    start: int,
    end: int,
) -> dict[str, Any]:
    reference_length = int(profile["reference_length"])
    if not 0 <= start <= end < reference_length:
        raise ValueError("reference range is outside the alignment")
    reference_columns = profile["reference_columns"]
    alignment_start = int(reference_columns[start])
    alignment_end = int(reference_columns[end])
    selected = profile["columns"][alignment_start : alignment_end + 1]
    reference_selected = [
        column
        for column in selected
        if column["reference_position"] is not None
    ]
    scores = [float(column["conservation_score"]) for column in selected]
    entropies = [float(column["entropy_bits"]) for column in selected]
    coverages = [float(column["coverage"]) for column in selected]
    variant_positions = [
        {
            "position": column["reference_position"],
            "alignment_index": column["alignment_index"],
            "reference_base": column["reference_base"],
            "consensus_base": column["consensus_base"],
            "counts": dict(column["counts"]),
            "entropy_bits": column["entropy_bits"],
            "coverage": column["coverage"],
            "conservation_score": column["conservation_score"],
        }
        for column in reference_selected
        if column["is_variable"]
    ]
    return {
        "start": start,
        "end": end,
        "length": end - start + 1,
        "alignment_start": alignment_start,
        "alignment_end": alignment_end,
        "alignment_length": alignment_end - alignment_start + 1,
        "sequence": profile["reference_sequence"][start : end + 1],
        "consensus_sequence": "".join(
            str(column["consensus_base"] or "N")
            for column in reference_selected
        ),
        "conservation_score": round(sum(scores) / len(scores), 6),
        "mean_entropy_bits": round(sum(entropies) / len(entropies), 6),
        "maximum_entropy_bits": round(max(entropies), 6),
        "mean_coverage": round(sum(coverages) / len(coverages), 6),
        "minimum_coverage": round(min(coverages), 6),
        "variant_position_count": len(variant_positions),
        "variant_positions": variant_positions,
    }


def find_conserved_regions(
    aligned_records: list[SequenceRecord],
    rounds: list[dict],
    *,
    maximum_regions: int = 5,
) -> tuple[list[dict], str]:
    if not aligned_records:
        return [], "not_run"
    if len(aligned_records) == 1:
        sequence = normalize_sequence(aligned_records[0].sequence, allow_iupac=True).replace("-", "")
        return [
            {
                "start": 0,
                "length": len(sequence),
                "end": len(sequence) - 1,
                "conservation_score": 1.0,
                "label": "single_sequence_full_length",
            }
        ], "single_sequence"

    if maximum_regions < 1:
        raise ValueError("maximum_regions must be at least one")
    profile = build_conservation_profile(aligned_records)
    reference_length = int(profile["reference_length"])

    for round_index, round_config in enumerate(rounds, start=1):
        minimum_score = float(round_config["minimum_conservation"])
        minimum_length = int(round_config["minimum_length"])
        if not 0 <= minimum_score <= 1 or minimum_length < 1:
            raise ValueError("invalid conservation round")
        if reference_length < minimum_length:
            continue

        passing: list[tuple[int, dict[str, Any]]] = []
        for start in range(reference_length - minimum_length + 1):
            metrics = _range_metrics(
                profile,
                start,
                start + minimum_length - 1,
            )
            if metrics["conservation_score"] >= minimum_score:
                passing.append((start, metrics))
        if not passing:
            continue

        clusters: list[list[tuple[int, dict[str, Any]]]] = []
        for item in passing:
            if (
                not clusters
                or item[0] > clusters[-1][-1][0] + 1
            ):
                clusters.append([item])
            else:
                clusters[-1].append(item)

        regions: list[dict[str, Any]] = []
        for cluster in clusters:
            merged_start = cluster[0][0]
            merged_end = cluster[-1][0] + minimum_length - 1
            metrics = _range_metrics(profile, merged_start, merged_end)
            if metrics["conservation_score"] < minimum_score:
                metrics = max(
                    (item[1] for item in cluster),
                    key=lambda item: (
                        item["conservation_score"],
                        -item["start"],
                    ),
                )
            low_confidence_reasons: list[str] = []
            if round_index > 1:
                low_confidence_reasons.append("RELAXED_CONSERVATION_THRESHOLD")
            if len(aligned_records) < 20:
                low_confidence_reasons.append(
                    "REPRESENTATIVE_SEQUENCE_COUNT_BELOW_20"
                )
            regions.append(
                {
                    **metrics,
                    "label": str(round_config["label"]),
                    "selection_status": "SELECTED_CONSERVED_REGION",
                    "selection_round": round_index,
                    "selection_threshold": minimum_score,
                    "minimum_required_length": minimum_length,
                    "sequence_count": len(aligned_records),
                    "confidence": (
                        "HIGH" if not low_confidence_reasons else "LOW"
                    ),
                    "low_confidence_reasons": low_confidence_reasons,
                    "full_length_fallback": False,
                }
            )
        regions.sort(
            key=lambda item: (
                -float(item["conservation_score"]),
                -int(item["length"]),
                int(item["start"]),
            )
        )
        return regions[:maximum_regions], "mafft_shannon"

    fallback = _range_metrics(profile, 0, reference_length - 1)
    return [
        {
            **fallback,
            "label": "full_length_fallback",
            "selection_status": "NO_QUALIFYING_CONSERVED_REGION",
            "selection_round": None,
            "selection_threshold": None,
            "minimum_required_length": None,
            "sequence_count": len(aligned_records),
            "confidence": "LOW",
            "low_confidence_reasons": [
                "NO_REGION_MET_PROGRESSIVE_THRESHOLDS",
                *(
                    ["REPRESENTATIVE_SEQUENCE_COUNT_BELOW_20"]
                    if len(aligned_records) < 20
                    else []
                ),
            ],
            "full_length_fallback": True,
        }
    ], "mafft_shannon"


def candidate_conservation_evidence(
    profile: dict[str, Any] | None,
    *,
    intervals: dict[str, tuple[int, int] | None],
    region: dict[str, Any],
    status: str,
    method: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Map MAFFT evidence onto one candidate's binding sites."""

    sequence_count = int(
        (profile or {}).get("sequence_count")
        or region.get("sequence_count")
        or 1
    )
    if status != "EVALUATED" or profile is None:
        return {
            "status": "NOT_EVALUATED",
            "reason": reason or "MAFFT_CONSERVATION_NOT_COMPLETED",
            "method": method,
            "sequence_count": sequence_count,
            "reference_id": (profile or {}).get("reference_id"),
            "score": None,
            "coverage_percent": None,
            "confidence": "NOT_EVALUATED",
            "region": {
                key: region.get(key)
                for key in (
                    "start",
                    "end",
                    "length",
                    "label",
                    "selection_status",
                )
            },
            "binding_sites": {},
            "variant_positions": [],
        }

    reference_columns = profile["reference_columns"]
    reference_sequence = str(profile["reference_sequence"])
    aligned_sequences = profile["_aligned_sequences"]
    binding_sites: dict[str, dict[str, Any]] = {}
    exact_by_sequence = [True] * sequence_count
    covered_positions: set[int] = set()
    covered_alignment_columns: set[int] = set()
    for role, interval in intervals.items():
        if interval is None:
            continue
        start, end = interval
        if not 0 <= start <= end < len(reference_sequence):
            continue
        alignment_start = int(reference_columns[start])
        alignment_end = int(reference_columns[end])
        internal_insertion_columns = [
            alignment_index
            for alignment_index in range(alignment_start, alignment_end + 1)
            if profile["columns"][alignment_index]["reference_position"]
            is None
        ]
        identities: list[float] = []
        insertion_base_counts: list[int] = []
        exact_count = 0
        gap_free_count = 0
        for sequence_index, aligned_sequence in enumerate(aligned_sequences):
            compared = [
                (
                    reference_sequence[position],
                    aligned_sequence[reference_columns[position]],
                )
                for position in range(start, end + 1)
            ]
            matches = sum(
                observed == expected and observed in "ACGT"
                for expected, observed in compared
            )
            insertion_base_count = sum(
                aligned_sequence[alignment_index] in "ACGT"
                for alignment_index in internal_insertion_columns
            )
            insertion_base_counts.append(insertion_base_count)
            identity = (
                100.0
                * matches
                / (len(compared) + insertion_base_count)
            )
            identities.append(identity)
            gap_free = all(observed in "ACGT" for _expected, observed in compared)
            exact = (
                gap_free
                and insertion_base_count == 0
                and matches == len(compared)
            )
            gap_free_count += int(gap_free)
            exact_count += int(exact)
            exact_by_sequence[sequence_index] = (
                exact_by_sequence[sequence_index] and exact
            )
        covered_positions.update(range(start, end + 1))
        covered_alignment_columns.update(
            range(alignment_start, alignment_end + 1)
        )
        metrics = _range_metrics(profile, start, end)
        binding_sites[role] = {
            "start": start,
            "end": end,
            "length": end - start + 1,
            "conservation_score": metrics["conservation_score"],
            "mean_identity_percent": round(
                sum(identities) / len(identities),
                3,
            ),
            "minimum_identity_percent": round(min(identities), 3),
            "exact_match_sequence_count": exact_count,
            "exact_match_percent": round(
                100.0 * exact_count / sequence_count,
                3,
            ),
            "gap_free_sequence_count": gap_free_count,
            "internal_insertion_column_count": len(
                internal_insertion_columns
            ),
            "maximum_internal_insertion_base_count": max(
                insertion_base_counts,
                default=0,
            ),
            "variant_positions": metrics["variant_positions"],
        }

    if not binding_sites or not covered_positions:
        return {
            "status": "NOT_EVALUATED",
            "reason": "CANDIDATE_BINDING_COORDINATES_MISSING",
            "method": method,
            "sequence_count": sequence_count,
            "reference_id": profile.get("reference_id"),
            "score": None,
            "coverage_percent": None,
            "confidence": "NOT_EVALUATED",
            "region": dict(region),
            "binding_sites": binding_sites,
            "variant_positions": [],
        }

    scores = [
        float(
            profile["columns"][alignment_index]["conservation_score"]
        )
        for alignment_index in sorted(covered_alignment_columns)
    ]
    exact_all = sum(exact_by_sequence)
    variant_by_position: dict[int, dict[str, Any]] = {}
    for site in binding_sites.values():
        for variant in site["variant_positions"]:
            variant_by_position[int(variant["position"])] = variant
    low_confidence_reasons = list(
        region.get("low_confidence_reasons") or []
    )
    return {
        "status": "EVALUATED",
        "reason": "MAFFT_SHANNON_BINDING_SITES_EVALUATED",
        "method": method,
        "sequence_count": sequence_count,
        "reference_id": profile.get("reference_id"),
        "score": round(sum(scores) / len(scores), 6),
        "coverage_percent": round(
            100.0 * exact_all / sequence_count,
            3,
        ),
        "exact_match_sequence_count": exact_all,
        "confidence": (
            "HIGH" if not low_confidence_reasons else "LOW"
        ),
        "low_confidence_reasons": low_confidence_reasons,
        "region": {
            key: region.get(key)
            for key in (
                "start",
                "end",
                "length",
                "label",
                "selection_status",
                "selection_threshold",
                "conservation_score",
                "confidence",
            )
        },
        "binding_sites": binding_sites,
        "variant_positions": [
            variant_by_position[position]
            for position in sorted(variant_by_position)
        ],
    }
