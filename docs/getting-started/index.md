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

One direct path to a working gantry session is `Gantry.from_config(...)`.
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

For direct-drive setups, configure each axis with `name` and `ip`, and set backend to `"modbus"`.

> **Critical prerequisite (Edcon / Modbus):**
> Each physical axis must be commissioned in **Festo Automation Suite** first (network settings, drive readiness, and motion commissioning).
> If axes are not commissioned in Festo Automation Suite, Python commands can connect but motion calls are likely to fail or behave unpredictably.

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

## `component_config` fields that matter most

For production-style config files, `Gantry.from_config(...)` reads under:

- `component_config.components.<gantry_name>`

Most startup issues come from a small set of fields. Validate these first.

### 1) Gantry identity and backend selection

Inside `component_config.components`, choose one gantry object (usually `gantry_1`) with:

- `backend`: must be `"modbus"` or `"fposbapi"`
- `axes`: per-axis map (shape depends on backend)
- Optional orchestration fields: `axis_order`, `concurrent_axes`

Example:

```json
"component_config": {
    "components": {
        "gantry_1": {
            "backend": "fposbapi",
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
```

### 2) `interface` (FPosBAPI only)

For `backend: "fposbapi"`, `interface` identifies CECC-X endpoint:

- `interface.type`: use `"tcp/ip"`
- `interface.ip`: CECC-X PLC IPv4 address
- `interface.port`: FPosBAPI server port (typically `1234`)

How to identify values on your system:

- `ip`: read PLC Ethernet IP from your PLC engineering/deployment setup.
- `port`: use configured FPosBAPI server port in CoDeSys project/runtime (default `1234` if unchanged).

### 3) `axes` mapping

#### For FPosBAPI

Each axis entry requires:

- `name`: logical axis label used in Python movement dicts (for example `"X"`)
- `index`: 1-based PLC axis index used by FPosBAPI program

Example:

```json
"axes": {
    "X": {"name": "X", "index": 1},
    "Y": {"name": "Y", "index": 2},
    "Z": {"name": "Z", "index": 3}
}
```

How to identify `index` values:

- Open CoDeSys project for CECC-X.
- Check axis numbering used by FPosBAPI server logic.
- Copy that exact numbering into config. Do not assume physical wiring order equals PLC index order.

#### For Modbus / Edcon

Each axis entry requires:

- `name`: logical axis label used in Python movement dicts
- `ip`: drive IP for that axis

Example:

```json
"axes": {
    "X": {"name": "X", "ip": "192.168.0.100"},
    "Y": {"name": "Y", "ip": "192.168.0.101"},
    "Z": {"name": "Z", "ip": "192.168.0.102"}
}
```

How to identify `ip` values:

- Read each drive IP from Festo Automation Suite after commissioning.
- Confirm host machine can reach each drive IP on same subnet.

> **Commissioning reminder (must-do):**
> Edcon axis communication assumes drives were fully commissioned in **Festo Automation Suite**.
> If not commissioned, expect at worst damage components and at best failed homing/moves even if network ping succeeds.

### 4) `axis_order` and `concurrent_axes`

- `axis_order`: creation/execution order for axes.
    - Keep list aligned with keys in `axes`.
- `concurrent_axes`: optional list of axis names allowed to run in same movement batch when `concurrent=False`.
    - Use `null` to disable backend-driven grouping.

Example:

```json
"axis_order": ["X", "Y", "Z"],
"concurrent_axes": ["X", "Y"]
```

### 5) Build your own config from plant data

Use this process to fill values safely:

1. Pick backend (`"modbus"` direct-drive or `"fposbapi"` CECC-X PLC).
2. Create axis inventory table from real hardware:
     - logical name (`X`, `Y`, `Z`...)
     - device IP (Modbus) or PLC axis index (FPosBAPI)
3. Enter `axes` from that table.
4. Set `axis_order` to your intended deterministic move ordering.
5. Add `concurrent_axes` only for axis pairs validated as safe to run together.

Practical inventory example:

- Physical drives in cabinet:
    - CMMT-X at `192.168.0.100`
    - CMMT-Y at `192.168.0.101`
    - CMMT-Z at `192.168.0.102`
- Resulting Modbus `axes` map:
    - `X` uses `192.168.0.100`
    - `Y` uses `192.168.0.101`
    - `Z` uses `192.168.0.102`

For FPosBAPI, equivalent mapping comes from CoDeSys axis index assignment rather than per-axis drive IPs.

---

## Next steps

- **[FPosBAPI User Guide](../user-guide/fposbapi.md)** — protocol and PLC-backed architecture details.
- **[festo-edcon User Guide](../user-guide/edcon.md)** — direct-drive architecture and motion behavior.
- **[FPosBAPI Examples](../examples/fposbapi.md)** — copy-paste oriented FPosBAPI workflows.
- **[festo-edcon Examples](../examples/edcon.md)** — copy-paste oriented Modbus/festo-edcon workflows.
- **API Reference** — auto-generated class/method reference (shown in docs navigation after build).
