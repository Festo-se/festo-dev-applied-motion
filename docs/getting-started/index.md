# Getting Started

`festo-dev-applied-motion` gives you a clean Python interface for commanding Festo electrically-driven motion components.
Two axis backends are provided:

| Backend | Hardware | Transport |
|---|---|---|
| **Modbus** (`EdconAxis`) | CMMT / CMMT-ST servo drives | Modbus TCP per axis |
| **FPosBAPI** (`FPosBAxis`) | CECC-X PLC running FPosBAPI CoDeSys server | Single TCP connection, ASCII protocol |

This guide walks through installation and a minimal working session using the **FPosBAPI** backend — the recommended choice for multi-axis gantries controlled by a CECC-X.

---

## Hardware Requirements (FPosBAPI)

- **CECC-X PLC** with the FPosBAPI CoDeSys application deployed and running.
- The PLC listens on **TCP port 1234** by default.
- The controlling PC must be on the same Ethernet subnet as the PLC.

---

## Installation

### From a package registry

```bash
uv add festo-dev-applied-motion
```

### From the Git repository

```bash
uv pip install git+https://github.com/Festo-se/festo-dev-applied-motion.git
```

### Editable install from source

```bash
git clone https://github.com/Festo-se/festo-dev-applied-motion.git
cd festo-dev-applied-motion
uv pip install -e .
```

---

## Quick Start

The fastest path to a working gantry session is [`Gantry.from_config`][applied_motion.gantry.Gantry.from_config].
Pass it a JSON configuration dict (or path to a JSON file) describing your hardware:

```python
from pathlib import Path
from collections import deque
from applied_motion import Gantry

# Load from a JSON file on disk
gantry = Gantry.from_config(Path("my-gantry-config.json"))

# Home all axes before commanding motion
gantry.home()

# Move the X axis to 150 mm at 80 mm/s
gantry.move_to(deque([{"X": {"position": 150.0, "velocity": 80.0}}]))

# Read back all axis positions
print(gantry.get_location())
# → {'X': 150.0, 'Y': 0.0, 'Z': 0.0}
```

See [FPosBAPI Examples](../examples/fposbapi.md) for complete, runnable scripts.

---

## Configuration File Format

A minimal three-axis FPosBAPI config looks like this:

```json title="my-gantry-config.json"
{
    "spec_version": "3.0",
    "component_config": {
        "metadata": {},
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

Replace `192.168.10.25` with the actual IP address of your CECC-X PLC.  The `index` values must match the axis numbering configured in the CoDeSys program (1 = X, 2 = Y, 3 = Z by default).

---

## Next Steps

- **[FPosBAPI Backend Guide](../user-guide/fposbapi.md)** — deeper look at the client/axis/gantry architecture, protocol details, and configuration options.
- **[FPosBAPI Examples](../examples/fposbapi.md)** — copy-paste ready code for connection, homing, moves, position readback, and error handling.
- **[API Reference](../api/)** — auto-generated reference for every public class and method.
