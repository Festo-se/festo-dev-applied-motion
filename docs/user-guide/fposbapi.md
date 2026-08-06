# FPosBAPI Backend

The FPosBAPI backend connects Python directly to a **CECC-X PLC** running the FPosBAPI CoDeSys server application.
A single persistent TCP socket carries all motion commands for every axis of the gantry, serialised by an internal threading lock so concurrent Python threads cannot interleave frames.

---

## Architecture

```
┌─────────────────────────────────┐
│           Gantry                │
│  axes: {X: FPosBAxis, …}        │
│  _backend: FPosBAPIGantryBackend│
│                                 │
│  ┌──────────┐ ┌──────────────────────┐
│  │FPosBAxis │ │FPosBAPIGantryBackend │
│  │  name="X"│ │  ip / port   │  │
│  │  index=1 │ │  └─client────┐     │
│  │          │─│               │     │
│  │FPosBAxis │ │   FPosBAPIClient    │
│  │  name="Y"│ └──────────────────────┘
│  │  index=2 │         │         │
│  └──────────┘         │ TCP     │
└──────────────────────────────── ┘
                         │
              CECC-X PLC (CoDeSys)
              FPosBAPI server :1234
```

Three classes work together:

| Class | Responsibility |
|---|---|
| `FPosBAPIClient` | Opens and maintains the TCP socket; formats/parses ASCII frames; thread-safe via `threading.Lock`. |
| `FPosBAxis` | Represents one logical axis; translates `move()`, `home()`, and `get_current_axis_position()` into FPosBAPI commands. |
| `FPosBAPIGantryBackend` | Owns the shared `FPosBAPIClient` and backend-specific behavior (`home`, diagnostics, teach operations). |
| `Gantry` | Owns axis mapping and delegates backend behavior to `FPosBAPIGantryBackend`; dispatches movement queues. |

---

## Wire Protocol

The server speaks a line-oriented ASCII protocol over TCP.

**Request format**

```
MSG_ID, COMMAND[, PARAM, ...]\r\n
```

**Response format**

```
MSG_ID, COMMAND[, ECHO_PARAMS..., RETURN_VALS...], ERR_ID, ERR_TYPE, ERR_MSG\r\n
```

On success the last three comma-delimited fields are always `0, NULL, SUCCESS`.
On error they carry a non-zero error id, an error type string, and a message string.

Example exchange for a move command:

```
→ 1, SET_PAR, 103, 80.0\r\n
← 1, SET_PAR, 103, 80.0, 0, NULL, SUCCESS\r\n
→ 2, MOVE_AXIS, 1, 0, 150.0\r\n
← 2, MOVE_AXIS, 1, 0, 150.0, 0, NULL, SUCCESS\r\n
```

---

## FPosBAPIClient

`FPosBAPIClient` is instantiated once per gantry and shared across all axes.
You normally let `Gantry.from_config` create it; but for special use cases you can build it directly:

```python
from applied_motion.backends.fposbapi_client import FPosBAPIClient

client = FPosBAPIClient(ip="192.168.10.25", port=1234, timeout=10.0)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `ip` | `str` | — | IPv4 address of the CECC-X PLC. |
| `port` | `int` | `1234` | TCP port the FPosBAPI server is listening on. |
| `timeout` | `float \| None` | `None` | Socket timeout in seconds; `None` = blocking, no timeout. |

### Sending raw commands

```python
# Send a command with no parameters
lines = client.send_command("ENABLE")

# Send a command with parameters
lines = client.send_command("SET_PAR", 103, 80.0)

# Send a move command (axis_index=1, relative=0, position_mm=150.0)
lines = client.send_command("MOVE_AXIS", 1, 0, 150.0)
```

`send_command` returns a list of all response lines received before the empty frame terminator.
It raises `FPosBAPIClientError` if the server reports an error or the connection is lost.

### Reconnection behaviour

If the TCP connection drops mid-session, `FPosBAPIClient` performs exactly **one automatic reconnect** attempt before raising `FPosBAPIClientError`.
TCP keepalive is enabled on the socket (15 s idle time, 5 s interval) to detect silent link failures early.

### Command wrappers available today

`FPosBAPIClient` provides typed wrappers around many PLC commands in addition to `send_command`, including:

- Motion: `enable`, `disable`, `home`, `move_pos`, `move_loc`, `halt`, `resume`, `abort`
- Teaching: `teach_pos`, `teach_tray`, `read_pos`, `write_pos`, `read_tray`, `write_tray`
- Diagnostics: `sys_status`, `is_error`, `fpb_error`, `read_err`, `err_log`, `com_log`
- Parameters / I/O: `get_par`, `set_par`, `get_io`, `set_io`

---

## FPosBAxis

`FPosBAxis` represents a single logical axis of the gantry.
It stores a **1-based axis index** that must match the axis numbering in the CoDeSys program:

| Index | Axis |
|---|---|
| `1` | X |
| `2` | Y |
| `3` | Z |

### Construction (manual)

```python
from applied_motion.backends.fposbapi_client import FPosBAPIClient
from applied_motion.backends.fposbapi_axis import FPosBAxis

client = FPosBAPIClient(ip="192.168.10.25")
x_axis = FPosBAxis(name="X", index=1, client=client)
y_axis = FPosBAxis(name="Y", index=2, client=client)
z_axis = FPosBAxis(name="Z", index=3, client=client)
```

### Moving an axis

```python
# Absolute move: go to 200 mm at 100 mm/s
x_axis.move(position=200.0, velocity=100.0)

# Relative move: advance 10 mm from the current position
x_axis.move(position=10.0, velocity=50.0, position_type="relative")
```

> **Note on velocity:** `move()` writes the velocity to PLC parameter 103 (global speed)
> immediately before the `MOVE_AXIS` command.  When axes move concurrently the two
> commands are not atomic — use sequential moves when per-move velocity accuracy matters.

### Reading the current position

```python
pos_mm = x_axis.get_current_axis_position()
print(f"X is at {pos_mm} mm")
```

### Homing

Homing via a single axis proxy sends the `HOME` command to the PLC, which always
homes **all axes simultaneously**.  Prefer `Gantry.home()` to avoid issuing the
command more than once.

```python
x_axis.home()  # homes X, Y, and Z together
```

### Drive-ready state and homed state

`FPosBAxis.ready_for_motion()` queries `IS_ENBL`, and `FPosBAxis.is_homed()` queries `IS_HOME`.

```python
if not x_axis.ready_for_motion():
    raise RuntimeError("Drives are not enabled")

if not x_axis.is_homed():
    x_axis.home()
```

---

## Gantry (FPosBAPI mode)

### From a configuration file

```python
from pathlib import Path
from applied_motion import Gantry

gantry = Gantry.from_config(Path("my-gantry-config.json"))
```

`from_config` reads the `backend` key, creates one `FPosBAPIClient`, sends
an initial `ENABLE` command to verify the connection, then wraps each axis entry
in an `FPosBAxis` proxy.

### Supported config keys

```json
{
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
```

| Key | Required | Description |
|---|---|---|
| `backend` | Yes | Must be `"fposbapi"` to select this backend. |
| `interface.ip` | Yes | IPv4 address of the CECC-X PLC. |
| `interface.port` | No | TCP port (default `1234`). |
| `axes` | Yes | Dict of axis name → `{name, index}`. |
| `axis_order` | No | Ordered list of axis names; defaults to dict insertion order. |
| `concurrent_axes` | No | List of axis names allowed to move simultaneously, or `null`. |

### Full config file layout (spec version 3.0)

`from_config` also accepts the full Festo component config schema used in production:

```json title="gantry-config.json"
{
    "spec_version": "3.0",
    "system_config": {"metadata": {}},
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

When the top-level `component_config` key is present, `from_config` scopes
automatically to `component_config.components.<name>` (default `"gantry_1"`).

---

## Gantry API Reference

### `Gantry.home()`

Sends a single `HOME` command via the shared `FPosBAPIClient`.  All axes home
simultaneously on the PLC side.

```python
gantry.home()
```

### `Gantry.move_to(movements, timeout=None, concurrent=False)`

Dispatches a queue of movement dicts to the gantry axes.

```python
from collections import deque

movements = deque([
    {"X": {"position": 150.0, "velocity": 80.0}},
    {"Y": {"position": 75.0,  "velocity": 60.0}},
    {"Z": {"position": 20.0,  "velocity": 40.0}},
])
gantry.move_to(movements)
```

Each dict maps one axis name to its kinematic parameters.

| `move_to` parameter | Description |
|---|---|
| `movements` | `deque` of `{axis_name: {position, velocity}}` dicts. |
| `timeout` | Per-move time limit in seconds, or `None`. |
| `concurrent` | `True` to dispatch all valid movements in one parallel batch. |

When `concurrent=False`, the gantry still uses `concurrent_axes` grouping from config:

- axes listed in `concurrent_axes` may run in the same batch,
- axes not in that set are dispatched as individual batches.

### `Gantry.get_location()`

Returns a dict of axis name → current position (mm) by querying each axis.

```python
location = gantry.get_location()
# {'X': 150.0, 'Y': 75.0, 'Z': 20.0}
```

### `Gantry.get_status()`

Returns backend metadata, per-axis states, and controller diagnostics.

```python
status = gantry.get_status()
print(status["backend"])
print(status["summary"]["healthy"])
print(status["controller"])  # sys_status/is_error/fpb_error/read_err/error
```

### Teaching helpers and command discovery

For FPosBAPI backends:

```python
if gantry.supports_teach():
    gantry.teach_pos(1)
    gantry.teach_tray(tray_id=1, tray_pos=1)

print(gantry.list_commands())
```

---

## Error Handling

All FPosBAPI errors surface as `FPosBAPIClientError`.

```python
from applied_motion.backends.fposbapi_client import FPosBAPIClientError

try:
    gantry.move_to(deque([{"X": {"position": 9999.0, "velocity": 80.0}}]))
except FPosBAPIClientError as exc:
    print(f"PLC rejected the command: {exc}")
```

Motion failures at the `Gantry` level raise `MovementError`, which wraps the underlying exception.

```python
from applied_motion.applied_motion import MovementError

try:
    gantry.move_to(movements)
except MovementError as exc:
    print(f"Gantry move failed: {exc}")
```
