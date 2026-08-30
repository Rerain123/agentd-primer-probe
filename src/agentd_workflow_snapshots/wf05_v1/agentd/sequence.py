"""C1 sequence IO and deterministic sequence utilities."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from .errors import InputValidationError
from .models import SequenceRecord


DNA_BASES = frozenset("ACGT")
IUPAC_BASES = frozenset("ACGTRYSWKMBDHVN")
COMPLEMENT = str.maketrans("ACGTRYSWKMBDHVN", "TGCAYRSWMKVHDBN")


def normalize_sequence(
    sequence: str,
    *,
    allow_iupac: bool = False,
    allow_gaps: bool = False,
) -> str:
    normalized = re.sub(r"\s+", "", sequence).upper().replace("U", "T")
    if not normalized:
        raise InputValidationError("序列不能为空")
    allowed = IUPAC_BASES if allow_iupac else DNA_BASES
    if allow_gaps:
        allowed = allowed | {"-"}
    invalid = sorted(set(normalized) - allowed)
    if invalid:
        raise InputValidationError(
            "序列包含不支持的碱基",
            details={"invalid_bases": invalid, "allow_iupac": allow_iupac},
        )
    return normalized


def reverse_complement(sequence: str) -> str:
    return normalize_sequence(sequence, allow_iupac=True).translate(COMPLEMENT)[::-1]


def gc_percent(sequence: str) -> float:
    sequence = normalize_sequence(sequence, allow_iupac=True)
    defined = sum(sequence.count(base) for base in "ACGT")
    if defined == 0:
        return 0.0
    return round((sequence.count("G") + sequence.count("C")) / defined * 100.0, 3)


def wallace_tm(sequence: str) -> float:
    """Development-only Tm approximation; never labeled as SantaLucia."""
    sequence = normalize_sequence(sequence)
    return float(2 * (sequence.count("A") + sequence.count("T")) + 4 * (sequence.count("G") + sequence.count("C")))


def read_fasta(
    path: str | Path,
    *,
    allow_gaps: bool = False,
    max_records: int | None = None,
    max_sequence_length: int | None = None,
    max_total_length: int | None = None,
) -> list[SequenceRecord]:
    records: list[SequenceRecord] = []
    current_id: str | None = None
    current_description = ""
    chunks: list[str] = []
    source = str(Path(path).resolve())
    total_length = 0

    def flush() -> None:
        nonlocal current_id, current_description, chunks, total_length
        if current_id is not None:
            normalized = normalize_sequence(
                "".join(chunks), allow_iupac=True, allow_gaps=allow_gaps
            )
            if max_records is not None and len(records) >= max_records:
                raise InputValidationError(
                    "FASTA记录数超过运行时资源上限",
                    details={"maximum": max_records},
                )
            if (
                max_sequence_length is not None
                and len(normalized) > max_sequence_length
            ):
                raise InputValidationError(
                    "FASTA单条记录超过运行时资源上限",
                    details={
                        "record_id": current_id,
                        "length": len(normalized),
                        "maximum": max_sequence_length,
                    },
                )
            if (
                max_total_length is not None
                and total_length + len(normalized) > max_total_length
            ):
                raise InputValidationError(
                    "FASTA记录总量超过运行时资源上限",
                    details={"maximum": max_total_length},
                )
            records.append(
                SequenceRecord(
                    id=current_id,
                    description=current_description,
                    sequence=normalized,
                    source=source,
                )
            )
            total_length += len(normalized)
        chunks = []

    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith(">"):
                    flush()
                    header = line[1:].strip()
                    current_id = header.split(maxsplit=1)[0] if header else f"sequence_{len(records)+1}"
                    current_description = header
                else:
                    if current_id is None:
                        raise InputValidationError("FASTA首个非空行必须以 > 开头")
                    chunks.append(line)
        flush()
    except OSError as exc:
        raise InputValidationError(f"无法读取FASTA文件: {path}", details={"error": str(exc)}) from exc
    if not records:
        raise InputValidationError(f"FASTA文件中没有序列: {path}")
    return records


def format_fasta(records: Iterable[SequenceRecord], width: int = 80) -> str:
    lines: list[str] = []
    for record in records:
        lines.append(f">{record.id}")
        lines.extend(record.sequence[i : i + width] for i in range(0, len(record.sequence), width))
    return "\n".join(lines) + "\n"


def longest_three_prime_complement(seq1: str, seq2: str, *, max_length: int = 8) -> int:
    """Return longest exact complement involving either oligo's 3' terminus."""
    first = normalize_sequence(seq1)
    second_rc = reverse_complement(seq2)
    limit = min(max_length, len(first), len(second_rc))
    best = 0
    for length in range(1, limit + 1):
        if first[-length:] in second_rc or second_rc[-length:] in first:
            best = length
    return best


def find_amplicon(template: str, forward: str, reverse: str) -> tuple[int, int, str] | None:
    template = normalize_sequence(template)
    forward = normalize_sequence(forward)
    reverse_binding = reverse_complement(reverse)
    left = template.find(forward)
    if left < 0:
        return None
    right_binding_start = template.find(reverse_binding, left + len(forward))
    if right_binding_start < 0:
        return None
    end = right_binding_start + len(reverse_binding) - 1
    return left, end, template[left : end + 1]
