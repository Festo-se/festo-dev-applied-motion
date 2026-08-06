# Festo Applied Motion

`festo-dev-applied-motion` is a Python library for controlling Festo electric motion systems with a consistent gantry API.

It supports two production backends:

| Backend | Best for | Transport |
|---|---|---|
| **Modbus / festo-edcon** (`EdconAxis`) | Direct per-drive control (CMMT/CMMT-ST) | Modbus TCP per axis |
| **FPosBAPI** (`FPosBAxis`) | CECC-X PLC controlled multi-axis gantries | Single shared TCP socket |

## Install

```bash
uv add festo-dev-applied-motion
```

Or from source:

```bash
git clone https://github.com/Festo-se/festo-dev-applied-motion.git
cd festo-dev-applied-motion
uv pip install -e .
```

## Quick links

- [Getting Started](getting-started/index.md)
- [FPosBAPI User Guide](user-guide/fposbapi.md)
- [festo-edcon User Guide](user-guide/edcon.md)
- [FPosBAPI Examples](examples/fposbapi.md)
- [festo-edcon Examples](examples/edcon.md)
- API reference pages (auto-generated during docs build; available in left navigation)

## Notes

- Position units are **millimetres (mm)** throughout the public API.
- `Gantry.from_config(...)` is convenience wrapper over `Gantry(config=...)`.

## Festo resources

- [Repository](https://github.com/Festo-se/festo-dev-applied-motion)
- [Issues Tracker](https://github.com/Festo-se/festo-dev-applied-motion/issues)
