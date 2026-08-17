"""C5 MAFFT adapter and conservative-region selection."""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

from .errors import ToolUnavailableError
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
        }

    def align(self, records: list[SequenceRecord]) -> tuple[list[SequenceRecord], str, list[str]]:
        if not records:
            self.last_evidence = {
                "tool": "MAFFT",
                "status": "NOT_RUN",
                "input_fasta": "",
                "output_fasta": "",
            }
            return [], "not_run", ["没有可比对序列"]
        if len(records) == 1:
            fasta = format_fasta(records)
            self.last_evidence = {
                "tool": "MAFFT",
                "status": "SKIPPED_SINGLE_SEQUENCE",
                "input_fasta": fasta,
                "output_fasta": fasta,
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
                return read_fasta(output_path, allow_gaps=True), "mafft_shannon", []
        equal_lengths = len({len(record.sequence) for record in records}) == 1
        if self.allow_fallback and equal_lengths:
            fasta = format_fasta(records)
            self.last_evidence = {
                "tool": "MAFFT",
                "status": "DEVELOPMENT_FALLBACK",
                "method": "prealigned_shannon_fallback",
                "input_fasta": fasta,
                "output_fasta": fasta,
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
            }
            return [records[0]], "full_length_fallback", ["MAFFT不可用且序列不等长；仅使用首条参考序列"]
        self.last_evidence = {
            "tool": "MAFFT",
            "status": "UNAVAILABLE",
            "input_fasta": format_fasta(records),
            "output_fasta": "",
        }
        raise ToolUnavailableError("MAFFT不可用", details={"executable": self.executable})


def _column_entropy(column: list[str]) -> float:
    bases = [base for base in column if base in "ACGT"]
    if not bases:
        return 2.0
    counts = Counter(bases)
    total = len(bases)
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


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

    aligned = [record.sequence.upper() for record in aligned_records]
    width = len(aligned[0])
    if any(len(sequence) != width for sequence in aligned):
        raise ValueError("aligned records must have equal length")
    entropies = [_column_entropy([sequence[index] for sequence in aligned]) for index in range(width)]
    scores = [max(0.0, 1.0 - entropy / 2.0) for entropy in entropies]

    for round_config in rounds:
        minimum_score = float(round_config["minimum_conservation"])
        minimum_length = int(round_config["minimum_length"])
        regions: list[dict] = []
        for start in range(0, max(0, width - minimum_length + 1)):
            window = scores[start : start + minimum_length]
            average = sum(window) / len(window)
            if average >= minimum_score:
                ungapped_start = sum(1 for char in aligned[0][:start] if char != "-")
                ungapped_length = sum(1 for char in aligned[0][start : start + minimum_length] if char != "-")
                if ungapped_length >= 50:
                    regions.append(
                        {
                            "start": ungapped_start,
                            "length": ungapped_length,
                            "end": ungapped_start + ungapped_length - 1,
                            "conservation_score": round(average, 6),
                            "label": round_config["label"],
                        }
                    )
        if regions:
            regions.sort(key=lambda item: (-item["conservation_score"], -item["length"], item["start"]))
            selected: list[dict] = []
            for region in regions:
                if all(abs(region["start"] - existing["start"]) >= 20 for existing in selected):
                    selected.append(region)
                if len(selected) >= maximum_regions:
                    break
            return selected, "mafft_shannon"

    reference = aligned_records[0].sequence.replace("-", "")
    return [
        {
            "start": 0,
            "length": len(reference),
            "end": len(reference) - 1,
            "conservation_score": round(sum(scores) / len(scores), 6),
            "label": "full_length_fallback",
        }
    ], "full_length_fallback"
