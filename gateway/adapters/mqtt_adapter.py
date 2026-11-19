"""Adapter for MQTT-native machines that publish JSON in vendor-specific shapes."""

from typing import Any

from hollow import geo

from .base import Fingerprint


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """{"obs": {"humidity": 40}} -> {"obs.humidity": 40}"""
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            out.update(flatten(value, f"{prefix}{key}."))
    else:
        out[prefix.rstrip(".")] = obj
    return out


# (kind, keys that must all be present, description). First match wins.
RULES = [
    ("scout_robot", {"pose.x", "pose.y", "scan.hits"}, "reports a pose and LiDAR scan hits"),
    ("radiation_probe", {"cpm", "dose_rate"}, "has Geiger count (cpm) and dose_rate"),
    ("weather_mast", {"obs.temperature_f", "obs.humidity"}, "nested obs block with temperature_f and humidity"),
    ("air_sensor", {"pm25", "co2_ppm", "aqi"}, "has pm25, co2_ppm and aqi fields"),
    ("field_station", {"gas.adc", "gas.base", "gas.warm"}, "raw gas ADC counts with its own clean-air baseline"),
]

# kind -> {raw flattened key: (common key, converter)}
TRANSLATIONS = {
    "air_sensor": {
        "pm25": ("pm25_ugm3", float),
        "co2_ppm": ("co2_ppm", float),
        "t": ("temp_c", float),
        "aqi": ("air_quality_aqi", float),
    },
    "radiation_probe": {
        "dose_rate": ("radiation_usv_h", float),
        "cpm": ("radiation_cpm", float),
    },
    "weather_mast": {
        "obs.temperature_f": ("temp_c", lambda f: round((float(f) - 32) * 5 / 9, 2)),
        "obs.humidity": ("humidity_pct", float),
        "obs.wind_kph": ("wind_kph", float),
    },
    "scout_robot": {
        "battery": ("battery_pct", float),
        "env.rad": ("radiation_usv_h", float),
        "env.aqi": ("air_quality_aqi", float),
        "env.temp": ("temp_c", float),
    },
    # BX-1 field station (the real Arduino in hardware/uno_station). Gas is handled in translate().
    "field_station": {
        "dht.t": ("temp_c", float),
        "ds.t": ("temp_c", float),          # 18B20 (±0.5 °C); listed after dht.t so it wins when present
        "dht.rh": ("humidity_pct", float),
        "light": ("light_pct", lambda adc: round(float(adc) / 10.23, 1)),
        "hatch": ("hatch_open", float),
        "relay": ("relay_on", float),
    },
}

# Scout vendor surface codes -> common survey classes
SCOUT_SURFACE_CODES = {
    0: "open", 1: "rubble", 2: "building", 3: "building_damaged", 4: "vegetation", 5: "water",
}


def _scout_cells(hits: Any) -> list[list]:
    cells = []
    if not isinstance(hits, list):
        return cells
    for hit in hits:
        try:
            gx, gy, height, code = hit
            cells.append([int(gx), int(gy), round(float(height), 1), SCOUT_SURFACE_CODES[int(code)]])
        except (TypeError, ValueError, KeyError):
            continue  # drop malformed hits rather than the whole scan
    return cells


class MqttAdapter:
    protocol = "mqtt"

    def fingerprint(self, raw: Any) -> Fingerprint:
        if not isinstance(raw, dict):
            return Fingerprint("unknown", 0.0, "payload is not a JSON object")
        keys = set(flatten(raw))
        for kind, required, why in RULES:
            if required <= keys:
                return Fingerprint(kind, 0.9, why)
        return Fingerprint("unknown", 0.1, f"no rule matched keys: {', '.join(sorted(keys))}")

    def translate(self, kind: str, raw: Any) -> dict[str, float]:
        flat = flatten(raw)
        readings: dict[str, float] = {}
        for raw_key, (common_key, convert) in TRANSLATIONS.get(kind, {}).items():
            if raw_key in flat:
                try:
                    readings[common_key] = convert(flat[raw_key])
                except (TypeError, ValueError):
                    pass
        # Radiation probes may report in other units; only trust uSv/h as-is.
        if kind == "radiation_probe" and raw.get("unit") not in (None, "uSv/h"):
            readings.pop("radiation_usv_h", None)
        if kind == "field_station":
            # The MQ-2 only gives a raw voltage; the meaningful number is how far it has risen above the
            # clean air it measured after warm-up. Before warm-up finishes there's nothing to report.
            gas = raw.get("gas") or {}
            try:
                if not gas.get("warm") and float(gas["base"]) > 0:
                    readings["gas_vs_clean_air"] = round(float(gas["adc"]) / float(gas["base"]), 2)
            except (KeyError, TypeError, ValueError):
                pass
        return readings

    def extras(self, kind: str, raw: Any) -> dict[str, Any]:
        if kind != "scout_robot":
            return {}
        pose = raw.get("pose", {})
        scan = raw.get("scan", {})
        out: dict[str, Any] = {}
        try:
            out["location"] = {"x": round(float(pose["x"]), 1), "y": round(float(pose["y"]), 1)}
            out["heading_deg"] = round(float(pose.get("theta", 0)), 1)
        except (KeyError, TypeError, ValueError):
            pass
        cells = _scout_cells(scan.get("hits"))
        if cells:
            out["survey"] = {"cell_size": int(scan.get("cell", 10)), "cells": cells}
        task = raw.get("task")
        if isinstance(task, dict) and task.get("ref"):
            out["task"] = {"task_id": str(task["ref"]), "state": str(task.get("state", "unknown"))}
        return out

    def command(self, kind: str, cmd: dict) -> dict | None:
        """SCOUT-MK3 vendor commands: {"op": "goto", "x", "y", "ref"} and {"op": "halt", "ref"}.
        BX-1 field station: {"op": "relay", "on": bool, "ref"}; a stop (kill switch) switches it off."""
        if kind == "field_station":
            if cmd.get("type") == "stop":
                return {"op": "relay", "on": False, "ref": cmd.get("task_id")}
            if cmd.get("type") == "relay":
                return {"op": "relay", "on": bool((cmd.get("params") or {}).get("on")), "ref": cmd.get("task_id")}
            return None
        if kind != "scout_robot":
            return None
        params = cmd.get("params") or {}
        ref = cmd.get("task_id")
        kind_of_task = cmd.get("type")
        if kind_of_task == "stop":
            return {"op": "halt", "ref": ref}
        if kind_of_task == "investigate" and "x" in params and "y" in params:
            return {"op": "goto", "x": float(params["x"]), "y": float(params["y"]), "ref": ref}
        if kind_of_task == "scout_zone" and params.get("zone", "").startswith("Z"):
            idx = int(params["zone"][1:]) - 1
            w, h = geo.MAP_W / geo.ZONE_COLS, geo.MAP_H / geo.ZONE_ROWS
            x, y = (idx % geo.ZONE_COLS + 0.5) * w, (idx // geo.ZONE_COLS + 0.5) * h
            return {"op": "goto", "x": x, "y": y, "ref": ref}
        return None
