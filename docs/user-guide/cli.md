# CLI Reference

`festo-dev-applied-motion` ships an interactive commissioning CLI for jogging axes and recording positions.

## Installation

Requires the `cli` extra:

```bash
uv add festo-dev-applied-motion[cli]
```

## Entry point

```bash
applied-motion --config gantry.json [subcommand]
```

If no subcommand is given, the interactive teach shell starts automatically.

### Global flags

| Flag | Default | Description |
|---|---|---|
| `--config PATH` | *(required)* | Path to gantry JSON config |
| `--gantry-name NAME` | `gantry_1` | Gantry component key in config |
| `--log-level LEVEL` | `WARNING` | Python logging threshold |

---

## Subcommands

| Subcommand | Description |
|---|---|
| `shell` | Interactive teach REPL (default) |
| `where` | Print current axis positions once |
| `home` | Home all axes once |
| `status` | Print gantry status snapshot |
| `jog` | Single non-interactive jog step |
| `jog-tui` | Arrow-key interactive jog TUI (no REPL) |
| `teach-pos` | Send `TEACH_POS` to PLC (FPosBAPI only) |
| `teach-tray` | Send `TEACH_TRAY` to PLC (FPosBAPI only) |

---

## Jog

Jogging moves one axis by a fixed step distance. Two modes are available.

### Non-interactive jog step

```bash
applied-motion --config gantry.json jog <axis> <direction> <step> [--velocity V] [--timeout T]
```

| Argument | Description |
|---|---|
| `axis` | Axis name (e.g. `X`, `Y`, `Z`) |
| `direction` | `+` (positive) or `-` (negative) |
| `step` | Distance in mm (must be positive) |
| `--velocity` | Speed in mm/s (default: `10.0`) |
| `--timeout` | Move timeout in seconds (default: `30`) |

Prints the full gantry position table after the move completes, then exits.

**Examples:**

```bash
# Move X axis +5 mm at default velocity
applied-motion --config gantry.json jog X + 5

# Move Z axis -10 mm at 25 mm/s with 60 s timeout
applied-motion --config gantry.json jog Z - 10 --velocity 25 --timeout 60
```

### Arrow-key jog TUI

```bash
applied-motion --config gantry.json jog-tui
```

Connects to the gantry and opens a full-screen TUI. No teach REPL involved. Press `Esc` or `q` to exit.

#### Key bindings

| Key | Action |
|---|---|
| `←` / `→` | Step axis[0] (typically X) negative / positive |
| `↑` / `↓` | Step axis[1] (typically Y) positive / negative |
| `Page Up` / `Page Down` | Step depth axis (Z or active depth axis) positive / negative |
| `+` | Increase step size (cycles 0.1 → 0.5 → 1 → 5 → 10 → 25 → 50 mm) |
| `-` | Decrease step size |
| `Tab` | Cycle Page Up/Page Down target to next depth axis (3+ axes) |
| `Shift+Tab` | Cycle to previous depth axis |
| `Esc` or `q` | Exit jog TUI |

The TUI displays current position for all axes, the active step size, and the last operation status. When three or more axes are configured, `Tab` cycles which axis responds to Page Up/Page Down — the active axis is marked with `◀`.

### Jog inside the teach shell

After launching `shell`, the `jog` command is also available at the REPL prompt:

```
motion> jog                          # enter arrow-key jog mode
motion> jog X + 5                    # single step, 5 mm at default velocity
motion> jog X + 5 25                 # single step, 5 mm at 25 mm/s
```

---

## Interactive teach shell

```bash
applied-motion --config gantry.json shell
```

### REPL commands

| Command | Description |
|---|---|
| `jog` | Enter arrow-key jog mode |
| `jog <axis> <+/-> <step> [vel]` | Single step-move |
| `where` | Print current axis positions |
| `home` | Home all axes |
| `capture <label>` | Record current position as *label* |
| `teach pos <pos_id>` | Send `TEACH_POS` to PLC (FPosBAPI only) |
| `teach tray <tray_id> <tray_pos>` | Send `TEACH_TRAY` to PLC (FPosBAPI only) |
| `list` | List all captured positions |
| `save <path>` | Write positions to a JSON file |
| `load <path>` | Merge positions from a JSON file |
| `help` | Print command reference |
| `quit` | Exit |

Tab-completion and command history are available at the prompt.

### Typical commissioning workflow

1. Start the shell and home the gantry:
   ```
   motion> home
   ```

2. Jog to a target position using arrow-key mode or single-step commands:
   ```
   motion> jog
   # ... move with arrow keys, press Esc when done
   ```

3. Capture the position under a label:
   ```
   motion> capture deck_a1
   ```

4. Repeat for each position. Review with `list`.

5. Save to JSON:
   ```
   motion> save deck_layout.json
   ```

For FPosBAPI backends, use `teach pos <id>` after each capture to commit the position directly to the PLC slot before saving.

---

## Programmatic use

`TeachSession` is available without the `cli` extra and has no dependency on `prompt_toolkit` or `rich`:

```python
from applied_motion import Gantry
from applied_motion.cli import TeachSession

with Gantry.from_config("gantry.json") as gantry:
    gantry.home()
    session = TeachSession(gantry)
    session.jog("X", "+", 5.0)           # step 5 mm
    session.capture("deck_a1")
    session.save("deck_layout.json")
```

The `on_capture` hook fires after every `capture` call — use it to commit positions to the PLC:

```python
def plc_hook(label: str, pos: dict[str, float]) -> None:
    gantry.teach_pos(pos_id=label_to_id[label])

session = TeachSession(gantry, on_capture=plc_hook)
```

`TeachSession.jog` returns the full location dict after the move:

```python
location = session.jog("Z", "-", 10.0, velocity=25.0, timeout=60)
print(location)  # {'X': 150.0, 'Y': 0.0, 'Z': -10.0}
```
