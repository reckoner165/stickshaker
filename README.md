<p align="center"><img src="assets/stickshaker.svg" width="96" alt="stickshaker icon: an aircraft yoke"></p>

# stickshaker

stickshaker plays a ZX Spectrum tape sound when Claude Code writes memory or compacts its context. The synth makes the sound from the bytes of the event, so different data makes a different sound.

For the design, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Requirements

- macOS
- [uv](https://docs.astral.sh/uv/)
- Claude Code

## Set up

1. Clone the repository:

   ```sh
   git clone git@github.com:reckoner165/stickshaker.git
   cd stickshaker
   ```

2. Install the dependencies:

   ```sh
   uv sync
   ```

3. Install the hooks in `~/.claude/settings.json`:

   ```sh
   uv run stickshaker install-hooks
   ```

   The installer adds 3 hooks. It does not change other hooks or settings. It makes a backup at `~/.claude/settings.json.stickshaker-bak`.

## Run

1. Start the daemon. Keep this terminal open:

   ```sh
   uv run stickshaker run -v
   ```

2. In a different terminal, check the setup:

   ```sh
   uv run stickshaker doctor
   ```

   All lines must show `ok`. You hear one short test sound.

3. Start a new Claude Code session. Hooks load only when a session starts.

4. Do one of these:
   - Tell Claude to save something to memory. You hear a short `memory` sound.
   - Type `/compact`. You hear a `save` sound, then a `load` sound.

The daemon log shows each event and each sound.

### Codex (optional)

stickshaker also plays sounds for Codex compaction and Codex memory writes. This support is based on the Codex docs only. It is not tested with a real Codex.

1. Install the Codex hooks in `~/.codex/hooks.json`:

   ```sh
   uv run stickshaker install-hooks --codex
   ```

2. Open the Codex CLI and run `/hooks`. Trust the stickshaker hooks. Codex does not run a new hook before you trust it.
3. To get sounds for Codex memory writes, turn on Codex memories. In the Codex app, go to **Settings > Personalization**. The daemon watches `~/.codex/memories/`.

To remove the Codex hooks, run `uv run stickshaker uninstall-hooks --codex`.

### Debug mode

In debug mode, a daemon that is not available shows as a hook error in Claude Code. Claude does not stop.

```sh
uv run stickshaker install-hooks --debug
```

To go back to silent mode, run `uv run stickshaker install-hooks` again.

### Stop and remove

1. To stop the daemon, push `Ctrl-C` in its terminal.
2. To remove the hooks:

   ```sh
   uv run stickshaker uninstall-hooks
   ```

If the hooks stay installed and the daemon does not run, Claude Code operates normally. You hear no sounds.

## Test

Run the tests:

```sh
uv run pytest
```

The tests make no sound and do not change your Claude Code settings.

To listen to the sound for a file without the daemon:

```sh
uv run scripts/demo.py stickshaker/synth.py --kind save
```

The values for `--kind` are `save`, `load`, and `memory`. To write a WAV file instead of playing the sound, add `--wav out.wav`.

## Troubleshooting

| Problem | Action |
|---|---|
| `doctor` shows `no socket` | Start the daemon with `uv run stickshaker run`. |
| `another stickshaker is listening` | A daemon is already running. Use it, or stop it first. |
| No sound in a Claude session | Start a new session. Then run `uv run stickshaker doctor`. |
| No sound for a memory write | Make sure the file is in `~/.claude/projects/<project>/memory/`. See "Memory detection" in [ARCHITECTURE.md](ARCHITECTURE.md). |

## Credits

The yoke icon is from [Arcticons](https://github.com/Arcticons-Team/Arcticons), licensed under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). The colour was changed to follow light and dark mode.
