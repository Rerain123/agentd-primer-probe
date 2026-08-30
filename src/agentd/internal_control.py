"""P5 internal-control design orchestrator with built-in Entrez acquisition."""

from __future__ import annotations

import copy
from collections import Counter
from pathlib import Path
from typing import Any

from .alignment import MafftAdapter
from .database import DatabaseCatalog
from .entrez_sequences import EntrezSequenceError, NCBIEntrezProvider
from .errors import InputValidationError, ToolUnavailableError
from .models import (
    Candidate,
    EvidenceLevel,
    RuleStatus,
    SequenceRecord,
    SpecificityModeResult,
    SpecificityResult,
)
from .primer3_tool import Primer3Adapter
from .rules import HARD_RULES, RuleEngine
from .scoring import score_candidate
from .sequence import normalize_sequence
from .specificity import P2SpecificityBatchService
from .structure import ViennaRNAAdapter


P5_PRIMARY_SCIENTIFIC_NAMES = {
    "pig": "Sus scrofa",
    "cattle": "Bos taurus",
    "chicken": "Gallus gallus",
}


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
    def __init__(
        self,
        config: dict,
        catalog: DatabaseCatalog,
        *,
        p2_service: P2SpecificityBatchService | None = None,
    ) -> None:
        self.config = config
        self.catalog = catalog
        self.entrez = NCBIEntrezProvider(config)
        self.p2_service = p2_service

    def _get_p2_service(
        self,
        *,
        allow_fallback: bool,
    ) -> P2SpecificityBatchService:
        return self.p2_service or P2SpecificityBatchService(
            self.config,
            allow_development_fallback=allow_fallback,
        )

    def status(self, *, allow_fallback: bool = False) -> dict:
        primer3 = Primer3Adapter(self.config, allow_fallback=allow_fallback)
        mafft = MafftAdapter(self.config["tools"]["mafft_executable"], allow_fallback=allow_fallback)
        p2 = self._get_p2_service(allow_fallback=allow_fallback)
        dependencies = {
            "P1": primer3.status(),
            "C5": mafft.status(),
            "P2_C6": p2.status(),
            "NCBI_ENTREZ": self.entrez.status(),
            "C0": self.catalog.status(),
        }
        full_dependencies_available = all(
            dependencies[key]["available"]
            for key in ("P1", "C5", "P2_C6", "NCBI_ENTREZ")
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

    @staticmethod
    def _candidate_queries(
        candidates: list[Candidate],
    ) -> tuple[dict[str, str], list[dict[str, str]]]:
        queries: dict[str, str] = {}
        query_maps: list[dict[str, str]] = []
        for index, candidate in enumerate(candidates, start=1):
            prefix = f"p5_c{index:04d}"
            query_ids = {
                "forward": f"{prefix}_forward",
                "reverse": f"{prefix}_reverse",
            }
            if candidate.probe:
                query_ids["probe"] = f"{prefix}_probe"
            queries[query_ids["forward"]] = candidate.forward_primer.sequence_5to3
            queries[query_ids["reverse"]] = candidate.reverse_primer.sequence_5to3
            if candidate.probe:
                queries[query_ids["probe"]] = candidate.probe.sequence_5to3
            query_maps.append(query_ids)
        return queries, query_maps

    @staticmethod
    def _payload_resource_version(payload: dict, field: str) -> str | None:
        direct = payload.get(f"{field}_version")
        if direct:
            return str(direct)
        versions = payload.get("database_versions")
        if isinstance(versions, dict) and versions.get(field):
            return str(versions[field])
        for resolution in payload.get("_database_resolution") or []:
            if (
                isinstance(resolution, dict)
                and resolution.get("field") == field
                and resolution.get("version")
            ):
                return str(resolution["version"])
        return None

    def _resolve_p2_resource(
        self,
        payload: dict,
        *,
        field: str,
        keys: list[str],
        scope: dict[str, Any],
    ) -> dict[str, Any] | None:
        catalog_resource = self.catalog.resolve_versioned(*keys, scope=scope)
        supplied_path = payload.get(field)
        if not supplied_path:
            return catalog_resource
        resolved_supplied = str(Path(str(supplied_path)).resolve())
        if (
            catalog_resource
            and catalog_resource.get("path") == resolved_supplied
        ):
            return catalog_resource
        return self.catalog.request_resource(
            resolved_supplied,
            key=f"request:{field}",
            version=self._payload_resource_version(payload, field),
            scope=scope,
        )

    @staticmethod
    def _mode_from_p2(
        source: dict[str, Any],
        *,
        mode: str,
    ) -> SpecificityModeResult:
        status = str(source.get("status") or "NOT_EVALUATED").upper()
        return SpecificityModeResult(
            mode=mode,
            verdict="NOT_CHECKED" if status == "NOT_EVALUATED" else status,
            blast_hits=len(source.get("hits") or []),
            details=copy.deepcopy(source.get("hits") or []),
            recommendation=str(source.get("reason") or "") or None,
            mode_specific={
                **copy.deepcopy(source.get("mode_specific") or {}),
                "p2_status": status,
                "database": copy.deepcopy(source.get("database")),
            },
            method=str(source.get("method") or "not_run"),
        )

    @classmethod
    def _specificity_model(
        cls,
        specificity: dict[str, Any],
    ) -> SpecificityResult:
        primary = cls._mode_from_p2(
            specificity.get("primary_match") or {},
            mode="check_host",
        )
        related = cls._mode_from_p2(
            specificity.get("related_background_nt") or {},
            mode="check_cross_species",
        )
        target = SpecificityModeResult(
            mode="check_target",
            verdict="NOT_APPLICABLE",
            blast_hits=0,
            recommendation="P5_PROFILE_DOES_NOT_REQUIRE_TARGET_MODE",
            method="not_run",
        )
        overall = str(
            specificity.get("overall_status") or "NOT_EVALUATED"
        ).upper()
        rating = {
            "PASS": "PASS",
            "CAUTION": "PASS_WITH_WARNINGS",
            "FAIL": "FAIL",
            "NOT_EVALUATED": "NOT_EVALUATED",
        }.get(overall, "NOT_EVALUATED")
        methods = {primary.method, related.method}
        warnings = [
            str(item.get("reason"))
            for item in (
                specificity.get("primary_match") or {},
                specificity.get("related_background_nt") or {},
            )
            if item.get("status") != "PASS" and item.get("reason")
        ]
        return SpecificityResult(
            target=target,
            host=primary,
            cross_species=related,
            rating=rating,
            method="blastn" if methods == {"blastn"} else "mixed_or_degraded",
            warnings=warnings,
        )

    @classmethod
    def _apply_p2_candidate_results(
        cls,
        candidates: list[Candidate],
        p2_result: dict[str, Any],
    ) -> None:
        results = p2_result.get("candidate_results")
        if not isinstance(results, list) or len(results) != len(candidates):
            raise InputValidationError(
                "P2返回的候选结果数量与P5输入不一致",
                details={
                    "expected_candidates": len(candidates),
                    "actual_candidates": (
                        len(results) if isinstance(results, list) else None
                    ),
                },
            )
        allowed = {"PASS", "CAUTION", "FAIL", "NOT_EVALUATED"}
        for index, (candidate, item) in enumerate(zip(candidates, results)):
            specificity = (
                item.get("specificity") if isinstance(item, dict) else None
            )
            status = (
                str(specificity.get("overall_status") or "").upper()
                if isinstance(specificity, dict)
                else ""
            )
            if status not in allowed:
                raise InputValidationError(
                    "P2返回了无效的P5候选状态",
                    details={"candidate_index": index, "status": status},
                )
            evidence = str(
                item.get("evidence_level") or "in_silico_only"
            )
            try:
                candidate.evidence_level = EvidenceLevel(evidence)
            except ValueError as exc:
                raise InputValidationError(
                    "P2返回了无效的证据等级",
                    details={"candidate_index": index, "evidence_level": evidence},
                ) from exc
            candidate.metadata["_p2_specificity"] = copy.deepcopy(specificity)
            candidate.specificity = cls._specificity_model(specificity)

    @staticmethod
    def _specificity_summary(
        candidates: list[Candidate],
        p2_result: dict[str, Any],
    ) -> dict[str, Any]:
        candidate_results = p2_result.get("candidate_results") or []
        return {
            "verdict": p2_result.get("overall_status", "NOT_EVALUATED"),
            "blast_verified": bool(candidates)
            and all(
                item.get("evidence_level") == EvidenceLevel.BLAST_VERIFIED.value
                for item in candidate_results
                if isinstance(item, dict)
            )
            and len(candidate_results) == len(candidates),
            "profile": p2_result.get("profile"),
            "policy_version": p2_result.get("policy_version"),
            "candidate_results": copy.deepcopy(candidate_results),
            "provider_status": copy.deepcopy(p2_result.get("provider_status")),
            "remote_background_provider_status": copy.deepcopy(
                p2_result.get("remote_background_provider_status")
            ),
            "effective_related_resource": copy.deepcopy(
                p2_result.get("effective_related_resource")
            ),
            "raw_search_outputs": copy.deepcopy(
                p2_result.get("raw_search_outputs") or {}
            ),
        }

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
                "warnings": [f"P5未配置{host_species}默认管家基因"],
                "candidates": [],
                "recommendation": "提供自定义管家基因及RefSeq编号，或使用P5已支持的宿主物种",
            }
        allow_fallback = bool(payload.get("allow_fallback", self.config["tools"]["allow_development_fallback"]))
        selected: dict | None = None
        attempts: list[dict] = []
        warnings: list[str] = []
        for gene in preferred:
            gene_name = str(gene)
            refseq = (
                payload.get("gene_accessions", {}).get(gene_name)
                or settings["refseq"].get(host_species, {}).get(gene_name)
            )
            records, source = self.catalog.housekeeping_records(
                payload, host_species, gene_name
            )
            minimum_sequences = int(settings["minimum_homolog_sequences"])
            if not records:
                try:
                    records, source = self.entrez.fetch_housekeeping_records(
                        host_species=host_species,
                        gene=gene_name,
                        refseq_accession=str(refseq or ""),
                        minimum_records=minimum_sequences,
                    )
                except EntrezSequenceError as exc:
                    attempts.append(
                        {
                            "gene": gene_name,
                            "status": "SEQUENCE_FETCH_FAILED",
                            "message": str(exc),
                            "data_source": {
                                "method": "ncbi_entrez_live",
                                "refseq_accession": refseq,
                            },
                        }
                    )
                    continue
            if len(records) < minimum_sequences:
                attempts.append(
                    {
                        "gene": gene_name,
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
                attempts.append({"gene": gene_name, "status": "TOOL_UNAVAILABLE", "message": exc.message, "data_source": source})
                continue
            conservation = _alignment_conservation(aligned)
            status = "PASS" if conservation >= float(settings["pass_conservation"]) else ("CAUTION" if conservation >= float(settings["caution_conservation"]) else "FAIL")
            attempts.append({"gene": gene_name, "status": status, "conservation": conservation, "sequence_count": len(records), "alignment_method": method, "data_source": source})
            warnings.extend(alignment_warnings)
            if status == "FAIL":
                continue
            if selected is None or (selected["status"] == "CAUTION" and status == "PASS"):
                selected = {"gene": gene_name, "records": records, "conservation": conservation, "status": status, "alignment_method": method, "data_source": source, "refseq_accession": refseq}
            if status == "PASS":
                break
        if selected is None:
            return {
                "status": "FAILED",
                "verdict": "FAIL",
                "gene_attempts": attempts,
                "candidates": [],
                "warnings": warnings + ["未获得满足保守性要求的管家基因序列"],
                "recommendation": "检查服务器到NCBI Entrez的网络与RefSeq配置；仍失败时考虑外源性异源内标",
            }

        gene = selected["gene"]
        refseq = selected.get("refseq_accession")
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
        p2 = self._get_p2_service(allow_fallback=allow_fallback)
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
        primary_object = {
            "object_id": f"p5:primary:{host_species}",
            "role": "P5_PRIMARY",
            "identifier_type": "CANONICAL_NAME",
            "identifier": host_species,
            "normalized_name": host_species,
            "scientific_name": P5_PRIMARY_SCIENTIFIC_NAMES.get(host_species),
        }
        primary_scope = {
            "role": "P5_PRIMARY",
            "object_id": primary_object["object_id"],
            "identifier": host_species,
        }
        related_scope = {
            "role": "RELATED_BACKGROUND",
            "target_object_id": primary_object["object_id"],
            "mode": "P5_BROAD_BACKGROUND",
        }
        primary_resource = self._resolve_p2_resource(
            payload,
            field="host_db",
            keys=[f"host:{host_species}:blast", f"host:{host_species}"],
            scope=primary_scope,
        )
        related_resource = self._resolve_p2_resource(
            payload,
            field="cross_db",
            keys=[
                "nt:default:blast",
                "nt:default",
                "cross:default:blast",
                "cross:default",
            ],
            scope=related_scope,
        )
        queries, query_maps = self._candidate_queries(accepted)
        related_background_objects = copy.deepcopy(
            payload.get("related_background_objects") or []
        )
        contract_error: dict[str, Any] | None = None
        p2_result = p2.evaluate_p5_batch(
            queries=queries,
            query_maps=query_maps,
            primary_object=primary_object,
            primary_resource=primary_resource,
            related_background_objects=related_background_objects,
            related_resource=related_resource,
        )
        try:
            self._apply_p2_candidate_results(accepted, p2_result)
        except InputValidationError as exc:
            contract_error = {
                **exc.to_dict(),
                "code": "P2_CONTRACT_ERROR",
            }
            p2_result = {
                **p2_result,
                "overall_status": "NOT_EVALUATED",
                "candidate_results": [],
                "contract_error": contract_error,
            }
            for candidate in accepted:
                candidate.specificity = None
                candidate.evidence_level = EvidenceLevel.IN_SILICO_ONLY
                candidate.metadata.pop("_p2_specificity", None)
            warnings.append("P2_CONTRACT_ERROR")
        specificity = self._specificity_summary(accepted, p2_result)
        if contract_error:
            specificity["contract_error"] = contract_error
        for item in p2_result.get("candidate_results") or []:
            candidate_specificity = item.get("specificity") or {}
            if candidate_specificity.get("overall_status") != "PASS":
                reason = candidate_specificity.get("reason")
                if reason:
                    warnings.append(f"P2:{reason}")
        evidence = (
            EvidenceLevel.BLAST_VERIFIED
            if specificity["blast_verified"]
            else EvidenceLevel.IN_SILICO_ONLY
        )
        for candidate in accepted:
            candidate.rules = rule_engine.evaluate(candidate)
            rule_engine.apply_verdict(candidate)
            score_candidate(candidate, design_config)
            if cross_check and cross_check["verdict"] in {"CAUTION", "FAIL"}:
                candidate.warnings.append("P5_TARGET_CROSS_DIMER")
        accepted.sort(key=lambda item: (-item.score, item.candidate_id))
        for rank, candidate in enumerate(accepted, start=1):
            candidate.rank = rank
        if contract_error:
            verdict = "FAIL"
        elif cross_check and cross_check["verdict"] == "FAIL":
            verdict = "FAIL"
        elif specificity["verdict"] == "FAIL":
            verdict = "FAIL"
        elif warnings or specificity["verdict"] != "PASS" or (cross_check and cross_check["verdict"] != "PASS"):
            verdict = "CAUTION"
        else:
            verdict = "PASS"
        if any(target.get("fluorophore") == "Cy5" for target in existing_targets):
            warnings.append("靶标已占用Cy5，内标通道需在WF-02重新指派")
        return {
            "status": (
                "P2_CONTRACT_ERROR"
                if contract_error
                else ("COMPLETED" if verdict != "FAIL" else "FAILED")
            ),
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
            "errors": [contract_error] if contract_error else [],
            "tool_status": {
                "P1": primer3.status(),
                "P2_C6": p2.status(),
                "P6": vienna.status(),
            },
        }
