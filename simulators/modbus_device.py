"""Fake Modbus TCP field devices: real Modbus servers, simulated sensors.

Each device is its own Modbus TCP server (one port each, standing in for one IP
address each on a site's local network). Like real industrial gear, they don't
announce themselves and don't send JSON: the gateway has to scan for them, ask
who they are (Modbus "Read Device Identification", function 43/14), then poll
raw 16-bit registers and decode them using that model's register map.

Two vendors, two deliberately different register layouts:

  Kestrel Instruments ARM-200, area radiation monitor (holding registers)
    0-1  dose rate, nSv/h, uint32, high word first
    2    count rate, counts/min, uint16 (65535 = saturated)
    3    enclosure temperature, 0.1 degC, int16 (signed)
    4    status bits: bit 0 = detector OK

  Halden Controls GM-4, gas and particulate monitor (holding registers)
    0-1  air quality index, float32, LOW word first (word-swapped)
    2-3  PM2.5, ug/m3, float32, low word first
    4    CO2, ppm, uint16
    5    temperature, 0.1 degF, int16

    python -m simulators.modbus_device                       # default devices for GATEWAY_ID
    python -m simulators.modbus_device --faulty modbus-rad-11

Values come from the hidden world at each device's position (gateway/config
positions, like simulators.mqtt_sensor). tools.attack works on these too.
"""

import argparse
import asyncio
import json
import logging
import multiprocessing
import random
import time

from pymodbus import ModbusDeviceIdentification
from pymodbus.client import ModbusTcpClient
from pymodbus.constants import ExcCodes
from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice

from hollow import config, topics
from hollow.mqtt import connect, make_client

from .mqtt_sensor import unit_positions
from .world import environment

REGISTER_COUNT = 16
BASE_PORT = 5020

# kind -> identity the device reports, and the attack-tool value it understands
MODELS = {
    "rad": {"VendorName": "Kestrel Instruments", "ProductCode": "ARM-200",
            "ProductName": "Area Radiation Monitor", "ModelName": "ARM-200/G", "MajorMinorRevision": "2.4"},
    "air": {"VendorName": "Halden Controls", "ProductCode": "GM-4",
            "ProductName": "Gas & Particulate Monitor", "ModelName": "GM-4 Outdoor", "MajorMinorRevision": "1.11"},
}

u32 = lambda v: ModbusTcpClient.convert_to_registers(int(v), ModbusTcpClient.DATATYPE.UINT32, word_order="big")
f32_swapped = lambda v: ModbusTcpClient.convert_to_registers(float(v), ModbusTcpClient.DATATYPE.FLOAT32, word_order="little")
i16 = lambda v: ModbusTcpClient.convert_to_registers(int(v), ModbusTcpClient.DATATYPE.INT16)


def rad_registers(env: dict, faulty: bool, lie: float | None) -> list[int]:
    dose = env["radiation_usv_h"] if lie is None else lie * random.uniform(0.95, 1.05)
    if faulty:  # detector pinned at the top of its range but still claiming to be OK
        return [*u32(4_294_967), 65535, *i16(round(env["temp_c"] * 10)), 1]
    cpm = min(65534, int(dose / 0.0065))
    return [*u32(round(dose * 1000)), cpm, *i16(round(env["temp_c"] * 10)), 1]


def air_registers(env: dict, faulty: bool, lie: float | None) -> list[int]:
    aqi = env["aqi"] if lie is None else lie * random.uniform(0.95, 1.05)
    if faulty:
        aqi = 500.0
    pm25 = env["pm25_ugm3"] if lie is None and not faulty else aqi * 0.28 + random.uniform(-1, 1)
    temp_f10 = round((env["temp_c"] * 9 / 5 + 32) * 10)
    return [*f32_swapped(aqi), *f32_swapped(pm25), min(65535, env["co2_ppm"]), *i16(temp_f10)]


REGISTERS = {"rad": rad_registers, "air": air_registers}


class Device:
    def __init__(self, unit_id: str, port: int):
        self.unit_id, self.port = unit_id, port
        self.kind = unit_id.split("-")[1]
        self.info = {**MODELS[self.kind], "UserApplicationName": unit_id}  # the installer's tag for this device
        self.latest = [0] * REGISTER_COUNT
        self.server: ModbusTcpServer | None = None

    async def on_access(self, function_code, start_address, address, count, current_registers, set_values):
        # Serve whatever the sensor measured most recently.
        for i, value in enumerate(self.latest):
            current_registers[i - start_address] = value
        if set_values is not None:
            return ExcCodes.ILLEGAL_FUNCTION  # these sensors are read-only
        return None

    def make_server(self) -> ModbusTcpServer:
        """Must be called inside the running event loop."""
        identity = ModbusDeviceIdentification(info_name=self.info)
        device = SimDevice(
            id=0,  # answer on any unit id, like a standalone TCP device
            simdata=[SimData(0, values=[0] * REGISTER_COUNT, datatype=DataType.REGISTERS)],
            identity=identity,
            action=self.on_access,
        )
        self.server = ModbusTcpServer(device, identity=identity, address=("127.0.0.1", self.port))
        return self.server

    def update(self, env: dict, faulty: bool, lie: float | None) -> None:
        regs = REGISTERS[self.kind](env, faulty, lie)
        self.latest = regs + [0] * (REGISTER_COUNT - len(regs))



async def serve(device: Device, position: dict, state: dict, interval: float) -> None:
    server = asyncio.create_task(device.make_server().serve_forever())
    start = time.time()
    try:
        while True:
            env = environment(position["x"], position["y"], time.time() - start, random.Random())
            device.update(env, state["faulty"], state["lie"])
            await asyncio.sleep(interval)
    finally:
        await device.server.shutdown()
        server.cancel()


def run_device(unit_id: str, port: int, gateway: str, faulty: bool, interval: float) -> None:
    """One process per device: pymodbus keeps device identity per process, and real devices are separate boxes."""
    logging.basicConfig(level=logging.ERROR)
    logging.getLogger("pymodbus").setLevel(logging.ERROR)
    device = Device(unit_id, port)
    state = {"faulty": faulty, "lie": None}

    # Simulation-only control channel so tools.attack can compromise a Modbus device too.
    def on_control(client, userdata, msg):
        try:
            ctl = json.loads(msg.payload)
        except json.JSONDecodeError:
            return
        if ctl.get("unit_id") != unit_id:
            return
        if ctl.get("mode") == "compromised":
            state["lie"] = float(ctl["value"])
            print(f"{unit_id} is now compromised: reporting {state['lie']}", flush=True)
        elif ctl.get("mode") == "healthy":
            state.update(lie=None, faulty=False)
            print(f"{unit_id} restored", flush=True)

    control = make_client(f"sim-{unit_id}")
    control.on_connect = lambda c, u, f, rc, p: c.subscribe(topics.sim_control(gateway))
    control.on_message = on_control
    try:
        connect(control)
        control.loop_start()
    except OSError:
        print(f"{unit_id}: no MQTT broker reachable; attack control channel off", flush=True)

    position = unit_positions().get(unit_id, {"x": 400, "y": 250})
    try:
        asyncio.run(serve(device, position, state, interval))
    except KeyboardInterrupt:
        pass
    finally:
        control.loop_stop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway", default=config.GATEWAY_ID)
    parser.add_argument("--units", default="modbus-rad-11,modbus-air-12,modbus-rad-13",
                        help="comma-separated unit ids; the middle part (rad/air) picks the model")
    parser.add_argument("--port", type=int, default=BASE_PORT, help="first TCP port; each device gets the next one")
    parser.add_argument("--faulty", default="", help="comma-separated unit ids that report garbage")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between register updates")
    args = parser.parse_args()

    units = [u.strip() for u in args.units.split(",") if u.strip()]
    for unit in units:
        if unit.split("-")[1] not in MODELS:
            parser.error(f"{unit}: model must be one of {', '.join(MODELS)}")
    faulty = {u.strip() for u in args.faulty.split(",") if u.strip()}

    procs = []
    for i, unit in enumerate(units):
        port = args.port + i
        model = MODELS[unit.split("-")[1]]
        print(f"{unit}: {model['VendorName']} {model['ProductCode']} on modbus tcp 127.0.0.1:{port}"
              f"{'  (faulty)' if unit in faulty else ''}", flush=True)
        proc = multiprocessing.Process(
            target=run_device, args=(unit, port, args.gateway, unit in faulty, args.interval), daemon=True)
        proc.start()
        procs.append(proc)
    try:
        for proc in procs:
            proc.join()
    except KeyboardInterrupt:
        for proc in procs:
            proc.terminate()


if __name__ == "__main__":
    main()
