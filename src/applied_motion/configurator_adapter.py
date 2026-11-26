# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT
"""Applied-motion registration adapter for the configurator kernel."""

from pathlib import Path
from typing import Any, Mapping

from configurator import (
    COMPAT,
    BuilderError,
    ComponentView,
    Configurator,
    Registration,
    Registry,
    ValidationError,
    ValidationMode,
)

from applied_motion.config import GantryConfig
from applied_motion.applied_motion import Gantry
from applied_motion.gantry_factory import (
    GantryConstruction,
    _build_fposbapi_from_gcfg,
    _build_modbus_from_gcfg,
)

_SUPPORTED_ROOT_SPEC_VERSION = "4.0"
_SUPPORTED_COMPONENT_SPEC_VERSION = "3.0"


def build_gantry_from_view(
    view: ComponentView,
    *,
    mode: ValidationMode = COMPAT,
    options: Mapping[str, Any] | None = None,
    resolver: Any = None,
    runtime: Any = None,
) -> GantryConstruction | Gantry:
    """Build a gantry construction from a validated configurator view.

    Args:
        view: Validated configurator view for one ``gantry`` component.
        mode: Configurator validation mode, accepted for builder protocol
            compatibility.
        options: Configurator build options, accepted for builder protocol
            compatibility.
        resolver: Configurator resolver, accepted for builder protocol
            compatibility.
        runtime: Shared runtime registry for transparent construction. When
            absent, the compatibility builder returns a construction bundle.

    Returns:
        Existing applied-motion construction bundle containing axes,
        concurrent-axis configuration, and backend ownership.

    Raises:
        ValueError: If the view is not a gantry component or its backend is
            unsupported by the existing applied-motion factory.
    """
    _ = mode, resolver
    if view.component_type != "gantry":
        raise ValueError(f"Expected a gantry component, got {view.component_type!r}")

    gantry_config = GantryConfig(_adapt_gantry_source(view, options), view.name)
    if gantry_config.backend == "modbus":
        construction = _build_modbus_from_gcfg(gantry_config)
    elif gantry_config.backend == "fposbapi":
        construction = _build_fposbapi_from_gcfg(gantry_config)
        if options is not None and options.get("run_referencing") is True:
            construction.backend.home(construction.axes)
    else:
        raise ValueError(f"Unsupported backend: {gantry_config.backend!r}")
    if runtime is None:
        return construction
    return Gantry._from_construction(construction, runtime)


def validate_gantry_view(
    view: ComponentView,
    *,
    options: Mapping[str, Any] | None = None,
    **_: Any,
) -> None:
    """Validate a selected gantry without opening a backend or axis.

    Args:
        view: Selected configurator component view.
        options: Optional application-level build options.

    Raises:
        ValueError: If the selected component has an unsupported version or
            does not satisfy the applied-motion configuration contract.
    """
    if view.component_type != "gantry":
        raise ValueError(f"Expected a gantry component, got {view.component_type!r}")
    root_version = view.root.get("spec_version")
    if root_version != _SUPPORTED_ROOT_SPEC_VERSION:
        raise ValueError(f"Unsupported spec_version: {root_version}. Expected {_SUPPORTED_ROOT_SPEC_VERSION}")
    component_version = view.data.get("component_spec_version")
    if component_version != _SUPPORTED_COMPONENT_SPEC_VERSION:
        raise ValueError(
            f"Unsupported component_spec_version: {component_version}. Expected {_SUPPORTED_COMPONENT_SPEC_VERSION}"
        )
    GantryConfig(_adapt_gantry_source(view, options), view.name)


def _adapt_gantry_source(view: ComponentView, options: Mapping[str, Any] | None) -> dict[str, Any]:
    """Copy a selected source and adapt legacy axis names without mutation."""
    root = dict(view.root)
    components = root.get("components")
    if not isinstance(components, Mapping):
        return root
    component = components.get(view.name)
    if not isinstance(component, Mapping):
        return root

    adapted_component = dict(component)
    axes = adapted_component.get("axes")
    if isinstance(axes, Mapping):
        adapted_axes = {}
        for axis_name, axis_config in axes.items():
            if isinstance(axis_config, Mapping):
                adapted_axis = dict(axis_config)
                adapted_axis.setdefault("name", axis_name)
                if options is not None and "run_referencing" in options:
                    adapted_axis["run_referencing"] = options["run_referencing"]
                adapted_axes[axis_name] = adapted_axis
            else:
                adapted_axes[axis_name] = axis_config
        adapted_component["axes"] = adapted_axes

    root["components"] = dict(components)
    root["components"][view.name] = adapted_component
    return root


def gantry_registration() -> Registration:
    """Return the explicit configurator registration for gantry components.

    Returns:
        Registration that binds the logical ``gantry`` component type to the
        applied-motion construction adapter.
    """
    return Registration(
        name="gantry",
        component_type="gantry",
        schema_versions=frozenset({_SUPPORTED_COMPONENT_SPEC_VERSION}),
        builder=build_gantry_from_view,
        validator=validate_gantry_view,
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
    try:
        result = Configurator.bootstrap(
            source,
            name,
            Registry.compose(gantry_registration()),
            options={"legacy_component_types": {name: "gantry"}},
        )
    except (BuilderError, ValidationError) as exc:
        if isinstance(exc.cause, ValueError):
            raise exc.cause  # noqa: B904
        raise
    return result.instance


def close_gantry_construction(instance: Any) -> None:
    """Close a builder-created gantry construction.

    Args:
        instance: Construction returned by the gantry builder.

    Raises:
        TypeError: If the managed instance is not a gantry construction.
    """
    if isinstance(instance, GantryConstruction):
        instance.close()
        return
    if isinstance(instance, Gantry):
        instance._close_backend()
        return
    raise TypeError(f"Expected GantryConstruction or Gantry, got {type(instance).__name__}")
