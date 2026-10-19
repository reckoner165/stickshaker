# stickshaker architecture

stickshaker plays a tape-loading sound when a coding agent writes memory or compacts its context. It supports Claude Code and Codex. The sound is a ZX Spectrum tape signal. The synth makes the signal from the bytes of the event, so different data makes a different sound.

This document describes the components, the data flow, and the contracts between the components.

## Overview

```
 Claude Code                         stickshaker daemon (one process)
┌─────────────────┐  hook JSON   ┌──────────────────────────────────────────────┐
│ PreCompact      │─────────────▶│ server.py   read one JSON object, close      │
│ PostCompact     │  Unix socket │     │                                        │
│ PostToolUse     │  nc -U       │     ▼                                        │
└─────────────────┘              │ events.py   payload ──▶ Event or None        │
 Codex                           │     │        (memory.py checks memory files) │
┌─────────────────┐  hook JSON,  │     │                                        │
│ PreCompact      │─────────────▶│     │                                        │
│ PostCompact     │  tagged      │     ▼                                        │
└─────────────────┘              │ player.py   queue (max 2) ──▶ worker thread  │
 ~/.codex/memories/   FSEvents   │     ▲                          │             │
  (background job) ─────────────▶│ codex.py    folder watcher     ▼             │
                                 │ synth.py    bytes ──▶ float32 samples        │──▶ audio out
                                 └──────────────────────────────────────────────┘
```

The design has two parts:

- **Hooks** in Claude Code and Codex. A hook is a short shell command. It sends the hook JSON to the daemon and stops immediately.
- **The daemon.** This is one long-running process. It filters events, makes the sounds, and plays them. It also watches `~/.codex/memories/`, because Codex writes memories without a hook (see "Codex").

Hooks must not make the agent slow. Thus the hook does no work. The daemon does all the work on its own threads.

## Components

| File | Function |
|---|---|
| `stickshaker/synth.py` | Changes bytes into audio samples. Pure functions. No I/O. |
| `stickshaker/events.py` | Changes a hook payload into an `Event`, or into `None` (no sound). |
| `stickshaker/memory.py` | Finds memory writes. Compares a small set of files to their last state. |
| `stickshaker/codex.py` | Watches `~/.codex/memories/` for Codex memory writes. |
| `stickshaker/player.py` | Plays events in sequence on one worker thread. |
| `stickshaker/server.py` | Receives hook JSON on a Unix socket. |
| `stickshaker/install.py` | Adds and removes stickshaker hooks in Claude Code `settings.json` and Codex `hooks.json`. |
| `stickshaker/doctor.py` | Checks the daemon, the socket, and the hooks. |
| `stickshaker/cli.py` | The `stickshaker` command. |
| `scripts/demo.py` | Plays or saves sounds for files. Does not use the daemon. |

## Data flow

1. Claude Code or Codex starts a hook. The hook command is `nc -U ~/.stickshaker.sock || true`. For Codex, `sed` first adds `"stickshaker_source":"codex"` to the payload.
2. `nc` connects to the socket and sends the hook JSON.
3. `server.py` reads one complete JSON object. Then it closes the connection. `nc` stops.
4. `events.py` changes the payload into an `Event`. If no sound is necessary, it returns `None`.
5. `player.py` puts the `Event` in the queue. If the queue is full, the player drops the event.
6. The worker thread gets the `Event`. It calls `synth.render()` and plays the samples.

Steps 1 to 5 take approximately 2 ms. Step 6 occurs on the worker thread. The agent does not wait for step 6.

Codex memory writes do not use steps 1 to 4. `codex.py` makes the `Event` and gives it to the player (step 5).

## Contracts

### Hook to daemon (IPC)

- Transport: Unix domain socket at `~/.stickshaker.sock`. File permissions are `0600`. Only the owner can connect.
- Protocol: one connection carries one JSON object. The JSON object is the hook payload from the agent. Claude Code payloads arrive without changes. Codex payloads have one added key, `stickshaker_source` (see "Codex"). The daemon does not send a reply.
- The server closes the connection after it reads one object. This is necessary because macOS `nc` keeps the connection open until the other side closes it.
- The server stops a read after 2 seconds or after 8 MB.
- The messages are "fire and forget". If a message is lost, one sound does not play. Nothing else occurs.

### Hooks

For the Codex hooks, see "Codex". `stickshaker install-hooks` adds these hooks to `~/.claude/settings.json`:

| Hook event | Matcher | Sound |
|---|---|---|
| `PreCompact` | (none) | `save` |
| `PostCompact` | (none) | `load` |
| `PostToolUse` | `Write\|Edit\|MultiEdit\|NotebookEdit\|Bash` | `memory`, only if a memory file changed |

The hook events and their input fields are documented in the [Claude Code hooks reference](https://code.claude.com/docs/en/hooks). `PreCompact` and `PostCompact` fire for `/compact`, for auto-compact, and for idle compaction.

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
| `source` | `"claude"` or `"codex"`. |
| `received` | Monotonic time when the event arrived. Used for timing logs only. |

Mapping from hook payload to `Event`:

| Hook | `data` | `size` |
|---|---|---|
| `PreCompact` | The last 32 KB of the transcript. | The full transcript size. |
| `PostCompact` | `compact_summary` from the payload. This is the summary that the compaction made. If the field is empty, the last 32 KB of the transcript. | Length of `data`. |
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

Rule: the daemon reads files only inside `~/.claude` and `~/.codex`. It never reads files in other user directories, for example repositories. This rule applies to every path, also to paths in hook payloads.

For each `PostToolUse` hook, the daemon examines only these files:

1. `~/.claude/projects/<project>/memory/*.md` for the current session. The daemon gets this folder from `transcript_path` in the hook payload.
2. `~/.claude/CLAUDE.md`.

If `tool_input.file_path` is one of these files, the daemon also uses it. If it is a different file, the daemon ignores it.

The daemon resolves symbolic links before it does these checks. A link inside `~/.claude` that points outside it is ignored.

Transcripts: the daemon reads a transcript only if its path resolves inside `~/.claude` or `~/.codex` (`ALLOWED_ROOTS` in `events.py`). If not, the sound uses the hook payload.

The daemon does not search directories. It does not poll between hooks.

One check costs approximately 70 µs for a folder with 15 files. The `nc` process of the hook costs approximately 50 times more.

### Change detection

`MemoryTracker` keeps a snapshot `(mtime, content)` for each file that it examined.

- If the `mtime` did not change, the daemon does not read the file.
- If the content changed, the event data is the lines that were added. If lines were only removed, the event data is the full file.
- The first time the daemon sees a file, it keeps a baseline and makes no sound. Exception: if the file changed in the last 5 seconds, the daemon makes a sound.
- Changes to many files in one check make one event. For example, a memory file and `MEMORY.md` make one sound.
- Each change makes a sound one time only. The next check sees the new snapshot.

### Out of scope

Changes to `CLAUDE.md` or `AGENTS.md` files in repositories make no sound. These files are outside `~/.claude` and `~/.codex`.

## Codex

stickshaker tracks two things in Codex: compaction and memory writes. It does not examine `AGENTS.md` files.

Sources:

- Hooks: https://learn.chatgpt.com/docs/hooks
- Memories: https://learn.chatgpt.com/docs/customization/memories.md

### Compaction

`stickshaker install-hooks --codex` adds `PreCompact` and `PostCompact` to `~/.codex/hooks.json`. The format is the same as Claude Code: event, matcher group, handlers.

The Codex hook command adds a tag to the payload before it sends it:

```
sed '1s/^{/{"stickshaker_source":"codex",/' | nc -U ~/.stickshaker.sock || true
```

The daemon uses the tag to identify Codex events. The daemon does not use `transcript_path` for this, because the Codex docs say that `transcript_path` "isn't a stable interface for hooks and may change over time".

| Hook | Sound | `data` |
|---|---|---|
| `PreCompact` | `save` | The last 32 KB of the transcript. If the daemon cannot read the transcript, the hook payload. |
| `PostCompact` | `load` | The same as `PreCompact`. Codex `PostCompact` has no summary field. |

Codex runs a new hook only after you trust it. Open the Codex CLI and run `/hooks`.

### Memory writes

Codex writes memories in a background job after a chat is idle. No tool call occurs, so no hook starts. For this reason, the daemon watches one folder: `~/.codex/memories/`.

- The watcher uses FSEvents. It does not poll.
- It watches only `~/.codex/memories/` and its subfolders. It ignores links that point outside this folder.
- It waits 1 second after the last change (`DEBOUNCE_SECS`). Then it makes one `memory` event for all changes. It uses the same `MemoryTracker` comparison as Claude Code memory.
- Codex memories are off by default. If the folder does not exist, the watcher does not start. The daemon tries again when a Codex hook arrives.

### Not verified

There is no Codex installation to test on. These items come from the docs only:

- Hooks in the Codex desktop app. The docs describe hooks for the CLI.
- The exit-code behavior of Codex compaction hooks in debug mode. The docs do not describe it.
- The file names and formats in `~/.codex/memories/`. The docs do not list them. The watcher reads all files in the folder.
- The payload starts with `{` on its first line. If it does not, the tag is not added, and the daemon treats the event as a Claude Code event. Compaction still makes a sound.

## Sound

### Encoding

The synth uses the ZX Spectrum ROM tape format. Times are in Z80 T-states at 3.5 MHz.

| Part | Half-pulse length |
|---|---|
| Pilot tone | 2168 T (approximately 807 Hz) |
| Sync | 667 T, then 735 T |
| Bit 0 | 2 × 855 T (approximately 2 kHz) |
| Bit 1 | 2 × 1710 T (approximately 1 kHz) |

`plan()` makes all timing and pitch decisions. `render()` turns the plan into samples. The tests examine `plan()` directly.

Procedure in `plan()`:

1. Use the raw bytes of `data`. Do not compress them. Compression removes the line structure (see step 6).
2. Calculate the tape speed: voice speed × content pitch × size turbo.
   - Voice speed comes from the kind (see "Voices").
   - Content pitch comes from a hash of `data`. It is between 0.9 and 1.1. Different content gets a different pitch. The same content always gets the same pitch.
   - Size turbo is between 1.0 and 1.4 (`MAX_TURBO`). It increases on a log scale with `size` and reaches 1.4 at 256 KB (`TURBO_FULL_KB`).
3. Calculate the target duration: `clamp(0.4 + 0.6 × log2(1 + KB), 0.5, 4.0)` seconds.
4. If the voice has a header, add a 17-byte Spectrum header block. The header contains `size`. Add a gap of 0.12 seconds after it.
5. Add the data block: flag byte `0xFF`, payload, XOR checksum.
6. Add a pause after each newline byte. The pause is 21000 T-states (`LINE_GAP_T`, 6 ms at real speed). The mix makes the pause silent. Code with short lines stutters. Prose plays in long runs. JSON on one line plays without pauses.
7. If the data is too long for the target duration at this speed, keep the longest start of the data that fits.
8. Give all remaining time to the pilot tone. The pilot gets a minimum share of the duration from the voice. A small payload thus gets a long pilot tone and a short burst of data.

The tape speed sets the pitch. The target duration sets the length. The two are independent: the pilot and the cut absorb the difference.

Pitch and duration cannot both be fixed while the data fills the time exactly. An earlier design fitted the data to the duration first and then shifted the speed. For mid-size payloads, the shift cancelled out, and all voices had the same pitch.

### Voices

| Kind | Speed | Minimum pilot | Header block |
|---|---|---|---|
| `memory` | 1.25 | 10 % | No |
| `save` | 1.0 | 30 % | Yes |
| `load` | 0.8 | 20 % | Yes |

The content pitch range (±10 %) is smaller than the steps between voices. Size turbo can still make a large `save` higher than a small `memory`. The header and pilot length still identify the kind.

### Lo-fi chain

```
square() ──▶ grit() ──▶ quantize ──▶ mix ──▶ sample-and-hold ──▶ tone() ──▶ × AMP ──▶ fade
22.05 kHz    22.05 kHz   4 bits                22.05 → 44.1 kHz    44.1 kHz
```

| Stage | Function | Effect |
|---|---|---|
| `square()` | Makes a ±1 square wave at `CRUSH_RATE` (22050 Hz). No band limit. Adds slow wow/flutter. | Aliasing. |
| `grit()` | Adds noise, a one-pole tape low-pass at `TAPE_LP_HZ` (8 kHz), dropouts, and crackle. | Tape texture. Soft edges, but the bit tones stay clear. |
| Quantize | Rounds to `CRUSH_BITS` (4 bits). | Bit crush. The low-pass ramps become steps. |
| Mix | Multiplies the data section by `DATA_LEVEL` (0.5, −6 dB). Pilot and sync stay at 1.0. A 10 ms ramp connects the two levels. | The data section sounds like broadband noise. This keeps it below the pilot tone. |
| Sample-and-hold | Repeats each sample 2 times up to 44.1 kHz. No interpolation filter. | Keeps the aliasing images. |
| `tone()` | FFT EQ: 2nd-order high-pass at 700 Hz. | Removes low frequencies. The curve agrees with a spectrum of real Spectrum tape audio. |

Each half-pulse must be longer than one sample at `CRUSH_RATE`. If it is shorter, the bit pattern cannot show, and the data section becomes a wash of noise. At 22050 Hz and 1.4 × turbo, the shortest half-pulse (bit 0) is approximately 4 samples. For this reason, `MIN_SCALE` and `CRUSH_RATE` must change together. Earlier values (7350 Hz, 4 × turbo, low-pass at 800 Hz) made a wash in the low-mid frequencies.

`CRUSH_RATE` must divide 44100 exactly. Then each sample repeats the same number of times. With a rate such as 8000 Hz, the repeat count changes between 5 and 6 in a pattern that repeats at 100 Hz. This pattern adds a buzz at 100 Hz and its harmonics.

The sound fades in over 30 ms and fades out over 5 ms.

Noise layers in `grit()`:

| Constant | Layer |
|---|---|
| `HISS` | A low constant noise floor. |
| `DATA_NOISE` | Noise level follows the value of each byte (cubed). Most bytes are quiet. Some bytes are loud. The pilot and sync have no data noise. |
| `GRAIN_NOISE`, `GRAIN_DENSITY` | Noise that switches on and off in random grains of 2 to 20 ms. |
| `CRACKLE_PER_SEC` | Sparse clicks. |
| `DROPOUT` | Slow random decreases in gain. |

All noise is cubed Gaussian noise. This noise is mostly quiet and has sharp peaks. It is less flat than white noise.

The output level is `AMP` (0.1). The macOS system volume controls the final level. There is no volume control in stickshaker.

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
2. Does the daemon answer? It sends a test `PostCompact` event with a summary and measures the round trip. This event always makes a sound.
3. For each agent (Claude Code, Codex): are its hooks installed? Which mode does each hook use? Each agent is optional. If an agent has some stickshaker hooks, all its hooks must be there.
4. Is at least one agent set up? If no agent has stickshaker hooks, this is an error.
5. Does `~/.codex/memories/` exist? This is information only.

### Hook timing in Claude Code

`claude --debug` writes the execution and duration of each hook.

## Installer

`install.py` merges stickshaker hooks into `~/.claude/settings.json`, or into `~/.codex/hooks.json` with `--codex`. It does not replace the file.

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
| `uv run stickshaker install-hooks [--codex] [--debug]` | Install the hooks for Claude Code, or for Codex. |
| `uv run stickshaker uninstall-hooks [--codex]` | Remove the hooks. |
| `uv run stickshaker doctor` | Check the daemon and the hooks. |
| `uv run scripts/demo.py FILE --kind KIND [--rate HZ] [--bits N] [--wav OUT]` | Play or save a sound for a file. |
| `uv run pytest` | Run the tests. |

Hooks load when an agent session starts. After you install or change the hooks, start a new session.

## Tests

| File | Subject |
|---|---|
| `tests/test_events.py` | Mapping from compaction hooks to events. |
| `tests/test_memory.py` | Memory scope, baseline, change detection, Bash writes, bursts. |
| `tests/test_daemon.py` | Real socket round trip, socket permissions, one daemon only, old socket, queue drops. |
| `tests/test_install.py` | Merge, idempotence, mode change, uninstall, backup. |
| `tests/test_synth.py` | Plan decisions: voices, content pitch, durations, truncation, newline pauses. |
| `tests/test_doctor.py` | Each agent optional, at least one required, partial installs reported. |
| `tests/test_codex.py` | Codex payloads as documented, the `sed` tag, Codex install, and the memories watcher with real FSEvents. |

The tests make no sound. They replace `render` and `play` with fakes.

Unix socket paths on macOS have a limit of approximately 104 bytes. The tests make sockets in a short temporary directory for this reason.

## Open items

| Item | Status |
|---|---|
| Output device change | PortAudio can continue to use the old output device after you change the device. Not examined. |
| Spectrum notches | The reference spectrum has dips at approximately 4.35 kHz and 9 kHz. Add them to `tone()` later. |
| Codex: not tested with a real Codex | All Codex behavior comes from the Codex docs. See "Not verified" in the Codex section. |
| Menu bar | Stage 5. Icon with Enable/Disable. Uses `Player.enabled`. Save the state in `~/.config/stickshaker/state.json`. |
| Start at login | Later. Use a launchd agent. |
