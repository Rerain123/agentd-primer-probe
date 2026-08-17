"""P2 specificity orchestration around C6 BLAST+ and P1 thermodynamics."""

from __future__ import annotations

import csv
import copy
import io
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

from .errors import InputValidationError, ToolUnavailableError
from .models import Oligo, SequenceRecord, SpecificityModeResult, SpecificityResult
from .primer3_tool import Primer3Adapter
from .sequence import format_fasta, normalize_sequence, read_fasta, reverse_complement


FASTA_SUFFIXES = {".fa", ".fasta", ".fna", ".fas"}

# BLAST E-values for short oligos scale with database size.  Keep target
# retrieval permissive, then let the target policy decide from identity,
# full-query coverage and 3' mismatches instead of using E-value as a gate.
SHORT_OLIGO_TARGET_SEARCH_EVALUE = 1000.0
SHORT_OLIGO_TARGET_MIN_IDENTITY_PCT = 95.0
SHORT_OLIGO_TARGET_MIN_QUERY_COVERAGE_PCT = 95.0
SHORT_OLIGO_TARGET_MAX_HSPS_PER_SUBJECT = 100


class BlastAdapter:
    def __init__(self, executable: str = "blastn", *, allow_fallback: bool = False) -> None:
        self.executable = executable
        self.allow_fallback = allow_fallback
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
        identity = float(hit.get("identity_pct", 0.0))
        coverage = float(hit.get("query_coverage_pct", 0.0))
        dg = cls._hit_dg(hit)
        three_prime_mismatches = hit.get("three_prime_mismatches")
        if (
            identity >= 95.0
            and coverage >= 95.0
            and dg is not None
            and dg < -9.0
            and three_prime_mismatches == 0
        ):
            return "PASS"
        if identity >= 90.0 and coverage >= 90.0:
            if dg is None:
                return "CAUTION"
            if 90.0 <= identity < 95.0 and -9.0 <= dg <= -6.0 and (
                three_prime_mismatches is not None and three_prime_mismatches <= 1
            ):
                return "CAUTION"
            if identity >= 95.0 and dg >= -9.0:
                return "CAUTION"
            if three_prime_mismatches is not None and three_prime_mismatches <= 1:
                return "CAUTION"
        return "FAIL"

    @classmethod
    def _perfect_target_hit(cls, hit: dict) -> bool:
        return (
            float(hit.get("identity_pct", 0.0)) == 100.0
            and float(hit.get("query_coverage_pct", 0.0)) == 100.0
            and hit.get("three_prime_mismatches") == 0
            and cls._hit_dg(hit) is not None
            and float(cls._hit_dg(hit)) < -9.0
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
    def _compatible_host_amplicons(hits: list[dict], maximum_span_bp: int = 1000) -> list[dict]:
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

    def check_target(self, queries: dict[str, str], database: str | Path | None) -> SpecificityModeResult:
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
                or (method != "blastn" and any(self._development_exact_hit(hit) for hit in query_hits))
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
            mode="check_target", verdict=verdict, blast_hits=len(hits), details=hits,
            recommendation=recommendation,
            mode_specific={
                "required_query_ids": sorted(required),
                "per_query_verdict": per_query,
                "perfect_query_ids": sorted(perfect_query_ids),
            },
            method=method,
        )

    def check_host(self, queries: dict[str, str], database: str | Path | None, host_species: str | None) -> SpecificityModeResult:
        if not database:
            return SpecificityModeResult(
                mode="check_host", verdict="NOT_CHECKED", blast_hits=0,
                recommendation="配置宿主基因组数据库后重新验证",
                mode_specific={"host_species": host_species}, method="not_run",
            )
        hits, method = self.blast.search(queries, database, evalue=0.1, mode="host")
        self._add_thermodynamics(hits, queries)
        relevant_hits = [hit for hit in hits if self._evalue_below(hit, 0.1)]
        high_risk_hits = [
            hit
            for hit in relevant_hits
            if self._hit_dg(hit) is not None
            and float(self._hit_dg(hit)) < -6.0
            and hit.get("three_prime_mismatches") is not None
            and int(hit["three_prime_mismatches"]) <= 1
        ]
        compatible_amplicons = self._compatible_host_amplicons(relevant_hits)
        hit_counts = defaultdict(int)
        for hit in relevant_hits:
            hit_counts[str(hit.get("query_id") or "")] += 1
        repeat_like_queries = sorted(query_id for query_id, count in hit_counts.items() if count > 10)
        if high_risk_hits or compatible_amplicons:
            verdict = "FAIL"
            recommendation = (
                f"在宿主{host_species or ''}数据库发现"
                f"{len(high_risk_hits)}个强结合/低3′错配命中和"
                f"{len(compatible_amplicons)}个兼容扩增位点，请更换区域"
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
            mode="check_host", verdict=verdict, blast_hits=len(hits), details=hits,
            recommendation=recommendation,
            mode_specific={
                "host_species": host_species,
                "high_risk_hit_count": len(high_risk_hits),
                "compatible_amplicons": compatible_amplicons,
                "repeat_like_query_ids": repeat_like_queries,
            },
            method=method,
        )

    def check_cross_species(self, queries: dict[str, str], database: str | Path | None, target_species: str | None) -> SpecificityModeResult:
        genus = target_species.split()[0] if target_species and len(target_species.split()) >= 2 else None
        if not database:
            return SpecificityModeResult(
                mode="check_cross_species", verdict="NOT_CHECKED", blast_hits=0,
                recommendation="配置近缘种/共感染病原数据库后重新验证",
                mode_specific={"target_species": target_species, "target_genus": genus}, method="not_run",
            )
        hits, method = self.blast.search(queries, database, evalue=1e-5, mode="cross_species")
        self._add_thermodynamics(hits, queries)
        normalized_target = " ".join((target_species or "").lower().split())
        cross_hits: list[dict] = []
        species_names: set[str] = set()
        same_genus_names: set[str] = set()
        for hit in hits:
            scientific_name = self._scientific_name(hit)
            normalized_name = " ".join((scientific_name or "").lower().split())
            if normalized_target and normalized_name == normalized_target:
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
                "cross_species": sorted(species_names),
                "same_genus_species": sorted(same_genus_names),
            },
            method=method,
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
        cross = self.check_cross_species(queries, cross_db, target_species)
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


def write_subject_fasta(sequence: str, directory: str | Path, *, record_id: str = "target") -> Path:
    """Create a task-local subject FASTA for BLAST/exact-match checking."""
    path = Path(directory) / f"{record_id}.fasta"
    path.write_text(format_fasta([SequenceRecord(id=record_id, sequence=normalize_sequence(sequence))]), encoding="utf-8")
    return path
