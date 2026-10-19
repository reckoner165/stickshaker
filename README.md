<p align="center"><img src="assets/stickshaker.svg" width="96" alt="stickshaker icon: an aircraft yoke"></p>

# stickshaker

stickshaker plays a ZX Spectrum tape sound when Claude Code or Codex writes memory or compacts its context. The synth makes the sound from the bytes of the event, so different data makes a different sound.

stickshaker reads files only in `~/.claude` and `~/.codex`. It does not read files in other directories on your computer.

For the design, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Requirements

- macOS
- [uv](https://docs.astral.sh/uv/)

## Supported agents

Set up stickshaker for one agent or for both. Each agent is optional.

| Agent | Sounds | Status |
|---|---|---|
| Claude Code | Compaction, memory writes | Tested |
| Codex | Compaction, memory writes | Based on the Codex docs only. Not tested with a real Codex. |

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

3. Install the hooks for each agent that you use. The installer does not change other hooks or settings. It makes a backup of the file before it writes.

   **Claude Code** (`~/.claude/settings.json`):

   ```sh
   uv run stickshaker install-hooks
   ```

   **Codex** (`~/.codex/hooks.json`):

   ```sh
   uv run stickshaker install-hooks --codex
   ```

   Then open the Codex CLI and run `/hooks`. Trust the stickshaker hooks. Codex does not run a new hook before you trust it. For sounds on Codex memory writes, turn on Codex memories: in the Codex app, go to **Settings > Personalization**.

## Run

1. Start the daemon. Keep this terminal open:

   ```sh
   uv run stickshaker run -v
   ```

2. In a different terminal, check the setup:

   ```sh
   uv run stickshaker doctor
   ```

   You hear one short test sound. The hook lines for each agent that you set up must show `ok`.

3. Start a new agent session. Hooks load only when a session starts.

4. Try it:
   - Claude Code: tell Claude to save something to memory, or type `/compact`.
   - Codex: type `/compact`.

   A memory write plays a short `memory` sound. A compaction plays a `save` sound, then a `load` sound. The daemon log shows each event and each sound.

### Debug mode

In debug mode, a daemon that is not available shows as a hook error in the agent. The agent does not stop.

```sh
uv run stickshaker install-hooks --debug           # Claude Code
uv run stickshaker install-hooks --codex --debug   # Codex
```

To go back to silent mode, install the hooks again without `--debug`.

### Stop and remove

1. To stop the daemon, push `Ctrl-C` in its terminal.
2. To remove the hooks:

   ```sh
   uv run stickshaker uninstall-hooks           # Claude Code
   uv run stickshaker uninstall-hooks --codex   # Codex
   ```

If the hooks stay installed and the daemon does not run, the agents operate normally. You hear no sounds.

## Test

Run the tests:

```sh
uv run pytest
```

The tests make no sound and do not change your agent settings.

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
| No sound in an agent session | Start a new session. Then run `uv run stickshaker doctor`. |
| No sound for a Claude Code memory write | Make sure the file is in `~/.claude/projects/<project>/memory/`. See "Memory detection" in [ARCHITECTURE.md](ARCHITECTURE.md). |
| No sound for a Codex memory write | Make sure Codex memories are on. Codex writes memories some time after a chat becomes idle. |

## Credits

The yoke icon is from [Arcticons](https://github.com/Arcticons-Team/Arcticons), licensed under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). The colour was changed to follow light and dark mode.
