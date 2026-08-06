# Getting Started

`festo-dev-applied-motion` gives you one Python API (`Gantry`) across both supported backend types:

| Backend | Hardware | Transport |
|---|---|---|
| **Modbus / festo-edcon** (`EdconAxis`) | CMMT / CMMT-ST drives | Modbus TCP per axis |
| **FPosBAPI** (`FPosBAxis`) | CECC-X PLC with CoDeSys FPosBAPI server | Shared TCP ASCII protocol |

Use this page for the quickest path to a running configuration, then jump into the backend-specific guide.

---

## Hardware requirements (FPosBAPI)

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

## Quick start

One direct path to a working gantry session is [`Gantry.from_config`][applied_motion.gantry.Gantry.from_config].
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

See [FPosBAPI Examples](../examples/fposbapi.md) for complete examples.

---

## Quick start (Modbus / festo-edcon)

For direct-drive setups, configure each axis with `name` and `ip`, and set backend to `"modbus"`:

```json title="my-modbus-gantry-config.json"
{
    "spec_version": "3.0",
    "component_config": {
        "metadata": {},
        "components": {
            "gantry_1": {
                "backend": "modbus",
                "axes": {
                    "X": {"name": "X", "ip": "192.168.0.100"},
                    "Y": {"name": "Y", "ip": "192.168.0.101"},
                    "Z": {"name": "Z", "ip": "192.168.0.102"}
                },
                "axis_order": ["X", "Y", "Z"],
                "concurrent_axes": ["X", "Y"]
            }
        }
    }
}
```

```python
from collections import deque
from pathlib import Path

from applied_motion import Gantry

gantry = Gantry.from_config(Path("my-modbus-gantry-config.json"))
gantry.home()
gantry.move_to(deque([{"X": {"position": 120.0, "velocity": 60.0}}]))
print(gantry.get_location())
```

See [festo-edcon Examples](../examples/edcon.md) for more patterns.

---

## Configuration file format

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

## Next steps

- **[FPosBAPI User Guide](../user-guide/fposbapi.md)** — protocol and PLC-backed architecture details.
- **[festo-edcon User Guide](../user-guide/edcon.md)** — direct-drive architecture and motion behavior.
- **[FPosBAPI Examples](../examples/fposbapi.md)** — copy-paste oriented FPosBAPI workflows.
- **[festo-edcon Examples](../examples/edcon.md)** — copy-paste oriented Modbus/festo-edcon workflows.
- **API Reference** — auto-generated class/method reference (shown in docs navigation after build).
