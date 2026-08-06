# festo-edcon / Modbus Examples

Copy-paste oriented examples for direct-drive setups using `EdconAxis` and `Gantry`.

---

## 1. Connect via configuration file

```python title="connect_from_config.py" linenums="1"
from pathlib import Path

from applied_motion import Gantry

gantry = Gantry.from_config(Path("modbus-gantry-config.json"))
print(repr(gantry))
```

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
                "axis_order": ["X", "Y", "Z"],
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
from applied_motion.gantry import Gantry

x = EdconAxis(name="X", ip="192.168.0.100")
y = EdconAxis(name="Y", ip="192.168.0.101")
z = EdconAxis(name="Z", ip="192.168.0.102")

gantry = Gantry(axes={"X": x, "Y": y, "Z": z})
```

---

## 3. Home then move sequentially

```python title="sequential_move.py" linenums="1"
from collections import deque
from pathlib import Path

from applied_motion import Gantry

gantry = Gantry.from_config(Path("modbus-gantry-config.json"))
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

gantry = Gantry.from_config(Path("modbus-gantry-config.json"))
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

gantry = Gantry.from_config(Path("modbus-gantry-config.json"))
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

gantry = Gantry.from_config(Path("modbus-gantry-config.json"))

print(gantry.get_location())
pprint(gantry.get_status())
```

---

## 7. Error handling

```python title="error_handling.py" linenums="1"
from collections import deque
from pathlib import Path

from applied_motion import Gantry
from applied_motion.gantry import MovementError

gantry = Gantry.from_config(Path("modbus-gantry-config.json"))

try:
    gantry.move_to(deque([
        {"X": {"position": 999999.0, "velocity": 5.0}},
    ]))
except MovementError as exc:
    print(f"Movement failed: {exc}")
```
