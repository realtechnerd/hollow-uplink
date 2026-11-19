"""Turning measurements into a zone score, a recommendation and a plain-language verdict.

Thresholds are deliberately simple and written down here so judges (and residents)
can see exactly how a verdict was reached.
"""

import math

# Every zone is expected to be measured for these; confidence counts how many of them are covered.
CORE_QUANTITIES = ("radiation_usv_h", "air_quality_aqi", "temp_c")
# gas_vs_clean_air comes from the real MQ-2 gas sensor (hardware/uno_station): how many times its
# signal has risen above the clean air it measured at start-up. Only zones with one get it.
QUANTITIES = CORE_QUANTITIES + ("gas_vs_clean_air",)
LABEL = {"radiation_usv_h": "radiation", "air_quality_aqi": "air quality", "temp_c": "heat",
         "gas_vs_clean_air": "gas or smoke"}


def _interp(x: float, points: list[tuple[float, float]]) -> float:
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def subscore(quantity: str, value: float) -> float:
    """0 (deadly) .. 100 (fine) for one measurement."""
    if quantity == "radiation_usv_h":
        # Normal background is about 0.1-0.3 uSv/h. Compare on a log scale.
        pts = [(0.3, 100), (1, 80), (5, 50), (20, 20), (100, 0)]
        return _interp(math.log10(max(value, 1e-3)), [(math.log10(x), y) for x, y in pts])
    if quantity == "air_quality_aqi":
        return _interp(value, [(50, 100), (100, 80), (150, 60), (200, 40), (300, 15), (400, 0)])
    if quantity == "temp_c":
        return _interp(abs(value - 22), [(8, 100), (18, 60), (28, 20), (38, 0)])
    if quantity == "gas_vs_clean_air":
        # MQ-2 output drifts ~20% on its own; smoke or solvent vapour pushes it several times higher.
        return _interp(value, [(1.5, 100), (2, 80), (3, 50), (4, 25), (6, 0)])
    return 100.0


def recommendation(score: float) -> str:
    if score >= 80:
        return "outposts"
    if score >= 55:
        return "domes"
    if score >= 30:
        return "suits"
    return "not_safe"


REC_TEXT = {
    "outposts": "Safe enough for open-air outposts.",
    "domes": "Suited trips are fine; living here needs domes with filtered air.",
    "suits": "Short trips in full suits only.",
    "not_safe": "Stay out.",
    "unknown": "Not enough of the zone measured to judge yet.",
}


def describe(quantity: str, value: float) -> str:
    """Plain-language description of one measurement, e.g. 'radiation elevated (2.63 µSv/h)'."""
    if quantity == "radiation_usv_h":
        word = ("normal" if value <= 0.3 else "slightly raised" if value <= 1 else
                "elevated" if value <= 5 else "high" if value <= 20 else "dangerous")
        return f"radiation {word} ({value:.2f} µSv/h)"
    if quantity == "air_quality_aqi":
        word = ("good" if value <= 50 else "moderate" if value <= 100 else "poor" if value <= 150 else
                "unhealthy" if value <= 200 else "hazardous")
        return f"air {word} (AQI {value:.0f})"
    if quantity == "temp_c":
        return f"{value:.0f} °C"
    if quantity == "gas_vs_clean_air":
        word = ("normal" if value <= 1.5 else "slightly raised" if value <= 2 else "elevated" if value <= 3 else
                "high" if value <= 4 else "dangerous")
        return f"gas or smoke {word} ({value:.1f}× clean air)"
    return f"{quantity} {value}"


def verdict(measured: dict[str, float], rec: str, limiting: str | None, flagged_count: int,
            measured_pct: int = 100) -> str:
    parts = [describe(q, measured[q]) for q in QUANTITIES if q in measured]
    text = (parts[0][0].upper() + ", ".join(parts)[1:] + ". ") if parts else "No machine has measured conditions here yet. "
    text += REC_TEXT[rec]
    if parts and measured_pct < 100:
        text += f" Based on {measured_pct}% of the zone."
    if rec in ("domes", "suits", "not_safe") and limiting:
        text += f" Main concern: {LABEL[limiting]}."
    if flagged_count:
        text += f" Ignoring {flagged_count} faulty sensor{'s' if flagged_count > 1 else ''}."
    return text
