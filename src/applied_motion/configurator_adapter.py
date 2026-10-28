# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT
"""Applied-motion registration adapter for the configurator kernel."""

from typing import Any, Mapping, cast
from pathlib import Path

from configurator import (
    COMPAT,
    BuilderError,
    ComponentView,
    Configurator,
    Registration,
    Registry,
    ValidationMode,
    load_source,
    normalize_root,
)

from applied_motion.config import GantryConfig
from applied_motion.gantry_factory import (
    GantryConstruction,
    _build_fposbapi_from_gcfg,
    _build_modbus_from_gcfg,
)


def build_gantry_from_view(
    view: ComponentView,
    *,
    mode: ValidationMode = COMPAT,
    options: Mapping[str, Any] | None = None,
    resolver: Any = None,
) -> GantryConstruction:
    """Build a gantry construction from a validated configurator view.

    Args:
        view: Validated configurator view for one ``gantry`` component.
        mode: Configurator validation mode, accepted for builder protocol
            compatibility.
        options: Configurator build options, accepted for builder protocol
            compatibility.
        resolver: Configurator resolver, accepted for builder protocol
            compatibility.

    Returns:
        Existing applied-motion construction bundle containing axes,
        concurrent-axis configuration, and backend ownership.

    Raises:
        ValueError: If the view is not a gantry component or its backend is
            unsupported by the existing applied-motion factory.
    """
    del mode, options, resolver
    if view.component_type != "gantry":
        raise ValueError(f"Expected a gantry component, got {view.component_type!r}")

    gantry_config = GantryConfig(cast(dict, view.root), view.name)
    if gantry_config.backend == "modbus":
        return _build_modbus_from_gcfg(gantry_config)
    if gantry_config.backend == "fposbapi":
        return _build_fposbapi_from_gcfg(gantry_config)
    raise ValueError(f"Unsupported backend: {gantry_config.backend!r}")


def gantry_registration() -> Registration:
    """Return the explicit configurator registration for gantry components.

    Returns:
        Registration that binds the logical ``gantry`` component type to the
        applied-motion construction adapter.
    """
    return Registration(
        name="gantry",
        component_type="gantry",
        builder=build_gantry_from_view,
        cleanup=close_gantry_construction,
    )


def build_gantry_from_config(source: Mapping[str, Any] | str | Path, name: str = "gantry_1") -> GantryConstruction:
    """Build a gantry through the canonical configurator bootstrap.

    Args:
        source: Configuration mapping, JSON string, or JSON file path.
        name: Named gantry component to select.

    Returns:
        Gantry construction bundle produced by the registered builder.

    Raises:
        ValueError: If a legacy selected component has an unsupported backend.
        configurator.ConfiguratorError: If loading, selection, validation, or
            construction fails.
    """
    normalized = _prepare_legacy_gantry_source(source, name)
    try:
        result = Configurator.bootstrap(
            normalized,
            name,
            Registry.compose(gantry_registration()),
        )
    except BuilderError as exc:
        if isinstance(exc.cause, ValueError):
            raise exc.cause  # noqa: B904
        raise
    return result.instance


def _prepare_legacy_gantry_source(source: Mapping[str, Any] | str | Path, name: str) -> dict[str, Any]:
    """Add the canonical type to a selected legacy gantry component.

    Args:
        source: Configuration mapping, JSON string, or JSON file path.
        name: Legacy component name whose type may be absent.

    Returns:
        Canonically normalized configuration mapping.
    """
    raw, _ = load_source(source)
    normalized = normalize_root(raw)
    components = normalized.get("components")
    if not isinstance(components, Mapping):
        return normalized

    component = components.get(name)
    if not isinstance(component, Mapping):
        return normalized
    if any(
        key in component for key in ("component_type", "component_class", "type")
    ):  # TODO: "type" is probably not right here
        return normalized

    normalized = dict(normalized)
    normalized["components"] = dict(components)
    normalized["components"][name] = dict(component)
    normalized["components"][name]["component_type"] = "gantry"
    return normalized


def close_gantry_construction(instance: Any) -> None:
    """Close a builder-created gantry construction.

    Args:
        instance: Construction returned by the gantry builder.

    Raises:
        TypeError: If the managed instance is not a gantry construction.
    """
    if not isinstance(instance, GantryConstruction):
        raise TypeError(f"Expected GantryConstruction, got {type(instance).__name__}")
    instance.close()
