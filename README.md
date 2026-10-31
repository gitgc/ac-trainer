# AC Trainer: a voice coach for Assetto Corsa

A race-engineer voice in your headphones that watches your telemetry and tells you where the
time is, corner by corner, while you drive.

- **Learns the track by itself** from your first clean lap: corners, braking points, apexes.
- **After every lap** it compares each corner with your best version of it, and with the
  fastest driver in the same car if the CSP app is installed. It finds *why* you lost time:
  braking too early or too late, soft braking, lockups, coasting, slow apex speed, late
  throttle, wheelspin, lifting, the wrong gear, short shifting or hitting the limiter.
- **Works on one or two corners at a time**, like a real coach. It gives you a cue on the
  straight before the corner ("Turn 4, brake 10 meters later") and a quick verdict after it
  ("Better", "Still early on the brakes").
- **Only talks when you have time to listen.** It stays quiet under braking and through
  corners, and a corner cue interrupts small talk.
- **Pushes you once you're consistent**: "Turn 1, try braking 5 meters later", then
  "Yes! 1 tenth quicker. Keep that marker." or "Too deep. Back to your old marker."
- **Shows you where, too** (optional): the in-game CSP app paints the braking marker to aim
  for on the track, plus your best line coloured by braking / coasting / throttle.
- **Remembers everything.** Your best laps and the fastest rivals' laps are saved per car and
  track, so next time it starts from where you left off.

It reads Assetto Corsa's shared memory (the same interface SimHub and CrewChief use), so it
works with Content Manager and Custom Shaders Patch with no game mods required. Speech uses
the Windows voices built into the PC, so there's no internet and no lag.

## Setup (on the PC that runs Assetto Corsa)

1. Install **[uv](https://docs.astral.sh/uv/)**, which sets up Python for you: open a
   terminal and run `winget install --id=astral-sh.uv -e`.
   Alternatively, install **Python 3.11 or newer** from <https://www.python.org/downloads/>
   and tick **"Add python.exe to PATH"** during setup.
2. Get the code onto the PC, e.g. into `C:\ac-trainer`:
   `git clone https://github.com/gitgc/ac-trainer.git C:\ac-trainer`, or on GitHub use
   **Code → Download ZIP** and extract it. To update later, run `git pull` in that folder
   (or extract a new ZIP over it). Your learned data is kept elsewhere (see
   [Your data carries over](#your-data-carries-over)), so updating never resets it.
3. Double-click **`run_coach.bat`**. The first run installs what it needs (about a minute).
4. *(Recommended)* Double-click **`install_csp_app.bat`** to install the in-game app that
   records other cars' laps (see [Learning from faster drivers](#learning-from-faster-drivers))
   and draws the [on-track overlay](#on-track-overlay).
   If AC isn't in the default Steam folder, pass your install path:
   `install_csp_app.bat "D:\SteamLibrary\steamapps\common\assettocorsa"`
5. **Headphones:** the coach speaks on the Windows default output. To pick a device
   explicitly, run `run_coach.bat voices` to list device names, then set `audio_device` in
   `config.toml` (copy `config.example.toml`). Check it with `run_coach.bat say`.
6. Start a practice session from Content Manager. The coach connects on its own and keeps
   running between sessions. Leave the window open.

Want to hear it first? `run_coach.bat demo` runs a simulated driver on a fictional circuit,
with a faster rival car. The simulated driver follows the advice, so you can hear it get
quicker lap by lap. No game needed.

## What you'll hear

| When | Example |
|---|---|
| Approaching a focus corner | "Turn 3, no coasting, back to the throttle" |
| Right after it | "Better" / "Still coasting" / "Great, 2 tenths quicker" |
| Crossing the line | "1 42 point 3, plus 2 tenths." / "New personal best! 1 41 point 9" |
| Lap summary | "Turn 5 cost 3 tenths, braking too early." |
| With rival data | "Turn 5: Marco is 4 tenths quicker, carrying more speed to the apex." |
| Gearing | "Hold third gear longer, shift near 7400." / "You're hitting the limiter in fourth." |
| Pushing | "Let's find time at Turn 1. Try braking 5 meters later." |

The console shows a table for every lap, plus a session summary when you stop:

```
=== Lap 4  1:20.025  valid  -0.725 vs best 1:20.750  | ideal 1:19.925  optimum 1:18.482
  corner           loss  brake   vmin   exit  gear  rival   main issue
 *Turn 1          -0.04     +0   +0.5   +0.4     3  +0.21   braking too early
  Turn 3          -0.60     +0   +2.4   +9.2     2  +0.12
```

`loss` is seconds against your best version of that corner. `brake` is how much later (+)
or earlier (-) you braked, in metres. `vmin` is apex speed and `exit` is exit speed, both in
km/h compared with that best. `rival` is how far behind the fastest rival you are there.

## How it decides what to say

- **Corners** are found from the speed trace of your best lap and numbered in the order you
  reach them, so they may differ from the official numbering. You can name them in
  `config.toml`. The map settles after 3 clean laps and is then fixed for that car and track.
- **Each corner's segment** runs from the end of the straight before it to the end of the
  straight after it. Time lost on a straight is charged to the corner exit that caused it.
- **Two kinds of checks:**
  - *Relative*: against your best time through that corner on any lap. For example, you
    braked 12 m earlier than on your best run, or you were 4 km/h slower at the apex.
  - *Absolute*: slow no matter what your best is. Examples: coasting into a corner (braked
    too early), coasting mid-corner, hanging on part throttle after the apex, using less
    grip than the car shows in similar corners, braking well below the deceleration the car
    manages elsewhere, lockups and wheelspin.
- **Focus:** a problem has to show up on two laps before it becomes a focus. That avoids
  chasing one-off mistakes. After 5 laps on a corner it rotates to something else.
- **Shift points** come from your own data. For every gear it measures how hard the car
  accelerates at each rpm. It then compares the gear you're in, at the rpm you shift, with
  the next gear at the rpm you land on. If the lower gear was still pulling harder you
  shifted too early; if the higher gear pulls harder you shifted too late. No car data files
  are needed.

## Learning from faster drivers

Shared memory and UDP only describe *your* car, but CSP lets in-game Lua apps see every car.
`install_csp_app.bat` installs **AC Trainer Link** (`csp_app/ACTrainerLink`). It records
every other car's laps, sampled every 2 m, to
`Documents\Assetto Corsa\actrainer\rival_laps.jsonl`. It starts on its own each session; you
can also open its small window from the in-game apps bar to see what it has recorded.

The coach reads that file live (including laps from earlier sessions) and keeps:
- only the **same car model** on the **same track and layout** (set `any_model = true` to
  change that);
- only clean laps: no pit visits, and no more than 2 wheels off track;
- the fastest laps, plus every rival lap that holds the best time through some corner.

From those it builds the **optimum lap**: for each corner, the better of your best and the
fastest rival's. The rival then becomes a reference for the feedback. If your own data
explains the corner precisely ("you're coasting") you hear that, with the rival's gap for
scale. If it doesn't, you hear what the rival does differently (brakes later, carries more
apex speed, exits faster, uses another gear). The rival is what reveals problems that
comparing you with yourself can't see, like being equally cautious in every corner.

Online servers may not send other cars' pedal inputs. When they don't, the coach works from
the speed trace alone, finding the braking point where the car starts slowing hard. Offline
races against AI include full pedal data.

## On-track overlay

The same CSP app draws on the track itself, from `Documents\Assetto Corsa\actrainer\overlay.json`,
which the coach rewrites after every lap:

- a **red bar across the track** at the braking point to aim for, labelled (`T5 BRAKE`). It's
  your best braking point for that corner, the rival's when the advice comes from a faster
  driver (`T5 BRAKE (Marco)`), or the new marker while the coach is pushing you
  (`T1 BRAKE +5 m`);
- a **green bar** at the throttle point when the problem is coasting or a late throttle;
- a **yellow dot** at the apex;
- **your best line**: per corner, the lap where you were fastest through it. Red = braking,
  white = coasting, green = throttle.

It has three modes. **Full lap** (the default) shows the line for the next 350 m everywhere,
with dimmer brake bars for every corner and the full markers for the corners the coach is
working on. **Focus corners** only shows those one or two corners, from just before the
braking zone to the exit. **Off** hides it.
Switch modes with the button in the AC Trainer Link window, or assign a key there to cycle
through them while driving. The choice is remembered.

The overlay needs the coach running on the same PC (it isn't written in `udp` mode). Set
`overlay.enabled = false` in `config.toml` to stop writing it.

## Your data carries over

Everything lives in `%USERPROFILE%\AssettoTrainer` (change it with `data_dir`):

```
tracks\<car>__<track>__<layout>\
    track.json        corner map
    reference.npz     your personal best lap
    laps\*.npz        optimum-lap library: every lap (yours or a rival's) holding a corner best
    optimum.json      the optimum lap, corner by corner, and who set each part
sessions\*.csv.gz     full telemetry of every session (open in Excel / pandas, or replay)
```

The library is saved every time a corner best improves, when you drive into the pits, and at
the end of every session. Laps that no longer hold any best are deleted, so it stays small.
On startup you'll hear something like *"Coach ready. Your best here is 1 41 point 9. Optimum
lap 1 40 point 8."* To start over for one car and track, delete its folder.

## Commands

```
run_coach.bat                 coach live (default)
run_coach.bat demo            simulated driver + rival, no game needed (--no-rival, --speed 2)
run_coach.bat analyze FILE    lap-by-lap analysis of a recorded session (sessions\*.csv.gz)
run_coach.bat replay FILE     replay a recorded session through the coach, with voice
run_coach.bat voices          list voices and audio outputs
run_coach.bat say "text"      test the voice / headphones
```

`replay` and `analyze` never change your saved bests.

## Configuration

Copy `config.example.toml` to `config.toml` next to `run_coach.bat`. The most useful settings:
- `voice.audio_device`, `voice.voice`, `voice.rate`;
- `coach.verbosity` (`quiet` / `normal` / `chatty`);
- `coach.lead_time`: how early the corner cue comes;
- corner names per track.

**Running the coach on a different computer** (e.g. a laptop on the same network): set
`telemetry.source = "udp"` and `udp_host` to the sim PC's IP address. This uses AC's built-in
remote telemetry on port 9996. It carries less data than shared memory, so the track length
and rev limit are estimated, and track-limit detection isn't available.

## Troubleshooting

- **"Waiting for Assetto Corsa…" never goes away:** the coach only connects once you're
  on track in a session, not while you're in Content Manager's menus.
- **No sound:** run `run_coach.bat say`. If that's silent, check `audio_device` and the
  Windows volume mixer. `run_coach.bat voices` lists the names to use.
- **It talks too much / too little:** set `verbosity`, or lower `max_focus` to 1.
- **The corners look wrong:** the map is built from your first clean laps. Delete that
  track's `track.json` and drive a few tidy laps to rebuild it.
- **No rival laps:** check that `Documents\Assetto Corsa\actrainer\rival_laps.jsonl`
  appears and grows during a race. If it doesn't, open the AC Trainer Link app once from the
  in-game apps bar; its window shows status and errors.
- **No overlay:** it appears after your first lap with the coach running, and only for the
  car and track it was written for. The AC Trainer Link window shows the overlay status; in
  **focus corners** mode there's nothing to draw until the coach picks a corner to work on.

## Status

The analysis, coaching, speech queue, rival feed and storage are covered by tests that run
against a built-in vehicle simulator with known driving mistakes (`pytest`). The Assetto
Corsa memory layouts and the CSP Lua calls come from the published structures and API (the
Lua calls are checked against the SDK shipped with CSP 0.3.0-preview535, and the app runs
against stubbed CSP functions), but have not yet been run against the live game. The first sessions on the real thing are the
real test.

## Development

```
uv sync
uv run pytest
```

Code map: `sources/` (shared memory, UDP, replay, simulator) → `laps.py` (lap splitting,
distance resampling) → `track.py` (corner detection) → `analysis.py` / `gears.py` (metrics
and diagnosis) → `coach.py` (decisions and timing) → `speech.py` (voice).
`rivals.py` + `csp_app/` handle other drivers; `storage.py` handles persistence.

## License

MIT, see [LICENSE](LICENSE).
