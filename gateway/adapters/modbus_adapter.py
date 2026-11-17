"""Adapter for Modbus TCP field devices.

Modbus is nothing like MQTT: devices never announce themselves, never say what a
number means, and only answer when polled. A register is a bare 16-bit integer.
So the gateway has to:

  1. scan the site's local network for anything answering Modbus TCP,
  2. ask each device who it is (function 43/14, Read Device Identification),
  3. look up that model's register map, and
  4. poll and decode registers: scaling, signed values, 32-bit values split across
     two registers, and vendors that disagree on which half comes first.

ModbusScanner (below) does 1-2 and polls; the adapter does 3-4. Its raw message is
{"identity": {name: value}, "registers": [int, ...]}, so it plugs into the gateway's
normal fingerprint -> translate -> sign path like any other protocol.
"""

import ipaddress
import logging
import re
import threading
import time
from typing import Any, Callable

from pymodbus.client import ModbusTcpClient

from .base import Fingerprint

log = logging.getLogger("gateway.modbus")
DT = ModbusTcpClient.DATATYPE
decode = ModbusTcpClient.convert_from_registers

# Standard Modbus device identification object ids (spec section 6.21).
ID_OBJECTS = {0: "VendorName", 1: "ProductCode", 2: "MajorMinorRevision", 3: "VendorUrl",
              4: "ProductName", 5: "ModelName", 6: "UserApplicationName"}


def _kestrel_arm200(r: list[int]) -> dict[str, float]:
    if not r[4] & 0x1:  # status bit 0 clear = detector fault; the numbers mean nothing
        return {}
    return {
        "radiation_usv_h": round(decode(r[0:2], DT.UINT32, word_order="big") / 1000, 3),
        "radiation_cpm": float(r[2]),
        "temp_c": decode(r[3:4], DT.INT16) / 10,
    }


def _halden_gm4(r: list[int]) -> dict[str, float]:
    # This vendor stores 32-bit floats low word first ("CDAB"); read them big-endian and you get nonsense.
    return {
        "air_quality_aqi": round(decode(r[0:2], DT.FLOAT32, word_order="little"), 1),
        "pm25_ugm3": round(decode(r[2:4], DT.FLOAT32, word_order="little"), 1),
        "co2_ppm": float(r[4]),
        "temp_c": round((decode(r[5:6], DT.INT16) / 10 - 32) * 5 / 9, 2),
    }


# (VendorName, ProductCode) -> what it is, which holding registers to read, how to decode them.
PROFILES: dict[tuple[str, str], dict[str, Any]] = {
    ("Kestrel Instruments", "ARM-200"): {"kind": "radiation_probe", "start": 0, "count": 5, "decode": _kestrel_arm200},
    ("Halden Controls", "GM-4"): {"kind": "air_sensor", "start": 0, "count": 6, "decode": _halden_gm4},
}


def profile_for(identity: dict[str, str]) -> dict[str, Any] | None:
    return PROFILES.get((identity.get("VendorName", ""), identity.get("ProductCode", "")))


class ModbusAdapter:
    protocol = "modbus"

    def fingerprint(self, raw: Any) -> Fingerprint:
        identity = (raw or {}).get("identity") or {}
        if not identity:
            return Fingerprint("unknown", 0.1, "answers Modbus but not Read Device Identification")
        name = f"{identity.get('VendorName', '?')} {identity.get('ProductCode', '?')}"
        profile = profile_for(identity)
        if profile is None:
            return Fingerprint("unknown", 0.3, f"Modbus device identifies as {name}; no register map for that model")
        return Fingerprint(profile["kind"], 0.95,
                           f"Modbus device identification: {name} ({identity.get('ProductName', 'no product name')})")

    def translate(self, kind: str, raw: Any) -> dict[str, float]:
        profile = profile_for(raw.get("identity") or {})
        registers = raw.get("registers") or []
        if profile is None or len(registers) < profile["count"]:
            return {}
        try:
            return profile["decode"](registers)
        except (ValueError, IndexError, TypeError):
            return {}

    def extras(self, kind: str, raw: Any) -> dict[str, Any]:
        return {}  # fixed devices: placed on the map by gateway config

    def command(self, kind: str, cmd: dict) -> dict | None:
        return None  # these models are read-only sensors


# ---------------------------------------------------------------- scanning and polling

def parse_targets(specs: list[str]) -> list[tuple[str, int]]:
    """"127.0.0.1:5020-5029", "192.168.4.0/28:502" or "10.0.0.7" (port 502) -> [(host, port), ...].
    Only private and loopback addresses: the gateway scans its own site, never the internet."""
    out = []
    for spec in specs:
        net, _, ports = spec.partition(":")
        lo, _, hi = (ports or "502").partition("-")
        network = ipaddress.ip_network(net, strict=False)
        if not (network.is_private or network.is_loopback):
            raise ValueError(f"refusing to scan {net}: not a local network")
        if network.num_addresses > 256:
            raise ValueError(f"refusing to scan {net}: more than 256 addresses")
        hosts = [network.network_address] if network.num_addresses == 1 else list(network.hosts())
        for host in hosts:
            for port in range(int(lo), int(hi or lo) + 1):
                out.append((str(host), port))
    return out


def safe_id(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", text).strip("-")[:48]


class ModbusScanner:
    """Finds Modbus TCP devices on the site network and polls them, in a background thread.

    on_raw(unit_id, address, raw) is called for every poll, with raw in the adapter's format.
    """

    def __init__(self, targets: list[str], on_raw: Callable[[str, str, dict], None],
                 poll_s: float = 2.0, rescan_s: float = 30.0, timeout: float = 0.5):
        self.targets = parse_targets(targets)
        self.on_raw = on_raw
        self.poll_s, self.rescan_s, self.timeout = poll_s, rescan_s, timeout
        self.devices: dict[tuple[str, int], dict] = {}   # (host, port) -> {"client", "identity", "unit_id"}
        self.failures: dict[tuple[str, int], int] = {}

    def start(self) -> None:
        # Empty addresses are normal during a scan; pymodbus would log each one as an error.
        logging.getLogger("pymodbus").setLevel(logging.CRITICAL)
        threading.Thread(target=self._run, name="modbus-scanner", daemon=True).start()

    def _identify(self, client: ModbusTcpClient) -> dict[str, str]:
        # Regular category (read code 2) covers objects 0-6, including the installer's tag.
        response = client.read_device_information(read_code=2, object_id=0)
        if response.isError():
            return {}
        return {ID_OBJECTS[k]: v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
                for k, v in (response.information or {}).items() if k in ID_OBJECTS}

    def scan(self) -> None:
        for host, port in self.targets:
            if (host, port) in self.devices:
                continue
            client = ModbusTcpClient(host, port=port, timeout=self.timeout, retries=0)
            if not client.connect():
                continue
            try:
                identity = self._identify(client)
            except Exception as exc:  # a device that drops the connection mid-handshake
                log.info("%s:%s answered but identification failed: %s", host, port, exc)
                identity = {}
            unit_id = safe_id(identity.get("UserApplicationName") or f"modbus-{host}-{port}")
            self.devices[(host, port)] = {"client": client, "identity": identity, "unit_id": unit_id}
            self.failures[(host, port)] = 0
            log.info("Modbus device at %s:%s: %s", host, port,
                     " / ".join(identity.get(k, "?") for k in ("VendorName", "ProductCode", "UserApplicationName")))

    def poll(self) -> None:
        for (host, port), dev in list(self.devices.items()):
            profile = profile_for(dev["identity"])
            registers: list[int] = []
            if profile is not None:
                try:
                    rr = dev["client"].read_holding_registers(profile["start"], count=profile["count"])
                    if rr.isError():
                        raise IOError(str(rr))
                    registers = list(rr.registers)
                    self.failures[(host, port)] = 0
                except Exception as exc:
                    self.failures[(host, port)] += 1
                    if self.failures[(host, port)] >= 3:
                        log.warning("%s at %s:%s stopped answering (%s); will look for it again on the next scan",
                                    dev["unit_id"], host, port, exc)
                        dev["client"].close()
                        del self.devices[(host, port)]
                    continue
            self.on_raw(dev["unit_id"], f"modbus-tcp://{host}:{port}",
                        {"identity": dev["identity"], "registers": registers})

    def _run(self) -> None:
        log.info("Scanning %d Modbus TCP address(es) on the site network", len(self.targets))
        last_scan = 0.0
        while True:
            if time.time() - last_scan >= self.rescan_s:
                self.scan()
                last_scan = time.time()
            self.poll()
            time.sleep(self.poll_s)
