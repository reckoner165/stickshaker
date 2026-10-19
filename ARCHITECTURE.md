# stickshaker architecture

stickshaker plays a tape-loading sound when a coding agent writes memory or compacts its context. The sound is a ZX Spectrum tape signal. The synth makes the signal from the bytes of the event, so different data makes a different sound.

This document describes the components, the data flow, and the contracts between the components.

## Overview

```
 Claude Code                         stickshaker daemon (one process)
┌─────────────────┐  hook JSON   ┌──────────────────────────────────────────────┐
│ PreCompact      │─────────────▶│ server.py   read one JSON object, close      │
│ SessionStart    │  Unix socket │     │                                        │
│ PostToolUse     │  nc -U       │     ▼                                        │
└─────────────────┘              │ events.py   payload ──▶ Event or None        │
                                 │     │        (memory.py checks memory files) │
                                 │     ▼                                        │
                                 │ player.py   queue (max 2) ──▶ worker thread  │
                                 │                                │             │
                                 │                                ▼             │
                                 │ synth.py    bytes ──▶ float32 samples        │──▶ audio out
                                 └──────────────────────────────────────────────┘
```

The design has two parts:

- **Hooks** in Claude Code. A hook is a short shell command. It sends the hook JSON to the daemon and stops immediately.
- **The daemon.** This is one long-running process. It filters events, makes the sounds, and plays them.

Hooks must not make Claude slow. Thus the hook does no work. The daemon does all the work on its own threads.

## Components

| File | Function |
|---|---|
| `stickshaker/synth.py` | Changes bytes into audio samples. Pure functions. No I/O. |
| `stickshaker/events.py` | Changes a hook payload into an `Event`, or into `None` (no sound). |
| `stickshaker/memory.py` | Finds memory writes. Compares a small set of files to their last state. |
| `stickshaker/player.py` | Plays events in sequence on one worker thread. |
| `stickshaker/server.py` | Receives hook JSON on a Unix socket. |
| `stickshaker/install.py` | Adds and removes stickshaker hooks in Claude Code `settings.json`. |
| `stickshaker/doctor.py` | Checks the daemon, the socket, and the hooks. |
| `stickshaker/cli.py` | The `stickshaker` command. |
| `scripts/demo.py` | Plays or saves sounds for files. Does not use the daemon. |

## Data flow

1. Claude Code starts a hook. The hook command is `nc -U ~/.stickshaker.sock || true`.
2. `nc` connects to the socket and sends the hook JSON.
3. `server.py` reads one complete JSON object. Then it closes the connection. `nc` stops.
4. `events.py` changes the payload into an `Event`. If no sound is necessary, it returns `None`.
5. `player.py` puts the `Event` in the queue. If the queue is full, the player drops the event.
6. The worker thread gets the `Event`. It calls `synth.render()` and plays the samples.

Steps 1 to 5 take approximately 2 ms. Step 6 occurs on the worker thread. Claude does not wait for step 6.

## Contracts

### Hook to daemon (IPC)

- Transport: Unix domain socket at `~/.stickshaker.sock`. File permissions are `0600`. Only the owner can connect.
- Protocol: one connection carries one JSON object. The JSON object is the hook payload from Claude Code, without changes. The daemon does not send a reply.
- The server closes the connection after it reads one object. This is necessary because macOS `nc` keeps the connection open until the other side closes it.
- The server stops a read after 2 seconds or after 8 MB.
- The messages are "fire and forget". If a message is lost, one sound does not play. Nothing else occurs.

### Hooks

`stickshaker install-hooks` adds these hooks to `~/.claude/settings.json`:

| Hook event | Matcher | Sound |
|---|---|---|
| `PreCompact` | (none) | `save` |
| `SessionStart` | `compact` | `load` |
| `PostToolUse` | `Write\|Edit\|MultiEdit\|NotebookEdit\|Bash` | `memory`, only if a memory file changed |

Each hook has a timeout of 5 seconds. Thus a daemon that does not respond can stop Claude for 5 seconds maximum.

There are two hook modes:

- **Silent** (default): the command ends with `|| true`. If the daemon is not available, the hook ends with exit code 0. You see nothing.
- **Debug** (`install-hooks --debug`): the command does not have `|| true`. If the daemon is not available, the hook ends with exit code 1. Claude Code shows a non-blocking hook error to you. Claude does not see the error and does not stop.

stickshaker never uses exit code 2. Exit code 2 sends the error into the context of Claude and can stop some events.

### Event

```python
Event(kind, data, size, source, received)
```

| Field | Meaning |
|---|---|
| `kind` | `"save"`, `"load"`, or `"memory"`. |
| `data` | The bytes that the synth encodes. |
| `size` | The logical size of the event. It sets the duration of the sound. |
| `source` | `"claude"`. Later also `"codex"`. |
| `received` | Monotonic time when the event arrived. Used for timing logs only. |

Mapping from hook payload to `Event`:

| Hook | `data` | `size` |
|---|---|---|
| `PreCompact` | The last 32 KB of the transcript. | The full transcript size. |
| `SessionStart` (`compact`) | The text of the compaction summary. If the summary is not found, the last 32 KB of the transcript. | Length of `data`. |
| `PostToolUse` | The new lines in the memory files that changed. | Length of `data`. |

### Synth

```python
render(data: bytes, size: int | None, kind: str, rate: int, bits: int) -> np.ndarray  # float32, 44.1 kHz, mono
```

`render()` has no side effects. The player, the demo, and the tests call the same function.

### Player

- One sound plays at a time.
- The queue holds a maximum of 2 events. The player drops more events. Thus a burst of events gives a maximum of 3 sounds (1 playing, 2 waiting).
- The `enabled` flag stops all sounds. The menu bar will use this flag.
- `render` and `play` are parameters. The tests replace them, so the tests make no sound.

## Memory detection

The daemon does not look at the tool name to find memory writes. It looks at the memory files.

### Why

Claude can write memory with many tools. For example, Claude can use the Bash tool with `python3` or `sed`. A matcher on tool names cannot find all of these writes. Thus every tool that can write files is a trigger. The memory files show if a change occurred.

### Scope

For each `PostToolUse` hook, the daemon examines only these files:

1. `~/.claude/projects/<project>/memory/*.md` for the current session. The daemon gets this folder from `transcript_path` in the hook payload.
2. `~/.claude/CLAUDE.md`.
3. A `CLAUDE.md` or `AGENTS.md` file, only if the tool call names that file in `tool_input.file_path`.

The daemon does not search directories. It does not read other files. It does not poll between hooks.

One check costs approximately 70 µs for a folder with 15 files. The `nc` process of the hook costs approximately 50 times more.

### Change detection

`MemoryTracker` keeps a snapshot `(mtime, content)` for each file that it examined.

- If the `mtime` did not change, the daemon does not read the file.
- If the content changed, the event data is the lines that were added. If lines were only removed, the event data is the full file.
- The first time the daemon sees a file, it keeps a baseline and makes no sound. Exception: if the file changed in the last 5 seconds, the daemon makes a sound.
- Changes to many files in one check make one event. For example, a memory file and `MEMORY.md` make one sound.
- Each change makes a sound one time only. The next check sees the new snapshot.

### Known limit

The daemon does not find a Bash edit to a `CLAUDE.md` or `AGENTS.md` in a repository. The daemon would have to search the file system to find these edits. This is not permitted by design.

## Sound

### Encoding

The synth uses the ZX Spectrum ROM tape format. Times are in Z80 T-states at 3.5 MHz.

| Part | Half-pulse length |
|---|---|
| Pilot tone | 2168 T (approximately 807 Hz) |
| Sync | 667 T, then 735 T |
| Bit 0 | 2 × 855 T (approximately 2 kHz) |
| Bit 1 | 2 × 1710 T (approximately 1 kHz) |

Procedure in `render()`:

1. Compress `data` with zlib. Compressed data is almost random. This gives a sound that is similar to a real tape.
2. For `save`, add a 17-byte Spectrum header block. The header contains `size`. Then add a gap of 0.12 seconds.
3. Add the data block: flag byte `0xFF`, payload, XOR checksum.
4. Calculate the target duration: `clamp(0.4 + 0.6 × log2(1 + KB), 0.5, 4.0)` seconds.
5. Calculate the time scale. The data bits must fill the target duration. The scale is between 1.0 (real Spectrum speed) and 0.25 (4 × turbo).
6. If the data is too long at 4 × turbo, cut the data block.
7. Give part of the duration to the pilot tone: 30 % for `save`, 20 % for `load`, 10 % for `memory`.

Small payloads play at real Spectrum pitch. Large payloads play faster and at a higher pitch.

### Lo-fi chain

```
square() ──▶ grit() ──▶ quantize ──▶ sample-and-hold ──▶ tone() ──▶ fade ──▶ × AMP
 8 kHz        8 kHz      4 bits        8 kHz → 44.1 kHz    44.1 kHz
```

| Stage | Function | Effect |
|---|---|---|
| `square()` | Makes a ±1 square wave at `CRUSH_RATE` (8 kHz). No band limit. Adds slow wow/flutter. | Aliasing. |
| `grit()` | Adds noise, a one-pole tape low-pass, dropouts, and crackle. | Tape texture. |
| Quantize | Rounds to `CRUSH_BITS` (4 bits). | Bit crush. The low-pass ramps become steps. |
| Sample-and-hold | Repeats samples up to 44.1 kHz. No interpolation filter. | Keeps the aliasing images. |
| `tone()` | FFT EQ: 2nd-order high-pass at 700 Hz. | Removes low frequencies. The curve agrees with a spectrum of real Spectrum tape audio. |

Noise layers in `grit()`:

| Constant | Layer |
|---|---|
| `HISS` | A low constant noise floor. |
| `DATA_NOISE` | Noise level follows the value of each byte (cubed). Most bytes are quiet. Some bytes are loud. The pilot and sync have no data noise. |
| `GRAIN_NOISE`, `GRAIN_DENSITY` | Noise that switches on and off in random grains of 2 to 20 ms. |
| `CRACKLE_PER_SEC` | Sparse clicks. |
| `DROPOUT` | Slow random decreases in gain. |

All noise is cubed Gaussian noise. This noise is mostly quiet and has sharp peaks. It is less flat than white noise.

The output level is `AMP` (0.15). The macOS system volume controls the final level. There is no volume control in stickshaker.

## Daemon life cycle

### Start

1. If `~/.stickshaker.sock` exists, try to connect to it.
   - If a daemon answers, stop with exit code 1. Only one daemon can run.
   - If no daemon answers, the file is old. Delete it.
2. Make the socket and set permissions to `0600`.
3. Start the player worker thread.
4. Wait for connections.

### Stop

`Ctrl-C` and `SIGTERM` stop the server, delete the socket file, and stop the player.

## Diagnostics

### Timing log

`stickshaker run` writes one log line for each event and one log line for each sound:

```
PostToolUse -> memory, 18 bytes, queued (0.2 ms)
played memory: waited 0 ms, render 34 ms, play 389 ms
```

The event line shows the outcome: `queued`, `dropped: disabled`, or `dropped: queue full`. The time in parentheses is the time to read and filter the event.

`stickshaker run -v` also writes a line for each ignored event.

### Doctor

`stickshaker doctor` does these checks:

1. Is the socket available?
2. Does the daemon answer? It sends a test memory event and measures the round trip.
3. Are the three hooks installed? Which mode does each hook use?

### Hook timing in Claude Code

`claude --debug` writes the execution and duration of each hook.

## Installer

`install.py` merges stickshaker hooks into `settings.json`. It does not replace the file.

- It identifies stickshaker entries by the text `.stickshaker.sock` in the command.
- It removes old stickshaker entries before it adds new entries. Thus it is safe to run it again or to change the mode.
- It does not change other hooks or other settings.
- It copies the file to `settings.json.stickshaker-bak` before it writes.
- It writes to a temporary file and then moves the file. Thus a failure cannot leave a partial file.
- If `settings.json` is not valid JSON, it stops and does not change the file.

`stickshaker uninstall-hooks` removes only stickshaker entries. If the `hooks` key becomes empty, it removes the key.

## Commands

| Command | Function |
|---|---|
| `uv run stickshaker run [-v]` | Start the daemon. |
| `uv run stickshaker install-hooks [--debug]` | Install the hooks. |
| `uv run stickshaker uninstall-hooks` | Remove the hooks. |
| `uv run stickshaker doctor` | Check the daemon and the hooks. |
| `uv run scripts/demo.py FILE --kind KIND [--rate HZ] [--bits N] [--wav OUT]` | Play or save a sound for a file. |
| `uv run pytest` | Run the tests. |

Hooks load when a Claude Code session starts. After you install or change the hooks, start a new session.

## Tests

| File | Subject |
|---|---|
| `tests/test_events.py` | Mapping from compaction hooks to events. |
| `tests/test_memory.py` | Memory scope, baseline, change detection, Bash writes, bursts. |
| `tests/test_daemon.py` | Real socket round trip, socket permissions, one daemon only, old socket, queue drops. |
| `tests/test_install.py` | Merge, idempotence, mode change, uninstall, backup. |

The tests make no sound. They replace `render` and `play` with fakes.

Unix socket paths on macOS have a limit of approximately 104 bytes. The tests make sockets in a short temporary directory for this reason.

## Open items

| Item | Status |
|---|---|
| Compaction summary format | Not verified. `events.py` looks for an `isCompactSummary` entry. If it is not found, `load` uses the end of the transcript. Verify with a real `/compact`. |
| Output device change | PortAudio can continue to use the old output device after you change the device. Not examined. |
| Spectrum notches | The reference spectrum has dips at approximately 4.35 kHz and 9 kHz. Add them to `tone()` later. |
| Codex support | Stage 4. Codex keeps memory in `~/.codex/memories_1.sqlite` and session logs in `~/.codex/sessions/`. Use the same snapshot method. |
| Menu bar | Stage 5. Icon with Enable/Disable. Uses `Player.enabled`. Save the state in `~/.config/stickshaker/state.json`. |
| Start at login | Later. Use a launchd agent. |
