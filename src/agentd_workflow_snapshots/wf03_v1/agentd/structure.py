"""P6 ViennaRNA ssDNA amplicon secondary-structure adapter."""

from __future__ import annotations

import threading
from importlib import metadata

from .errors import InputValidationError, ToolUnavailableError
from .sequence import normalize_sequence


class ViennaRNAAdapter:
    _lock = threading.RLock()

    def __init__(self, config: dict) -> None:
        self.config = config
        self._module = None
        self._load_error: str | None = None
        try:
            import RNA  # type: ignore

            self._module = RNA
        except (ImportError, OSError) as exc:
            self._load_error = str(exc)

    @property
    def available(self) -> bool:
        return self._module is not None

    @property
    def version(self) -> str | None:
        if self._module is None:
            return None
        try:
            return metadata.version("ViennaRNA")
        except metadata.PackageNotFoundError:
            return getattr(self._module, "__version__", "unknown")

    def status(self) -> dict:
        dna_parameters = False
        error = self._load_error
        if self._module is not None:
            try:
                with self._lock:
                    dna_parameters = bool(self._module.params_load_DNA_Mathews2004())
            except Exception as exc:  # pragma: no cover - native-library boundary
                error = str(exc)
        return {
            "tool": "P6_ViennaRNA",
            "available": self.available and dna_parameters,
            "version": self.version,
            "dna_params_available": dna_parameters,
            "capability": "P1_DEFAULT" if self.available and dna_parameters else "P0b_SKIP",
            "error": error,
        }

    @staticmethod
    def not_evaluated(reason: str) -> dict:
        return {
            "status": "not_evaluated",
            "mfe_dg": None,
            "mfe_structure": None,
            "tool": "not_evaluated",
            "reason": reason,
            "warnings": ["二级结构未评估"],
        }

    def _fold(self, amplicon_sequence: str):
        if not self.available:
            raise ToolUnavailableError("ViennaRNA不可用", details=self.status())
        sequence = normalize_sequence(amplicon_sequence)
        if len(sequence) > 500:
            raise InputValidationError("P6仅支持长度≤500nt的扩增子", details={"length_nt": len(sequence)})
        settings = self.config["structure"]
        with self._lock:
            loaded = bool(self._module.params_load_DNA_Mathews2004())
            if not loaded:
                raise ToolUnavailableError("ViennaRNA DNA Mathews2004参数不可用")
            md = self._module.md()
            md.temperature = float(settings["temperature_c"])
            md.salt = float(settings["salt_na_mm"]) / 1000.0
            md.uniq_ML = 1
            compound = self._module.fold_compound(sequence.replace("T", "U"), md)
            structure, mfe = compound.mfe()
        return compound, str(structure), float(mfe)

    def _classify(self, dg: float) -> tuple[str, list[str]]:
        settings = self.config["structure"]
        warning = float(settings["warning_below_dg_kcal"])
        caution = float(settings["caution_below_dg_kcal"])
        if dg < warning:
            return "WARNING", [f"扩增区域存在稳定二级结构 ΔG={dg:.1f} kcal/mol (<{warning:g})，可能阻碍延伸"]
        if dg < caution:
            return "CAUTION", [f"扩增区域存在中等二级结构 ΔG={dg:.1f} kcal/mol，建议关注"]
        return "PASS", []

    def assess(self, amplicon_sequence: str) -> dict:
        _compound, structure, mfe = self._fold(amplicon_sequence)
        status, warnings = self._classify(mfe)
        settings = self.config["structure"]
        return {
            "status": status,
            "mfe_dg": round(mfe, 1),
            "mfe_structure": structure,
            "temperature_celsius": float(settings["temperature_c"]),
            "salt_na_mm": float(settings["salt_na_mm"]),
            "parameter_set": "SantaLucia2004_DNA",
            "tool": "ViennaRNA",
            "tool_version": self.version,
            "warnings": warnings,
        }

    def assess_full(
        self,
        amplicon_sequence: str,
        *,
        num_subopt: int | None = None,
        subopt_delta_kcal: float | None = None,
    ) -> dict:
        compound, structure, mfe = self._fold(amplicon_sequence)
        settings = self.config["structure"]
        limit = int(num_subopt if num_subopt is not None else settings["num_subopt"])
        delta = float(subopt_delta_kcal if subopt_delta_kcal is not None else settings["subopt_delta_kcal"])
        collected: list[dict] = []

        def collect(item_structure, energy, data) -> None:
            if item_structure is not None and len(data) < limit:
                data.append({"structure": str(item_structure), "dg": round(float(energy), 1)})

        with self._lock:
            compound.subopt_cb(int(delta * 100), collect, collected)
        all_dg = [mfe] + [float(item["dg"]) for item in collected]
        most_stable = min(all_dg)
        status, warnings = self._classify(most_stable)
        return {
            "status": status,
            "mfe_dg": round(mfe, 1),
            "mfe_structure": structure,
            "most_stable_dg": round(most_stable, 1),
            "subopt_structures": collected,
            "ensemble_diversity": f"{len({item['structure'] for item in collected})}/{len(collected)}",
            "temperature_celsius": float(settings["temperature_c"]),
            "salt_na_mm": float(settings["salt_na_mm"]),
            "parameter_set": "SantaLucia2004_DNA",
            "confidence": "medium",
            "tool": "ViennaRNA",
            "tool_version": self.version,
            "warnings": warnings,
        }
