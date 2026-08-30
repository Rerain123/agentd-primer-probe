"""P4 multiplex fluorescence and thermodynamic compatibility evaluation."""

from __future__ import annotations

import itertools

from .errors import InputValidationError
from .primer3_tool import Primer3Adapter
from .sequence import normalize_sequence


FLUOROPHORE_DB = {
    "FAM": {"ex": 495, "em": 520, "brightness": 1.00, "quencher": "BHQ-1"},
    "TET": {"ex": 521, "em": 538, "brightness": 0.25, "quencher": "BHQ-1"},
    "JOE": {"ex": 529, "em": 555, "brightness": 0.35, "quencher": "BHQ-1"},
    "Yakima Yellow": {"ex": 530, "em": 550, "brightness": 0.40, "quencher": "BHQ-1"},
    "HEX": {"ex": 535, "em": 556, "brightness": 0.40, "quencher": "BHQ-1"},
    "VIC": {"ex": 538, "em": 554, "brightness": 0.50, "quencher": "BHQ-1"},
    "SUN": {"ex": 538, "em": 554, "brightness": 0.50, "quencher": "BHQ-1"},
    "ABY": {"ex": 550, "em": 575, "brightness": None, "quencher": "QSY-7"},
    "JUN": {"ex": 585, "em": 615, "brightness": None, "quencher": "QSY-9"},
    "ROX": {"ex": 587, "em": 610, "brightness": 0.30, "quencher": "BHQ-2"},
    "Texas Red": {"ex": 595, "em": 615, "brightness": 0.25, "quencher": "BHQ-2"},
    "Quasar 670": {"ex": 647, "em": 670, "brightness": 0.25, "quencher": "BHQ-2"},
    "Cy5": {"ex": 649, "em": 670, "brightness": 0.25, "quencher": "BHQ-2"},
    "Cy5.5": {"ex": 675, "em": 695, "brightness": 0.15, "quencher": "BHQ-3"},
}

QUENCHER_DB = {
    "BHQ-1": (480, 580), "BHQ-2": (560, 670), "BHQ-3": (620, 730),
    "MGB-NFQ": (430, 700), "QSY-7": (500, 600), "QSY-9": (550, 650),
}

INSTRUMENT_DB = {
    "ABI 7500 Fast": {"channels": [(500, 525), (540, 565), (560, 594), (575, 625), (675, 710)], "needs_rox": True, "max_channels": 5},
    "QuantStudio 5": {"channels": [(470, 520), (520, 558), (550, 587), (580, 623), (640, 682)], "needs_rox": True, "max_channels": 5},
    "QuantStudio 6/7 Pro": {"channels": [(470, 520), (520, 558), (550, 587), (580, 623), (640, 682), (662, 711)], "needs_rox": True, "max_channels": 6},
    "CFX96": {"channels": [(470, 522), (525, 570), (575, 630), (635, 685), (678, 715)], "needs_rox": False, "max_channels": 5},
    "Bio-Rad CFX96 Touch": {"channels": [(470, 522), (525, 570), (575, 630), (635, 685), (678, 715)], "needs_rox": False, "max_channels": 5},
    "Roche LightCycler 480 II": {"channels": [(440, 488), (465, 510), (498, 580), (533, 610), (618, 640), (650, 660)], "needs_rox": False, "max_channels": 6},
    "Roche LightCycler 96": {"channels": [(470, 514), (533, 572), (577, 620), (645, 697)], "needs_rox": False, "max_channels": 4},
    "Qiagen Rotor-Gene Q": {"channels": [(470, 510), (530, 555), (585, 610), (625, 660), (680, 710)], "needs_rox": False, "max_channels": 6},
}


def recommended_quencher(dye: str, *, mgb: bool = False) -> str:
    if mgb:
        return "MGB-NFQ"
    if dye not in FLUOROPHORE_DB:
        raise InputValidationError("未知荧光基团", details={"fluorophore": dye})
    return str(FLUOROPHORE_DB[dye]["quencher"])


def channel_order(instrument: str, *, rox_as_reference: bool | None, include_internal: bool) -> list[str]:
    inst = INSTRUMENT_DB.get(instrument)
    if inst is None:
        raise InputValidationError("P4未配置该仪器", details={"instrument": instrument, "supported": sorted(INSTRUMENT_DB)})
    needs_rox = bool(inst and inst["needs_rox"] and rox_as_reference is not False)
    if needs_rox:
        order = ["FAM", "VIC", "ABY", "JUN", "Cy5", "Cy5.5"]
    else:
        order = ["FAM", "VIC", "ROX", "Cy5", "Cy5.5"]
    if include_internal:
        order = [item for item in order if item != "Cy5"]
    report_channels = int(inst["max_channels"]) - (1 if needs_rox else 0)
    target_channels = report_channels - (1 if include_internal else 0)
    return order[:target_channels]


class MultiplexEvaluator:
    def __init__(self, primer3: Primer3Adapter, config: dict) -> None:
        self.primer3 = primer3
        self.config = config

    def status(self) -> dict:
        return {
            "tool": "P4_Multiplex",
            "available": self.primer3.available,
            "version": "spectral-thermodynamic-1.0",
            "fluorophores": len(FLUOROPHORE_DB),
            "instruments": len(INSTRUMENT_DB),
            "base_dependency": self.primer3.status(),
        }

    @staticmethod
    def _validate_panel(panel: list[dict]) -> None:
        if not panel:
            raise InputValidationError("P4面板不能为空")
        required = {"target_id", "fwd_seq", "rev_seq", "probe_seq", "fwd_tm", "rev_tm", "probe_tm", "gc_percent", "fluorophore", "quencher"}
        for index, entry in enumerate(panel):
            missing = sorted(required - set(entry))
            if missing:
                raise InputValidationError("P4面板条目字段不完整", details={"index": index, "missing": missing})
            for field in ("fwd_seq", "rev_seq", "probe_seq"):
                normalize_sequence(entry[field])
            if entry["fluorophore"] not in FLUOROPHORE_DB:
                raise InputValidationError("未知荧光基团", details={"fluorophore": entry["fluorophore"]})

    def check_panel(
        self,
        panel: list[dict],
        *,
        instrument: str | None = None,
        rox_as_reference: bool | None = None,
        relaxed: bool = False,
        tm_spread_limit: float | None = None,
    ) -> dict:
        self._validate_panel(panel)
        settings = self.config["multiplex"]
        selected_instrument = instrument or settings["default_instrument"]
        if selected_instrument not in INSTRUMENT_DB:
            raise InputValidationError("P4未配置该仪器", details={"instrument": selected_instrument, "supported": sorted(INSTRUMENT_DB)})
        issues: list[str] = []
        spectral_status = "PASS"
        minimum_delta: float | None = None
        worst_spectral_pair: tuple[str, str] = ("", "")
        dyes = [str(entry["fluorophore"]) for entry in panel]
        for left, right in itertools.combinations(dyes, 2):
            delta = abs(float(FLUOROPHORE_DB[left]["em"]) - float(FLUOROPHORE_DB[right]["em"]))
            if minimum_delta is None or delta < minimum_delta:
                minimum_delta = delta
                worst_spectral_pair = (left, right)
            if left == right or {left, right} == {"VIC", "HEX"}:
                issues.append(f"[FAIL] {left} 与 {right} 不可光谱区分")
                spectral_status = "FAIL"
            elif delta < 15:
                issues.append(f"[FAIL] {left}-{right} 发射峰间距{delta:.0f}nm<15nm")
                spectral_status = "FAIL"
            elif delta < 30:
                issues.append(f"[CAUTION] {left}-{right} 发射峰间距{delta:.0f}nm")
                if spectral_status == "PASS":
                    spectral_status = "CAUTION"

        quencher_mismatches: list[str] = []
        for entry in panel:
            dye = str(entry["fluorophore"])
            quencher = str(entry["quencher"])
            if "TAMRA" in quencher.upper():
                issues.append(f"[FAIL] {entry['target_id']}使用TAMRA，不适用于多重检测")
                quencher_mismatches.append(f"{entry['target_id']}:TAMRA")
                continue
            normalized_quencher = "MGB-NFQ" if "MGB" in quencher.upper() else quencher
            absorption = QUENCHER_DB.get(normalized_quencher)
            if absorption is None or not absorption[0] <= int(FLUOROPHORE_DB[dye]["em"]) <= absorption[1]:
                mismatch = f"{quencher}不覆盖{dye}"
                quencher_mismatches.append(mismatch)
                issues.append(f"[CAUTION] {mismatch}")

        inst = INSTRUMENT_DB[selected_instrument]
        rox_conflict = bool(inst["needs_rox"] and rox_as_reference is not False and "ROX" in dyes)
        available_channels = int(inst["max_channels"]) - (1 if inst["needs_rox"] and rox_as_reference is not False else 0)
        if rox_conflict:
            issues.append("[FAIL] ROX通道被被动参比占用")
        if len(panel) > available_channels:
            issues.append(f"[FAIL] 面板数{len(panel)}超过仪器可用报告通道{available_channels}")
        unsupported: list[str] = []
        for dye in dyes:
            emission = float(FLUOROPHORE_DB[dye]["em"])
            if not any(abs(float(channel_em) - emission) <= 20 for _channel_ex, channel_em in inst["channels"]):
                unsupported.append(dye)
                issues.append(f"[CAUTION] {selected_instrument}无滤光片覆盖{dye}")

        oligos: list[tuple[str, str]] = []
        for entry in panel:
            for role in ("fwd", "rev", "probe"):
                oligos.append((f"{entry['target_id']}:{role}", entry[f"{role}_seq"]))
        worst_dg: float | None = None
        worst_pair: tuple[str, str] = ("", "")
        worst_three_prime: float | None = None
        three_prime_risks: list[dict] = []
        evaluated_pairs = 0
        for (left_id, left_seq), (right_id, right_seq) in itertools.combinations(oligos, 2):
            full = self.primer3.calc_heterodimer(left_seq, right_seq)
            end = self.primer3.calc_heterodimer(left_seq[-6:], right_seq[-6:])
            dg = full.get("dg_kcal")
            end_dg = end.get("dg_kcal")
            if dg is None or end_dg is None:
                continue
            evaluated_pairs += 1
            if worst_dg is None or float(dg) < worst_dg:
                worst_dg = float(dg)
                worst_pair = (left_id, right_id)
            if worst_three_prime is None or float(end_dg) < worst_three_prime:
                worst_three_prime = float(end_dg)
            if float(end_dg) <= float(settings["three_prime_dimer_dg_kcal"]):
                three_prime_risks.append({"pair": [left_id, right_id], "dg_kcal": round(float(end_dg), 3)})
        dimer_limit = float(settings["relaxed_cross_dimer_dg_kcal"] if relaxed else settings["standard_cross_dimer_dg_kcal"])
        if evaluated_pairs == 0:
            issues.append("[CAUTION] P1不可用，交叉二聚体未评估")
        elif worst_dg is not None and worst_dg <= float(settings["relaxed_cross_dimer_dg_kcal"]):
            issues.append(f"[FAIL] 最差交叉二聚体ΔG={worst_dg:.1f} kcal/mol")
        elif worst_dg is not None and worst_dg <= dimer_limit:
            issues.append(f"[CAUTION] 最差交叉二聚体ΔG={worst_dg:.1f} kcal/mol")
        if three_prime_risks:
            issues.append(f"[CAUTION] 发现{len(three_prime_risks)}个3′端局部二聚体风险")

        primer_tms = [float(entry[key]) for entry in panel for key in ("fwd_tm", "rev_tm")]
        probe_tms = [float(entry["probe_tm"]) for entry in panel]
        primer_spread = max(primer_tms) - min(primer_tms)
        probe_spread = max(probe_tms) - min(probe_tms)
        selected_tm_limit = float(tm_spread_limit if tm_spread_limit is not None else settings["tm_spread_max_c"])
        if primer_spread > selected_tm_limit:
            issues.append(f"[CAUTION] 引物Tm极差{primer_spread:.1f}°C>{selected_tm_limit:.1f}°C")
        if probe_spread > selected_tm_limit:
            issues.append(f"[CAUTION] 探针Tm极差{probe_spread:.1f}°C>{selected_tm_limit:.1f}°C")
        gc_values = [float(entry["gc_percent"]) for entry in panel]
        gc_spread = max(gc_values) - min(gc_values)
        if gc_spread > float(settings["gc_spread_warning_percent"]):
            issues.append(f"[CAUTION] 扩增子GC%极差{gc_spread:.1f}%>{settings['gc_spread_warning_percent']}%")
        elif gc_spread > float(settings["gc_spread_caution_percent"]):
            issues.append(f"[CAUTION] 扩增子GC%极差{gc_spread:.1f}%")

        verdict = "INCOMPATIBLE" if any(issue.startswith("[FAIL]") for issue in issues) else ("MARGINAL" if issues else "COMPATIBLE")
        return {
            "verdict": verdict,
            "relaxed": relaxed,
            "thresholds": {"cross_dimer_dg_kcal": dimer_limit, "tm_spread_celsius": selected_tm_limit},
            "spectral_check": {"min_delta_em_nm": round(minimum_delta or 0.0, 1), "worst_pair": list(worst_spectral_pair), "status": spectral_status},
            "quencher_check": {"all_matched": not quencher_mismatches, "mismatches": quencher_mismatches},
            "instrument_check": {"instrument": selected_instrument, "all_supported": not unsupported, "unsupported_dyes": sorted(set(unsupported)), "rox_conflict": rox_conflict, "available_channels": available_channels, "needs_rox": bool(inst["needs_rox"])},
            "heterodimer": {"total_pairs": len(oligos) * (len(oligos) - 1) // 2, "evaluated_pairs": evaluated_pairs, "worst_pair": list(worst_pair), "worst_dg_kcal": round(worst_dg, 3) if worst_dg is not None else None, "worst_dg_cal_per_mol": round(worst_dg * 1000, 1) if worst_dg is not None else None, "worst_three_prime_dg_kcal": round(worst_three_prime, 3) if worst_three_prime is not None else None, "three_prime_risks": three_prime_risks},
            "tm_spread_celsius": {"primers": round(primer_spread, 3), "probes": round(probe_spread, 3)},
            "gc_spread_percent": round(gc_spread, 3),
            "issues": issues,
        }
