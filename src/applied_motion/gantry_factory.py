# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Factory helpers for building gantry axes and backend state from config."""

from dataclasses import dataclass
from pathlib import Path

from applied_motion.backends.axis_protocol import Axis
from applied_motion.backends.edcon_axis import EdconAxis
from applied_motion.backends.fposbapi_axis import FPosBAxis
from applied_motion.backends.fposbapi_client import FPosBAPIClient
from applied_motion.backends.gantry_backend import FPosBAPIGantryBackend, GantryBackend, ModbusGantryBackend
from applied_motion.config import GantryConfig, SystemConfig


AxisMap = dict[str, Axis]


def _coerce_config_source(config: dict | str | Path) -> dict | Path:
    """Normalize config source to dict or Path."""
    if isinstance(config, str):
        return Path(config)
    return config


@dataclass(frozen=True)
class GantryConstruction:
    """Fully built gantry components returned by config factories.

    Args:
        axes: Mapping of axis names to constructed axis objects.
        concurrent_axes: Optional concurrent movement subset.
        backend: Backend strategy that owns shared gantry behavior.
    """

    axes: AxisMap
    concurrent_axes: AxisMap | None
    backend: GantryBackend


def build_modbus_gantry(config: dict | str | Path, name: str = "gantry_1") -> GantryConstruction:
    """Build Modbus gantry axes and backend from config.

    Args:
        config: Parsed config mapping or JSON file path.
        name: Component name to load.

    Returns:
        Gantry construction bundle for Modbus backend.
    """
    gcfg = GantryConfig(SystemConfig(_coerce_config_source(config))(), name)
    return _build_modbus_from_gcfg(gcfg)


def _build_modbus_from_gcfg(gcfg: GantryConfig) -> GantryConstruction:
    axes: AxisMap = {
        axis_name: EdconAxis(
            name=gcfg.axes_cfg[axis_name]["name"],
            ip=gcfg.axes_cfg[axis_name]["ip"],
            run_referencing=gcfg.axes_cfg[axis_name].get("run_referencing", False),
        )
        for axis_name in gcfg.axis_order
    }
    concurrent_axes: AxisMap | None = (
        {axis_name: axes[axis_name] for axis_name in gcfg.concurrent_raw if axis_name in axes}
        if gcfg.concurrent_raw
        else None
    )
    return GantryConstruction(axes=axes, concurrent_axes=concurrent_axes, backend=ModbusGantryBackend())


def build_fposbapi_gantry(config: dict | str | Path, name: str = "gantry_1") -> GantryConstruction:
    """Build FPosBAPI gantry axes and backend from config.

    Args:
        config: Parsed config mapping or JSON file path.
        name: Component name to load.

    Returns:
        Gantry construction bundle for FPosBAPI backend.
    """
    gcfg = GantryConfig(SystemConfig(_coerce_config_source(config))(), name)
    return _build_fposbapi_from_gcfg(gcfg)


def _build_fposbapi_from_gcfg(gcfg: GantryConfig) -> GantryConstruction:
    conn = gcfg.interface
    if not isinstance(conn, dict):
        raise ValueError("FPosBAPI config must contain an 'interface' mapping")

    client_kwargs = {"timeout": conn["timeout"]} if "timeout" in conn else {}
    client = FPosBAPIClient(ip=conn["ip"], port=conn.get("port", 1234), **client_kwargs)
    backend_handler = FPosBAPIGantryBackend(client)
    try:
        client.send_command("ENABLE")
        axes: AxisMap = {
            axis_name: FPosBAxis(
                name=gcfg.axes_cfg[axis_name]["name"],
                index=gcfg.axes_cfg[axis_name]["index"],
                client=client,
            )
            for axis_name in gcfg.axis_order
        }
        concurrent_axes: AxisMap | None = (
            {axis_name: axes[axis_name] for axis_name in gcfg.concurrent_raw if axis_name in axes}
            if gcfg.concurrent_raw
            else None
        )
        return GantryConstruction(axes=axes, concurrent_axes=concurrent_axes, backend=backend_handler)
    except Exception:
        backend_handler.close()
        raise


def build_gantry_from_config(config: dict | str | Path, name: str = "gantry_1") -> GantryConstruction:
    """Build gantry components for configured backend.

    Args:
        config: Parsed config mapping or JSON file path.
        name: Component name to load.

    Returns:
        Gantry construction bundle.
    """
    gcfg = GantryConfig(SystemConfig(_coerce_config_source(config))(), name)
    if gcfg.backend == "modbus":
        return _build_modbus_from_gcfg(gcfg)
    return _build_fposbapi_from_gcfg(gcfg)
