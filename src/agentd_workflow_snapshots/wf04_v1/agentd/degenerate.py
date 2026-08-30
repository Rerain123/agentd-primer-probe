"""WF-01 parallel degenerate-primer engineering strategy."""

from __future__ import annotations

import itertools
from collections import Counter
from pathlib import Path

from .errors import ToolUnavailableError
from .models import Candidate, SequenceRecord
from .primer3_tool import Primer3Adapter
from .sequence import reverse_complement
from .specificity import BlastAdapter


BASES_TO_IUPAC = {
    frozenset("A"): "A", frozenset("C"): "C", frozenset("G"): "G", frozenset("T"): "T",
    frozenset("AG"): "R", frozenset("CT"): "Y", frozenset("CG"): "S", frozenset("AT"): "W",
    frozenset("GT"): "K", frozenset("AC"): "M", frozenset("CGT"): "B", frozenset("AGT"): "D",
    frozenset("ACT"): "H", frozenset("ACG"): "V", frozenset("ACGT"): "N",
}
IUPAC_TO_BASES = {code: tuple(sorted(bases)) for bases, code in BASES_TO_IUPAC.items()}


def expand_iupac(sequence: str, *, maximum: int = 8) -> list[str]:
    choices = [IUPAC_TO_BASES.get(base, (base,)) for base in sequence]
    count = 1
    for item in choices:
        count *= len(item)
    if count > maximum:
        return []
    return ["".join(parts) for parts in itertools.product(*choices)]


def _reference_columns(reference_alignment: str) -> dict[int, int]:
    mapping: dict[int, int] = {}
    position = -1
    for column, base in enumerate(reference_alignment):
        if base != "-":
            position += 1
            mapping[position] = column
    return mapping


def _alleles_at_columns(aligned: list[SequenceRecord], columns: list[int]) -> list[Counter]:
    result: list[Counter] = []
    for column in columns:
        result.append(Counter(record.sequence[column] for record in aligned if record.sequence[column] in "ACGT"))
    return result


def _merge_binding_site(
    aligned: list[SequenceRecord],
    reference_columns: dict[int, int],
    start: int,
    end: int,
    *,
    reverse_orientation: bool,
    minimum_observations: int = 2,
) -> tuple[str, list[dict]]:
    positions = list(range(start, end + 1))
    columns = [reference_columns[position] for position in positions]
    counters = _alleles_at_columns(aligned, columns)
    codes: list[str] = []
    variants: list[dict] = []
    reference = aligned[0].sequence
    for local_index, (position, column, counts) in enumerate(zip(positions, columns, counters)):
        reference_base = reference[column]
        accepted = {reference_base}
        accepted.update(base for base, count in counts.items() if base != reference_base and count >= minimum_observations)
        code = BASES_TO_IUPAC.get(frozenset(accepted), "N")
        codes.append(code)
        alt_counts = {base: count for base, count in sorted(counts.items()) if base != reference_base and count >= minimum_observations}
        if alt_counts:
            variants.append(
                {
                    "reference_position_1based": position + 1,
                    "oligo_position_1based": local_index + 1,
                    "ref": reference_base,
                    "alt": sorted(alt_counts),
                    "alt_counts": alt_counts,
                    "iupac": code,
                }
            )
    merged = "".join(codes)
    if reverse_orientation:
        merged = reverse_complement(merged)
        length = len(codes)
        for item in variants:
            item["oligo_position_1based"] = length - int(item["oligo_position_1based"]) + 1
    return merged, variants


def _specificity_batch(
    blast: BlastAdapter,
    variants: dict[str, list[str]],
    *,
    target_db: str | Path | None,
    host_db: str | Path | None,
    cross_db: str | Path | None,
) -> dict:
    queries: dict[str, str] = {}
    for role, sequences in variants.items():
        for index, sequence in enumerate(sequences, start=1):
            queries[f"{role}_{index}"] = sequence
    output: dict = {"queries_tested": len(queries), "modes": {}}
    for mode, database, evalue in (
        ("target", target_db, 1e-5),
        ("host", host_db, 0.1),
        ("cross_species", cross_db, 1e-5),
    ):
        if not database:
            output["modes"][mode] = {"verdict": "NOT_CHECKED", "method": "not_run", "hits": 0}
            continue
        try:
            hits, method = blast.search(queries, database, evalue=evalue, mode=f"degenerate_{mode}")
        except ToolUnavailableError as exc:
            output["modes"][mode] = {"verdict": "NOT_CHECKED", "method": "not_run", "hits": 0, "reason": exc.message}
            continue
        strong = [hit for hit in hits if hit["identity_pct"] >= 95 and hit["query_coverage_pct"] >= 90]
        if mode == "target":
            hit_ids = {hit["query_id"] for hit in strong}
            verdict = "PASS" if set(queries).issubset(hit_ids) and method == "blastn" else ("CAUTION" if set(queries).issubset(hit_ids) else "FAIL")
        elif mode == "host":
            verdict = "FAIL" if strong else ("PASS" if method == "blastn" else "CAUTION")
        else:
            verdict = "CAUTION" if strong or method != "blastn" else "PASS"
        output["modes"][mode] = {"verdict": verdict, "method": method, "hits": len(hits), "strong_hits": len(strong)}
    verdicts = {item["verdict"] for item in output["modes"].values()}
    if "FAIL" in verdicts:
        output["rating"] = "FAIL"
    elif verdicts == {"PASS"}:
        output["rating"] = "PASS"
    else:
        output["rating"] = "PASS_WITH_WARNINGS"
    return output


def assess_degenerate_strategy(
    aligned: list[SequenceRecord],
    candidate: Candidate | None,
    primer3: Primer3Adapter,
    blast: BlastAdapter,
    config: dict,
    *,
    relaxation_round_inherited: int,
    target_db: str | Path | None,
    host_db: str | Path | None,
    cross_db: str | Path | None,
) -> dict | None:
    if candidate is None or len(aligned) < 2:
        return None
    width = len(aligned[0].sequence)
    if any(len(record.sequence) != width for record in aligned):
        return None
    reference_columns = _reference_columns(aligned[0].sequence)
    oligos = (
        ("forward", candidate.forward_primer, False),
        ("reverse", candidate.reverse_primer, True),
    )
    merged: dict[str, str] = {}
    positions: list[dict] = []
    for role, oligo, reverse_orientation in oligos:
        if oligo.position_start is None or oligo.position_end is None:
            return None
        if any(position not in reference_columns for position in range(oligo.position_start, oligo.position_end + 1)):
            return None
        merged_sequence, found = _merge_binding_site(
            aligned,
            reference_columns,
            oligo.position_start,
            oligo.position_end,
            reverse_orientation=reverse_orientation,
        )
        for item in found:
            item["oligo"] = role
            if role == "reverse":
                item["location"] = "reverse_primer"
            else:
                item["location"] = "forward_primer"
        merged[role] = merged_sequence
        positions.extend(found)
    settings = config["degenerate"]
    if len(positions) < int(settings["trigger_min_variant_sites"]):
        return None
    base = {
        "triggered": True,
        "relaxation_round_inherited": relaxation_round_inherited,
        "candidate_inherited": candidate.candidate_id,
        "variant_positions": sorted(positions, key=lambda item: (item["reference_position_1based"], item["oligo"])),
        "merged_primers": merged,
        "merged_primer_sequence": merged["forward"],
        "limitations": [
            "Tm极差阈值未经实验梯度PCR标定",
            "拆分枚举忽略混合引物池中变体间竞争或协同效应",
            "3'端简并位点的延伸效率差异不能仅由Tm极差描述",
        ],
    }
    if len(positions) > int(settings["max_degenerate_positions"]):
        return {
            **base,
            "combinations_tested": 0,
            "tm_spread_celsius": None,
            "tm_ranges_celsius": {},
            "feasibility": "INFEASIBLE",
            "notes": f"简并位点{len(positions)}>{settings['max_degenerate_positions']}，未枚举",
            "specificity": {"status": "NOT_RUN"},
        }
    forward_variants = expand_iupac(merged["forward"], maximum=int(settings["max_combinations"]))
    reverse_variants = expand_iupac(merged["reverse"], maximum=int(settings["max_combinations"]))
    combinations = len(forward_variants) * len(reverse_variants)
    if not forward_variants or not reverse_variants or combinations > int(settings["max_combinations"]):
        return {
            **base,
            "combinations_tested": 0,
            "tm_spread_celsius": None,
            "tm_ranges_celsius": {},
            "feasibility": "INFEASIBLE",
            "notes": f"枚举组合{combinations}>{settings['max_combinations']}，未执行",
            "specificity": {"status": "NOT_RUN"},
        }
    tm_values: dict[str, list[float]] = {
        "forward": [float(primer3.calc_tm(item)["tm_celsius"]) for item in forward_variants],
        "reverse": [float(primer3.calc_tm(item)["tm_celsius"]) for item in reverse_variants],
    }
    spreads = {role: max(values) - min(values) for role, values in tm_values.items()}
    spread = max(spreads.values())
    if spread <= float(settings["feasible_tm_spread_c"]):
        feasibility = "FEASIBLE"
    elif spread <= float(settings["marginal_tm_spread_c"]):
        feasibility = "MARGINAL"
    else:
        feasibility = "INFEASIBLE"
    specificity = _specificity_batch(
        blast,
        {"forward": forward_variants, "reverse": reverse_variants},
        target_db=target_db,
        host_db=host_db,
        cross_db=cross_db,
    )
    return {
        **base,
        "combinations_tested": combinations,
        "expanded_variants": {"forward": forward_variants, "reverse": reverse_variants},
        "tm_spread_celsius": round(spread, 3),
        "tm_ranges_celsius": {
            role: {"min": round(min(values), 3), "max": round(max(values), 3), "spread": round(spreads[role], 3)}
            for role, values in tm_values.items()
        },
        "feasibility": feasibility,
        "notes": f"简并引物最大Tm极差{spread:.1f}°C",
        "specificity": specificity,
    }
