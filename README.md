# Festo Applied Motion

`festo-dev-applied-motion` is a Python library for controlling Festo electrically driven motion components through a common gantry API.

It supports:

| Backend | Hardware | Transport |
| --- | --- | --- |
| **Modbus / `festo-edcon`** | CMMT and CMMT-ST drives | Modbus TCP per axis |
| **FPosBAPI** | CECC-X PLC running the FPosBAPI CoDeSys server | Shared TCP connection |

> [!WARNING]
> This library can command real mechanical motion. Commission and test the hardware first, keep the machine clear, and verify that every target position and velocity is safe before running an example.

## Installation

Install from a package registry:

```bash
uv add festo-dev-applied-motion
```

Install the current repository in editable mode:

```bash
git clone https://github.com/Festo-se/festo-dev-applied-motion.git
cd festo-dev-applied-motion
uv pip install -e .
```

For the optional commissioning CLI, install the `cli` extra:

```bash
uv add "festo-dev-applied-motion[cli]"
```

## Quick start

Create a JSON configuration for the gantry, then load it with `Gantry.from_config`:

```python
from collections import deque

from applied_motion import Gantry

with Gantry.from_config("gantry.json", name="gantry_1") as gantry:
	gantry.home()
	gantry.move_to(
		deque([
			{"X": {"position": 150.0, "velocity": 80.0}},
		])
	)
	print(gantry.get_location())
```

Positions are measured in **millimetres (mm)** and velocities in **millimetres per second (mm/s)**.

## Configuration

`Gantry.from_config` accepts a configuration dictionary or a path to a JSON file. The repository includes [`gantry.json`](gantry.json) as a reference configuration containing both backend styles.

The relevant component is located at `component_config.components.<gantry_name>`:

```json
{
	"spec_version": "3.0",
	"component_config": {
		"components": {
			"gantry_1": {
				"backend": "fposbapi",
				"interface": {
					"type": "tcp/ip",
					"ip": "192.168.10.25",
					"port": 1234
				},
				"axes": {
					"X": {"name": "X", "index": 1},
					"Y": {"name": "Y", "index": 2},
					"Z": {"name": "Z", "index": 3}
				},
				"axis_order": ["X", "Y", "Z"],
				"concurrent_axes": null
			}
		}
	}
}
```

For a Modbus gantry, set `backend` to `"modbus"` and configure each axis with its drive IP instead:

```json
{
	"backend": "modbus",
	"axes": {
		"X": {"name": "X", "ip": "192.168.0.100"},
		"Y": {"name": "Y", "ip": "192.168.0.101"}
	},
	"axis_order": ["X", "Y"],
	"concurrent_axes": ["X", "Y"]
}
```

> [!IMPORTANT]
> Modbus drives must be commissioned in Festo Automation Suite before motion commands are issued. FPosBAPI axis indices must match the numbering in the CECC-X CoDeSys application.

## Core API

`Gantry` provides a backend-independent interface for common operations:

- `home()` — home all axes.
- `move_to(movements, timeout=None, concurrent=False)` — execute queued single-axis moves sequentially or concurrently.
- `get_location()` — read the current position of every axis.
- `get_status()` — return per-axis state, aggregate health, and controller diagnostics where available.
- `is_stopped()` and `is_ready_for_motion()` — query aggregate axis state.
- `supports_teach()`, `teach_pos()`, and `teach_tray()` — use PLC teaching operations when supported by FPosBAPI.
- `list_commands()` — list available backend commands.

Movement entries are single-axis dictionaries. Set `position_type` to `"relative"` for relative motion; absolute motion is the default:

```python
from collections import deque

movements = deque(
	[
		{"X": {"position": 120.0, "velocity": 60.0}},
		{"Y": {"position": 10.0, "velocity": 30.0, "position_type": "relative"}},
	]
)
gantry.move_to(movements)
```

## Commissioning CLI

The optional CLI provides an interactive motion shell and one-shot commands:

```bash
applied-motion --config gantry.json status
applied-motion --config gantry.json where
applied-motion --config gantry.json home
applied-motion --config gantry.json jog X + 5
applied-motion --config gantry.json shell
```

Use `--help` for command-specific options. The `teach-pos` and `teach-tray` commands are available for FPosBAPI configurations.

## Examples and documentation

- [Getting started](docs/getting-started/index.md)
- [FPosBAPI user guide](docs/user-guide/fposbapi.md)
- [Modbus / `festo-edcon` user guide](docs/user-guide/edcon.md)
- [CLI reference](docs/user-guide/cli.md)
- [FPosBAPI examples](docs/examples/fposbapi.md)
- [Modbus / `festo-edcon` examples](docs/examples/edcon.md)
- [`examples/` directory](examples/)

## Development

Install development dependencies and run the test suite:

```bash
uv sync
uv run pytest
```

Hardware-dependent tests are marked with `hardware` and can be excluded with `-m "not hardware"`.
