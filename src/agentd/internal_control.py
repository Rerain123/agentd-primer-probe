"""P5 internal-control design orchestrator with server-injectable sequence data."""

from __future__ import annotations

import copy
import tempfile
from collections import Counter
from pathlib import Path

from .alignment import MafftAdapter
from .database import DatabaseCatalog
from .errors import ToolUnavailableError
from .models import Candidate, EvidenceLevel, RuleStatus, SequenceRecord
from .primer3_tool import Primer3Adapter
from .rules import HARD_RULES, RuleEngine
from .scoring import score_candidate
from .sequence import format_fasta, normalize_sequence
from .specificity import BlastAdapter
from .structure import ViennaRNAAdapter


def _alignment_conservation(records: list[SequenceRecord]) -> float:
    if not records:
        return 0.0
    if len(records) == 1:
        return 1.0
    width = len(records[0].sequence)
    scores: list[float] = []
    for column in range(width):
        counts = Counter(record.sequence[column] for record in records if record.sequence[column] in "ACGT")
        scores.append(max(counts.values()) / len(records) if counts else 0.0)
    return round(sum(scores) / len(scores), 6) if scores else 0.0


class InternalControlDesigner:
    def __init__(self, config: dict, catalog: DatabaseCatalog) -> None:
        self.config = config
        self.catalog = catalog

    def status(self, *, allow_fallback: bool = False) -> dict:
        primer3 = Primer3Adapter(self.config, allow_fallback=allow_fallback)
        mafft = MafftAdapter(self.config["tools"]["mafft_executable"], allow_fallback=allow_fallback)
        blast = BlastAdapter(self.config["tools"]["blastn_executable"], allow_fallback=allow_fallback)
        dependencies = {
            "P1": primer3.status(),
            "C5": mafft.status(),
            "P2_C6": blast.status(),
            "C0": self.catalog.status(),
        }
        full_dependencies_available = all(
            dependencies[key]["available"] for key in ("P1", "C5", "P2_C6")
        )
        return {
            "tool": "P5_Internal_Control",
            "available": full_dependencies_available,
            "framework_available": True,
            "capability": "FULL" if full_dependencies_available else "FRAMEWORK_DEGRADED",
            "version": "provider-orchestrator-1.0",
            "dependencies": dependencies,
        }

    @staticmethod
    def _hard_failed(candidate: Candidate) -> bool:
        return any(rule.code in HARD_RULES and rule.status == RuleStatus.FAIL for rule in candidate.rules)

    @staticmethod
    def _target_oligos(existing_targets: list[dict]) -> list[tuple[str, str]]:
        sequences: list[tuple[str, str]] = []
        for target in existing_targets:
            target_id = str(target.get("target_id", "target"))
            for role in ("fwd", "rev", "probe"):
                sequence = target.get(f"{role}_seq")
                if sequence:
                    sequences.append((f"{target_id}:{role}", str(sequence)))
        return sequences

    def _cross_check(self, candidates: list[Candidate], existing_targets: list[dict], primer3: Primer3Adapter) -> dict | None:
        targets = self._target_oligos(existing_targets)
        if not targets:
            return None
        checked = 0
        worst_dg: float | None = None
        worst_pair: list[str] = []
        for candidate in candidates:
            for role, oligo in (("forward", candidate.forward_primer), ("reverse", candidate.reverse_primer), ("probe", candidate.probe)):
                if oligo is None:
                    continue
                for target_id, target_sequence in targets:
                    result = primer3.calc_heterodimer(oligo.sequence_5to3, target_sequence)
                    dg = result.get("dg_kcal")
                    if dg is None:
                        continue
                    checked += 1
                    if worst_dg is None or float(dg) < worst_dg:
                        worst_dg = float(dg)
                        worst_pair = [f"{candidate.candidate_id}:{role}", target_id]
        if checked == 0:
            verdict = "NOT_EVALUATED"
        elif worst_dg is not None and worst_dg <= -7.0:
            verdict = "FAIL"
        elif worst_dg is not None and worst_dg <= -6.0:
            verdict = "CAUTION"
        else:
            verdict = "PASS"
        return {
            "performed": checked > 0,
            "heterodimer_pairs_checked": checked,
            "worst_heterodimer_dg_kcal": round(worst_dg, 3) if worst_dg is not None else None,
            "worst_pair": worst_pair,
            "verdict": verdict,
        }

    def _specificity(
        self,
        candidates: list[Candidate],
        blast: BlastAdapter,
        *,
        reference: SequenceRecord,
        host_db: str | None,
        cross_db: str | None,
        temp_dir: str,
    ) -> dict:
        queries: dict[str, str] = {}
        for candidate in candidates:
            queries[f"{candidate.candidate_id}:forward"] = candidate.forward_primer.sequence_5to3
            queries[f"{candidate.candidate_id}:reverse"] = candidate.reverse_primer.sequence_5to3
            if candidate.probe:
                queries[f"{candidate.candidate_id}:probe"] = candidate.probe.sequence_5to3
        reference_path = Path(temp_dir) / "internal-control-reference.fasta"
        reference_path.write_text(format_fasta([reference]), encoding="utf-8")
        modes: dict[str, dict] = {}
        all_blast = True
        try:
            reference_hits, reference_method = blast.search(queries, reference_path, evalue=1e-5, mode="internal_reference")
            reference_hit_ids = {hit["query_id"] for hit in reference_hits if hit["identity_pct"] >= 95 and hit["query_coverage_pct"] >= 95}
            reference_verdict = "PASS" if set(queries).issubset(reference_hit_ids) else "FAIL"
            if reference_method != "blastn" and reference_verdict == "PASS":
                reference_verdict = "CAUTION"
                all_blast = False
            modes["reference_locus"] = {"verdict": reference_verdict, "hits": len(reference_hits), "method": reference_method}
        except ToolUnavailableError as exc:
            modes["reference_locus"] = {"verdict": "NOT_CHECKED", "hits": 0, "method": "not_run", "reason": exc.message}
            all_blast = False
        for name, database, evalue in (("host_genome", host_db, 0.1), ("pathogen_cross", cross_db, 1e-5)):
            if not database:
                modes[name] = {"verdict": "NOT_CHECKED", "hits": 0, "method": "not_run"}
                all_blast = False
                continue
            try:
                hits, method = blast.search(queries, database, evalue=evalue, mode=f"internal_{name}")
                if name == "host_genome":
                    # A host hit is expected; multiple genomic loci need review.
                    counts = Counter(hit["query_id"] for hit in hits if hit["identity_pct"] >= 95 and hit["query_coverage_pct"] >= 90)
                    verdict = "PASS" if counts and all(value == 1 for value in counts.values()) and set(queries).issubset(counts) else "CAUTION"
                else:
                    verdict = "FAIL" if any(hit["identity_pct"] >= 95 and hit["query_coverage_pct"] >= 90 for hit in hits) else "PASS"
                if method != "blastn":
                    verdict = "CAUTION" if verdict == "PASS" else verdict
                    all_blast = False
                modes[name] = {"verdict": verdict, "hits": len(hits), "method": method}
            except ToolUnavailableError as exc:
                modes[name] = {"verdict": "NOT_CHECKED", "hits": 0, "method": "not_run", "reason": exc.message}
                all_blast = False
        verdicts = {item["verdict"] for item in modes.values()}
        overall = "FAIL" if "FAIL" in verdicts else ("PASS" if verdicts == {"PASS"} else "CAUTION")
        return {"verdict": overall, "modes": modes, "blast_verified": all_blast and overall == "PASS"}

    def design(self, payload: dict) -> dict:
        host_species = str(payload.get("host_species", "")).strip().lower()
        settings = self.config["internal_control"]
        if not host_species:
            return {"status": "INVALID_INPUT", "verdict": "FAIL", "warnings": ["host_species为必填参数"], "candidates": []}
        preferred = payload.get("preferred_genes") or settings["gene_priority"].get(host_species, [])
        if not preferred:
            return {
                "status": "NOT_CONFIGURED",
                "verdict": "FAIL",
                "warnings": [f"P5未配置{host_species}默认管家基因；请提供preferred_genes和gene_sequences或服务器目录资源"],
                "candidates": [],
                "recommendation": "提供自定义管家基因序列，或部署后配置internal:<species>:<gene>:sequences资源",
            }
        allow_fallback = bool(payload.get("allow_fallback", self.config["tools"]["allow_development_fallback"]))
        selected: dict | None = None
        attempts: list[dict] = []
        warnings: list[str] = []
        for gene in preferred:
            records, source = self.catalog.housekeeping_records(payload, host_species, str(gene))
            if not records:
                attempts.append({"gene": gene, "status": "NOT_CONFIGURED", "data_source": source})
                continue
            minimum_sequences = int(settings["minimum_homolog_sequences"])
            if len(records) < minimum_sequences:
                attempts.append(
                    {
                        "gene": gene,
                        "status": "INSUFFICIENT_SEQUENCES",
                        "sequence_count": len(records),
                        "minimum_sequence_count": minimum_sequences,
                        "data_source": source,
                    }
                )
                continue
            records[0].sequence = normalize_sequence(records[0].sequence)
            mafft = MafftAdapter(self.config["tools"]["mafft_executable"], allow_fallback=allow_fallback)
            try:
                aligned, method, alignment_warnings = mafft.align(records)
            except ToolUnavailableError as exc:
                attempts.append({"gene": gene, "status": "TOOL_UNAVAILABLE", "message": exc.message, "data_source": source})
                continue
            conservation = _alignment_conservation(aligned)
            status = "PASS" if conservation >= float(settings["pass_conservation"]) else ("CAUTION" if conservation >= float(settings["caution_conservation"]) else "FAIL")
            attempts.append({"gene": gene, "status": status, "conservation": conservation, "sequence_count": len(records), "alignment_method": method, "data_source": source})
            warnings.extend(alignment_warnings)
            if status == "FAIL":
                continue
            if selected is None or (selected["status"] == "CAUTION" and status == "PASS"):
                selected = {"gene": str(gene), "records": records, "conservation": conservation, "status": status, "alignment_method": method, "data_source": source}
            if status == "PASS":
                break
        if selected is None:
            missing_only = attempts and all(item["status"] == "NOT_CONFIGURED" for item in attempts)
            return {
                "status": "NOT_CONFIGURED" if missing_only else "FAILED",
                "verdict": "FAIL",
                "gene_attempts": attempts,
                "candidates": [],
                "warnings": warnings + ["未获得满足保守性要求的管家基因序列"],
                "recommendation": "服务器配置管家基因序列资源，或提供自定义gene_sequences；仍失败时考虑外源性异源内标",
            }

        gene = selected["gene"]
        refseq = settings["refseq"].get(host_species, {}).get(gene) or payload.get("gene_accessions", {}).get(gene)
        design_config = copy.deepcopy(self.config)
        design_config["maximum_candidates"] = 5
        if gene == "18S rRNA":
            design_config["thermodynamics"]["primer_dna_conc_nm"] = 50.0
        parameters = {
            **design_config["standard_design"],
            "product_min_bp": 70,
            "product_max_bp": 120,
        }
        primer3 = Primer3Adapter(design_config, allow_fallback=allow_fallback)
        blast = BlastAdapter(self.config["tools"]["blastn_executable"], allow_fallback=allow_fallback)
        vienna = ViennaRNAAdapter(self.config)
        try:
            candidates = primer3.design(selected["records"][0].sequence, sequence_id=f"IC-{host_species}-{gene}", parameters=parameters)
        except ToolUnavailableError as exc:
            return {"status": "TOOL_UNAVAILABLE", "verdict": "FAIL", "gene_selected": gene, "candidates": [], "warnings": warnings + [exc.message], "gene_attempts": attempts}
        rule_engine = RuleEngine()
        accepted: list[Candidate] = []
        for candidate in candidates:
            candidate.conservation_score = selected["conservation"]
            candidate.conservation_method = selected["alignment_method"]
            candidate.conservation_sequence_count = len(selected["records"])
            candidate.metadata.update(
                {
                    "role": "internal_control",
                    "gene": gene,
                    "refseq_accession": refseq,
                    "fluorophore": settings["default_fluorophore"],
                    "quencher": settings["default_quencher"],
                    "primer_concentration_nm": 50.0 if gene == "18S rRNA" else 250.0,
                }
            )
            candidate.amplicon_structure = vienna.assess_full(candidate.amplicon_sequence_5to3) if vienna.available else vienna.not_evaluated("ViennaRNA不可用")
            candidate.rules = rule_engine.evaluate(candidate)
            rule_engine.apply_verdict(candidate)
            if self._hard_failed(candidate):
                continue
            score_candidate(candidate, design_config)
            accepted.append(candidate)
        accepted.sort(key=lambda item: (-item.score, item.candidate_id))
        accepted = accepted[: int(settings["top_candidates"])]
        for rank, candidate in enumerate(accepted, start=1):
            candidate.rank = rank
        if not accepted:
            return {"status": "FAILED", "verdict": "FAIL", "gene_selected": gene, "refseq_accession": refseq, "conservation": selected["conservation"], "gene_attempts": attempts, "candidates": [], "warnings": warnings + ["全部内标候选被硬性规则排除"]}

        existing_targets = payload.get("existing_targets") or []
        cross_check = self._cross_check(accepted, existing_targets, primer3)
        with tempfile.TemporaryDirectory(prefix="agentd-p5-") as temp_dir:
            specificity = self._specificity(
                accepted,
                blast,
                reference=selected["records"][0],
                host_db=payload.get("host_db"),
                cross_db=payload.get("cross_db"),
                temp_dir=temp_dir,
            )
        evidence = EvidenceLevel.BLAST_VERIFIED if specificity["blast_verified"] else EvidenceLevel.IN_SILICO_ONLY
        for candidate in accepted:
            candidate.evidence_level = evidence
            candidate.rules = rule_engine.evaluate(candidate)
            rule_engine.apply_verdict(candidate)
            if cross_check and cross_check["verdict"] in {"CAUTION", "FAIL"}:
                candidate.warnings.append("P5_TARGET_CROSS_DIMER")
        if cross_check and cross_check["verdict"] == "FAIL":
            verdict = "FAIL"
        elif specificity["verdict"] == "FAIL":
            verdict = "FAIL"
        elif warnings or specificity["verdict"] != "PASS" or (cross_check and cross_check["verdict"] != "PASS"):
            verdict = "CAUTION"
        else:
            verdict = "PASS"
        if any(target.get("fluorophore") == "Cy5" for target in existing_targets):
            warnings.append("靶标已占用Cy5，内标通道需在WF-02/WF-06重新指派")
        return {
            "status": "COMPLETED" if verdict != "FAIL" else "FAILED",
            "gene_selected": gene,
            "refseq_accession": refseq,
            "conservation": selected["conservation"],
            "conservation_status": selected["status"],
            "gene_attempts": attempts,
            "data_source": selected["data_source"],
            "candidates": [candidate.to_dict() for candidate in accepted],
            "candidate_objects": accepted,
            "limiting_primer_strategy": "18S rRNA引物浓度50–100 nM" if gene == "18S rRNA" else None,
            "cross_check": cross_check,
            "specificity_check": specificity,
            "evidence_level": evidence.value,
            "verdict": verdict,
            "warnings": sorted(set(warnings)),
            "tool_status": {"P1": primer3.status(), "P2_C6": blast.status(), "P6": vienna.status()},
        }
