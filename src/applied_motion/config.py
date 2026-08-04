# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG

"""
Configuration class for loading and validating the configuration for config.

Prototype to be move into its own module eventually.
"""

from typing import cast

import logging
from pathlib import Path
import json


logger = logging.getLogger(__name__)


class SystemConfig(dict):
    """
    Configuration class for Festo modules and subsystems.

    Used to dynamically instantiate a subsystem running using the Festo Python framework
    and specify the topology and operating parameters of said system.

    """

    def __init__(self, config: dict | Path) -> None:
        """Load and normalise *config* from a dict or JSON file path."""
        if isinstance(config, SystemConfig):
            self.config = config
        else:
            config = self._load_config_source(config)
            logger.debug("SystemConfig: loaded config source=%s", type(config).__name__)
            parsed_config = self._normalize_config(config)
            self.config = parsed_config

    def __call__(self) -> dict:
        """Return the normalised component configuration dict."""
        return self.config

    @staticmethod
    def _load_config_source(config: dict | Path) -> dict:
        """Return a raw config dict loaded from *config*.

        Args:
            config: Parsed configuration dict or path to a JSON file.

        Returns:
            Raw configuration mapping.

        Raises:
            ValueError: If the loaded configuration is not a dict.
        """
        if isinstance(config, Path):
            with config.open() as fh:
                config = json.load(fh)
        if not isinstance(config, dict):
            raise ValueError("Config config must load as a dict")
        return config

    @staticmethod
    def _normalize_config(raw_config: dict) -> dict:
        """Return the normalized component config mapping.

        Args:
            raw_config: Raw configuration dict, possibly wrapped in a
                top-level ``component_config`` key.

        Returns:
            Normalized component configuration dict.

        Raises:
            ValueError: If the normalized configuration is not a dict.
        """
        parsed_config = raw_config.get("component_config", raw_config)
        if not isinstance(parsed_config, dict):
            raise ValueError("Normalized config config must be a dict")
        return parsed_config


class GantryConfig:
    """Validated, structured configuration for a single named gantry component."""

    name: str
    backend: str
    axes_cfg: dict
    axis_order: list[str]
    concurrent_raw: list[str] | None
    interface: dict | None  # populated only for fposbapi backend

    def __init__(self, sys_config: dict, name: str) -> None:
        """Validate and store gantry config fields from *sys_config* for component *name*."""
        self.name = name
        gantry_cfg, backend, axes_cfg, axis_order, concurrent_raw = self._validate_config(sys_config, name)
        self.backend = backend
        self.axes_cfg = axes_cfg
        self.axis_order = axis_order
        self.concurrent_raw = concurrent_raw
        self.interface = gantry_cfg.get("interface")

    @staticmethod
    def _validate_axis_name_list(value: object, field_name: str) -> list[str]:
        """Return *value* as a validated list of axis-name strings.

        Args:
            value: Value to validate.
            field_name: Config field name used in error messages.

        Returns:
            Validated list of axis-name strings.

        Raises:
            ValueError: If *value* is not a list of strings.
        """
        if not isinstance(value, list) or not all(isinstance(axis_name, str) for axis_name in value):
            raise ValueError(f"Config {field_name} must be a list of axis-name strings")
        return cast(list[str], value)

    @staticmethod
    def _validate_modbus_axes(axes_cfg: dict, axis_order: list[str]) -> None:
        """Validate backend-specific Modbus axis config fields.

        Args:
            axes_cfg: Axis config mapping.
            axis_order: Ordered list of configured axis names.

        Raises:
            ValueError: If any axis config is missing required fields.
        """
        for axis_name in axis_order:
            axis_cfg = axes_cfg.get(axis_name)
            if not isinstance(axis_cfg, dict):
                raise ValueError(f"Axis config for {axis_name!r} must be a dict")
            if not isinstance(axis_cfg.get("name"), str):
                raise ValueError(f"Modbus axis {axis_name!r} must define string field 'name'")
            if not isinstance(axis_cfg.get("ip"), str):
                raise ValueError(f"Modbus axis {axis_name!r} must define string field 'ip'")

    @staticmethod
    def _validate_fposbapi_config(config_cfg: dict, axes_cfg: dict, axis_order: list[str]) -> None:
        """Validate backend-specific FPosBAPI interface and axis fields.

        Args:
            config_cfg: Config component config.
            axes_cfg: Axis config mapping.
            axis_order: Ordered list of configured axis names.

        Raises:
            ValueError: If required interface or axis fields are missing or invalid.
        """
        interface = config_cfg.get("interface")
        if not isinstance(interface, dict):
            raise ValueError("FPosBAPI config config must contain an 'interface' mapping")
        if not isinstance(interface.get("ip"), str):
            raise ValueError("FPosBAPI interface must define string field 'ip'")
        if "port" in interface and not isinstance(interface["port"], int):
            raise ValueError("FPosBAPI interface 'port' must be an int")
        if (
            "timeout" in interface
            and interface["timeout"] is not None
            and not isinstance(interface["timeout"], (int, float))
        ):
            raise ValueError("FPosBAPI interface 'timeout' must be numeric or null")

        for axis_name in axis_order:
            axis_cfg = axes_cfg.get(axis_name)
            if not isinstance(axis_cfg, dict):
                raise ValueError(f"Axis config for {axis_name!r} must be a dict")
            if not isinstance(axis_cfg.get("name"), str):
                raise ValueError(f"FPosBAPI axis {axis_name!r} must define string field 'name'")
            if not isinstance(axis_cfg.get("index"), int):
                raise ValueError(f"FPosBAPI axis {axis_name!r} must define int field 'index'")

    @staticmethod
    def _validate_config(parsed_config: dict, name: str) -> tuple[dict, str, dict, list[str], list[str] | None]:
        """Validate and extract one config component config.

        Args:
            parsed_config: Normalized component configuration mapping.
            name: Component name to extract.

        Returns:
            Tuple of ``(config_cfg, backend, axes_cfg, axis_order, concurrent_axes)``.

        Raises:
            ValueError: If required config structure or backend-specific fields
                are missing or invalid.
        """
        components = parsed_config.get("components")
        if not isinstance(components, dict):
            raise ValueError("Config config must contain a 'components' mapping")
        if name not in components:
            raise ValueError(f"Config config does not contain component {name!r}")

        config_cfg = components[name]
        if not isinstance(config_cfg, dict):
            raise ValueError(f"Config component {name!r} must be a dict")

        backend = config_cfg.get("backend", "modbus")
        if backend not in {"modbus", "fposbapi"}:
            raise ValueError(f'Unsupported backend: {backend!r}. Expected "modbus" or "fposbapi".')

        axes_cfg = config_cfg.get("axes")
        if not isinstance(axes_cfg, dict):
            raise ValueError(f"Config component {name!r} must contain an 'axes' mapping")

        axis_order = config_cfg.get("axis_order", list(axes_cfg.keys()))
        axis_order = GantryConfig._validate_axis_name_list(axis_order, "axis_order")
        unknown_axis_order = [axis_name for axis_name in axis_order if axis_name not in axes_cfg]
        if unknown_axis_order:
            raise ValueError(f"Config axis_order references unknown axes: {unknown_axis_order}")

        concurrent_raw = config_cfg.get("concurrent_axes")
        if not concurrent_raw:
            concurrent_axes = None
        else:
            concurrent_axes = GantryConfig._validate_axis_name_list(concurrent_raw, "concurrent_axes")
            unknown_concurrent = [axis_name for axis_name in concurrent_raw if axis_name not in axes_cfg]
            if unknown_concurrent:
                raise ValueError(f"Config concurrent_axes references unknown axes: {unknown_concurrent}")

        if backend == "modbus":
            GantryConfig._validate_modbus_axes(axes_cfg, axis_order)
        else:
            GantryConfig._validate_fposbapi_config(config_cfg, axes_cfg, axis_order)

        return config_cfg, backend, axes_cfg, axis_order, concurrent_axes
