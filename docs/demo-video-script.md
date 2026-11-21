# Hollow Uplink demo video script

A 3-minute video in three parts, one presenter each, recorded over a single run of the scripted demo.

## Run of show

Each presenter owns a run of the demo's 11 scripted steps. Everything is recorded on Aariz's laptop, because the sensor box must plug into the laptop that runs the system.

| Part | Presenter | Time | Demo steps | What the viewer learns |
| --- | --- | --- | --- | --- |
| 1 | Nithil | 0:00–1:00 | 1–2 | The problem, and a system that finds and identifies machines on its own |
| 2 | Jazib | 1:00–2:05 | 3–8 | Nobody can fake, replay or quietly lie through it |
| 3 | Aariz | 2:05–3:00 | 9–11 | One machine is real hardware, humans stay in charge, and the closing line |

If the judging platform sets a different length, trim Part 2 first: the replay attack (step 4) and the map layers (step 5) are the easiest cuts.

## Before recording

Allow 30 minutes for setup and one full rehearsal. All commands run in PowerShell from the project folder.

**Laptop (once)**

- [ ] Pull the latest `main`: `git pull`
- [ ] Install everything: `.\.venv\Scripts\python -m pip install -r requirements-dev.txt`
- [ ] Check the tests pass: `.\.venv\Scripts\python -m unittest` (about 105 tests, a second or two)
- [ ] Close the Arduino Serial Monitor, other heavy apps and notifications (Focus Assist on)
- [ ] Set screen-recording software to full screen at 1080p: OBS, or Xbox Game Bar with Win+Alt+R

**Sensor box (Aariz)**

- [ ] Box wired as in `hardware/README.md`; sketch uploaded; Serial Monitor shows a JSON line every 2 s, then `# gas baseline` after about a minute
- [ ] Note the COM port from Device Manager > Ports (the steps below say COM3)
- [ ] Box plugged straight into the laptop, USB cable taped down. The bridge does not reconnect if the cable is bumped
- [ ] Box powered on at least 3 minutes before recording, so the gas sensor warms up
- [ ] Phone on a stand filming the box, breadboard and relay LED together, ready to record
- [ ] Matches and something to put them out in. No hand sanitizer near the flame

**Start the system**

1. **Terminal A (the system):** `.\.venv\Scripts\python run.py --uno COM3 --no-browser`. Leave it running for the whole session.
2. Open `http://localhost:8000/dashboard/city.html?captions=1` in the browser. The `?captions=1` part shows each step's caption on screen.
3. Make the browser full screen (F11).
4. Click **Unlock operator** in the 3D view and pick `keys/operator.key`. This enables the Approve button and the relay switch.
5. Wait 3 minutes. The scouts need that long to map zones Z2 and Z5; until then the hijack and smoke steps have no verdict to change.
6. **Terminal B (the script):** `.\.venv\Scripts\python -m tools.demo`. It waits for Enter before each step.

Whoever is at the keyboard presses Enter in Terminal B when the presenter finishes the lines for a step. Keep Terminal B off screen; the recording shows only the browser.

**Retakes:** press Ctrl+C in Terminal B only, then rerun it from the start of that part: `.\.venv\Scripts\python -m tools.demo --start 3` (1 for Part 1, 3 for Part 2, 9 for Part 3). Terminal A keeps running, so the map stays mapped. Part 2 is the exception: the verifier remembers the refuted probe, so to retake Part 2, restart Terminal A and wait 3 minutes again.

## Part 1 — Nithil: the problem and the machines (0:00–1:00)

Nithil sets up the story and shows that the system finds machines without being told about them. Start recording with the map already mapped, Terminal B waiting at step 1.

| Time | On screen | Nithil says | Keyboard |
| --- | --- | --- | --- |
| 0:00–0:15 | The 3D view, with the Directorate's claim in the Surface report on the left | "For thirty years the Directorate has told the Hollow that the surface air kills in minutes. Nobody has gone up to check." | — |
| 0:15–0:30 | The city and the machines on it | "The machines left up there are still running. They come from dozens of vendors, speak different protocols, and nobody knows which ones still tell the truth. Hollow Uplink is a translator for those machines, and a referee for what they say." | Enter: step 1 |
| 0:30–0:45 | Step 1 caption; scouts driving, the map lit where they have scanned | "The map starts dark. Every tile you see comes from a scout's LiDAR scan, signed at the gateway. Our gateways found these machines on their own, over MQTT and Modbus, and worked out what each one is from the shape of its data." | Enter: step 2 |
| 0:45–1:00 | Step 2 caption; `mqtt-rad-02` labelled faulty | "Some are broken. This radiation probe has been stuck at its maximum since it switched on. The verifier flags it and leaves it out, instead of declaring the zone deadly. Jazib will show you what happens when someone tries to lie." | Hand over |

**Part 1, click by click**

Before you start recording (the person at the laptop):

1. If a **Hollow Uplink** welcome box covers the map, click **Start watching**.
2. Click **Unlock operator** (top left) and choose `keys/operator.key`. The **Kill switch** button appears beside it; don't press it.
3. Check the map strip on the right shows **Everything** (the top icon). If not, click it.
4. Start the screen recording.

During Part 1:

1. Say the first two lines with nothing clicked.
2. Press **Enter** in Terminal B. The step 1 caption appears.
3. Hover the mouse over a lit tile to show who scanned it and when.
4. Press **Enter**. The step 2 caption appears.
5. Click the machine labelled **mqtt-rad-02 · faulty** on the map. Its panel opens on the right showing the faulty flag.
6. Click **×** to close the panel, then hand over to Jazib.

**Nithil's notes**

- Hover a tile at 0:30 to show who scanned it and when, if there's time.
- Click `mqtt-rad-02` at 0:45 to open its panel with the faulty flag.
- Speak slower than feels natural; captions are on screen as backup.

## Part 2 — Jazib: nobody can fake the answer (1:00–2:05)

Jazib shows every attack failing, ending with the lie a signature can't catch. The scout's drive in step 8 takes longer than the script allows; speed it up or cut it in editing.

| Time | On screen | Jazib says | Keyboard |
| --- | --- | --- | --- |
| 1:00–1:15 | Step 3 caption; two red "rejected" lines in the event feed, bottom left | "Every reading is signed at the gateway. Here someone sends a reading signed with a key nobody trusts, then a real one edited to say the radiation is lethal. Both are rejected, and nothing on the map changes." | Enter: step 3, then step 4 |
| 1:15–1:25 | Step 4 caption; "replayed" rejection in the feed | "Copying a genuine message and sending it again doesn't work either. The signature is valid, but we've already seen that sequence number." | Enter: step 5 |
| 1:25–1:35 | Step 5; the CO₂ layer on the map, gas pooling in Z4 | "Layers show one kind of data at a time. Every cell is the latest value a trusted machine measured there; nothing unmeasured is guessed." | Enter: step 6 |
| 1:35–1:50 | Step 6 caption; Z2's verdict drops | "Now the hard one. The Directorate hijacks a real radiation probe. It reports a believable 8 microsieverts, through the real gateway, validly signed. A signature can't catch this." | Enter: step 7 |
| 1:50–2:05 | Step 7: the investigation in the Surface report; click **Approve: send scout-0X to check**. Step 8: the scout drives there (dashed line) and the claim shows **REFUTED** | "But one unconfirmed machine is changing the answer, so the verifier doesn't believe it or bury it. It asks us to send another machine to check. The scout measures 0.12 there. The claim is refuted, the probe is flagged as compromised, and Z2 recovers. Aariz will show you one machine that's real." | Click Approve (or Enter); Enter: step 8; hand over |

**Part 2, click by click**

The demo sends every attack and switches the map layers by itself. Jazib's only click is **Approve**.

1. Press **Enter** in Terminal B (step 3). Point the mouse at the event feed, bottom left: two red "Rejected" lines appear.
2. Press **Enter** (step 4). A third "replayed" rejection appears in the feed.
3. Press **Enter** (step 5). The map switches to **Radiation** on its own: the hospital in Z3 glows.
4. Press **Enter** when Terminal B asks. The map switches to **CO₂**: gas pooling in Z4. Hover a coloured cell to show its value and who measured it.
5. Press **Enter** when asked. The map goes back to **Everything**.
6. Press **Enter** (step 6). The probe is hijacked; Z2's verdict drops in the Surface report, top left.
7. Press **Enter** (step 7). Terminal B says it's waiting for an investigation; within seconds one appears in the Surface report.
8. Click **Approve: send scout-0X to check** in the Surface report (the name and distance vary). Pressing Enter in Terminal B does the same thing if the button is hard to reach.
9. Press **Enter** (step 8). Watch the scout follow the dashed line to the probe.
10. Wait for **REFUTED** in the Surface report and the event feed, then hand over to Aariz.

**Jazib's notes**

- Point at the event feed (bottom left) for steps 3 and 4; the rejections are the proof.
- For step 5 the demo shows CO₂ on its own; you can also click the radiation layer in the strip on the right.
- Wait for the word **REFUTED** before your last line.

## Part 3 — Aariz: the real sensor box (2:05–3:00)

Aariz proves one machine is physical hardware, then shows humans stay in charge. This part is filmed twice at once: the screen recording, and a phone filming the box, breadboard and relay LED. They go side by side in editing.

| Time | Screen | Phone (the box) | Aariz says | Keyboard |
| --- | --- | --- | --- | --- |
| 2:05–2:15 | Step 9 caption; click the small grey box `uno-gas-01` in Z5 to open its readings | Aariz beside the box, pointing at it | "One of these machines is real. This is an Arduino in a box we 3D-printed, with a gas sensor, a lid switch and a relay. It speaks its own made-up format, and the gateway worked out what it is on its own." | Enter: step 9 |
| 2:15–2:30 | Gas level rises; Z5 turns **Unsafe**: "gas or smoke dangerous" | Aariz blows out a match and lets the smoke drift into the box | "Watch what happens when there's smoke in the air up there." Then, once Z5 turns red: "Within seconds, Z5 is unsafe, and it says why." | Wait for Z5 to change |
| 2:30–2:40 | Caption "A forged command goes nowhere"; red rejection in the event feed | The relay LED stays off | "Someone pretends to be the operator and orders the relay on. The gateway rejects it, and nothing clicks." | Enter |
| 2:40–2:45 | Caption about the real command | The relay clicks and the LED lights | "The real operator's signed order goes through." | Enter |
| 2:45–2:52 | Step 10: red KILL SWITCH ENGAGED banner | The LED goes off | "And one kill switch halts everything that moves, on every site, until a human releases it." | Enter: step 10 |
| 2:52–3:00 | Step 11: the closing caption over the city | Optional: all three presenters on camera | "The machines kept us out. Now they're building our way home." | Enter: step 11; stop recording |

**Part 3, click by click**

The demo sends the forged command, the real relay command and the kill switch by itself. Aariz's job is the match and pointing at the box; someone else presses Enter.

1. Clap once on camera (for lining up the phone video later).
2. Press **Enter** in Terminal B (step 9). The caption "One of these machines is real" appears.
3. Click the small grey box labelled **uno-gas-01** in Z5. Its panel opens on the right with the gas level, temperature, hatch and relay in plain words.
4. Aariz blows out a match and holds the smoking match near the gas sensor inside the box. Terminal B says it's waiting for gas.
5. Wait until Z5 turns **Unsafe** in the Surface report and the panel's gas reading jumps. Terminal B prints `Z5: not_safe`.
6. Press **Enter**. A forged relay command is sent: a red rejection appears in the event feed, and the relay LED stays off.
7. Press **Enter**. The real signed command is sent: the relay clicks and the LED lights. The panel shows the relay on. (Optional: click **Switch relay on** in the panel yourself instead, before pressing Enter.)
8. Press **Enter** (step 10). A red **KILL SWITCH ENGAGED** banner appears at the top and the relay LED goes off.
9. Press **Enter** (step 11). The closing caption appears. Hold for 3 seconds, then stop the screen recording and the phone.

**Aariz's notes**

- Power the box at least 3 minutes before this part, or the gas sensor is still warming up and step 9 won't react.
- **Never use a flame and hand sanitizer together.** Blow the match out first, then hold the smoking match near the sensor. Have a cup of water for used matches.
- If Z5 doesn't change within about 15 seconds, bring the smoke closer to the MQ-2 sensor. If it still doesn't change, the scouts may not have mapped Z5 yet: wait a minute and retake.
- Don't use a hair dryer for heat. The verifier may flag the box for disagreeing with nearby scouts and leave it out of the score.
- Between takes, lift the lid and air the box out for a minute or two so the gas reading drops back to normal.
- Clap once on camera at the start of the take: it makes lining up the phone and screen recordings easy.

## After recording

Record a backup first, edit the three parts into one video, then upload and submit before 8 PM.

**Backup take (do this first, about 6 minutes)**

- [ ] Record one complete run with the fake board, in case the real box misbehaves: stop Terminal A, run `.\.venv\Scripts\python run.py --demo --auto --uno fake --record`, and screen-record it. This also saves a fresh `dashboard/recordings/demo.jsonl.gz` for the live link.

**Editing**

- [ ] Put the three parts in order and cut pauses between steps.
- [ ] Speed up the scout's drive in step 8 (2–4×) so Part 2 stays near 65 seconds.
- [ ] In Part 3, place the phone footage of the box beside or over the screen recording, lined up on Aariz's clap. The smoke and Z5 turning red must be visible at the same moment.
- [ ] If voices were recorded separately, lay each presenter's voiceover over their part.
- [ ] Optional last 5 seconds: the repo link and the three names (Nithil, Jazib, Aariz).
- [ ] Check the total length against the platform's limit; trim steps 4 and 5 first if it runs long.

**Upload and submit**

- [ ] Upload to YouTube as unlisted (or public) and open the link in a private window to check it plays.
- [ ] Make the repo public and check the README shows on GitHub.
- [ ] Put the live replay link on the submission: the hosted `city.html?replay=recordings/demo.jsonl.gz`.
- [ ] Enter Nithil, Jazib and Aariz on the judging platform before submitting; teams can't be edited afterwards.
- [ ] Select any sponsor prize tracks, then submit by 8 PM.
