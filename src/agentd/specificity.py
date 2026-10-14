"""P2 specificity orchestration around C6 BLAST+ and P1 thermodynamics."""

from __future__ import annotations

import csv
import copy
import hashlib
import io
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .blast_cache import BlastResultCache
from .errors import AgentDError, InputValidationError, ToolUnavailableError
from .models import Oligo, SequenceRecord, SpecificityModeResult, SpecificityResult
from .primer3_tool import Primer3Adapter
from .sequence import format_fasta, normalize_sequence, read_fasta, reverse_complement


FASTA_SUFFIXES = {".fa", ".fasta", ".fna", ".fas"}

# BLAST E-values for short oligos scale with database size.  Keep target
# retrieval permissive, then let the target policy decide from identity,
# full-query coverage and 3' mismatches instead of using E-value as a gate.
SHORT_OLIGO_TARGET_SEARCH_EVALUE = 1000.0
# Retrieval must be at least as permissive as the lowest decision-table band.
# Otherwise BLAST can discard a CAUTION-level target hit before P2 sees it.
SHORT_OLIGO_TARGET_MIN_IDENTITY_PCT = 90.0
SHORT_OLIGO_TARGET_MIN_QUERY_COVERAGE_PCT = 90.0
SHORT_OLIGO_TARGET_MAX_HSPS_PER_SUBJECT = 100

# P2 is the only owner of specificity search and decision thresholds.  WF
# modules may map these results into their own rule/report contracts, but must
# not redefine or reinterpret the values below.
P2_POLICY_VERSION = "p2-specificity-contract-v7"
P2_POLICY_STATUS = "ACTIVE_FOR_IN_SILICO_RANKING_REQUIRES_EXPERIMENTAL_CALIBRATION"
P2_SCORE_BY_STATUS = {
    "PASS": 100.0,
    "CAUTION": 70.0,
    "FAIL": 0.0,
}
P2_THRESHOLDS: dict[str, dict[str, Any]] = {
    "TARGET_HIT": {
        "search_evalue": SHORT_OLIGO_TARGET_SEARCH_EVALUE,
        "decision_uses_evalue": False,
        "pass_identity_pct": 95.0,
        "pass_coverage_pct": 95.0,
        "pass_dg_lt_kcal": -9.0,
        "pass_three_prime_mismatches": 0,
        "caution_identity_pct_min": 90.0,
        "caution_coverage_pct_min": 90.0,
        "caution_dg_range_kcal": [-9.0, -6.0],
        "caution_three_prime_mismatches_max": 1,
    },
    "HOST_EXCLUSION": {
        "search_evalue": 0.1,
        "decision_evalue_lt": 0.1,
        "high_risk_dg_lt_kcal": -6.0,
        "high_risk_three_prime_mismatches_max": 1,
        "compatible_amplicon_span_max_bp": 1000,
    },
    "P5_PRIMARY_MATCH": {
        "search_evalue": 0.1,
        "pass_identity_pct": 95.0,
        "pass_coverage_pct": 90.0,
        "pass_hit_count_per_query": 1,
    },
    "RELATED_BACKGROUND_NT": {
        "search_evalue": 1e-5,
        "decision_evalue_lt": 1e-5,
    },
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


_ACCESSION_PREFIX_TOKENS = {
    "dbj",
    "emb",
    "gb",
    "gnl",
    "lcl",
    "ref",
}


def _accession_forms(value: Any) -> set[str]:
    """Return stable comparison forms for BLAST sequence identifiers."""

    raw = str(value or "").strip()
    if not raw:
        return set()
    candidates = {raw, raw.split(maxsplit=1)[0]}
    if "|" in raw:
        candidates.update(part.strip() for part in raw.split("|") if part.strip())
    forms: set[str] = set()
    for candidate in candidates:
        normalized = re.sub(r"\.\d+$", "", candidate.strip()).casefold()
        if normalized and normalized not in _ACCESSION_PREFIX_TOKENS:
            forms.add(normalized)
    return forms


class BlastAdapter:
    def __init__(
        self, executable: str = "blastn", *, allow_fallback: bool = False,
        runtime_config: dict | None = None,
    ) -> None:
        self.executable = executable
        self.allow_fallback = allow_fallback
        settings = runtime_config if runtime_config is not None else {}
        if not isinstance(settings, dict):
            raise InputValidationError("tools.blast_runtime 必须是对象")
        unknown = set(settings) - {"num_threads", "timeout_seconds", "mode_timeouts"}
        if unknown:
            raise InputValidationError("未知 BLAST 运行配置", details={"keys": sorted(unknown)})

        def positive_integer(value: Any, name: str, maximum: int) -> int:
            if type(value) is not int or not 1 <= value <= maximum:
                raise InputValidationError(f"BLAST {name} 必须是 1 到 {maximum} 的整数")
            return value

        self.num_threads = positive_integer(settings.get("num_threads", 1), "num_threads", 256)
        self.timeout_seconds = (None if settings.get("timeout_seconds", 600) is None else
                                positive_integer(settings.get("timeout_seconds", 600), "timeout_seconds", 86400))
        timeouts = settings.get("mode_timeouts", {})
        if not isinstance(timeouts, dict):
            raise InputValidationError("BLAST mode_timeouts 必须是对象")
        self.mode_timeouts = {}
        for mode, value in timeouts.items():
            if not isinstance(mode, str) or not mode.strip():
                raise InputValidationError("BLAST mode_timeouts 的模式名必须是非空字符串")
            self.mode_timeouts[mode.upper()] = (None if value is None else
                                              positive_integer(value, f"mode_timeouts.{mode}", 86400))
        self.last_search_evidence: dict = {}

    @property
    def available(self) -> bool:
        return shutil.which(self.executable) is not None

    def status(self) -> dict:
        return {
            "tool": "C6_BLAST+",
            "available": self.available,
            "executable": shutil.which(self.executable),
            "fallback_enabled": self.allow_fallback,
            "runtime_limits": {"num_threads": self.num_threads, "timeout_seconds": self.timeout_seconds, "mode_timeouts": dict(self.mode_timeouts)},
            "fallback_method": "exact_fasta_match" if self.allow_fallback else None,
        }

    def search(
        self,
        queries: dict[str, str],
        database: str | Path,
        *,
        evalue: float,
        mode: str,
    ) -> tuple[list[dict], str]:
        database_path = Path(database)
        if self.available:
            return self._blast_search(queries, database_path, evalue=evalue, mode=mode), "blastn"
        if self.allow_fallback and database_path.is_file() and database_path.suffix.lower() in FASTA_SUFFIXES:
            hits = self._exact_search(queries, database_path)
            self.last_search_evidence = {
                "tool": "BLAST+",
                "status": "DEVELOPMENT_FALLBACK",
                "mode": mode,
                "database": str(database_path),
                "evalue": evalue,
                "queries": copy.deepcopy(queries),
                "raw_output": copy.deepcopy(hits),
            }
            return hits, "exact_fasta_development_fallback"
        self.last_search_evidence = {
            "tool": "BLAST+",
            "status": "UNAVAILABLE",
            "mode": mode,
            "database": str(database_path),
            "evalue": evalue,
            "queries": copy.deepcopy(queries),
            "raw_output": None,
        }
        raise ToolUnavailableError(
            "BLAST+不可用或数据库未配置",
            details={"executable": self.executable, "database": str(database)},
        )

    def _blast_search(self, queries: dict[str, str], database: Path, *, evalue: float, mode: str) -> list[dict]:
        with tempfile.TemporaryDirectory(prefix="agentd-blast-") as temp_dir:
            query_path = Path(temp_dir) / "queries.fasta"
            query_path.write_text(
                format_fasta([SequenceRecord(id=query_id, sequence=sequence) for query_id, sequence in queries.items()]),
                encoding="utf-8",
            )
            command = [
                self.executable,
                "-task", "blastn-short",
                "-query", str(query_path),
                "-evalue", str(evalue),
                "-max_target_seqs", "100",
                "-outfmt", "6 qseqid sseqid pident length mismatch gapopen qstart qend sstart send evalue bitscore qseq sseq sstrand stitle",
            ]
            # The permissive target E-value is required for exact 20 nt hits
            # in larger databases (their E-value can be slightly above 1e-5).
            # Without an HSP-level quality filter, however, BLAST also emits
            # vast numbers of short local matches that the target policy can
            # never classify as PASS or CAUTION.  Filter those at the source so
            # they do not inflate M4 evidence and M6 reports by hundreds of MB.
            # These limits are identical to the target policy's lowest useful
            # identity and query-coverage thresholds; decision logic remains
            # unchanged.
            if mode.upper() in {"TARGET", "TARGET_HIT"}:
                command.extend(
                    [
                        "-perc_identity",
                        str(SHORT_OLIGO_TARGET_MIN_IDENTITY_PCT),
                        "-qcov_hsp_perc",
                        str(SHORT_OLIGO_TARGET_MIN_QUERY_COVERAGE_PCT),
                        "-max_hsps",
                        str(SHORT_OLIGO_TARGET_MAX_HSPS_PER_SUBJECT),
                    ]
                )
            if database.is_file() and database.suffix.lower() in FASTA_SUFFIXES:
                command.extend(["-subject", str(database)])
            else:
                command.extend(["-db", str(database)])
                if self.num_threads > 1:
                    command.extend(["-num_threads", str(self.num_threads)])
            timeout_seconds = self.mode_timeouts.get(mode.upper(), self.timeout_seconds)
            runtime_limits = {"timeout_seconds": timeout_seconds,
                              "num_threads": self.num_threads if "-db" in command else 1}
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=timeout_seconds,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                self.last_search_evidence = {
                    "tool": "BLAST+",
                    "status": (
                        "TIMED_OUT"
                        if isinstance(exc, subprocess.TimeoutExpired)
                        else "FAILED"
                    ),
                    "mode": mode,
                    "database": str(database),
                    "evalue": evalue,
                    "command": command,
                    "runtime_limits": runtime_limits,
                    "returncode": None,
                    "query_fasta": query_path.read_text(encoding="utf-8"),
                    "stdout": getattr(exc, "stdout", None),
                    "stderr": getattr(exc, "stderr", None),
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    },
                }
                raise ToolUnavailableError(
                    f"BLAST {mode}执行异常",
                    details=self.last_search_evidence["error"],
                ) from exc
            self.last_search_evidence = {
                "tool": "BLAST+",
                "status": "COMPLETED" if completed.returncode == 0 else "FAILED",
                "runtime_limits": runtime_limits,
                "mode": mode,
                "database": str(database),
                "evalue": evalue,
                "command": command,
                "returncode": completed.returncode,
                "query_fasta": query_path.read_text(encoding="utf-8"),
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
            if completed.returncode != 0:
                raise ToolUnavailableError(
                    f"BLAST {mode}执行失败",
                    details={"returncode": completed.returncode, "stderr": completed.stderr[-3000:]},
                )
            return self._parse_tabular(completed.stdout, queries)

    @staticmethod
    def _parse_tabular(output: str, queries: dict[str, str]) -> list[dict]:
        fields = [
            "query_id", "accession", "identity_pct", "alignment_length", "mismatches", "gap_opens",
            "query_start", "query_end", "subject_start", "subject_end", "evalue", "bitscore",
            "aligned_query", "aligned_subject", "strand", "title",
        ]
        hits: list[dict] = []
        reader = csv.reader(io.StringIO(output), delimiter="\t")
        for row in reader:
            if len(row) < len(fields):
                continue
            raw = dict(zip(fields, row))
            query_id = raw["query_id"]
            query = queries.get(query_id, "")
            query_length = len(query)
            aligned_query = raw["aligned_query"].upper()
            aligned_subject = raw["aligned_subject"].upper()
            covered_query_bases = sum(base != "-" for base in aligned_query)
            three_prime_mismatches = BlastAdapter._three_prime_mismatches(
                query,
                aligned_query,
                aligned_subject,
                int(raw["query_start"]),
                int(raw["query_end"]),
            )
            hit = {
                "query_id": query_id,
                "accession": raw["accession"],
                "identity_pct": float(raw["identity_pct"]),
                "alignment_length": int(raw["alignment_length"]),
                "query_coverage_pct": round(covered_query_bases / query_length * 100, 3) if query_length else 0.0,
                "mismatches": int(raw["mismatches"]),
                "gap_opens": int(raw["gap_opens"]),
                "query_start": int(raw["query_start"]),
                "query_end": int(raw["query_end"]),
                "subject_start": int(raw["subject_start"]),
                "subject_end": int(raw["subject_end"]),
                "evalue": float(raw["evalue"]),
                "bitscore": float(raw["bitscore"]),
                "hit_sequence": aligned_subject.replace("-", ""),
                "strand": raw["strand"],
                "title": raw["title"],
                "three_prime_mismatches": three_prime_mismatches,
                "thermodynamics": None,
                "raw_tabular_fields": raw,
            }
            hits.append(hit)
        return hits

    @staticmethod
    def _three_prime_mismatches(
        query: str,
        aligned_query: str,
        aligned_subject: str,
        query_start: int,
        query_end: int,
    ) -> int | None:
        """Count mismatches in the query primer's terminal five 3′ bases.

        Missing terminal coverage is counted as a mismatch so a partial BLAST
        alignment can never be promoted to a fully matched 3′ end.
        """
        if not query or len(aligned_query) != len(aligned_subject):
            return None
        step = 1 if query_end >= query_start else -1
        query_index = query_start - 1
        aligned_by_position: dict[int, tuple[str, str]] = {}
        for query_base, subject_base in zip(aligned_query, aligned_subject):
            if query_base == "-":
                continue
            aligned_by_position[query_index] = (query_base, subject_base)
            query_index += step
        terminal_positions = range(max(0, len(query) - 5), len(query))
        mismatches = 0
        for position in terminal_positions:
            aligned = aligned_by_position.get(position)
            if aligned is None or aligned[1] == "-" or aligned[0] != aligned[1]:
                mismatches += 1
        return mismatches

    @staticmethod
    def _exact_search(queries: dict[str, str], fasta_path: Path) -> list[dict]:
        records = read_fasta(fasta_path)
        hits: list[dict] = []
        for query_id, sequence in queries.items():
            query = normalize_sequence(sequence)
            alternatives = [(query, "+"), (reverse_complement(query), "-")]
            for record in records:
                subject = normalize_sequence(record.sequence, allow_iupac=False)
                for motif, strand in alternatives:
                    start = subject.find(motif)
                    while start >= 0:
                        hits.append(
                            {
                                "query_id": query_id,
                                "accession": record.id,
                                "identity_pct": 100.0,
                                "alignment_length": len(query),
                                "query_coverage_pct": 100.0,
                                "mismatches": 0,
                                "gap_opens": 0,
                                "query_start": 1,
                                "query_end": len(query),
                                "subject_start": start + 1,
                                "subject_end": start + len(query),
                                "strand": strand,
                                "evalue": None,
                                "bitscore": None,
                                "hit_sequence": motif,
                                "title": record.description,
                                "three_prime_mismatches": 0,
                                "thermodynamics": None,
                            }
                        )
                        start = subject.find(motif, start + 1)
        return hits


class SpecificityEvaluator:
    def __init__(self, blast: BlastAdapter, primer3: Primer3Adapter) -> None:
        self.blast = blast
        self.primer3 = primer3

    @staticmethod
    def _queries(forward: Oligo, reverse: Oligo, probe: Oligo | None) -> dict[str, str]:
        queries = {"forward": forward.sequence_5to3, "reverse": reverse.sequence_5to3}
        if probe is not None:
            queries["probe"] = probe.sequence_5to3
        return queries

    def _add_thermodynamics(self, hits: list[dict], queries: dict[str, str]) -> None:
        if not self.primer3.available:
            return
        for hit in hits:
            query = queries.get(hit["query_id"])
            context = hit.get("hit_sequence")
            if query and context:
                try:
                    # BLAST reports the aligned subject residues in the same
                    # orientation as the query. Primer3 needs the complementary
                    # strand to estimate primer-template duplex stability.
                    hit["thermodynamics"] = self.primer3.calc_heterodimer(
                        query,
                        reverse_complement(context),
                    )
                except InputValidationError:
                    hit["thermodynamics"] = None

    @staticmethod
    def _hit_dg(hit: dict) -> float | None:
        thermodynamics = hit.get("thermodynamics")
        if not isinstance(thermodynamics, dict):
            return None
        value = thermodynamics.get("dg_kcal")
        return float(value) if value is not None else None

    @staticmethod
    def _evalue_below(hit: dict, threshold: float) -> bool:
        value = hit.get("evalue")
        return value is not None and float(value) < threshold

    @classmethod
    def _target_hit_verdict(cls, hit: dict) -> str:
        threshold = P2_THRESHOLDS["TARGET_HIT"]
        identity = float(hit.get("identity_pct", 0.0))
        coverage = float(hit.get("query_coverage_pct", 0.0))
        dg = cls._hit_dg(hit)
        three_prime_mismatches = hit.get("three_prime_mismatches")
        if (
            identity >= threshold["pass_identity_pct"]
            and coverage >= threshold["pass_coverage_pct"]
            and dg is not None
            and dg < threshold["pass_dg_lt_kcal"]
            and three_prime_mismatches
            == threshold["pass_three_prime_mismatches"]
        ):
            return "PASS"
        if (
            identity >= threshold["caution_identity_pct_min"]
            and coverage >= threshold["caution_coverage_pct_min"]
        ):
            if dg is None:
                return "CAUTION"
            caution_dg_min, caution_dg_max = threshold["caution_dg_range_kcal"]
            if (
                threshold["caution_identity_pct_min"]
                <= identity
                < threshold["pass_identity_pct"]
                and caution_dg_min <= dg <= caution_dg_max
                and three_prime_mismatches is not None
                and three_prime_mismatches
                <= threshold["caution_three_prime_mismatches_max"]
            ):
                return "CAUTION"
            if (
                identity >= threshold["pass_identity_pct"]
                and dg >= threshold["pass_dg_lt_kcal"]
            ):
                return "CAUTION"
            if (
                three_prime_mismatches is not None
                and three_prime_mismatches
                <= threshold["caution_three_prime_mismatches_max"]
            ):
                return "CAUTION"
        return "FAIL"

    @classmethod
    def _perfect_target_hit(cls, hit: dict) -> bool:
        threshold = P2_THRESHOLDS["TARGET_HIT"]
        return (
            float(hit.get("identity_pct", 0.0)) == 100.0
            and float(hit.get("query_coverage_pct", 0.0)) == 100.0
            and hit.get("three_prime_mismatches")
            == threshold["pass_three_prime_mismatches"]
            and cls._hit_dg(hit) is not None
            and float(cls._hit_dg(hit)) < threshold["pass_dg_lt_kcal"]
        )

    @staticmethod
    def _development_exact_hit(hit: dict) -> bool:
        return (
            float(hit.get("identity_pct", 0.0)) == 100.0
            and float(hit.get("query_coverage_pct", 0.0)) == 100.0
            and hit.get("three_prime_mismatches") == 0
        )

    @staticmethod
    def _scientific_name(hit: dict) -> str | None:
        explicit = str(hit.get("scientific_name") or "").strip()
        if explicit:
            return explicit
        bracketed = re.findall(r"\[([A-Z][a-z]+(?:\s+[a-z][A-Za-z0-9_.-]*)+)\]", str(hit.get("title") or ""))
        return bracketed[-1] if bracketed else None

    @staticmethod
    def _compatible_host_amplicons(
        hits: list[dict],
        maximum_span_bp: int = int(
            P2_THRESHOLDS["HOST_EXCLUSION"]["compatible_amplicon_span_max_bp"]
        ),
    ) -> list[dict]:
        grouped: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
        for hit in hits:
            query_id = str(hit.get("query_id") or "")
            if query_id not in {"forward", "reverse"}:
                continue
            grouped[str(hit.get("accession") or "")][query_id].append(hit)
        compatible: list[dict] = []
        for accession, roles in grouped.items():
            for forward in roles.get("forward", []):
                for reverse in roles.get("reverse", []):
                    forward_position = min(int(forward["subject_start"]), int(forward["subject_end"]))
                    reverse_position = min(int(reverse["subject_start"]), int(reverse["subject_end"]))
                    span = abs(reverse_position - forward_position)
                    if span <= maximum_span_bp:
                        compatible.append(
                            {
                                "accession": accession,
                                "forward_position": forward_position,
                                "reverse_position": reverse_position,
                                "span_bp": span,
                            }
                        )
        return compatible

    def evaluate_target_hits(
        self,
        queries: dict[str, str],
        hits: list[dict],
        *,
        method: str,
    ) -> SpecificityModeResult:
        """Apply the P2 target decision table to already collected hits."""

        required = set(queries)
        per_query: dict[str, str] = {}
        perfect_query_ids: list[str] = []
        for query_id, sequence in queries.items():
            query_hits = [hit for hit in hits if hit.get("query_id") == query_id]
            hit_verdicts = [self._target_hit_verdict(hit) for hit in query_hits]
            if "PASS" in hit_verdicts:
                per_query[query_id] = "PASS"
            elif (
                "CAUTION" in hit_verdicts
                or (
                    method != "blastn"
                    and any(self._development_exact_hit(hit) for hit in query_hits)
                )
                or (query_id == "probe" and len(sequence) < 15)
            ):
                per_query[query_id] = "CAUTION"
            else:
                per_query[query_id] = "FAIL"
            if any(self._perfect_target_hit(hit) for hit in query_hits):
                perfect_query_ids.append(query_id)
        if "FAIL" in per_query.values():
            verdict = "FAIL"
            recommendation = "至少一个引物或探针未在靶标数据库中获得符合P2判定表的命中"
        elif method != "blastn":
            verdict = "CAUTION"
            recommendation = "当前仅完成开发降级匹配，仍需BLAST+与P1热力学联合验证"
        elif "CAUTION" in per_query.values():
            verdict = "CAUTION"
            recommendation = "靶标命中存在弱结合、低覆盖、错配或短探针BLAST灵敏度限制"
        else:
            verdict = "PASS"
            recommendation = None
        return SpecificityModeResult(
            mode="check_target",
            verdict=verdict,
            blast_hits=len(hits),
            details=hits,
            recommendation=recommendation,
            mode_specific={
                "required_query_ids": sorted(required),
                "per_query_verdict": per_query,
                "perfect_query_ids": sorted(perfect_query_ids),
            },
            method=method,
        )

    def check_target(
        self,
        queries: dict[str, str],
        database: str | Path | None,
    ) -> SpecificityModeResult:
        if not database:
            return SpecificityModeResult(
                mode="check_target", verdict="NOT_CHECKED", blast_hits=0,
                recommendation="配置靶标BLAST数据库后重新验证", method="not_run",
            )
        hits, method = self.blast.search(
            queries,
            database,
            evalue=SHORT_OLIGO_TARGET_SEARCH_EVALUE,
            mode="target",
        )
        self._add_thermodynamics(hits, queries)
        return self.evaluate_target_hits(queries, hits, method=method)

    def evaluate_host_hits(
        self,
        queries: dict[str, str],
        hits: list[dict],
        *,
        method: str,
        host_species: str | None,
    ) -> SpecificityModeResult:
        """Apply the P2 host-exclusion table to already collected hits."""

        threshold = P2_THRESHOLDS["HOST_EXCLUSION"]
        relevant_hits = [
            hit
            for hit in hits
            if self._evalue_below(hit, threshold["decision_evalue_lt"])
        ]
        high_risk_hits = [
            hit
            for hit in relevant_hits
            if self._hit_dg(hit) is not None
            and float(self._hit_dg(hit)) < threshold["high_risk_dg_lt_kcal"]
            and hit.get("three_prime_mismatches") is not None
            and int(hit["three_prime_mismatches"])
            <= threshold["high_risk_three_prime_mismatches_max"]
        ]
        compatible_amplicons = self._compatible_host_amplicons(
            relevant_hits,
            int(threshold["compatible_amplicon_span_max_bp"]),
        )
        hit_counts = defaultdict(int)
        for hit in relevant_hits:
            hit_counts[str(hit.get("query_id") or "")] += 1
        repeat_like_queries = sorted(
            query_id for query_id, count in hit_counts.items() if count > 10
        )
        high_risk_query_ids = sorted(
            {
                str(hit.get("query_id") or "")
                for hit in high_risk_hits
                if str(hit.get("query_id") or "")
            }
        )
        if compatible_amplicons:
            verdict = "FAIL"
            recommendation = (
                f"在宿主{host_species or ''}数据库发现"
                f"{len(compatible_amplicons)}个兼容扩增位点，请更换区域"
            )
        elif high_risk_hits:
            verdict = "CAUTION"
            recommendation = (
                "发现单条高风险宿主命中，但尚无成对兼容证据；"
                "保留为警告并人工复核"
            )
        elif relevant_hits:
            verdict = "CAUTION"
            recommendation = "存在宿主命中，但结合较弱或3′端错配较多；按P2规则保留并人工复核"
        elif method != "blastn":
            verdict = "CAUTION"
            recommendation = "精确匹配未发现宿主命中，但仍需BLAST+验证"
        else:
            verdict = "PASS"
            recommendation = None
        return SpecificityModeResult(
            mode="check_host",
            verdict=verdict,
            blast_hits=len(hits),
            details=hits,
            recommendation=recommendation,
            mode_specific={
                "host_species": host_species,
                "high_risk_hit_count": len(high_risk_hits),
                "high_risk_query_ids": high_risk_query_ids,
                "compatible_amplicons": compatible_amplicons,
                "repeat_like_query_ids": repeat_like_queries,
            },
            method=method,
        )

    def check_host(
        self,
        queries: dict[str, str],
        database: str | Path | None,
        host_species: str | None,
    ) -> SpecificityModeResult:
        if not database:
            return SpecificityModeResult(
                mode="check_host", verdict="NOT_CHECKED", blast_hits=0,
                recommendation="配置宿主基因组数据库后重新验证",
                mode_specific={"host_species": host_species}, method="not_run",
            )
        hits, method = self.blast.search(
            queries,
            database,
            evalue=P2_THRESHOLDS["HOST_EXCLUSION"]["search_evalue"],
            mode="host",
        )
        self._add_thermodynamics(hits, queries)
        return self.evaluate_host_hits(
            queries,
            hits,
            method=method,
            host_species=host_species,
        )

    def evaluate_p5_primary_hits(
        self,
        queries: dict[str, str],
        hits: list[dict],
        *,
        method: str,
    ) -> SpecificityModeResult:
        """Apply the P2-owned P5 primary-match policy to collected hits."""

        threshold = P2_THRESHOLDS["P5_PRIMARY_MATCH"]
        qualifying = [
            hit
            for hit in hits
            if float(hit.get("identity_pct", 0.0))
            >= float(threshold["pass_identity_pct"])
            and float(hit.get("query_coverage_pct", 0.0))
            >= float(threshold["pass_coverage_pct"])
        ]
        counts = {
            query_id: sum(
                1 for hit in qualifying if hit.get("query_id") == query_id
            )
            for query_id in queries
        }
        missing = sorted(
            query_id for query_id, count in counts.items() if count == 0
        )
        non_unique = sorted(
            query_id
            for query_id, count in counts.items()
            if count > int(threshold["pass_hit_count_per_query"])
        )
        if missing:
            verdict = "FAIL"
            recommendation = "P5_PRIMARY_MATCH_REQUIRED_QUERY_MISSING"
        elif non_unique:
            verdict = "CAUTION"
            recommendation = "P5_PRIMARY_MATCH_MULTIPLE_HITS_REQUIRE_REVIEW"
        elif method != "blastn":
            verdict = "CAUTION"
            recommendation = "P5_PRIMARY_MATCH_REQUIRES_FORMAL_P2_EXECUTION"
        else:
            verdict = "PASS"
            recommendation = None
        return SpecificityModeResult(
            mode="check_host",
            verdict=verdict,
            blast_hits=len(hits),
            details=hits,
            recommendation=recommendation,
            mode_specific={
                "per_query_hit_count": counts,
                "missing_query_ids": missing,
                "non_unique_query_ids": non_unique,
            },
            method=method,
        )

    def evaluate_cross_species_hits(
        self,
        queries: dict[str, str],
        hits: list[dict],
        *,
        method: str,
        target_species: str | None,
        target_accessions: set[str] | None = None,
    ) -> SpecificityModeResult:
        """Apply the P2 cross-species table to already collected hits."""

        genus = target_species.split()[0] if target_species and len(target_species.split()) >= 2 else None
        target_accession_forms = {
            form
            for value in (target_accessions or set())
            for form in _accession_forms(value)
        }
        threshold = P2_THRESHOLDS["RELATED_BACKGROUND_NT"]
        relevant_hits = [
            hit
            for hit in hits
            if self._evalue_below(hit, threshold["decision_evalue_lt"])
        ]
        normalized_target = " ".join((target_species or "").lower().split())
        cross_hits: list[dict] = []
        species_names: set[str] = set()
        same_genus_names: set[str] = set()
        excluded_target_hit_count = 0
        for hit in relevant_hits:
            hit_accession_forms = _accession_forms(hit.get("accession"))
            if target_accession_forms & hit_accession_forms:
                excluded_target_hit_count += 1
                continue
            scientific_name = self._scientific_name(hit)
            normalized_name = " ".join((scientific_name or "").lower().split())
            if normalized_target and normalized_name == normalized_target:
                excluded_target_hit_count += 1
                continue
            cross_hits.append(hit)
            if scientific_name:
                species_names.add(scientific_name)
                if genus and scientific_name.split()[0].lower() == genus.lower():
                    same_genus_names.add(scientific_name)
        verdict = "CAUTION" if cross_hits else ("PASS" if method == "blastn" else "CAUTION")
        recommendation = "发现近缘种或背景病原命中，请确认检测边界" if cross_hits else (None if method == "blastn" else "仍需BLAST+复核")
        return SpecificityModeResult(
            mode="check_cross_species", verdict=verdict, blast_hits=len(cross_hits), details=cross_hits,
            recommendation=recommendation,
            mode_specific={
                "target_species": target_species,
                "target_genus": genus,
                "excluded_target_hit_count": excluded_target_hit_count,
                "cross_species": sorted(species_names),
                "same_genus_species": sorted(same_genus_names),
            },
            method=method,
        )

    def check_cross_species(
        self,
        queries: dict[str, str],
        database: str | Path | None,
        target_species: str | None,
        target_accessions: set[str] | None = None,
    ) -> SpecificityModeResult:
        genus = target_species.split()[0] if target_species and len(target_species.split()) >= 2 else None
        if not database:
            return SpecificityModeResult(
                mode="check_cross_species", verdict="NOT_CHECKED", blast_hits=0,
                recommendation="配置近缘种/共感染病原数据库后重新验证",
                mode_specific={"target_species": target_species, "target_genus": genus}, method="not_run",
            )
        hits, method = self.blast.search(
            queries,
            database,
            evalue=P2_THRESHOLDS["RELATED_BACKGROUND_NT"]["search_evalue"],
            mode="cross_species",
        )
        self._add_thermodynamics(hits, queries)
        return self.evaluate_cross_species_hits(
            queries,
            hits,
            method=method,
            target_species=target_species,
            target_accessions=target_accessions,
        )

    def evaluate(
        self,
        forward: Oligo,
        reverse: Oligo,
        probe: Oligo | None,
        *,
        target_db: str | Path | None,
        host_db: str | Path | None,
        cross_db: str | Path | None,
        host_species: str | None,
        target_species: str | None,
    ) -> SpecificityResult:
        queries = self._queries(forward, reverse, probe)
        target = self.check_target(queries, target_db)
        host = self.check_host(queries, host_db, host_species)
        target_accessions = {
            str(hit.get("accession") or "").strip()
            for hit in target.details
            if str(hit.get("accession") or "").strip()
        }
        cross = self.check_cross_species(
            queries,
            cross_db,
            target_species,
            target_accessions=target_accessions,
        )
        verdicts = {target.verdict, host.verdict, cross.verdict}
        if "FAIL" in verdicts:
            rating = "FAIL"
        elif verdicts == {"PASS"}:
            rating = "PASS"
        else:
            rating = "PASS_WITH_WARNINGS"
        methods = {target.method, host.method, cross.method}
        overall_method = "blastn" if methods == {"blastn"} else "mixed_or_degraded"
        warnings = [
            item.recommendation
            for item in (target, host, cross)
            if item.recommendation
        ]
        return SpecificityResult(
            target=target,
            host=host,
            cross_species=cross,
            rating=rating,
            method=overall_method,
            warnings=warnings,
        )


def _p2_resource_public(resource: dict[str, Any] | None) -> dict[str, Any] | None:
    if not resource:
        return None
    return {
        "resource_key": resource.get("key"),
        "kind": resource.get("kind"),
        "path": resource.get("path"),
        "uri": resource.get("uri"),
        "version": resource.get("version"),
        "version_valid": resource.get("version_valid"),
        "exists": resource.get("exists"),
        "scope": resource.get("scope"),
        "resolution_status": resource.get("resolution_status"),
        "resolution_reason": resource.get("resolution_reason"),
        "version_semantics": resource.get("version_semantics"),
        "record_count": resource.get("record_count"),
    }


def _p2_candidate_hits(
    hits: list[dict[str, Any]],
    query_ids: dict[str, str],
) -> list[dict[str, Any]]:
    by_id = {query_id: role for role, query_id in query_ids.items()}
    selected: list[dict[str, Any]] = []
    for hit in hits:
        query_id = str(hit.get("query_id") or "")
        if query_id not in by_id:
            continue
        normalized = copy.deepcopy(hit)
        normalized["batch_query_id"] = query_id
        normalized["query_id"] = by_id[query_id]
        selected.append(normalized)
    return selected


def p2_not_evaluated_mode(
    mode: str,
    *,
    objects: list[dict[str, Any]],
    resource: dict[str, Any] | None,
    reason: str,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an explicit P2 non-result without inventing a verdict."""

    return {
        "mode": mode,
        "status": "NOT_EVALUATED",
        "objects": copy.deepcopy(objects),
        "database": _p2_resource_public(resource),
        "thresholds": copy.deepcopy(P2_THRESHOLDS[mode]),
        "method": "not_run",
        "queries": [],
        "hits": [],
        "reason": reason,
        "error": copy.deepcopy(error),
        "evaluated_at": _utc_now(),
        "policy_version": P2_POLICY_VERSION,
    }


def p2_not_applicable_mode(
    mode: str,
    *,
    reason: str,
    objects: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result = {
        "mode": mode,
        "status": "NOT_APPLICABLE",
        "objects": copy.deepcopy(objects or []),
        "database": None,
        "thresholds": copy.deepcopy(P2_THRESHOLDS[mode]),
        "method": "not_run",
        "queries": [],
        "hits": [],
        "reason": reason,
        "error": None,
        "evaluated_at": _utc_now(),
        "policy_version": P2_POLICY_VERSION,
    }
    if mode == "HOST_EXCLUSION":
        result["object_results"] = []
    return result


def p2_design_only_result() -> dict[str, Any]:
    """Represent the deliberate absence of P2 on WF-01 DESIGN_ONLY."""

    return {
        "policy_version": P2_POLICY_VERSION,
        "policy_status": P2_POLICY_STATUS,
        "overall_status": "NOT_EVALUATED",
        "specificity_score": None,
        "required_modes": ["TARGET_HIT", "RELATED_BACKGROUND_NT"],
        "optional_modes": ["HOST_EXCLUSION"],
        "host_exclusion_requested": False,
        "target_hit": p2_not_evaluated_mode(
            "TARGET_HIT",
            objects=[],
            resource=None,
            reason="TARGET_OBJECT_MISSING",
        ),
        "host_exclusion": p2_not_applicable_mode(
            "HOST_EXCLUSION",
            reason="HOST_NOT_PROVIDED_OPTIONAL_CHECK_SKIPPED",
        ),
        "related_background_nt": p2_not_evaluated_mode(
            "RELATED_BACKGROUND_NT",
            objects=[],
            resource=None,
            reason="TARGET_OBJECT_MISSING",
        ),
        "reason": "P2_NOT_CALLED_ON_DESIGN_ONLY",
    }


class P2BatchSearchProvider:
    """Run one P2 BLAST/P1 batch per specificity mode."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        allow_development_fallback: bool = False,
        blast_adapter: BlastAdapter | None = None,
        primer3_adapter: Primer3Adapter | None = None,
    ) -> None:
        executable = config.get("tools", {}).get("blastn_executable", "blastn")
        self.blast = blast_adapter or BlastAdapter(
            executable,
            allow_fallback=allow_development_fallback,
            runtime_config=config.get("tools", {}).get("blast_runtime"),
        )
        self.cache = BlastResultCache(config.get("tools", {}).get("blast_cache"))
        self.primer3 = primer3_adapter or Primer3Adapter(
            config,
            allow_fallback=allow_development_fallback,
        )

    def status(self) -> dict[str, Any]:
        return {
            "tool": "P2_SPECIFICITY",
            "blast": self.blast.status(),
            "primer3": self.primer3.status(),
            "policy_version": P2_POLICY_VERSION,
        }

    def _add_thermodynamics(
        self,
        hits: list[dict[str, Any]],
        queries: dict[str, str],
    ) -> None:
        if not self.primer3.available:
            return
        for hit in hits:
            query = queries.get(str(hit.get("query_id") or ""))
            context = hit.get("hit_sequence")
            if not query or not context:
                continue
            try:
                hit["thermodynamics"] = self.primer3.calc_heterodimer(
                    query,
                    reverse_complement(str(context)),
                )
            except InputValidationError:
                hit["thermodynamics"] = None

    def search(
        self,
        queries: dict[str, str],
        resource: dict[str, Any],
        *,
        mode: str,
        evalue: float,
    ) -> dict[str, Any]:
        if resource.get("kind") == "inline_fasta":
            raw_records = resource.get("records")
            if not isinstance(raw_records, list) or not raw_records:
                raise InputValidationError(
                    "P2 inline resource does not contain executable records",
                    details={"resource_key": resource.get("key")},
                )
            records: list[SequenceRecord] = []
            for ordinal, value in enumerate(raw_records, start=1):
                if not isinstance(value, dict):
                    raise InputValidationError(
                        "P2 inline resource contains an invalid record",
                        details={
                            "resource_key": resource.get("key"),
                            "record_ordinal": ordinal,
                        },
                    )
                sequence = normalize_sequence(str(value.get("sequence") or ""))
                checksum = hashlib.sha256(sequence.encode("ascii")).hexdigest()
                expected_checksum = str(
                    value.get("checksum_sha256") or ""
                ).strip()
                if expected_checksum and expected_checksum != checksum:
                    raise InputValidationError(
                        "P2 inline resource checksum does not match its record",
                        details={
                            "resource_key": resource.get("key"),
                            "record_ordinal": ordinal,
                        },
                    )
                raw_id = re.sub(
                    r"[^A-Za-z0-9_.-]+",
                    "_",
                    str(value.get("id") or "record"),
                ).strip("_")
                records.append(
                    SequenceRecord(
                        id=f"input_{ordinal:04d}_{raw_id or 'record'}",
                        sequence=sequence,
                    )
                )
            with tempfile.TemporaryDirectory(
                prefix="agentd-p2-inline-target-"
            ) as temp_dir:
                database_path = Path(temp_dir) / "input-snapshot.fasta"
                database_path.write_text(
                    format_fasta(records),
                    encoding="utf-8",
                )
                return self._search_path(
                    queries,
                    resource,
                    database_path,
                    mode=mode,
                    evalue=evalue,
                    temporary_materialization=True,
                )
        return self._search_path(
            queries,
            resource,
            str(resource["path"]),
            mode=mode,
            evalue=evalue,
            temporary_materialization=False,
        )

    def _search_path(
        self,
        queries: dict[str, str],
        resource: dict[str, Any],
        database_path: str | Path,
        *,
        mode: str,
        evalue: float,
        temporary_materialization: bool,
    ) -> dict[str, Any]:
        def compute():
            hits, method = self.blast.search(
                queries, str(database_path), evalue=evalue, mode=mode,
            )
            return {"hits": hits, "method": method,
                    "evidence": copy.deepcopy(getattr(self.blast, "last_search_evidence", None)) or {}}

        result = self.cache.run(
            lambda: self.cache.identity(queries, resource, self.blast, mode, evalue), compute,
        )
        hits, method, evidence = result["hits"], result["method"], result["evidence"]
        self.blast.last_search_evidence = copy.deepcopy(evidence)
        # Recompute P1 evidence with this task's configuration, even on cache hits.
        self._add_thermodynamics(hits, queries)
        if not isinstance(evidence, dict) or not evidence:
            evidence = {
                "tool": "BLAST+",
                "status": "COMPLETED",
                "database": str(database_path),
                "mode": mode,
                "evalue": evalue,
                "queries": copy.deepcopy(queries),
                "raw_output": copy.deepcopy(hits),
            }
        if temporary_materialization:
            evidence["database"] = resource.get("key")
            evidence["database_resource"] = _p2_resource_public(resource)
            evidence["materialization"] = "TASK_LOCAL_TEMPORARY_FASTA"
        return {
            "hits": hits,
            "method": method,
            "tool": self.status(),
            "raw_tool_output": evidence,
        }


class P2SpecificityBatchService:
    """Authoritative P2 batch contract consumed by WF-01 M4 and P5.

    P2 owns BLAST/P1 execution, hit-level thresholds and three-mode verdicts.
    Callers supply versioned resources and candidate query mappings, then store
    and map this result without re-deciding specificity.
    """

    def __init__(
        self,
        config: dict[str, Any],
        *,
        allow_development_fallback: bool = False,
        provider: Any | None = None,
        remote_background_provider: Any | None = None,
    ) -> None:
        self.provider = provider or P2BatchSearchProvider(
            config,
            allow_development_fallback=allow_development_fallback,
        )
        self.remote_background_provider = remote_background_provider
        primer3 = getattr(self.provider, "primer3", None)
        blast = getattr(self.provider, "blast", None)
        self.evaluator = SpecificityEvaluator(
            blast or BlastAdapter(allow_fallback=allow_development_fallback),
            primer3 or Primer3Adapter(
                config,
                allow_fallback=allow_development_fallback,
            ),
        )

    def status(self) -> dict[str, Any]:
        provider_status = (
            self.provider.status()
            if callable(getattr(self.provider, "status", None))
            else {"tool": type(self.provider).__name__}
        )
        provider_status = (
            copy.deepcopy(provider_status)
            if isinstance(provider_status, dict)
            else {}
        )
        blast_status = provider_status.get("blast") or {}
        primer3_status = provider_status.get("primer3") or {}
        available = bool(
            blast_status.get("available")
            and primer3_status.get("available")
        )
        return {
            "tool": "P2_SPECIFICITY",
            "available": available,
            "policy_version": P2_POLICY_VERSION,
            "provider": provider_status,
        }

    @staticmethod
    def _resource_ready(resource: dict[str, Any] | None) -> bool:
        return bool(resource and resource.get("resolution_status") == "RESOLVED")

    def _get_remote_background_provider(self) -> Any:
        if self.remote_background_provider is None:
            # Lazy import avoids the remote adapter's dependency on
            # BlastAdapter forming a module-import cycle.
            from .remote_blast import NCBIBlastMCPProvider

            self.remote_background_provider = NCBIBlastMCPProvider()
        return self.remote_background_provider

    @staticmethod
    def _remote_provider_status(
        provider: Any,
        *,
        status: str,
        reason: str,
    ) -> dict[str, Any]:
        value = (
            provider.status()
            if callable(getattr(provider, "status", None))
            else {"tool": type(provider).__name__}
        )
        result = copy.deepcopy(value) if isinstance(value, dict) else {}
        result["status"] = status
        result["reason"] = reason
        return result

    def _search(
        self,
        queries: dict[str, str],
        resource: dict[str, Any] | None,
        *,
        mode: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if not self._resource_ready(resource):
            return None, None
        try:
            result = self.provider.search(
                queries,
                resource,
                mode=mode,
                evalue=float(P2_THRESHOLDS[mode]["search_evalue"]),
            )
            if (
                not isinstance(result, dict)
                or not isinstance(result.get("hits"), list)
                or not str(result.get("method") or "").strip()
            ):
                return None, {
                    "code": "P2_INVALID_RESULT",
                    "message": "P2 特异性工具返回结构不完整",
                    "details": {"mode": mode},
                }
            result.setdefault(
                "raw_tool_output",
                {
                    "tool": "P2/BLAST+",
                    "status": "COMPLETED",
                    "mode": mode,
                    "database": copy.deepcopy(resource),
                    "evalue": P2_THRESHOLDS[mode]["search_evalue"],
                    "queries": copy.deepcopy(queries),
                    "raw_output": copy.deepcopy(result["hits"]),
                },
            )
            return result, None
        except AgentDError as exc:
            error = exc.to_dict()
        except (
            OSError,
            RuntimeError,
            UnicodeError,
            ValueError,
            subprocess.TimeoutExpired,
        ) as exc:
            error = {
                "code": "P2_RUNTIME_ERROR",
                "message": str(exc),
                "details": {
                    "exception_type": type(exc).__name__,
                    "mode": mode,
                },
            }
        blast = getattr(self.provider, "blast", None)
        evidence = getattr(blast, "last_search_evidence", None)
        if isinstance(evidence, dict) and evidence:
            error["raw_tool_output"] = copy.deepcopy(evidence)
        return None, error

    def _search_related_background(
        self,
        queries: dict[str, str],
        resource: dict[str, Any] | None,
        *,
        target_object: dict[str, Any],
    ) -> tuple[
        dict[str, Any] | None,
        dict[str, Any] | None,
        dict[str, Any] | None,
        dict[str, Any],
    ]:
        if self._resource_ready(resource):
            result, error = self._search(
                queries,
                resource,
                mode="RELATED_BACKGROUND_NT",
            )
            return result, error, resource, {
                "status": "NOT_INITIALIZED",
                "reason": "LOCAL_RELATED_BACKGROUND_USED",
            }

        remote = self._get_remote_background_provider()
        if not bool(getattr(remote, "configured", False)):
            return None, None, resource, self._remote_provider_status(
                remote,
                status="NOT_CONFIGURED",
                reason="LOCAL_RELATED_BACKGROUND_NOT_RESOLVED",
            )

        scope = copy.deepcopy((resource or {}).get("scope") or {})
        scope.update(
            {
                "role": "RELATED_BACKGROUND",
                "mode": "BROAD_REMOTE_BACKGROUND",
                "target_object_id": target_object.get("object_id"),
            }
        )
        effective_resource = remote.resource(scope=scope)
        exclusions = [
            str(target_object.get("scientific_name") or "").strip()
        ]
        exclusions = [value for value in exclusions if value]
        try:
            result = remote.search(
                queries,
                evalue=float(
                    P2_THRESHOLDS["RELATED_BACKGROUND_NT"][
                        "search_evalue"
                    ]
                ),
                exclude_organisms=exclusions,
            )
            if (
                not isinstance(result, dict)
                or not isinstance(result.get("hits"), list)
                or str(result.get("method") or "") != "blastn"
            ):
                error = {
                    "code": "P2_INVALID_REMOTE_RESULT",
                    "message": "P2 remote provider returned an invalid result",
                    "details": {"mode": "RELATED_BACKGROUND_NT"},
                }
                return (
                    None,
                    error,
                    effective_resource,
                    self._remote_provider_status(
                        remote,
                        status="ERROR",
                        reason="REMOTE_RESULT_INVALID",
                    ),
                )
            result.setdefault(
                "raw_tool_output",
                {
                    "tool": type(remote).__name__,
                    "status": "COMPLETED",
                    "mode": "RELATED_BACKGROUND_NT",
                    "database": copy.deepcopy(effective_resource),
                    "evalue": P2_THRESHOLDS["RELATED_BACKGROUND_NT"][
                        "search_evalue"
                    ],
                    "queries": copy.deepcopy(queries),
                    "raw_output": copy.deepcopy(result["hits"]),
                },
            )
            return (
                result,
                None,
                effective_resource,
                self._remote_provider_status(
                    remote,
                    status="USED",
                    reason="LOCAL_RELATED_BACKGROUND_NOT_RESOLVED",
                ),
            )
        except AgentDError as exc:
            error = exc.to_dict()
        except (
            OSError,
            RuntimeError,
            UnicodeError,
            ValueError,
            subprocess.TimeoutExpired,
        ) as exc:
            error = {
                "code": "P2_REMOTE_RUNTIME_ERROR",
                "message": str(exc),
                "details": {
                    "exception_type": type(exc).__name__,
                    "mode": "RELATED_BACKGROUND_NT",
                },
            }
        evidence = getattr(remote, "last_search_evidence", None)
        if isinstance(evidence, dict) and evidence:
            error["raw_tool_output"] = copy.deepcopy(evidence)
        return (
            None,
            error,
            effective_resource,
            self._remote_provider_status(
                remote,
                status="ERROR",
                reason="REMOTE_SEARCH_FAILED",
            ),
        )

    @staticmethod
    def _mode_result(
        mode: str,
        result: SpecificityModeResult,
        *,
        objects: list[dict[str, Any]],
        resource: dict[str, Any],
        queries: dict[str, str],
    ) -> dict[str, Any]:
        status = (
            "NOT_EVALUATED"
            if result.verdict == "NOT_CHECKED"
            else result.verdict
        )
        return {
            "mode": mode,
            "status": status,
            "objects": copy.deepcopy(objects),
            "database": _p2_resource_public(resource),
            "thresholds": copy.deepcopy(P2_THRESHOLDS[mode]),
            "method": result.method,
            "queries": [
                {"role": role, "sequence_5to3": sequence}
                for role, sequence in queries.items()
            ],
            "hits": copy.deepcopy(result.details),
            "reason": result.recommendation or f"P2_{mode}_{status}",
            "mode_specific": copy.deepcopy(result.mode_specific),
            "error": None,
            "evaluated_at": _utc_now(),
            "policy_version": P2_POLICY_VERSION,
        }

    @staticmethod
    def _overall_status(mode_results: list[dict[str, Any]]) -> str:
        statuses = {result["status"] for result in mode_results}
        if "FAIL" in statuses:
            return "FAIL"
        if "NOT_EVALUATED" in statuses:
            return "NOT_EVALUATED"
        if "CAUTION" in statuses:
            return "CAUTION"
        return "PASS"

    def _target_result(
        self,
        *,
        role_queries: dict[str, str],
        query_ids: dict[str, str],
        target_object: dict[str, Any],
        target_resource: dict[str, Any] | None,
        target_search: dict[str, Any] | None,
        target_error: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not target_object:
            return p2_not_evaluated_mode(
                "TARGET_HIT",
                objects=[],
                resource=target_resource,
                reason="TARGET_OBJECT_MISSING",
            )
        if not self._resource_ready(target_resource):
            return p2_not_evaluated_mode(
                "TARGET_HIT",
                objects=[target_object],
                resource=target_resource,
                reason="TARGET_DATABASE_OR_VERSION_NOT_RESOLVED",
            )
        if target_error:
            return p2_not_evaluated_mode(
                "TARGET_HIT",
                objects=[target_object],
                resource=target_resource,
                reason="TARGET_SEARCH_TOOL_ERROR",
                error=target_error,
            )
        if not target_search or str(target_search["method"]) != "blastn":
            result = p2_not_evaluated_mode(
                "TARGET_HIT",
                objects=[target_object],
                resource=target_resource,
                reason="DEVELOPMENT_FALLBACK_CANNOT_PROVE_TARGET_HIT",
            )
            if target_search:
                result["hits"] = _p2_candidate_hits(
                    target_search["hits"],
                    query_ids,
                )
                result["method"] = str(target_search["method"])
            return result
        evaluated = self.evaluator.evaluate_target_hits(
            role_queries,
            _p2_candidate_hits(target_search["hits"], query_ids),
            method=str(target_search["method"]),
        )
        return self._mode_result(
            "TARGET_HIT",
            evaluated,
            objects=[target_object],
            resource=target_resource,
            queries=role_queries,
        )

    def _host_result(
        self,
        *,
        role_queries: dict[str, str],
        query_ids: dict[str, str],
        host_objects: list[dict[str, Any]],
        host_searches: list[
            tuple[
                dict[str, Any],
                dict[str, Any] | None,
                dict[str, Any] | None,
                dict[str, Any] | None,
            ]
        ],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        object_results: list[dict[str, Any]] = []
        for object_, resource, search, error in host_searches:
            if not self._resource_ready(resource):
                result = p2_not_evaluated_mode(
                    "HOST_EXCLUSION",
                    objects=[object_],
                    resource=resource,
                    reason="HOST_DATABASE_OR_VERSION_NOT_RESOLVED",
                )
            elif error:
                result = p2_not_evaluated_mode(
                    "HOST_EXCLUSION",
                    objects=[object_],
                    resource=resource,
                    reason="HOST_SEARCH_TOOL_ERROR",
                    error=error,
                )
            else:
                host_species = str(
                    object_.get("scientific_name")
                    or object_.get("normalized_name")
                    or object_.get("identifier")
                    or ""
                ).strip() or None
                evaluated = self.evaluator.evaluate_host_hits(
                    role_queries,
                    _p2_candidate_hits(search["hits"], query_ids),
                    method=str(search["method"]),
                    host_species=host_species,
                )
                result = self._mode_result(
                    "HOST_EXCLUSION",
                    evaluated,
                    objects=[object_],
                    resource=resource,
                    queries=role_queries,
                )
            object_results.append(result)
        if not object_results:
            return p2_not_applicable_mode(
                "HOST_EXCLUSION",
                reason="HOST_NOT_PROVIDED_OPTIONAL_CHECK_SKIPPED",
            ), object_results
        return {
            "mode": "HOST_EXCLUSION",
            "status": self._overall_status(object_results),
            "object_results": object_results,
            "objects": copy.deepcopy(host_objects),
            "reason": "P2_HOST_EXCLUSION_EVALUATED_FOR_REQUESTED_HOSTS",
            "evaluated_at": _utc_now(),
            "policy_version": P2_POLICY_VERSION,
        }, object_results

    def _related_result(
        self,
        *,
        role_queries: dict[str, str],
        query_ids: dict[str, str],
        target_object: dict[str, Any],
        related_background_objects: list[dict[str, Any]],
        related_resource: dict[str, Any] | None,
        related_search: dict[str, Any] | None,
        related_error: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not target_object:
            return p2_not_evaluated_mode(
                "RELATED_BACKGROUND_NT",
                objects=related_background_objects,
                resource=related_resource,
                reason="TARGET_OBJECT_MISSING",
            )
        if not self._resource_ready(related_resource):
            return p2_not_evaluated_mode(
                "RELATED_BACKGROUND_NT",
                objects=related_background_objects,
                resource=related_resource,
                reason="NT_DATABASE_OR_VERSION_NOT_RESOLVED",
            )
        if related_error:
            return p2_not_evaluated_mode(
                "RELATED_BACKGROUND_NT",
                objects=related_background_objects,
                resource=related_resource,
                reason="NT_SEARCH_TOOL_ERROR",
                error=related_error,
            )
        if not related_search or str(related_search["method"]) != "blastn":
            return p2_not_evaluated_mode(
                "RELATED_BACKGROUND_NT",
                objects=related_background_objects,
                resource=related_resource,
                reason="DEVELOPMENT_FALLBACK_CANNOT_PROVE_BACKGROUND_EXCLUSION",
            )
        target_species = str(
            target_object.get("scientific_name")
            or target_object.get("normalized_name")
            or target_object.get("identifier")
            or ""
        ).strip() or None
        target_accessions = {
            str(value).strip()
            for value in [
                target_object.get("accession"),
                *(target_object.get("known_accessions") or []),
            ]
            if str(value or "").strip()
        }
        evaluated = self.evaluator.evaluate_cross_species_hits(
            role_queries,
            _p2_candidate_hits(related_search["hits"], query_ids),
            method=str(related_search["method"]),
            target_species=target_species,
            target_accessions=target_accessions,
        )
        return self._mode_result(
            "RELATED_BACKGROUND_NT",
            evaluated,
            objects=related_background_objects,
            resource=related_resource,
            queries=role_queries,
        )

    def _p5_primary_result(
        self,
        *,
        role_queries: dict[str, str],
        query_ids: dict[str, str],
        primary_object: dict[str, Any],
        primary_resource: dict[str, Any] | None,
        primary_search: dict[str, Any] | None,
        primary_error: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not primary_object:
            return p2_not_evaluated_mode(
                "P5_PRIMARY_MATCH",
                objects=[],
                resource=primary_resource,
                reason="P5_PRIMARY_OBJECT_MISSING",
            )
        if not self._resource_ready(primary_resource):
            return p2_not_evaluated_mode(
                "P5_PRIMARY_MATCH",
                objects=[primary_object],
                resource=primary_resource,
                reason="P5_PRIMARY_DATABASE_OR_VERSION_NOT_RESOLVED",
            )
        if primary_error:
            return p2_not_evaluated_mode(
                "P5_PRIMARY_MATCH",
                objects=[primary_object],
                resource=primary_resource,
                reason="P5_PRIMARY_SEARCH_TOOL_ERROR",
                error=primary_error,
            )
        if not primary_search or str(primary_search.get("method")) != "blastn":
            result = p2_not_evaluated_mode(
                "P5_PRIMARY_MATCH",
                objects=[primary_object],
                resource=primary_resource,
                reason="DEVELOPMENT_FALLBACK_CANNOT_PROVE_P5_PRIMARY_MATCH",
            )
            if primary_search:
                result["hits"] = _p2_candidate_hits(
                    primary_search.get("hits") or [],
                    query_ids,
                )
                result["method"] = str(primary_search.get("method") or "not_run")
            return result
        evaluated = self.evaluator.evaluate_p5_primary_hits(
            role_queries,
            _p2_candidate_hits(primary_search["hits"], query_ids),
            method=str(primary_search["method"]),
        )
        return self._mode_result(
            "P5_PRIMARY_MATCH",
            evaluated,
            objects=[primary_object],
            resource=primary_resource,
            queries=role_queries,
        )

    def evaluate_p5_batch(
        self,
        *,
        queries: dict[str, str],
        query_maps: list[dict[str, str]],
        primary_object: dict[str, Any],
        primary_resource: dict[str, Any] | None,
        related_background_objects: list[dict[str, Any]],
        related_resource: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Evaluate the two required P2 modes owned by the P5 profile."""

        primary_search, primary_error = (
            self._search(queries, primary_resource, mode="P5_PRIMARY_MATCH")
            if query_maps
            else (None, None)
        )
        (
            related_search,
            related_error,
            effective_related_resource,
            remote_background_provider_status,
        ) = (
            self._search_related_background(
                queries,
                related_resource,
                target_object=primary_object,
            )
            if primary_object and query_maps
            else (
                None,
                None,
                related_resource,
                {
                    "status": "NOT_INITIALIZED",
                    "reason": "P2_P5_BACKGROUND_SEARCH_NOT_REQUESTED",
                },
            )
        )
        related_resource = effective_related_resource

        candidate_results: list[dict[str, Any]] = []
        for query_ids in query_maps:
            role_queries = {
                role: queries[query_id]
                for role, query_id in query_ids.items()
            }
            primary_result = self._p5_primary_result(
                role_queries=role_queries,
                query_ids=query_ids,
                primary_object=primary_object,
                primary_resource=primary_resource,
                primary_search=primary_search,
                primary_error=primary_error,
            )
            related_result = self._related_result(
                role_queries=role_queries,
                query_ids=query_ids,
                target_object=primary_object,
                related_background_objects=related_background_objects,
                related_resource=related_resource,
                related_search=related_search,
                related_error=related_error,
            )
            overall = self._overall_status([primary_result, related_result])
            specificity = {
                "profile": "P5_INTERNAL_CONTROL_V1",
                "policy_version": P2_POLICY_VERSION,
                "policy_status": P2_POLICY_STATUS,
                "overall_status": overall,
                "specificity_score": P2_SCORE_BY_STATUS.get(overall),
                "required_modes": [
                    "P5_PRIMARY_MATCH",
                    "RELATED_BACKGROUND_NT",
                ],
                "optional_modes": [],
                "host_verdict": primary_result["status"],
                "cross_species_verdict": related_result["status"],
                "primary_match": primary_result,
                "related_background_nt": related_result,
                "reason": {
                    "PASS": "ALL_REQUIRED_P5_P2_MODES_PASS",
                    "CAUTION": "AT_LEAST_ONE_P5_P2_MODE_REQUIRES_REVIEW",
                    "FAIL": "AT_LEAST_ONE_P5_P2_MODE_FAILED",
                    "NOT_EVALUATED": (
                        "AT_LEAST_ONE_REQUIRED_P5_P2_MODE_NOT_EVALUATED"
                    ),
                }[overall],
            }
            evidence_level = (
                "blast_verified"
                if overall in {"PASS", "CAUTION"}
                and primary_result.get("method") == "blastn"
                and related_result.get("method") == "blastn"
                else "in_silico_only"
            )
            candidate_results.append(
                {
                    "specificity": specificity,
                    "evidence_level": evidence_level,
                }
            )

        overall_status = (
            self._overall_status(
                [
                    {"status": item["specificity"]["overall_status"]}
                    for item in candidate_results
                ]
            )
            if candidate_results
            else "NOT_EVALUATED"
        )
        provider_status = (
            self.provider.status()
            if callable(getattr(self.provider, "status", None))
            else {"tool": type(self.provider).__name__}
        )
        return {
            "profile": "P5_INTERNAL_CONTROL_V1",
            "policy_version": P2_POLICY_VERSION,
            "policy_status": P2_POLICY_STATUS,
            "overall_status": overall_status,
            "provider_status": provider_status,
            "remote_background_provider_status": copy.deepcopy(
                remote_background_provider_status
            ),
            "effective_related_resource": copy.deepcopy(related_resource),
            "raw_search_outputs": {
                "primary": {
                    "object_id": primary_object.get("object_id"),
                    "resource": copy.deepcopy(primary_resource),
                    "result": copy.deepcopy(primary_search),
                    "error": copy.deepcopy(primary_error),
                },
                "related_background": {
                    "object_ids": [
                        item.get("object_id")
                        for item in related_background_objects
                    ],
                    "resource": copy.deepcopy(related_resource),
                    "result": copy.deepcopy(related_search),
                    "error": copy.deepcopy(related_error),
                },
            },
            "candidate_results": candidate_results,
        }

    def evaluate(
        self,
        *,
        queries: dict[str, str],
        query_maps: list[dict[str, str]],
        target_object: dict[str, Any],
        host_objects: list[dict[str, Any]],
        related_background_objects: list[dict[str, Any]],
        target_resource: dict[str, Any] | None,
        host_resources: list[
            tuple[dict[str, Any], dict[str, Any] | None]
        ],
        related_resource: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Evaluate all candidates and return P2-owned evidence/verdicts."""

        target_search, target_error = (
            self._search(queries, target_resource, mode="TARGET_HIT")
            if query_maps
            else (None, None)
        )
        host_searches: list[
            tuple[
                dict[str, Any],
                dict[str, Any] | None,
                dict[str, Any] | None,
                dict[str, Any] | None,
            ]
        ] = []
        for object_, resource in host_resources:
            search, error = (
                self._search(queries, resource, mode="HOST_EXCLUSION")
                if query_maps
                else (None, None)
            )
            host_searches.append((object_, resource, search, error))
        (
            related_search,
            related_error,
            effective_related_resource,
            remote_background_provider_status,
        ) = (
            self._search_related_background(
                queries,
                related_resource,
                target_object=target_object,
            )
            if target_object and query_maps
            else (
                None,
                None,
                related_resource,
                {
                    "status": "NOT_INITIALIZED",
                    "reason": "P2_BACKGROUND_SEARCH_NOT_REQUESTED",
                },
            )
        )
        related_resource = effective_related_resource

        candidate_results: list[dict[str, Any]] = []
        for query_ids in query_maps:
            role_queries = {
                role: queries[query_id]
                for role, query_id in query_ids.items()
            }
            target_result = self._target_result(
                role_queries=role_queries,
                query_ids=query_ids,
                target_object=target_object,
                target_resource=target_resource,
                target_search=target_search,
                target_error=target_error,
            )
            host_result, host_object_results = self._host_result(
                role_queries=role_queries,
                query_ids=query_ids,
                host_objects=host_objects,
                host_searches=host_searches,
            )
            related_result = self._related_result(
                role_queries=role_queries,
                query_ids=query_ids,
                target_object=target_object,
                related_background_objects=related_background_objects,
                related_resource=related_resource,
                related_search=related_search,
                related_error=related_error,
            )
            required_results = [target_result, related_result]
            if host_object_results:
                required_results.append(host_result)
            overall = self._overall_status(required_results)
            specificity = {
                "policy_version": P2_POLICY_VERSION,
                "policy_status": P2_POLICY_STATUS,
                "overall_status": overall,
                "specificity_score": P2_SCORE_BY_STATUS.get(overall),
                "required_modes": ["TARGET_HIT", "RELATED_BACKGROUND_NT"],
                "optional_modes": ["HOST_EXCLUSION"],
                "host_exclusion_requested": bool(host_objects),
                "target_hit": target_result,
                "host_exclusion": host_result,
                "related_background_nt": related_result,
                "reason": {
                    "PASS": "ALL_REQUIRED_P2_MODES_PASS",
                    "CAUTION": "AT_LEAST_ONE_P2_MODE_REQUIRES_REVIEW",
                    "FAIL": "AT_LEAST_ONE_P2_MODE_FAILED",
                    "NOT_EVALUATED": "AT_LEAST_ONE_REQUIRED_P2_MODE_NOT_EVALUATED",
                }[overall],
            }
            evidence_level = (
                "blast_verified"
                if overall in {"PASS", "CAUTION"}
                and target_result.get("method") == "blastn"
                and related_result.get("method") == "blastn"
                and all(
                    result.get("method") == "blastn"
                    for result in host_object_results
                )
                else "in_silico_only"
            )
            candidate_results.append(
                {
                    "specificity": specificity,
                    "evidence_level": evidence_level,
                }
            )

        provider_status = (
            self.provider.status()
            if callable(getattr(self.provider, "status", None))
            else {"tool": type(self.provider).__name__}
        )
        return {
            "policy_version": P2_POLICY_VERSION,
            "policy_status": P2_POLICY_STATUS,
            "provider_status": provider_status,
            "remote_background_provider_status": copy.deepcopy(
                remote_background_provider_status
            ),
            "effective_related_resource": copy.deepcopy(related_resource),
            "raw_search_outputs": {
                "target": {
                    "object_id": target_object.get("object_id"),
                    "resource": copy.deepcopy(target_resource),
                    "result": copy.deepcopy(target_search),
                    "error": copy.deepcopy(target_error),
                },
                "hosts": [
                    {
                        "object_id": object_.get("object_id"),
                        "resource": copy.deepcopy(resource),
                        "result": copy.deepcopy(search),
                        "error": copy.deepcopy(error),
                    }
                    for object_, resource, search, error in host_searches
                ],
                "related_background": {
                    "object_ids": [
                        item.get("object_id")
                        for item in related_background_objects
                    ],
                    "resource": copy.deepcopy(related_resource),
                    "result": copy.deepcopy(related_search),
                    "error": copy.deepcopy(related_error),
                },
            },
            "candidate_results": candidate_results,
        }


def write_subject_fasta(sequence: str, directory: str | Path, *, record_id: str = "target") -> Path:
    """Create a task-local subject FASTA for BLAST/exact-match checking."""
    path = Path(directory) / f"{record_id}.fasta"
    path.write_text(format_fasta([SequenceRecord(id=record_id, sequence=normalize_sequence(sequence))]), encoding="utf-8")
    return path
