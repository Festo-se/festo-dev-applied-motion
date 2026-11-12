# festo-edcon / Modbus Examples

Copy-paste oriented examples for direct-drive setups using `EdconAxis` and `Gantry`.

---

## 1. Build from configuration

```python title="connect_config.py" linenums="1"
from pathlib import Path

from applied_motion import Gantry

with Gantry(config=Path("modbus-gantry-config.json"), name="gantry_1") as gantry:
    print(repr(gantry))
```

The constructor establishes `gantry.registry` internally. Config-based use does
not require importing a configurator or assembling registrations.

Config example:

```json title="modbus-gantry-config.json"
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
                "concurrent_axes": ["X", "Y"]
            }
        }
    }
}
```

---

## 2. Construct manually (without config)

```python title="connect_manual.py" linenums="1"
from applied_motion.backends.edcon_axis import EdconAxis
from applied_motion.applied_motion import Gantry

x = EdconAxis(name="X", ip="192.168.0.100")
y = EdconAxis(name="Y", ip="192.168.0.101")
z = EdconAxis(name="Z", ip="192.168.0.102")
axes = (x, y, z)

try:
    with Gantry(axes={"X": x, "Y": y, "Z": z}) as gantry:
        print(repr(gantry))
finally:
    for axis in axes:
        axis.close()
```

Manually supplied axes remain caller-owned. Close each axis with its own
`close()` method when finished; the gantry does not own those axis resources.

---

## 3. Home then move sequentially

```python title="sequential_move.py" linenums="1"
from collections import deque
from pathlib import Path

from applied_motion import Gantry

with Gantry(config=Path("modbus-gantry-config.json"), name="gantry_1") as gantry:
    gantry.home()

    movements = deque([
        {"X": {"position": 120.0, "velocity": 60.0}},
        {"Y": {"position":  80.0, "velocity": 50.0}},
        {"Z": {"position":  15.0, "velocity": 25.0}},
    ])

    gantry.move_to(movements)
```

---

## 4. Relative move

```python title="relative_move.py" linenums="1"
from collections import deque
from pathlib import Path

from applied_motion import Gantry

with Gantry(config=Path("modbus-gantry-config.json"), name="gantry_1") as gantry:
    gantry.home()

    gantry.move_to(deque([
        {"X": {"position": 10.0, "velocity": 30.0, "position_type": "relative"}},
    ]))
```

---

## 5. Run one full batch concurrently

```python title="concurrent_batch.py" linenums="1"
from collections import deque
from pathlib import Path

from applied_motion import Gantry

with Gantry(config=Path("modbus-gantry-config.json"), name="gantry_1") as gantry:
    gantry.home()

    movements = deque([
        {"X": {"position": 180.0, "velocity": 80.0}},
        {"Y": {"position":  40.0, "velocity": 80.0}},
    ])

    gantry.move_to(movements, concurrent=True)
```

---

## 6. Read status and position

```python title="status_and_location.py" linenums="1"
from pathlib import Path
from pprint import pprint

from applied_motion import Gantry

with Gantry(config=Path("modbus-gantry-config.json"), name="gantry_1") as gantry:
    print(gantry.get_location())
    pprint(gantry.get_status())
```

---

## 7. Error handling

```python title="error_handling.py" linenums="1"
from collections import deque
from pathlib import Path

from applied_motion import Gantry
from applied_motion.applied_motion import MovementError

with Gantry(config=Path("modbus-gantry-config.json"), name="gantry_1") as gantry:
    try:
        gantry.move_to(deque([
            {"X": {"position": 999999.0, "velocity": 5.0}},
        ]))
    except MovementError as exc:
        print(f"Movement failed: {exc}")
```
