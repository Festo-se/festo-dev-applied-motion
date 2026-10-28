# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT

"""Gantry-specific configuration validation."""

from typing import Any

from .common import require_mapping, require_string_list, unknown_items


def validate_gantry_component(component: dict[str, Any], name: str) -> dict[str, Any]:
    """Validate gantry component using applied-motion compatibility rules.

    Args:
        component: Selected gantry mapping.
        name: Component name used in errors.

    Returns:
        Validated component mapping.

    Raises:
        ValueError: If structure or backend-specific fields are invalid.
    """
    backend = component.get("backend", "modbus")
    if backend not in {"modbus", "fposbapi"}:
        raise ValueError(f'Unsupported backend: {backend!r}. Expected "modbus" or "fposbapi".')

    axes = component.get("axes")
    if not isinstance(axes, dict):
        raise ValueError(f"Config component {name!r} must contain an 'axes' mapping")

    axis_order = require_string_list(component.get("axis_order", list(axes)), "axis_order")
    unknown_order = unknown_items(axis_order, axes)
    if unknown_order:
        raise ValueError(f"Config axis_order references unknown axes: {unknown_order}")

    concurrent = component.get("concurrent_axes")
    if not concurrent:
        concurrent = None
    else:
        concurrent = require_string_list(concurrent, "concurrent_axes")
        unknown_concurrent = unknown_items(concurrent, axes)
        if unknown_concurrent:
            raise ValueError(f"Config concurrent_axes references unknown axes: {unknown_concurrent}")

    if backend == "modbus":
        _validate_modbus_axes(axes, axis_order)
    else:
        _validate_fposbapi(component, axes, axis_order)
    return component


def _validate_modbus_axes(axes: dict[str, Any], axis_order: list[str]) -> None:
    """Validate Modbus axis fields."""
    for axis_name in axis_order:
        axis = require_mapping(axes.get(axis_name), f"Axis config for {axis_name!r} must be a dict")
        if not isinstance(axis.get("name"), str):
            raise ValueError(f"Modbus axis {axis_name!r} must define string field 'name'")
        if not isinstance(axis.get("ip"), str):
            raise ValueError(f"Modbus axis {axis_name!r} must define string field 'ip'")


def _validate_fposbapi(component: dict[str, Any], axes: dict[str, Any], axis_order: list[str]) -> None:
    """Validate FPosBAPI interface and axis fields."""
    interface = component.get("interface")
    if not isinstance(interface, dict):
        raise ValueError("FPosBAPI config config must contain an 'interface' mapping")
    if not isinstance(interface.get("ip"), str):
        raise ValueError("FPosBAPI interface must define string field 'ip'")
    if "port" in interface and not isinstance(interface["port"], int):
        raise ValueError("FPosBAPI interface 'port' must be an int")
    timeout = interface.get("timeout")
    if "timeout" in interface and timeout is not None and not isinstance(timeout, (int, float)):
        raise ValueError("FPosBAPI interface 'timeout' must be numeric or null")
    for axis_name in axis_order:
        axis = require_mapping(axes.get(axis_name), f"Axis config for {axis_name!r} must be a dict")
        if not isinstance(axis.get("name"), str):
            raise ValueError(f"FPosBAPI axis {axis_name!r} must define string field 'name'")
        if not isinstance(axis.get("index"), int):
            raise ValueError(f"FPosBAPI axis {axis_name!r} must define int field 'index'")
