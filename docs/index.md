# Festo Gantry

`festo-dev-applied-motion` is a Python library providing quality-of-life controls for Festo electrically-driven, motion components.

## Installation

### From Codebase

Navigate to the directory where the code is stored and, using uv, type in the following command:

```
uv pip install -e .
```

This will package the library locally and can be used as regular imports.

### Official Packaged Releases

The latest released version of this package can be found on the package registry of this project.
Install using uv:

```
uv add festo-dev-applied-motion
```

### From Git Repository

```
uv pip install git+https://github.com/Festo-se/festo-dev-applied-motion.git
```

Or as an editable dependency with a local copy of the source code:

1. Clone the repository

```
git clone https://github.com/Festo-se/festo-dev-applied-motion.git <destination-directory>
```

2. Navigate to the clone destination directory

```
cd <destination>
```

3. Install with uv

```
uv pip install -e .
```

## Dependencies

`festo-dev-applied-motion` depends on `festo-edcon` for communicating with Festo servo drives over EtherNet/IP or PROFINET.

## Festo Resources

- [Festo CMMT Product Page](https://www.festo.com/us/en/p/servo-drive-for-synchronous-motors-id_CMMT-ST/)
- [Issues Tracker](https://github.com/Festo-se/festo-dev-fluid-motion/issues)
