"""WF-05 primer/probe variation-tolerance analysis."""

from __future__ import annotations

from collections import Counter

from ..alignment import MafftAdapter
from ..config import config_fingerprint
from ..errors import InputValidationError
from ..models import EvidenceLevel, SequenceRecord, WorkflowResult, WorkflowStatus
from ..primer3_tool import Primer3Adapter
from ..rules import RuleEngine
from ..sequence import normalize_sequence, read_fasta, reverse_complement
from ..structure import ViennaRNAAdapter


_COMPLEMENT_WITH_GAP = str.maketrans("ACGT-", "TGCA-")


def _orient(sequence: str, reverse: bool) -> str:
    return sequence.translate(_COMPLEMENT_WITH_GAP)[::-1] if reverse else sequence


def _reference_columns(reference_alignment: str) -> dict[int, int]:
    mapping: dict[int, int] = {}
    position = -1
    for column, base in enumerate(reference_alignment):
        if base != "-":
            position += 1
            mapping[position] = column
    return mapping


class WF05VariationTolerance:
    def __init__(self, config: dict) -> None:
        self.config = config

    @staticmethod
    def _records(payload: dict) -> list[SequenceRecord]:
        if payload.get("fasta_path"):
            return read_fasta(payload["fasta_path"])
        if not payload.get("sequences"):
            raise InputValidationError("WF-05需要sequences或fasta_path代表性序列集")
        records: list[SequenceRecord] = []
        for index, item in enumerate(payload["sequences"]):
            if isinstance(item, str):
                records.append(SequenceRecord(id=f"sequence_{index+1}", sequence=normalize_sequence(item, allow_iupac=index > 0)))
            else:
                records.append(
                    SequenceRecord(
                        id=str(item.get("id", f"sequence_{index+1}")),
                        sequence=normalize_sequence(item["sequence"], allow_iupac=index > 0),
                        description=str(item.get("description", "")),
                        source=str(item.get("source", "user")),
                    )
                )
        return records

    @staticmethod
    def _oligo_orientation(reference: str, start: int, end: int, oligo_sequence: str) -> bool:
        binding = reference[start : end + 1]
        if binding == oligo_sequence:
            return False
        if reverse_complement(binding) == oligo_sequence:
            return True
        raise InputValidationError(
            "寡核苷酸与参考序列坐标不一致",
            details={"start": start, "end": end, "oligo": oligo_sequence},
        )

    def run(self, payload: dict) -> WorkflowResult:
        for field in ("forward_primer", "reverse_primer"):
            if not payload.get(field):
                raise InputValidationError(f"WF-05需要{field}")
        records = self._records(payload)
        if len(records) < 2:
            raise InputValidationError("WF-05至少需要2条序列")
        reference = records[0]
        reference.sequence = normalize_sequence(reference.sequence)
        allow_fallback = bool(payload.get("allow_fallback", self.config["tools"]["allow_development_fallback"]))
        primer3 = Primer3Adapter(self.config, allow_fallback=allow_fallback)
        mafft = MafftAdapter(self.config["tools"]["mafft_executable"], allow_fallback=allow_fallback)
        vienna = ViennaRNAAdapter(self.config)
        forward = normalize_sequence(payload["forward_primer"])
        reverse = normalize_sequence(payload["reverse_primer"])
        probe = normalize_sequence(payload["probe"]) if payload.get("probe") else None
        candidate = primer3.validate_pair(
            forward,
            reverse,
            probe=probe,
            template=reference.sequence,
            sequence_id=str(payload.get("target_id", reference.id)),
        )
        if not candidate.amplicon_sequence_5to3:
            raise InputValidationError(
                "在首条参考序列中未定位到完整扩增子",
                details={"reference_id": reference.id, "suggestion": "确保第一条序列含正向引物及反向引物反向互补结合位点"},
            )
        aligned, alignment_method, alignment_warnings = mafft.align(records)
        if len(aligned) < 2:
            raise InputValidationError("MAFFT不可用且输入并非等长预比对序列，无法执行WF-05变异统计")
        width = len(aligned[0].sequence)
        if any(len(record.sequence) != width for record in aligned):
            raise InputValidationError("多序列比对结果长度不一致")
        columns = _reference_columns(aligned[0].sequence)
        oligos: list[dict] = []
        for role, oligo in (
            ("forward_primer", candidate.forward_primer),
            ("reverse_primer", candidate.reverse_primer),
            ("probe", candidate.probe),
        ):
            if oligo is None:
                continue
            if oligo.position_start is None or oligo.position_end is None:
                raise InputValidationError(f"无法定位{role}结合位点")
            needed = list(range(oligo.position_start, oligo.position_end + 1))
            if any(position not in columns for position in needed):
                raise InputValidationError(f"{role}结合位点未完整映射到比对")
            reverse_orientation = self._oligo_orientation(
                reference.sequence,
                oligo.position_start,
                oligo.position_end,
                oligo.sequence_5to3,
            )
            oligos.append(
                {
                    "role": role,
                    "oligo": oligo,
                    "positions": needed,
                    "columns": [columns[position] for position in needed],
                    "reverse": reverse_orientation,
                }
            )

        per_sequence: list[dict] = []
        for record in aligned:
            total_mismatches = 0
            has_three_prime_mismatch = False
            per_oligo: dict[str, dict] = {}
            invalid = False
            for item in oligos:
                observed_binding = "".join(record.sequence[column] for column in item["columns"])
                observed = _orient(observed_binding, item["reverse"])
                expected = item["oligo"].sequence_5to3
                mismatches = [index for index, (left, right) in enumerate(zip(expected, observed)) if left != right]
                invalid = invalid or any(base not in "ACGT" for base in observed)
                three_prime = item["role"] != "probe" and any(index >= len(expected) - 5 for index in mismatches)
                total_mismatches += len(mismatches)
                has_three_prime_mismatch = has_three_prime_mismatch or three_prime
                per_oligo[item["role"]] = {
                    "observed_sequence_5to3": observed,
                    "mismatch_positions_1based": [index + 1 for index in mismatches],
                    "three_prime_5nt_mismatch": three_prime,
                }
            per_sequence.append(
                {
                    "sequence_id": record.id,
                    "total_mismatches": total_mismatches,
                    "has_three_prime_5nt_mismatch": has_three_prime_mismatch,
                    "contains_gap_or_ambiguity": invalid,
                    "oligos": per_oligo,
                }
            )

        n = len(aligned)
        exact_count = sum(not item["contains_gap_or_ambiguity"] and item["total_mismatches"] == 0 for item in per_sequence)
        one_count = sum(
            not item["contains_gap_or_ambiguity"]
            and item["total_mismatches"] <= 1
            and not item["has_three_prime_5nt_mismatch"]
            for item in per_sequence
        )
        two_count = sum(not item["contains_gap_or_ambiguity"] and item["total_mismatches"] <= 2 for item in per_sequence)

        variation_settings = self.config["variation"]
        minimum_observations = int(variation_settings["minimum_variant_observations"])
        risk_sites: list[dict] = []
        reference_alignment = aligned[0].sequence
        for item in oligos:
            oligo = item["oligo"]
            for genomic_offset, (reference_position, column) in enumerate(zip(item["positions"], item["columns"])):
                reference_base_genomic = reference_alignment[column]
                counts = Counter(record.sequence[column] for record in aligned if record.sequence[column] in "ACGT")
                for alt_genomic, count in sorted(counts.items()):
                    if alt_genomic == reference_base_genomic or count < minimum_observations:
                        continue
                    if item["reverse"]:
                        oligo_position = len(item["positions"]) - genomic_offset - 1
                        ref_base = reverse_complement(reference_base_genomic)
                        alt_base = reverse_complement(alt_genomic)
                    else:
                        oligo_position = genomic_offset
                        ref_base = reference_base_genomic
                        alt_base = alt_genomic
                    if item["role"] == "probe":
                        risk_level = "MEDIUM"
                        location = "probe"
                    elif oligo_position >= oligo.length_nt - 5:
                        risk_level = "HIGH"
                        location = f"{item['role']}_3prime_5nt"
                    else:
                        risk_level = "LOW"
                        location = f"{item['role']}_5prime_or_middle"
                    mutated = list(oligo.sequence_5to3)
                    mutated[oligo_position] = alt_base
                    mutated_sequence = "".join(mutated)
                    variant_tm = primer3.calc_tm(mutated_sequence, probe=item["role"] == "probe")
                    reference_tm = oligo.tm_celsius
                    shift = float(variant_tm["tm_celsius"]) - float(reference_tm) if reference_tm is not None else None
                    risk_sites.append(
                        {
                            "position": oligo_position + 1,
                            "reference_position_1based": reference_position + 1,
                            "ref": ref_base,
                            "alt": alt_base,
                            "observations": count,
                            "frequency": round(count / n, 6),
                            "risk_level": risk_level,
                            "tm_shift_celsius": round(shift, 3) if shift is not None else None,
                            "possible_dropout": shift is not None and abs(shift) > float(variation_settings["tm_shift_warning_c"]),
                            "location": location,
                            "oligo": item["role"],
                            "variant_sequence_5to3": mutated_sequence,
                            "tm_method": variant_tm["method"],
                        }
                    )
        risk_sites.sort(key=lambda site: ({"HIGH": 0, "MEDIUM": 1, "LOW": 2}[site["risk_level"]], -site["frequency"], site["reference_position_1based"]))
        exact_coverage = exact_count / n
        high_risk = [site for site in risk_sites if site["risk_level"] == "HIGH"]
        high_frequency = any(site["frequency"] >= float(variation_settings["high_frequency"]) for site in high_risk)
        if exact_coverage < float(variation_settings["acceptable_coverage"]) or high_frequency:
            verdict = "REDESIGN_RECOMMENDED"
            redesign_route = "WF-01"
        elif exact_coverage < float(variation_settings["robust_coverage"]) or high_risk:
            verdict = "ACCEPTABLE"
            redesign_route = None
        else:
            verdict = "ROBUST"
            redesign_route = None
        minimum_sequences = int(variation_settings["minimum_sequences"])
        sequence_warning = None if n >= minimum_sequences else f"序列数不足(N={n}<{minimum_sequences})，覆盖率统计置信度低"
        variation = {
            "sequences_analyzed": n,
            "sequences_warning": sequence_warning,
            "reference_sequence_id": reference.id,
            "alignment_method": alignment_method,
            "minimum_variant_observations": minimum_observations,
            "minimum_detectable_frequency": round(minimum_observations / n, 6),
            "coverage_100pct_match": round(exact_coverage, 6),
            "coverage_1_mismatch": round(one_count / n, 6),
            "coverage_2_mismatches": round(two_count / n, 6),
            "coverage_counts": {"exact": exact_count, "one_non_3prime": one_count, "two": two_count, "total": n},
            "high_risk_sites": risk_sites,
            "verdict": verdict,
            "redesign_route": redesign_route,
            "thresholds": {
                "robust_coverage": variation_settings["robust_coverage"],
                "acceptable_coverage": variation_settings["acceptable_coverage"],
                "high_frequency": variation_settings["high_frequency"],
                "tm_shift_warning_c": variation_settings["tm_shift_warning_c"],
            },
        }
        if vienna.available:
            candidate.amplicon_structure = vienna.assess_full(candidate.amplicon_sequence_5to3)
        else:
            candidate.amplicon_structure = vienna.not_evaluated("ViennaRNA不可用")
        candidate.rules = RuleEngine().evaluate(candidate)
        RuleEngine.apply_verdict(candidate)
        candidate.rank = 1
        warnings = list(alignment_warnings)
        if sequence_warning:
            warnings.append(sequence_warning)
        if verdict == "REDESIGN_RECOMMENDED":
            warnings.append("变异耐受性不足，建议将高风险热点作为排除区重新运行WF-01")
        status = WorkflowStatus.SUCCEEDED
        if candidate.verdict == "FAIL":
            status = WorkflowStatus.FAILED
            warnings.append("已有引物探针违反硬性设计规则；变异报告仅供问题定位")
        elif alignment_method.endswith("fallback") or not primer3.available:
            status = WorkflowStatus.PARTIAL
        return WorkflowResult(
            workflow="WF-05",
            status=status,
            assay_id=str(payload.get("assay_id") or f"{payload.get('target_id', reference.id)}_variation_v1"),
            input={key: value for key, value in payload.items() if key not in {"sequences", "reference_sequence"}},
            target={
                "target_id": payload.get("target_id", reference.id),
                "organism": payload.get("target_species"),
                "reference_sequence_id": reference.id,
                "reference_length_nt": len(reference.sequence),
                "sequence_count": n,
            },
            candidates=[candidate],
            variation=variation,
            self_check={"verdict": "CAUTION" if warnings else "PASS", "risks": warnings},
            tool_status={
                "P1": primer3.status(),
                "C5": mafft.status(),
                "P6": vienna.status(),
                "C1": {"tool": "C1_sequence_io", "available": True},
                "C7": {"tool": "C7_schema", "available": True},
            },
            warnings=sorted(set(warnings)),
            provenance={
                "agentd_version": "0.3.15",
                "config_schema_version": self.config["schema_version"],
                "config_sha256": config_fingerprint(self.config),
            },
            evidence_level=EvidenceLevel.IN_SILICO_ONLY,
        )
