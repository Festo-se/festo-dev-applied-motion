# FPosBAPI Examples

Copy-paste ready examples for every common workflow with the FPosBAPI backend.
All examples assume a CECC-X PLC is reachable at `192.168.10.25:1234`.

---

## 1. Connect via Configuration File

Config-driven approach — pass JSON config file to `Gantry.from_config`:

```python title="connect_from_config.py" linenums="1"
from pathlib import Path
from collections import deque
from applied_motion import Gantry

# Point at your JSON config file
CONFIG_PATH = Path("gantry-config.json")

gantry = Gantry.from_config(CONFIG_PATH)

print(repr(gantry))
# Gantry(['X', 'Y', 'Z'])
```

The config file (`gantry-config.json`):

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

If you have multiple gantries in one config file, pass the component name as the second argument:

```python
gantry = Gantry.from_config(Path("gantry-config.json"), name="gantry_2")
```

---

## 2. Connect Manually (without a config file)

For quick scripts or interactive sessions you can build the stack by hand:

```python title="connect_manual.py" linenums="1"
from applied_motion.backends.fposbapi_client import FPosBAPIClient
from applied_motion.backends.fposbapi_axis import FPosBAxis
from applied_motion.backends.gantry_backend import FPosBAPIGantryBackend
from applied_motion.gantry import Gantry

# One shared TCP connection for the whole gantry
client = FPosBAPIClient(ip="192.168.10.25", port=1234, timeout=10.0)

# Axis index matches CoDeSys program: 1=X, 2=Y, 3=Z
x = FPosBAxis(name="X", index=1, client=client)
y = FPosBAxis(name="Y", index=2, client=client)
z = FPosBAxis(name="Z", index=3, client=client)

gantry = Gantry(
    axes={"X": x, "Y": y, "Z": z},
    concurrent_axes=None,
    _backend=FPosBAPIGantryBackend(client),
)
```

---

## 3. Homing

Always home the gantry before commanding absolute moves.
`Gantry.home()` sends a single `HOME` command to the PLC, which references all axes simultaneously:

```python title="homing.py" linenums="1"
from pathlib import Path
from applied_motion import Gantry

gantry = Gantry.from_config(Path("gantry-config.json"))

print("Homing all axes …")
gantry.home()
print("Homing complete.")
```

You can also check whether homing has already been completed via an individual axis proxy:

```python
from applied_motion.backends.fposbapi_axis import FPosBAxis

already_homed = x.is_homed()
if not already_homed:
    gantry.home()
```

---

## 4. Moving to an Absolute Position

Pass a `deque` of movement dicts to `Gantry.move_to`.
Each dict maps one axis name to its target `position` (mm) and `velocity` (mm/s):

```python title="absolute_move.py" linenums="1"
from pathlib import Path
from collections import deque
from applied_motion import Gantry

gantry = Gantry.from_config(Path("gantry-config.json"))
gantry.home()

# Move X → 150 mm, then Y → 75 mm, then Z → 20 mm (sequentially)
movements = deque([
    {"X": {"position": 150.0, "velocity": 80.0}},
    {"Y": {"position":  75.0, "velocity": 60.0}},
    {"Z": {"position":  20.0, "velocity": 40.0}},
])
gantry.move_to(movements)
```

---

## 5. Relative Move

Pass `position_type="relative"` to displace an axis from its current position:

```python title="relative_move.py" linenums="1"
from pathlib import Path
from collections import deque
from applied_motion import Gantry

gantry = Gantry.from_config(Path("gantry-config.json"))
gantry.home()

# Advance X by 10 mm from wherever it currently is
gantry.move_to(deque([
    {"X": {"position": 10.0, "velocity": 30.0, "position_type": "relative"}},
]))
```

---

## 6. Concurrent Axis Moves

Set `concurrent=True` on `move_to` to dispatch all movements in the queue simultaneously in parallel threads:

```python title="concurrent_move.py" linenums="1"
from pathlib import Path
from collections import deque
from applied_motion import Gantry

gantry = Gantry.from_config(Path("gantry-config.json"))
gantry.home()

# X and Y move at the same time
movements = deque([
    {"X": {"position": 200.0, "velocity": 100.0}},
    {"Y": {"position":  50.0, "velocity":  80.0}},
])
gantry.move_to(movements, concurrent=True)
```

> **Velocity note:** `SET_PAR 103` (global speed) is written immediately before each
> `MOVE_AXIS` command.  With `concurrent=True` these two commands are not atomic —
> interleaved `SET_PAR` writes may affect the speed of the other axis.  Use
> concurrent moves only when per-move velocity precision is not required.

If your config defines `concurrent_axes`, then with `concurrent=False` (default)
the gantry still batches those configured axes together.

---

## 7. Reading Current Positions

`Gantry.get_location()` queries every axis and returns a dict of positions in mm:

```python title="read_positions.py" linenums="1"
from pathlib import Path
from applied_motion import Gantry

gantry = Gantry.from_config(Path("gantry-config.json"))

location = gantry.get_location()
print(location)
# {'X': 150.0, 'Y': 75.0, 'Z': 20.0}

# Or query a single axis directly
x_pos = gantry.axes["X"].get_current_axis_position()
print(f"X axis: {x_pos} mm")
```

---

## 8. Sending a Raw FPosBAPI Command

For commands not yet wrapped by a higher-level method, call
`FPosBAPIClient.send_command` directly:

```python title="raw_command.py" linenums="1"
from applied_motion.backends.fposbapi_client import FPosBAPIClient

client = FPosBAPIClient(ip="192.168.10.25", port=1234)

# Enable the motion controller
client.send_command("ENABLE")

# Read a PLC parameter (parameter 103 = global speed)
lines = client.send_command("GET_PAR", 103)
print(lines)
# ['1, GET_PAR, 103, 80.0, 0, NULL, SUCCESS']

# Set a PLC parameter
client.send_command("SET_PAR", 103, 120.0)
```

---

## 9. Error Handling

### FPosBAPI protocol errors

`FPosBAPIClientError` is raised when the PLC returns a non-`SUCCESS` status or the connection is lost:

```python title="error_handling.py" linenums="1"
from pathlib import Path
from collections import deque
from applied_motion import Gantry
from applied_motion.backends.fposbapi_client import FPosBAPIClientError
from applied_motion.gantry import MovementError

gantry = Gantry.from_config(Path("gantry-config.json"))
gantry.home()

try:
    gantry.move_to(deque([
        {"X": {"position": 9999.0, "velocity": 80.0}},  # out of range
    ]))
except FPosBAPIClientError as exc:
    print(f"PLC rejected the command: {exc}")
except MovementError as exc:
    print(f"Gantry-level move failure: {exc}")
```

### Connection failure at startup

If the TCP connection cannot be established, `FPosBAPIClient.__init__` raises `OSError`.
`Gantry.from_config` propagates this after closing the socket cleanly:

```python
from pathlib import Path
from applied_motion import Gantry

try:
    gantry = Gantry.from_config(Path("gantry-config.json"))
except OSError as exc:
    print(f"Cannot connect to CECC-X: {exc}")
```

---

## 10. Enabling Logging

`festo-dev-applied-motion` uses Python's standard `logging` module throughout.
Enable `DEBUG` output to trace every frame exchanged with the PLC:

```python title="logging_setup.py" linenums="1"
import logging
from pathlib import Path
from applied_motion import Gantry

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

gantry = Gantry.from_config(Path("gantry-config.json"))
gantry.home()
```

Sample output:

```
2026-05-11 10:00:01,234 [INFO] applied_motion.backends.fposbapi_client: FPosBAPIClient connected to 192.168.10.25:1234
2026-05-11 10:00:01,235 [DEBUG] applied_motion.backends.fposbapi_client: FPosBAPIClient <- 1, ENABLE
2026-05-11 10:00:01,312 [INFO] applied_motion.gantry: Gantry.home: sending HOME via FPosBAPI client
2026-05-11 10:00:01,313 [DEBUG] applied_motion.backends.fposbapi_client: FPosBAPIClient <- 2, HOME
```

Use `logging.INFO` in production to keep only state-change events.

---

## 11. Read a Full Status Snapshot

Use `get_status()` when you want both axis and controller health in one call:

```python title="status_snapshot.py" linenums="1"
from pathlib import Path
from pprint import pprint

from applied_motion import Gantry

gantry = Gantry.from_config(Path("gantry-config.json"))
pprint(gantry.get_status())
```

---

## 12. Teach PLC Positions and Trays

When running against an FPosBAPI backend, you can call gantry-level teach helpers:

```python title="teach_examples.py" linenums="1"
from pathlib import Path

from applied_motion import Gantry

gantry = Gantry.from_config(Path("gantry-config.json"))
gantry.home()

if gantry.supports_teach():
    gantry.teach_pos(1)
    gantry.teach_tray(tray_id=1, tray_pos=1)
```

---

## 13. Discover Supported Controller Commands

Different PLC builds may expose different command sets:

```python title="list_commands.py" linenums="1"
from pathlib import Path

from applied_motion import Gantry

gantry = Gantry.from_config(Path("gantry-config.json"))
print(gantry.list_commands())
```
