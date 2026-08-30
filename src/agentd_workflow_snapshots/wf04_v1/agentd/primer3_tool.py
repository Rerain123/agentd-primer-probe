"""P1 Primer3 adapter.

The development fallback is intentionally identifiable in every measurement and
never claims SantaLucia thermodynamics.
"""

from __future__ import annotations

import copy
import importlib.util
import hashlib
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from importlib import metadata
from pathlib import Path
from typing import Any

from .errors import ToolUnavailableError
from .models import Candidate, Oligo
from .sequence import (
    gc_percent,
    longest_three_prime_complement,
    normalize_sequence,
    reverse_complement,
    wallace_tm,
)


def _kcal(value: float | None) -> float | None:
    """Convert Primer3 ``ThermoResult.dg`` from cal/mol to kcal/mol."""
    if value is None:
        return None
    return round(float(value) / 1000.0, 6)


class Primer3Adapter:
    _native_probe_cache: dict[str, tuple[bool, dict | None]] = {}

    def __init__(self, config: dict, *, allow_fallback: bool = False) -> None:
        self.config = config
        self.allow_fallback = allow_fallback
        self._module = None
        module_spec = importlib.util.find_spec("primer3")
        self._installed = module_spec is not None
        self._probe_error: dict | None = None
        self._import_root: str | None = None
        self.last_design_evidence: dict = {}
        self._ascii_mirror_used = False
        if self._installed:
            # primer3-py contains a native extension.  Probe it in a child
            # process first so a DLL/ABI/path crash cannot terminate the API.
            probe_executable = getattr(sys, "_base_executable", sys.executable)
            site_packages = sysconfig.get_paths()["purelib"]
            probe_import_root = site_packages
            source_package = None
            if module_spec and module_spec.submodule_search_locations:
                source_package = Path(next(iter(module_spec.submodule_search_locations))).resolve()
                probe_import_root = str(source_package.parent)
            if os.name == "nt" and source_package and not str(source_package).isascii():
                try:
                    probe_import_root = str(self._mirror_to_ascii_path(source_package))
                    self._ascii_mirror_used = True
                except OSError as exc:
                    self._probe_error = {
                        "returncode": None,
                        "stderr": f"无法创建Primer3 ASCII运行缓存: {exc}",
                        "stdout": "",
                    }
            self._import_root = probe_import_root
            probe_code = (
                "import sys; "
                f"sys.path.insert(0, {probe_import_root!r}); "
                f"sys.path.insert(1, {site_packages!r}); "
                "import primer3; print('ok')"
            )
            cache_key = f"{probe_executable}|{probe_import_root}|{self.version}"
            cached = self._native_probe_cache.get(cache_key)
            if self._probe_error is not None:
                probe_ok = False
                probe_error = self._probe_error
            elif cached is None:
                try:
                    probe = subprocess.run(
                        [probe_executable, "-B", "-c", probe_code],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=5,
                        check=False,
                    )
                    probe_ok = probe.returncode == 0
                    probe_error = None if probe_ok else {
                        "returncode": probe.returncode,
                        "stderr": probe.stderr[-1500:],
                        "stdout": probe.stdout[-500:],
                    }
                except subprocess.TimeoutExpired as exc:
                    probe_ok = False
                    probe_error = {
                        "returncode": None,
                        "stderr": "primer3 native import probe timed out after 5 seconds",
                        "stdout": (exc.stdout or "")[-500:] if isinstance(exc.stdout, str) else "",
                    }
                self._native_probe_cache[cache_key] = (probe_ok, probe_error)
            else:
                probe_ok, probe_error = cached
            if probe_ok:
                if probe_import_root not in sys.path:
                    sys.path.insert(0, probe_import_root)
                import primer3  # type: ignore

                self._module = primer3
            else:
                self._probe_error = probe_error

    def _mirror_to_ascii_path(self, source_package: Path) -> Path:
        cache_base = Path(os.environ.get("AGENTD_ASCII_CACHE_DIR", tempfile.gettempdir())).resolve()
        if not str(cache_base).isascii():
            raise OSError(
                "临时目录仍包含非ASCII字符，请通过AGENTD_ASCII_CACHE_DIR配置ASCII路径"
            )
        source_stamp = f"{source_package}|{source_package.stat().st_mtime_ns}|{self.version}"
        digest = hashlib.sha256(source_stamp.encode("utf-8")).hexdigest()[:12]
        import_root = cache_base / "agentd-primer3-cache" / f"{self.version}-{digest}"
        destination = import_root / "primer3"
        marker = import_root / ".complete"
        if not marker.exists():
            import_root.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source_package, destination, dirs_exist_ok=True)
            marker.write_text(source_stamp, encoding="utf-8")
        return import_root

    @property
    def available(self) -> bool:
        return self._module is not None

    @property
    def version(self) -> str | None:
        if not self._installed:
            return None
        try:
            return metadata.version("primer3-py")
        except metadata.PackageNotFoundError:
            return getattr(self._module, "__version__", "unknown")

    def status(self) -> dict:
        return {
            "tool": "P1_Primer3",
            "available": self.available,
            "installed": self._installed,
            "version": self.version,
            "native_probe_error": self._probe_error,
            "import_root": self._import_root,
            "ascii_mirror_used": self._ascii_mirror_used,
            "fallback_enabled": self.allow_fallback,
            "fallback_method": "wallace_tm_no_structure" if self.allow_fallback else None,
        }

    def _thermo_kwargs(self, *, probe: bool = False) -> dict:
        thermo = self.config["thermodynamics"]
        return {
            "mv_conc": float(thermo["mv_conc_mm"]),
            "dv_conc": float(thermo["dv_conc_mm"]),
            "dntp_conc": float(thermo["dntp_conc_mm"]),
            "dna_conc": float(thermo["probe_dna_conc_nm"] if probe else thermo["primer_dna_conc_nm"]),
        }

    def _structure_thermo_kwargs(self, *, probe: bool = False) -> dict:
        thermo = self.config["thermodynamics"]
        return {
            **self._thermo_kwargs(probe=probe),
            "temp_c": float(thermo["temperature_c"]),
            "max_loop": int(thermo.get("max_loop", 30)),
        }

    def _calc_tm(self, sequence: str, *, probe: bool = False) -> tuple[float, str]:
        if self.available:
            value = self._module.calc_tm(  # type: ignore[union-attr]
                sequence,
                salt_corrections_method="santalucia",
                **self._thermo_kwargs(probe=probe),
            )
            return round(float(value), 6), "primer3_santalucia_1998"
        if self.allow_fallback:
            return wallace_tm(sequence), "wallace_development_fallback"
        raise ToolUnavailableError("primer3-py不可用", details={"install_extra": "scientific"})

    def calc_tm(self, sequence: str, *, probe: bool = False) -> dict:
        """Public P1 Tm endpoint used by P3, WF-05 and degenerate analysis."""
        normalized = normalize_sequence(sequence)
        value, method = self._calc_tm(normalized, probe=probe)
        return {"tm_celsius": value, "method": method, "sequence_5to3": normalized}

    def _thermo_result(self, function_name: str, *sequences: str, probe: bool = False) -> dict:
        if not self.available:
            return {"tm": None, "dg_kcal": None, "structure_found": None}
        function = getattr(self._module, function_name)
        result = function(*sequences, **self._structure_thermo_kwargs(probe=probe))
        return {
            "tm": round(float(result.tm), 6),
            "dg_kcal": _kcal(result.dg),
            "structure_found": bool(result.structure_found),
        }

    def calc_heterodimer(self, sequence_1: str, sequence_2: str) -> dict:
        """Public P1 thermodynamic endpoint consumed by P2."""
        return self._thermo_result(
            "calc_heterodimer",
            normalize_sequence(sequence_1),
            normalize_sequence(sequence_2),
        )

    def _oligo(
        self,
        sequence: str,
        *,
        start: int | None,
        end: int | None,
        supplied_tm: float | None = None,
        probe: bool = False,
    ) -> Oligo:
        sequence = normalize_sequence(sequence)
        tm, method = self._calc_tm(sequence, probe=probe)
        hairpin = self._thermo_result("calc_hairpin", sequence, probe=probe)
        homodimer = self._thermo_result("calc_homodimer", sequence, probe=probe)
        three_prime = self._thermo_result("calc_homodimer", sequence[-6:], probe=probe)
        return Oligo(
            sequence_5to3=sequence,
            length_nt=len(sequence),
            tm_celsius=tm,
            gc_percent=gc_percent(sequence),
            position_start=start,
            position_end=end,
            hairpin_dg_kcal=hairpin["dg_kcal"],
            homodimer_dg_kcal=homodimer["dg_kcal"],
            three_prime_dimer_dg_kcal=three_prime["dg_kcal"],
            thermo_method=method,
        )

    def design(
        self,
        template: str,
        *,
        sequence_id: str,
        parameters: dict | None = None,
        target_region: tuple[int, int] | None = None,
    ) -> list[Candidate]:
        template = normalize_sequence(template)
        parameters = parameters or self.config["standard_design"]
        self.last_design_evidence = {
            "tool": "Primer3",
            "status": "STARTED",
            "sequence_args": {
                "SEQUENCE_ID": sequence_id,
                "SEQUENCE_TEMPLATE": template,
                "SEQUENCE_TARGET": list(target_region) if target_region else None,
            },
            "effective_parameters": copy.deepcopy(parameters),
            "raw_output": None,
        }
        if self.available:
            try:
                return self._design_primer3(
                    template,
                    sequence_id=sequence_id,
                    parameters=parameters,
                    target_region=target_region,
                )
            except Exception as exc:
                self.last_design_evidence["status"] = "FAILED"
                self.last_design_evidence["error"] = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
                raise
        if self.allow_fallback:
            candidates = self._design_heuristic(
                template,
                sequence_id=sequence_id,
                parameters=parameters,
                target_region=target_region,
            )
            self.last_design_evidence = {
                "tool": "Primer3",
                "status": "DEVELOPMENT_FALLBACK",
                "sequence_args": {
                    "SEQUENCE_ID": sequence_id,
                    "SEQUENCE_TEMPLATE": template,
                    "SEQUENCE_TARGET": list(target_region) if target_region else None,
                },
                "effective_parameters": copy.deepcopy(parameters),
                "raw_output": [candidate.to_dict() for candidate in candidates],
            }
            return candidates
        self.last_design_evidence = {
            "tool": "Primer3",
            "status": "UNAVAILABLE",
            "sequence_args": {
                "SEQUENCE_ID": sequence_id,
                "SEQUENCE_TEMPLATE": template,
            },
            "effective_parameters": copy.deepcopy(parameters),
            "raw_output": None,
        }
        raise ToolUnavailableError("primer3-py不可用", details={"install": "pip install -e .[scientific]"})

    def _global_args(self, parameters: dict) -> dict:
        standard = self.config["standard_design"]
        thermo = self.config["thermodynamics"]
        return {
            "PRIMER_TASK": "pick_pcr_primers_and_hyb_probe",
            "PRIMER_PICK_LEFT_PRIMER": 1,
            "PRIMER_PICK_RIGHT_PRIMER": 1,
            "PRIMER_PICK_INTERNAL_OLIGO": 1,
            "PRIMER_NUM_RETURN": int(self.config["maximum_candidates"]),
            "PRIMER_PRODUCT_SIZE_RANGE": [[int(parameters.get("product_min_bp", standard["product_min_bp"])), int(parameters.get("product_max_bp", standard["product_max_bp"]))]],
            "PRIMER_OPT_SIZE": int(parameters.get("primer_opt_size", standard["primer_opt_size"])),
            "PRIMER_MIN_SIZE": int(parameters.get("primer_min_size", standard["primer_min_size"])),
            "PRIMER_MAX_SIZE": int(parameters.get("primer_max_size", standard["primer_max_size"])),
            "PRIMER_OPT_TM": float(parameters.get("primer_opt_tm", standard["primer_opt_tm"])),
            "PRIMER_MIN_TM": float(parameters.get("primer_min_tm", standard["primer_min_tm"])),
            "PRIMER_MAX_TM": float(parameters.get("primer_max_tm", standard["primer_max_tm"])),
            "PRIMER_MAX_DIFF_TM": 2.0,
            "PRIMER_MAX_END_GC": 2,
            "PRIMER_MIN_GC": float(parameters.get("primer_min_gc", standard["primer_min_gc"])),
            "PRIMER_MAX_GC": float(parameters.get("primer_max_gc", standard["primer_max_gc"])),
            "PRIMER_INTERNAL_OPT_SIZE": int(parameters.get("probe_opt_size", standard["probe_opt_size"])),
            "PRIMER_INTERNAL_MIN_SIZE": int(parameters.get("probe_min_size", standard["probe_min_size"])),
            "PRIMER_INTERNAL_MAX_SIZE": int(parameters.get("probe_max_size", standard["probe_max_size"])),
            "PRIMER_INTERNAL_OPT_TM": float(parameters.get("probe_opt_tm", standard["probe_opt_tm"])),
            "PRIMER_INTERNAL_MIN_TM": float(parameters.get("probe_min_tm", standard["probe_min_tm"])),
            "PRIMER_INTERNAL_MAX_TM": float(parameters.get("probe_max_tm", standard["probe_max_tm"])),
            "PRIMER_DNA_CONC": float(thermo["primer_dna_conc_nm"]),
            "PRIMER_INTERNAL_DNA_CONC": float(thermo["probe_dna_conc_nm"]),
            "PRIMER_SALT_MONOVALENT": float(thermo["mv_conc_mm"]),
            "PRIMER_SALT_DIVALENT": float(thermo["dv_conc_mm"]),
            "PRIMER_DNTP_CONC": float(thermo["dntp_conc_mm"]),
            "PRIMER_INTERNAL_SALT_MONOVALENT": float(thermo["mv_conc_mm"]),
            "PRIMER_INTERNAL_SALT_DIVALENT": float(thermo["dv_conc_mm"]),
            "PRIMER_INTERNAL_DNTP_CONC": float(thermo["dntp_conc_mm"]),
            "PRIMER_MAX_POLY_X": 3,
            "PRIMER_INTERNAL_MAX_POLY_X": 3,
            "PRIMER_INTERNAL_MAX_SELF_ANY": 8,
        }

    def _design_primer3(
        self,
        template: str,
        *,
        sequence_id: str,
        parameters: dict,
        target_region: tuple[int, int] | None,
    ) -> list[Candidate]:
        seq_args: dict[str, Any] = {"SEQUENCE_ID": sequence_id, "SEQUENCE_TEMPLATE": template}
        if target_region and target_region[1] < len(template):
            seq_args["SEQUENCE_TARGET"] = [int(target_region[0]), int(target_region[1])]
        design_function = getattr(self._module, "design_primers", None) or self._module.bindings.design_primers
        global_args = self._global_args(parameters)
        raw = design_function(seq_args, global_args)
        self.last_design_evidence = {
            "tool": "Primer3",
            "status": "COMPLETED",
            "sequence_args": copy.deepcopy(seq_args),
            "global_args": copy.deepcopy(global_args),
            "raw_output": copy.deepcopy(dict(raw)),
        }
        count = int(raw.get("PRIMER_PAIR_NUM_RETURNED", 0))
        candidates: list[Candidate] = []
        for index in range(count):
            try:
                left_pos, left_length = raw[f"PRIMER_LEFT_{index}"]
                right_end, right_length = raw[f"PRIMER_RIGHT_{index}"]
                probe_start, probe_length = raw[f"PRIMER_INTERNAL_{index}"]
                left_end = int(left_pos) + int(left_length) - 1
                right_start = int(right_end) - int(right_length) + 1
                probe_end = int(probe_start) + int(probe_length) - 1
                amplicon = template[int(left_pos) : int(right_end) + 1]
                forward = self._oligo(raw[f"PRIMER_LEFT_{index}_SEQUENCE"], start=int(left_pos), end=left_end, supplied_tm=raw.get(f"PRIMER_LEFT_{index}_TM"))
                reverse = self._oligo(raw[f"PRIMER_RIGHT_{index}_SEQUENCE"], start=right_start, end=int(right_end), supplied_tm=raw.get(f"PRIMER_RIGHT_{index}_TM"))
                probe = self._oligo(raw[f"PRIMER_INTERNAL_{index}_SEQUENCE"], start=int(probe_start), end=probe_end, supplied_tm=raw.get(f"PRIMER_INTERNAL_{index}_TM"), probe=True)
                pair = self._thermo_result("calc_heterodimer", forward.sequence_5to3, reverse.sequence_5to3)
                end_pair = self._thermo_result("calc_heterodimer", forward.sequence_5to3[-6:], reverse.sequence_5to3[-6:])
                candidate = Candidate(
                    candidate_id=f"{sequence_id}-p3-{index+1}",
                    forward_primer=forward,
                    reverse_primer=reverse,
                    probe=probe,
                    amplicon_sequence_5to3=amplicon,
                    amplicon_start=int(left_pos),
                    amplicon_end=int(right_end),
                    primer3_pair_penalty=float(raw.get(f"PRIMER_PAIR_{index}_PENALTY", 0.0)),
                    pair_heterodimer_dg_kcal=pair["dg_kcal"],
                    three_prime_heterodimer_dg_kcal=end_pair["dg_kcal"],
                    three_prime_involved=longest_three_prime_complement(forward.sequence_5to3, reverse.sequence_5to3) >= 3,
                    design_method="primer3",
                    metadata=self.metadata(),
                )
                candidates.append(candidate)
            except KeyError:
                continue
        return candidates

    def _design_heuristic(
        self,
        template: str,
        *,
        sequence_id: str,
        parameters: dict,
        target_region: tuple[int, int] | None,
    ) -> list[Candidate]:
        standard = self.config["standard_design"]
        product_min = int(parameters.get("product_min_bp", standard["product_min_bp"]))
        product_max = min(int(parameters.get("product_max_bp", standard["product_max_bp"])), len(template))
        min_tm = float(parameters.get("primer_min_tm", standard["primer_min_tm"]))
        max_tm = float(parameters.get("primer_max_tm", standard["primer_max_tm"]))
        min_gc = float(parameters.get("primer_min_gc", standard["primer_min_gc"]))
        max_gc = float(parameters.get("primer_max_gc", standard["primer_max_gc"]))
        primer_min_size = int(parameters.get("primer_min_size", standard["primer_min_size"]))
        primer_max_size = int(parameters.get("primer_max_size", standard["primer_max_size"]))
        candidates: list[Candidate] = []
        seen: set[tuple[str, str]] = set()
        left_bounds = range(0, min(60, max(1, len(template) - product_min)))
        for product_size in range(product_min, product_max + 1, 5):
            for left_start in left_bounds:
                right_end = left_start + product_size - 1
                if right_end >= len(template):
                    continue
                if target_region:
                    target_start, target_length = target_region
                    target_end = target_start + target_length - 1
                    if not (left_start <= target_start and right_end >= target_end):
                        continue
                for primer_length in range(primer_min_size, min(24, primer_max_size) + 1):
                    forward_seq = template[left_start : left_start + primer_length]
                    right_start = right_end - primer_length + 1
                    reverse_seq = reverse_complement(template[right_start : right_end + 1])
                    f_tm = wallace_tm(forward_seq)
                    r_tm = wallace_tm(reverse_seq)
                    if not (min_tm <= f_tm <= max_tm and min_tm <= r_tm <= max_tm and abs(f_tm - r_tm) <= 2):
                        continue
                    if not (min_gc <= gc_percent(forward_seq) <= max_gc and min_gc <= gc_percent(reverse_seq) <= max_gc):
                        continue
                    if any((seq[-5:].count("G") + seq[-5:].count("C")) > 2 or "GGGG" in seq for seq in (forward_seq, reverse_seq)):
                        continue
                    key = (forward_seq, reverse_seq)
                    if key in seen:
                        continue
                    probe_choice = self._choose_probe(template, left_start, left_start + primer_length - 1, right_start, right_end, (f_tm + r_tm) / 2, parameters)
                    if probe_choice is None:
                        continue
                    probe_seq, probe_start, probe_end = probe_choice
                    forward = self._oligo(forward_seq, start=left_start, end=left_start + primer_length - 1)
                    reverse = self._oligo(reverse_seq, start=right_start, end=right_end)
                    probe = self._oligo(probe_seq, start=probe_start, end=probe_end, probe=True)
                    candidate = Candidate(
                        candidate_id=f"{sequence_id}-fallback-{len(candidates)+1}",
                        forward_primer=forward,
                        reverse_primer=reverse,
                        probe=probe,
                        amplicon_sequence_5to3=template[left_start : right_end + 1],
                        amplicon_start=left_start,
                        amplicon_end=right_end,
                        primer3_pair_penalty=None,
                        pair_heterodimer_dg_kcal=None,
                        three_prime_heterodimer_dg_kcal=None,
                        three_prime_involved=longest_three_prime_complement(forward_seq, reverse_seq) >= 3,
                        design_method="heuristic_development_fallback",
                        metadata=self.metadata(fallback=True),
                        warnings=["Primer3不可用：当前候选由开发降级算法生成，不能用于实验决策"],
                    )
                    candidates.append(candidate)
                    seen.add(key)
                    if len(candidates) >= int(self.config["maximum_candidates"]):
                        return candidates
        return candidates

    def _choose_probe(
        self,
        template: str,
        left_start: int,
        left_end: int,
        right_start: int,
        right_end: int,
        mean_primer_tm: float,
        parameters: dict,
    ) -> tuple[str, int, int] | None:
        standard = self.config["standard_design"]
        min_size = int(parameters.get("probe_min_size", standard["probe_min_size"]))
        max_size = int(parameters.get("probe_max_size", standard["probe_max_size"]))
        min_tm = float(parameters.get("probe_min_tm", standard["probe_min_tm"]))
        opt_tm = float(parameters.get("probe_opt_tm", standard["probe_opt_tm"]))
        max_tm = float(parameters.get("probe_max_tm", standard["probe_max_tm"]))
        best: tuple[float, str, int, int] | None = None
        start_min = left_end + 5
        end_limit = right_start - 5
        for size in range(min_size, max_size + 1):
            for start in range(start_min, end_limit - size + 2):
                end = start + size - 1
                sequence = template[start : end + 1]
                if len(sequence) != size or sequence.startswith("G") or any(base * 4 in sequence for base in "ACGT"):
                    continue
                tm = wallace_tm(sequence)
                if not min_tm <= tm <= max_tm:
                    continue
                delta = tm - mean_primer_tm
                distance = abs(delta - 9.0) + abs(tm - opt_tm) / 10
                if best is None or distance < best[0]:
                    best = (distance, sequence, start, end)
        if best is None:
            return None
        return best[1], best[2], best[3]

    def validate_pair(
        self,
        forward: str,
        reverse: str,
        *,
        probe: str | None = None,
        template: str | None = None,
        sequence_id: str = "existing",
    ) -> Candidate:
        forward = normalize_sequence(forward)
        reverse = normalize_sequence(reverse)
        normalized_template = normalize_sequence(template) if template else ""
        left = normalized_template.find(forward) if normalized_template else -1
        reverse_binding = reverse_complement(reverse)
        right_start = normalized_template.find(reverse_binding, left + len(forward)) if normalized_template and left >= 0 else -1
        right_end = right_start + len(reverse_binding) - 1 if right_start >= 0 else -1
        amplicon = normalized_template[left : right_end + 1] if left >= 0 and right_start >= 0 else ""
        probe_start = -1
        probe_end = -1
        normalized_probe = normalize_sequence(probe) if probe else None
        if normalized_probe and normalized_template:
            probe_start = normalized_template.find(normalized_probe, max(0, left))
            if probe_start < 0:
                probe_start = normalized_template.find(reverse_complement(normalized_probe), max(0, left))
            probe_end = probe_start + len(normalized_probe) - 1 if probe_start >= 0 else -1
        f_oligo = self._oligo(forward, start=left if left >= 0 else None, end=left + len(forward) - 1 if left >= 0 else None)
        r_oligo = self._oligo(reverse, start=right_start if right_start >= 0 else None, end=right_end if right_end >= 0 else None)
        p_oligo = self._oligo(normalized_probe, start=probe_start if probe_start >= 0 else None, end=probe_end if probe_end >= 0 else None, probe=True) if normalized_probe else None
        pair = self._thermo_result("calc_heterodimer", forward, reverse)
        end_pair = self._thermo_result("calc_heterodimer", forward[-6:], reverse[-6:])
        return Candidate(
            candidate_id=f"{sequence_id}-existing-1",
            forward_primer=f_oligo,
            reverse_primer=r_oligo,
            probe=p_oligo,
            amplicon_sequence_5to3=amplicon,
            amplicon_start=left if left >= 0 else 0,
            amplicon_end=right_end,
            pair_heterodimer_dg_kcal=pair["dg_kcal"],
            three_prime_heterodimer_dg_kcal=end_pair["dg_kcal"],
            three_prime_involved=longest_three_prime_complement(forward, reverse) >= 3,
            design_method="existing_primer_validation",
            metadata=self.metadata(fallback=not self.available),
        )

    def metadata(self, *, fallback: bool = False) -> dict:
        thermo = self.config["thermodynamics"]
        return {
            "software": "development_fallback" if fallback else "primer3-py",
            "software_version": "internal-0.1" if fallback else self.version,
            "tm_algorithm": "Wallace approximation (development only)" if fallback else thermo["algorithm"],
            "mv_conc_mm": thermo["mv_conc_mm"],
            "dv_conc_mm": thermo["dv_conc_mm"],
            "dntp_conc_mm": thermo["dntp_conc_mm"],
            "oligo_conc_nm": thermo["primer_dna_conc_nm"],
        }
