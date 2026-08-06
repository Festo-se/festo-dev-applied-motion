# festo-edcon / Modbus Backend

The festo-edcon backend connects Python directly to each Festo drive using Modbus TCP.
Each logical axis is represented by an [`EdconAxis`][applied_motion.backends.edcon_axis.EdconAxis], and the gantry coordinates those axis objects through [`Gantry`][applied_motion.gantry.Gantry].

---

## Architecture

```text
┌───────────────────────────────────────┐
│                 Gantry                │
│  axes: {X: EdconAxis, Y: EdconAxis…}  │
│  _backend: ModbusGantryBackend        │
│                                       │
│   ┌──────────┐   ┌──────────┐         │
│   │EdconAxis │   │EdconAxis │  ...    │
│   │ name="X" │   │ name="Y" │         │
│   │ ip=.100  │   │ ip=.101  │         │
│   └────┬─────┘   └────┬─────┘         │
└────────┼──────────────┼───────────────┘
         │              │
     Modbus TCP     Modbus TCP
         │              │
      CMMT-X         CMMT-Y
```

Unlike FPosBAPI, there is no shared PLC socket. Each axis owns its own transport.

---

## Axis Construction

You can construct axes directly:

```python
from applied_motion.backends.edcon_axis import EdconAxis

x_axis = EdconAxis(name="X", ip="192.168.0.100")
y_axis = EdconAxis(name="Y", ip="192.168.0.101")
```

Or, create through config:

```python
from pathlib import Path
from applied_motion import Gantry

gantry = Gantry.from_config(Path("modbus-gantry-config.json"))
```

---

## Config Shape (Modbus)

```json
{
    "backend": "modbus",
    "axes": {
        "X": {"name": "X", "ip": "192.168.0.100"},
        "Y": {"name": "Y", "ip": "192.168.0.101"},
        "Z": {"name": "Z", "ip": "192.168.0.102"}
    },
    "axis_order": ["X", "Y", "Z"],
    "concurrent_axes": ["X", "Y"]
}
```

Supported fields:

| Key | Required | Description |
|---|---|---|
| `backend` | No* | Defaults to `"modbus"` when omitted. |
| `axes` | Yes | Axis map with `name` + `ip` per axis. |
| `axis_order` | No | Execution/creation order (defaults to `axes` insertion order). |
| `concurrent_axes` | No | Optional subset of axis names that may be batched together. |

\*Omitting `backend` still selects Modbus.

---

## Motion Behavior

### `home()`

For Modbus backend, `Gantry.home()` calls `home()` on each axis sequentially.

### `move_to(movements, timeout=None, concurrent=False)`

- `concurrent=True` dispatches all valid moves in one parallel batch.
- `concurrent=False` (default) still uses `concurrent_axes` grouping when configured.

Each movement is a single-axis dict:

```python
{"X": {"position": 120.0, "velocity": 60.0}}
```

All user-facing position values are in **mm**.

---

## Status and Diagnostics

Use `get_status()` for per-axis health plus summary booleans:

```python
status = gantry.get_status()
print(status["summary"]["healthy"])
print(status["axes"]["X"])
```

For Modbus backend, `controller` fields in status are `None` (no shared PLC controller API).

---

## Error Handling

Axis-level move failures surface as [`MovementError`][applied_motion.gantry.MovementError] from `Gantry.move_to`.
Malformed movement entries and unknown axis names are skipped and logged.

```python
from collections import deque
from applied_motion.gantry import MovementError

try:
    gantry.move_to(deque([
        {"X": {"position": 999999.0, "velocity": 50.0}},
    ]))
except MovementError as exc:
    print(f"Move failed: {exc}")
```
