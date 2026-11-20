# The real machine: an Arduino Uno field station

One surface machine in the demo is real hardware: an Arduino Uno in a small box with a gas sensor, a climate sensor and a relay. It pretends to be a **BX-1 field station** from a made-up vendor, prints readings over USB in its own format, and knows nothing about Hollow Uplink. The gateway has to discover it, work out what it is from the shape of its data, translate it and sign it, the same as every simulated machine.

```
Uno (box) ──USB serial──▶ tools/uno_bridge.py ──surface/raw/<gw>/mqtt/uno-gas-01──▶ gateway ──signed──▶ verifier, dashboards
          ◀──"R1"/"R0"───                     ◀──surface/cmd/<gw>/mqtt/uno-gas-01── (only after the operator's signature checks out)
```

The bridge is just the board's network card. It forwards lines both ways and doesn't translate or sign anything.

## Wiring

There's a full diagram and box build guide in `hardware/wiring.html`. Power budget over USB: about 315 mA of the roughly 500 mA available.

On keyes modules, `-` goes to GND, the middle pin (or `+`) to 5V, and `S`/`AO` is the signal.

| Module | Signal pin goes to | What it's for |
| --- | --- | --- |
| MQ-2 gas sensor (`AO`) | A0 | Smoke and solvent vapour: the main hazard |
| DHT11 temperature and humidity | D2 | Humidity (and temperature if the 18B20 is missing) |
| 18B20 temperature | D4 | Temperature, ±0.5 °C. The keyes module has its pull-up resistor built in |
| Photoresistor | A1 | Light level |
| Crash sensor (lever switch) | D3 | The box's "hatch": the lid presses the lever. A reed switch or Hall sensor also works but needs a magnet on the lid |
| 5V relay | D7 | Something the operator can switch, such as an LED "air pump" |

The Uno's built-in LED (pin 13) copies the relay state, so you can test without the relay wired.

Two settings near the top of the sketch: if "hatch" reads backwards flip `HATCH_CLOSED_LEVEL`, and if the relay is backwards flip `RELAY_ON_LEVEL`.

## Set up the board (once)

1. Install the Arduino IDE. In **Tools > Manage Libraries**, install **DHT sensor library** by Adafruit (and **Adafruit Unified Sensor** when it asks), **OneWire**, and **DallasTemperature**.
2. Open `hardware/uno_station/uno_station.ino`, choose **Board: Arduino Uno** and the right port, then upload.
3. Open the Serial Monitor at 9600 baud. You should see `# BX-1 field station up`, then a JSON line every 2 s. After 60 s you get `# gas baseline NNN`. **Close the Serial Monitor** afterwards, or the bridge can't open the port.

## Run it

Start the stack as usual (broker, gateway, verifier, scouts). Start the scouts early: a zone only gets a verdict once a quarter of it has been measured, which takes the scouts about 2 to 3 minutes.

```
.\.venv\Scripts\python -m pip install -r requirements.txt     # adds pyserial
.\.venv\Scripts\python -m tools.uno_bridge --port COM3        # Device Manager > Ports shows the number
```

No board, or testing on another laptop: `python -m tools.uno_bridge --fake` imitates one, with a smoke event every 90 s.

Or start the bridge with everything else: `python run.py --uno COM3` (or `--uno fake`).

In the 3D view the station is the small grey box in Z5. Its lid lifts when the hatch opens, its lamp lights when the relay is on, and a glow around it shows the gas level. Click it for its readings in plain words. In operator mode the panel also has a **Switch relay on/off** button, which sends the same signed command as `tools.operator relay`.

The board sits at `uno-gas-01` in **Z5** (`gateway/config.example.json`; to move it, copy that file to `gateway/config.json` and change the zone and x/y). It must run on the same machine as the gateway whose `GATEWAY_ID` it uses.

## What the gateway and verifier do with it

- **Fingerprint:** raw `gas.adc`, `gas.base` and `gas.warm` mean "field station".
- **Translate:** `gas_vs_clean_air` is the gas signal divided by the clean-air level the board measured after warm-up, so 1.0 means normal. Also `temp_c`, `humidity_pct`, `light_pct`, `hatch_open` and `relay_on`. No gas reading is sent while the sensor is still warming up.
- **Score** (`verifier/scoring.py`): gas up to 1.5× is normal, 2× is "slightly raised", 3× is "elevated" (domes), 4× and above pushes the zone to "not safe". The verifier judges gas on the last 10 seconds, so the zone reacts within seconds and recovers once the air clears.
- **Commands:** `python -m tools.operator relay uno-gas-01 on|off` sends a signed command. The kill switch turns the relay off. A forged command (`python -m tools.spoof command uno-gas-01`) is rejected by the gateway before it ever reaches the cable.

**Known limits.** The MQ-2 shows a relative rise, not a calibrated ppm, which is why the reading is called "× clean air" and not "ppm". Gas readings can't be sent for investigation, because no scout can measure gas. The verifier skips its "stuck sensor" check for this board, because a DHT11 reports whole degrees and repeats itself in a calm room. If you heat the box (hair dryer) while scouts are within 15 m, the verifier may flag it for disagreeing with them. That's the cross-check working, but it removes the box from the score.

## Demo script (about 90 s)

Power the box up in normal room air at least 3 minutes before recording.

1. The box reads normal and Z5 shows its usual verdict. Point at `uno-gas-01` in the machine table: it was discovered and identified on its own.
2. Blow out a match and let a little smoke drift into the box, or put in a cotton ball with a few drops of hand sanitizer. **Never use the flame and the alcohol together.** Within a few seconds Z5 turns **Unsafe**: "gas or smoke dangerous (4.5× clean air) ... Main concern: gas or smoke."
3. Lift the lid: `hatch_open` goes to 1.
4. Send `tools.operator relay uno-gas-01 on`: the relay clicks and the LED lights. Send a forged one with `python -m tools.spoof command uno-gas-01`: the gateway rejects it and nothing happens. Engage the kill switch: the relay switches off.
5. Air the box out: Z5 recovers.
