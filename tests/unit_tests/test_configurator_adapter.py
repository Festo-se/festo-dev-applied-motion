# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG
# SPDX-License-Identifier: MIT
"""Unit tests for the applied-motion configurator registration adapter."""

from typing import Any
from unittest.mock import MagicMock

import pytest
from configurator import ComponentView, Configurator, Registry

from applied_motion.configurator_adapter import (
    build_gantry_from_config,
    build_gantry_from_view,
    close_gantry_construction,
    gantry_registration,
    validate_gantry_view,
)
from applied_motion.gantry_factory import GantryConstruction


def _view(
    component_type: str = "gantry",
    root_version: str | None = "4.0",
    component_version: str | None = "3.0",
) -> ComponentView:
    component = {
        "component_type": component_type,
        "backend": "modbus",
        "axes": {"X": {"name": "X", "ip": "192.168.0.193"}},
        "axis_order": ["X"],
        "concurrent_axes": None,
    }
    if component_version is not None:
        component["component_spec_version"] = component_version
    root: dict[str, Any] = {"components": {"gantry_1": component}}
    if root_version is not None:
        root["spec_version"] = root_version
    return ComponentView("gantry_1", component_type, component, root, "components.gantry_1", root["components"])


def test_build_gantry_from_view_reuses_modbus_factory(mocker):
    construction = MagicMock(spec=GantryConstruction)
    factory = mocker.patch("applied_motion.configurator_adapter._build_modbus_from_gcfg", return_value=construction)

    result = build_gantry_from_view(_view())

    assert result is construction
    factory.assert_called_once()
    assert factory.call_args.args[0].name == "gantry_1"


def test_legacy_axis_mapping_gets_identity_and_runtime_referencing_override(mocker):
    component = {
        "component_type": "gantry",
        "component_spec_version": "3.0",
        "backend": "modbus",
        "axes": {"ZP": {"ip": "192.168.0.103"}},
    }
    root = {"spec_version": "4.0", "components": {"gantry_1": component}}
    view = ComponentView("gantry_1", "gantry", component, root, "components.gantry_1", root["components"])
    factory = mocker.patch(
        "applied_motion.configurator_adapter._build_modbus_from_gcfg", return_value=MagicMock(spec=GantryConstruction)
    )

    build_gantry_from_view(view, options={"run_referencing": False})

    config = factory.call_args.args[0]
    assert config.axes_cfg["ZP"]["name"] == "ZP"
    assert config.axes_cfg["ZP"]["run_referencing"] is False
    assert "name" not in component["axes"]["ZP"]


def test_build_gantry_from_config_bootstraps_legacy_component(mocker):
    construction = MagicMock(spec=GantryConstruction)
    mocker.patch("applied_motion.configurator_adapter._build_modbus_from_gcfg", return_value=construction)
    source = {
        "spec_version": "4.0",
        "components": {
            "gantry_1": {
                "component_spec_version": "3.0",
                "backend": "modbus",
                "axes": {"X": {"name": "X", "ip": "192.168.0.193"}},
                "axis_order": ["X"],
                "concurrent_axes": None,
            }
        },
    }

    result = build_gantry_from_config(source)

    assert result is construction


@pytest.mark.parametrize("backend", ["can_bus", "opc_ua"])
def test_legacy_config_delegate_preserves_unsupported_backend_value_error(backend):
    source = {
        "spec_version": "4.0",
        "components": {
            "gantry_1": {
                "component_spec_version": "3.0",
                "backend": backend,
                "axes": {"X": {"name": "X", "ip": "192.168.0.100"}},
            }
        }
    }

    with pytest.raises(ValueError, match=backend):
        build_gantry_from_config(source)


def test_build_gantry_from_view_rejects_non_gantry_component():
    with pytest.raises(ValueError, match="Expected a gantry component"):
        build_gantry_from_view(_view("fluid"))


def test_gantry_registration_binds_gantry_builder():
    registration = gantry_registration()

    assert registration.name == "gantry"
    assert registration.component_type == "gantry"
    assert registration.builder is build_gantry_from_view
    assert registration.cleanup is close_gantry_construction
    assert registration.validator is validate_gantry_view
    assert registration.schema_versions == frozenset({"3.0"})


def test_gantry_registration_validates_without_opening_backend():
    assert validate_gantry_view(_view()) is None


@pytest.mark.parametrize("root_version", [None, "3.0", "5.0"])
def test_gantry_validator_rejects_unsupported_root_version(root_version):
    with pytest.raises(ValueError, match="Unsupported spec_version"):
        validate_gantry_view(_view(root_version=root_version))


@pytest.mark.parametrize("component_version", [None, "2.0"])
def test_gantry_validator_rejects_missing_or_unsupported_component_version(component_version):
    with pytest.raises(ValueError, match="Unsupported component_spec_version"):
        validate_gantry_view(_view(component_version=component_version))


def test_gantry_construction_closes_backend_once():
    backend = MagicMock()
    construction = GantryConstruction(axes={}, concurrent_axes=None, backend=backend)

    construction.close()
    construction.close()

    backend.close.assert_called_once_with()


def test_gantry_cleanup_rejects_non_construction_instance():
    with pytest.raises(TypeError, match="Expected GantryConstruction"):
        close_gantry_construction(object())


def test_managed_configurator_result_closes_gantry_ownership_once(mocker):
    backend = MagicMock()
    construction = GantryConstruction(axes={}, concurrent_axes=None, backend=backend)
    mocker.patch("applied_motion.configurator_adapter._build_modbus_from_gcfg", return_value=construction)

    result = Configurator.bootstrap(
        _view().root,
        "gantry_1",
        Registry.compose(gantry_registration()),
    )

    result.close()
    result.close()

    assert result.closed is True
    backend.close.assert_called_once_with()
