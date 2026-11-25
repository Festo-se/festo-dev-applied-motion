"""Unit tests for Gantry.from_config.

Coverage areas
--------------
* ``from_config`` with a modbus backend dict — creates EdconAxis instances
  with the correct name, ip, and honours axis_order.
* ``from_config`` with a Path to a JSON file — loads the file and constructs
  correctly.
* ``from_config`` with no ``backend`` key — defaults to ``"modbus"`` for
  backward compatibility with spec version 1.0 configs.
* ``from_config`` with a fposbapi backend dict — creates FPosBAPIClient with
  the correct ip/port, creates FPosBAxis instances with correct name and
  index, stores the client on the gantry.
* ``from_config`` with concurrent_axes specified — populates
  ``gantry.concurrent_axes``.
* ``from_config`` with an unsupported backend — raises ``ValueError``.
* ``Gantry.home`` for FPosBAPI backend — sends single HOME command via
  client rather than calling axis.home() on each proxy.
* ``Gantry.home`` for Modbus backend — delegates to each axis.

No hardware or network connection required.  EdconAxis and FPosBAPIClient
constructors are patched so no TCP or Modbus connections are attempted.
"""

import json
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from applied_motion.applied_motion import Gantry
from applied_motion.gantry_factory import GantryConstruction
from applied_motion.backends.edcon_axis import EdconAxis
from applied_motion.backends.fposbapi_axis import FPosBAxis
from applied_motion.backends.fposbapi_client import FPosBAPIClient
from applied_motion.backends.gantry_backend import ModbusGantryBackend
from applied_motion.backends.linear_axis import LinearAxis
from applied_motion.backends.rotary_axis import RotaryAxis
from applied_motion.config import GantryConfig, SystemConfig


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

_MODBUS_CONFIG = {
    "spec_version": "4.0",
    "system_config": {"metadata": {}},
    "component_config": {
        "metadata": {},
        "components": {
            "gantry_1": {
                "component_spec_version": "3.0",
                "backend": "modbus",
                "axes": {
                    "X": {"name": "X", "ip": "192.168.0.193"},
                    "Y": {"name": "Y", "ip": "192.168.0.32"},
                },
                "axis_order": ["X", "Y"],
                "concurrent_axes": None,
            }
        },
    },
}

_FPOSBAPI_CONFIG = {
    
    "_comment": "Festo component control config. General config covering fluid control and motion.",
    "spec_version": "4.0",
    "system_config": {
        "metadata": {}
    },
    "component_config": {
        "metadata": {},
        "components": 
            {"gantry_1": {
                "_comment": "FPosBAPI backend test fixture for Gantry. Targets a CECC-X PLC running the FPosBAPI CoDeSys server.",
                "component_spec_version": "3.0",
                "control_mode": "python",
                "motion_control": {
                    "_comment": "TODO: unify control scheme under this config spec",
                    "control_package": "festo-applied-motion",
                    "control_library": "applied_motion",
                    "control_system": "gantry",
                    "control_bus": "backends",
                    "control_handler": "fposbapi_client",
                    "control_modules": {
                        "com_modbus": "FPosBAPIClient",
                        "motion_handler": "Gantry"
                    }
                },
                "backend": "fposbapi",
                "interface": {
                    "type": "tcp/ip",
                    "ip": "192.168.10.25",
                    "port": 1234
                },
                "axes": {
                    "X": {
                        "name": "X",
                        "index": 1
                    },
                    "Y": {
                        "name": "Y",
                        "index": 2
                    },
                    "Z": {
                        "name": "Z",
                        "index": 3
                    }
                },
                "axis_order": [
                    "X",
                    "Y",
                    "Z"
                ],
                "concurrent_axes": ""
            },}
        }
    
}


def _base_modbus_component() -> dict:
    return {
        "backend": "modbus",
        "axes": {"X": {"name": "X", "ip": "192.168.0.193"}},
        "axis_order": ["X"],
        "concurrent_axes": None,
    }


@pytest.fixture()
def patched_festo_axis(mocker):
    """Patch LinearAxis so its constructor does not open a Modbus connection."""
    mock_cls = mocker.patch("applied_motion.gantry_factory.LinearAxis", autospec=True)
    mock_cls.side_effect = lambda name, ip, run_referencing=False, min_position=None, max_position=None: MagicMock(
        spec=EdconAxis, name=name, ip=ip
    )
    return mock_cls


@pytest.fixture()
def patched_fposbapi_client(mocker):
    """Patch FPosBAPIClient so from_config does not open a TCP socket."""
    mock_cls = mocker.patch("applied_motion.gantry_factory.FPosBAPIClient", autospec=True)
    mock_instance = MagicMock(spec=FPosBAPIClient)
    mock_instance.ip = "192.168.10.25"
    mock_instance.port = 1234
    mock_instance.send_command.return_value = ["1, HOME, 0, NULL, SUCCESS"]
    mock_cls.return_value = mock_instance
    return mock_cls, mock_instance


# ---------------------------------------------------------------------------
# Modbus backend
# ---------------------------------------------------------------------------


class TestFromConfigModbus:
    def test_constructor_accepts_modbus_config_directly(self, patched_festo_axis):
        g = Gantry(config=_MODBUS_CONFIG)
        assert list(g.axes.keys()) == ["X", "Y"]
        assert g.supports_teach() is False

    def test_modbus_factory_homes_axes_during_construction(self, mocker):
        mock_cls = mocker.patch("applied_motion.gantry_factory.LinearAxis", autospec=True)
        axis_x = MagicMock(spec=EdconAxis)
        axis_y = MagicMock(spec=EdconAxis)
        mock_cls.side_effect = [axis_x, axis_y]

        gcfg = GantryConfig(SystemConfig(_MODBUS_CONFIG)(), "gantry_1")
        from applied_motion.gantry_factory import _build_modbus_from_gcfg

        _build_modbus_from_gcfg(gcfg)

        # Verify LinearAxis instantiated with run_referencing=True for each axis
        assert mock_cls.call_count == 2
        for call in mock_cls.call_args_list:
            assert call.kwargs.get("run_referencing") is True

    def test_creates_festo_axis_for_each_entry(self, patched_festo_axis):
        g = Gantry.from_config(_MODBUS_CONFIG)
        assert "X" in g.axes
        assert "Y" in g.axes

    def test_axis_count_matches_config(self, patched_festo_axis):
        g = Gantry.from_config(_MODBUS_CONFIG)
        assert len(g.axes) == 2

    def test_festo_axis_called_with_correct_ip(self, patched_festo_axis):
        Gantry.from_config(_MODBUS_CONFIG)
        ips = {call_args.kwargs.get("ip") or call_args.args[1] for call_args in patched_festo_axis.call_args_list}
        assert "192.168.0.193" in ips
        assert "192.168.0.32" in ips

    def test_partial_axis_order_never_limits_modbus_axis_inventory(self, patched_festo_axis):
        """Construct all Modbus axes even when the preferred order is partial."""
        import copy

        from applied_motion.gantry_factory import _build_modbus_from_gcfg

        config = copy.deepcopy(_MODBUS_CONFIG)
        config["component_config"]["components"]["gantry_1"]["axis_order"] = ["Y"]
        gcfg = GantryConfig(SystemConfig(config)(), "gantry_1")
        construction = _build_modbus_from_gcfg(gcfg)
        assert set(construction.axes) == {"X", "Y"}

    def test_partial_axis_order_only_changes_modbus_homing_order(self, patched_festo_axis):
        """Use axis_order as a homing preference without dropping other axes."""
        import copy

        from applied_motion.gantry_factory import _build_modbus_from_gcfg

        config = copy.deepcopy(_MODBUS_CONFIG)
        config["component_config"]["components"]["gantry_1"]["axis_order"] = ["Y"]
        gcfg = GantryConfig(SystemConfig(config)(), "gantry_1")
        construction = _build_modbus_from_gcfg(gcfg)
        home_order = []
        construction.axes["X"].home.side_effect = lambda: home_order.append("X")
        construction.axes["Y"].home.side_effect = lambda: home_order.append("Y")
  
        construction.backend.home(construction.axes)

        assert home_order == ["Y", "X"]

    def test_axis_order_is_optional_for_modbus(self, patched_festo_axis):
        """Build all Modbus axes when axis_order is omitted."""
        import copy

        config = copy.deepcopy(_MODBUS_CONFIG)
        config["component_config"]["components"]["gantry_1"].pop("axis_order")

        gcfg = GantryConfig(SystemConfig(config)(), "gantry_1")
        from applied_motion.gantry_factory import _build_modbus_from_gcfg

        construction = _build_modbus_from_gcfg(gcfg)
        assert set(construction.axes) == {"X", "Y"}

    def test_axis_order_is_optional_for_fposbapi(self, patched_fposbapi_client):
        """Build all FPosBAPI axes when axis_order is omitted."""
        import copy

        from applied_motion.gantry_factory import _build_fposbapi_from_gcfg

        config = copy.deepcopy(_FPOSBAPI_CONFIG)
        config["component_config"]["components"]["gantry_1"].pop("axis_order")
        gcfg = GantryConfig(SystemConfig(config)(), "gantry_1")

        construction = _build_fposbapi_from_gcfg(gcfg)

        assert set(construction.axes) == {"X", "Y", "Z"}

    def test_partial_axis_order_cannot_skip_modbus_axis_validation(self):
        """Validate Modbus axes even when axis_order omits one."""
        component = {
            "backend": "modbus",
            "axes": {
                "X": {"name": "X"},
                "Y": {"name": "Y", "ip": "192.168.0.32"},
            },
            "axis_order": ["Y"],
        }

        with pytest.raises(ValueError, match="Modbus axis 'X' must define string field 'ip'"):
            GantryConfig({"components": {"gantry_1": component}}, "gantry_1")

    def test_client_is_none_for_modbus_backend(self, patched_festo_axis):
        g = Gantry.from_config(_MODBUS_CONFIG)
        assert not hasattr(g, "_client")

    def test_supports_teach_false_for_modbus_backend(self, patched_festo_axis):
        g = Gantry.from_config(_MODBUS_CONFIG)
        assert g.supports_teach() is False

    def test_concurrent_axes_none_when_not_specified(self, patched_festo_axis):
        g = Gantry.from_config(_MODBUS_CONFIG)
        assert g.concurrent_axes is None

    def test_concurrent_axes_populated_when_specified(self, patched_festo_axis):
        import copy
        config = copy.deepcopy(_MODBUS_CONFIG)
        config["component_config"]["components"]["gantry_1"]["concurrent_axes"] = ["X"]
        g = Gantry.from_config(config)
        assert "X" in g.concurrent_axes
        assert "Y" not in g.concurrent_axes

    def test_no_backend_key_defaults_to_modbus(self, patched_festo_axis):
        """Config without a backend key defaults to modbus."""
        config = {
            "spec_version": "4.0",
            "system_config": {"metadata": {}},
            "component_config": {
                "metadata": {},
                "components": {
                    "gantry_1": {
                        "component_spec_version": "3.0",
                        "axes": {"X": {"name": "X", "ip": "192.168.0.193"}},
                        "axis_order": ["X"],
                        "concurrent_axes": None,
                    }
                },
            },
        }
        g = Gantry.from_config(config)
        assert "X" in g.axes
        assert not hasattr(g, "_client")

    def test_explicit_gantry_name_selects_requested_component(self, patched_festo_axis):
        """Regression for #7: explicit name=... must select that component
        even when multiple gantries are defined in the config.
        """
        import copy

        config = copy.deepcopy(_MODBUS_CONFIG)
        config["component_config"]["components"]["gantry_2"] = {
            "component_spec_version": "3.0",
            "backend": "modbus",
            "axes": {
                "Y": {"name": "Y", "ip": "192.168.0.201"},
                "W": {"name": "W", "ip": "192.168.0.202"},
            },
            "axis_order": ["Y", "W"],
            "concurrent_axes": ["Y"],
        }

        g = Gantry.from_config(config, name="gantry_2")

        assert list(g.axes.keys()) == ["Y", "W"]
        assert g.concurrent_axes is not None
        assert set(g.concurrent_axes.keys()) == {"Y"}

    def test_close_releases_factory_owned_axes_once(self, mocker):
        mock_cls = mocker.patch("applied_motion.gantry_factory.LinearAxis", autospec=True)
        axis_x = MagicMock(spec=EdconAxis)
        axis_y = MagicMock(spec=EdconAxis)
        mock_cls.side_effect = [axis_x, axis_y]
        gcfg = GantryConfig(SystemConfig(_MODBUS_CONFIG)(), "gantry_1")
        from applied_motion.gantry_factory import _build_modbus_from_gcfg

        construction = _build_modbus_from_gcfg(gcfg)
        gantry = Gantry(axes=construction.axes, _backend=construction.backend)

        gantry.close()
        gantry.close()

        axis_x.close.assert_called_once_with()
        axis_y.close.assert_called_once_with()

    def test_config_backed_gantry_joins_runtime_without_closing_session(self, mocker):
        axis_x = MagicMock(spec=EdconAxis)
        axis_y = MagicMock(spec=EdconAxis)
        axis_z = MagicMock(spec=EdconAxis)
        mocker.patch("applied_motion.gantry_factory.LinearAxis", autospec=True, side_effect=[axis_x, axis_y, axis_z])
        first = Gantry(config=_MODBUS_CONFIG)
        second_config = {
            "spec_version": "4.0",
            "components": {
                "gantry_2": {
                    "component_spec_version": "3.0",
                    "backend": "modbus",
                    "axes": {"Y": {"name": "Y", "ip": "192.168.0.201"}},
                },
            }
        }

        joined = Gantry(config=second_config, name="gantry_2", registry=first.registry)

        assert joined.registry is first.registry
        assert joined._registry_owner is False
        joined.close()
        assert first.registry.closed is False
        first.close()
        assert first.registry.closed is True
        axis_x.close.assert_called_once_with()
        axis_y.close.assert_called_once_with()
        axis_z.close.assert_called_once_with()

    def test_run_referencing_override_is_forwarded_to_runtime_builder(self, mocker):
        mock_axis = MagicMock(spec=EdconAxis)
        axis_factory = mocker.patch("applied_motion.gantry_factory.LinearAxis", autospec=True, return_value=mock_axis)

        gantry = Gantry.from_config(_MODBUS_CONFIG, run_referencing=False)

        assert axis_factory.call_args.kwargs["run_referencing"] is False
        gantry.close()

    def test_configured_modbus_true_override_does_not_home_gantry_again(self, mocker):
        backend = MagicMock()
        construction = GantryConstruction(axes={"X": MagicMock(spec=EdconAxis)}, concurrent_axes=None, backend=backend)
        mocker.patch("applied_motion.configurator_adapter._build_modbus_from_gcfg", return_value=construction)

        Gantry.from_config(_MODBUS_CONFIG, run_referencing=True)

        backend.home.assert_not_called()

    def test_injected_axes_are_closed_only_when_explicitly_owned(self):
        axis = MagicMock(spec=EdconAxis)
        unowned = Gantry(axes={"X": axis}, _backend=ModbusGantryBackend())

        unowned.close()

        axis.close.assert_not_called()

        owned = Gantry(axes={"X": axis}, _backend=ModbusGantryBackend(owned_axes=(axis,)))
        owned.close()

        axis.close.assert_called_once_with()

    def test_one_axis_cleanup_failure_does_not_block_other_axes(self):
        axis_x = MagicMock(spec=EdconAxis)
        axis_y = MagicMock(spec=EdconAxis)
        axis_x.name = "X"
        axis_y.name = "Y"
        axis_x.close.side_effect = OSError("X connection close failed")
        gantry = Gantry(
            axes={"X": axis_x, "Y": axis_y},
            _backend=ModbusGantryBackend(owned_axes=(axis_x, axis_y)),
        )

        with pytest.raises(RuntimeError, match="X connection close failed"):
            gantry.close()

        axis_x.close.assert_called_once_with()
        axis_y.close.assert_called_once_with()
        gantry.close()
        axis_x.close.assert_called_once_with()
        axis_y.close.assert_called_once_with()

    def test_partial_modbus_construction_closes_previously_created_axes(self, mocker):
        axis_x = MagicMock(spec=EdconAxis)
        mocker.patch(
            "applied_motion.gantry_factory.LinearAxis",
            autospec=True,
            side_effect=[axis_x, RuntimeError("Y connection failed")],
        )
        from applied_motion.gantry_factory import _build_modbus_from_gcfg

        gcfg = GantryConfig(SystemConfig(_MODBUS_CONFIG)(), "gantry_1")

        with pytest.raises(RuntimeError, match="Y connection failed"):
            _build_modbus_from_gcfg(gcfg)

        axis_x.close.assert_called_once_with()


# ---------------------------------------------------------------------------
# FPosBAPI backend
# ---------------------------------------------------------------------------


class TestFromConfigFPosBAPI:
    def test_constructor_accepts_fposbapi_config_directly(self, patched_fposbapi_client):
        _, mock_client = patched_fposbapi_client
        g = Gantry(config=_FPOSBAPI_CONFIG)
        assert list(g.axes.keys()) == ["X", "Y", "Z"]
        assert g.supports_teach() is True
        mock_client.send_command.assert_any_call("ENABLE")
        assert not any(call.args == ("HOME",) for call in mock_client.send_command.call_args_list)

    def test_explicit_true_override_sends_one_fposbapi_home(self, patched_fposbapi_client):
        _, mock_client = patched_fposbapi_client

        g = Gantry.from_config(_FPOSBAPI_CONFIG, run_referencing=True)

        home_calls = [call for call in mock_client.send_command.call_args_list if call.args == ("HOME",)]
        assert len(home_calls) == 1
        g.close()

    def test_creates_fposbapi_client_with_correct_ip(self, patched_fposbapi_client):
        mock_cls, _ = patched_fposbapi_client
        Gantry.from_config(_FPOSBAPI_CONFIG)
        mock_cls.assert_called_once_with(ip="192.168.10.25", port=1234)

    def test_creates_fposbaxis_proxy_for_each_entry(self, patched_fposbapi_client):
        _, mock_client = patched_fposbapi_client
        g = Gantry.from_config(_FPOSBAPI_CONFIG)
        assert "X" in g.axes
        assert "Y" in g.axes
        assert "Z" in g.axes

    def test_proxy_types_are_fposbaxis_proxy(self, patched_fposbapi_client):
        _, mock_client = patched_fposbapi_client
        g = Gantry.from_config(_FPOSBAPI_CONFIG)
        for axis in g.axes.values():
            assert isinstance(axis, FPosBAxis)

    def test_proxy_indices_match_config(self, patched_fposbapi_client):
        _, mock_client = patched_fposbapi_client
        g = Gantry.from_config(_FPOSBAPI_CONFIG)
        assert g.axes["X"].index == 1
        assert g.axes["Y"].index == 2
        assert g.axes["Z"].index == 3

    def test_proxy_names_match_config(self, patched_fposbapi_client):
        _, mock_client = patched_fposbapi_client
        g = Gantry.from_config(_FPOSBAPI_CONFIG)
        assert g.axes["X"].name == "X"
        assert g.axes["Y"].name == "Y"
        assert g.axes["Z"].name == "Z"

    def test_gantry_client_is_shared_instance(self, patched_fposbapi_client):
        """All FPosBAxis proxies must share one injected client instance."""
        _, mock_client = patched_fposbapi_client
        g = Gantry.from_config(_FPOSBAPI_CONFIG)
        for axis in g.axes.values():
            assert axis._client is mock_client

    def test_partial_axis_order_never_limits_fposbapi_axis_inventory(self, patched_fposbapi_client):
        """Construct all FPosBAPI axes when axis_order lists only one."""
        import copy

        from applied_motion.gantry_factory import _build_fposbapi_from_gcfg

        config = copy.deepcopy(_FPOSBAPI_CONFIG)
        config["component_config"]["components"]["gantry_1"]["axis_order"] = ["Z"]
        gcfg = GantryConfig(SystemConfig(config)(), "gantry_1")
        construction = _build_fposbapi_from_gcfg(gcfg)
        assert set(construction.axes) == {"X", "Y", "Z"}

    def test_partial_axis_order_cannot_skip_fposbapi_axis_validation(self):
        """Validate FPosBAPI axes even when axis_order omits one."""
        import copy

        config = copy.deepcopy(_FPOSBAPI_CONFIG)
        component = config["component_config"]["components"]["gantry_1"]
        component["axis_order"] = ["X"]
        component["axes"]["Z"].pop("index")

        with pytest.raises(ValueError, match="FPosBAPI axis 'Z' must define int field 'index'"):
            GantryConfig(SystemConfig(config)(), "gantry_1")

    def test_enable_sent_on_from_config(self, patched_fposbapi_client):
        """ENABLE must be sent via send_command during from_config initialisation."""
        _, mock_client = patched_fposbapi_client
        Gantry.from_config(_FPOSBAPI_CONFIG)
        enable_calls = [c for c in mock_client.send_command.call_args_list if c[0][0] == "ENABLE"]
        assert len(enable_calls) == 1

    def test_supports_teach_true_for_fposbapi_backend(self, patched_fposbapi_client):
        _, _ = patched_fposbapi_client
        g = Gantry.from_config(_FPOSBAPI_CONFIG)
        assert g.supports_teach() is True

    def test_teach_pos_delegates_to_backend_client(self, patched_fposbapi_client):
        _, mock_client = patched_fposbapi_client
        g = Gantry.from_config(_FPOSBAPI_CONFIG)
        g.teach_pos(3)
        mock_client.teach_pos.assert_called_once_with(pos_id=3)

    def test_teach_tray_delegates_to_backend_client(self, patched_fposbapi_client):
        _, mock_client = patched_fposbapi_client
        g = Gantry.from_config(_FPOSBAPI_CONFIG)
        g.teach_tray(2, 5)
        mock_client.teach_tray.assert_called_once_with(tray_id=2, tray_pos=5)

    def test_close_closes_fposbapi_client(self, patched_fposbapi_client):
        _, mock_client = patched_fposbapi_client
        g = Gantry.from_config(_FPOSBAPI_CONFIG)
        g.close()
        mock_client.close.assert_called_once()

    def test_close_is_idempotent(self, patched_fposbapi_client):
        _, mock_client = patched_fposbapi_client
        g = Gantry.from_config(_FPOSBAPI_CONFIG)
        g.close()
        g.close()
        mock_client.close.assert_called_once()

    def test_context_manager_closes_fposbapi_client(self, patched_fposbapi_client):
        _, mock_client = patched_fposbapi_client
        with Gantry.from_config(_FPOSBAPI_CONFIG) as g:
            assert isinstance(g, Gantry)
        mock_client.close.assert_called_once()


class TestGantryFactoryHelpers:
    def test_modbus_factory_returns_construction_bundle(self, patched_festo_axis):
        from applied_motion.gantry_factory import build_modbus_gantry

        construction = build_modbus_gantry(_MODBUS_CONFIG)
        assert isinstance(construction, GantryConstruction)
        assert list(construction.axes.keys()) == ["X", "Y"]

    def test_fposbapi_factory_returns_construction_bundle(self, patched_fposbapi_client):
        from applied_motion.gantry_factory import build_fposbapi_gantry

        construction = build_fposbapi_gantry(_FPOSBAPI_CONFIG)
        assert isinstance(construction, GantryConstruction)
        assert list(construction.axes.keys()) == ["X", "Y", "Z"]


# ---------------------------------------------------------------------------
# from_config with Path
# ---------------------------------------------------------------------------


class TestFromConfigPath:
    def test_loads_modbus_json_file(self, tmp_path, patched_festo_axis):
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(_MODBUS_CONFIG))
        g = Gantry.from_config(spec_file)
        assert "X" in g.axes
        assert "Y" in g.axes

    def test_loads_fposbapi_json_file(self, tmp_path, patched_fposbapi_client):
        _, mock_client = patched_fposbapi_client
        spec_file = tmp_path / "fposbapi-spec.json"
        spec_file.write_text(json.dumps(_FPOSBAPI_CONFIG))
        g = Gantry.from_config(spec_file)
        assert len(g.axes) == 3

    def test_loads_canonical_modbus_fixture(self, patched_festo_axis):
        """The checked-in test-gantry-spec.json must parse without error."""
        fixture = Path(__file__).parent.parent / "fixtures" / "test-gantry-spec.json"
        g = Gantry.from_config(fixture)
        assert len(g.axes) == 4

    def test_loads_canonical_fposbapi_fixture(self, patched_fposbapi_client):
        """The checked-in test-gantry-spec-fposapi.json must parse without error."""
        _, _ = patched_fposbapi_client
        fixture = Path(__file__).parent.parent / "fixtures" / "test-gantry-spec-fposbapi.json"
        g = Gantry.from_config(fixture)
        assert len(g.axes) == 3


# ---------------------------------------------------------------------------
# Invalid backend
# ---------------------------------------------------------------------------


class TestFromConfigInvalidBackend:
    def test_unsupported_backend_raises_value_error(self):
        import copy
        config = copy.deepcopy(_MODBUS_CONFIG)
        config["component_config"]["components"]["gantry_1"]["backend"] = "can_bus"
        with pytest.raises(ValueError, match="Unsupported backend"):
            Gantry.from_config(config)

    def test_error_message_contains_backend_name(self):
        import copy
        config = copy.deepcopy(_MODBUS_CONFIG)
        config["component_config"]["components"]["gantry_1"]["backend"] = "opc_ua"
        with pytest.raises(ValueError, match="opc_ua"):
            Gantry.from_config(config)


class TestConfigValidationContracts:
    def test_system_config_rejects_non_dict_loaded_value(self):
        with pytest.raises(ValueError, match="Config config must load as a dict"):
            SystemConfig(cast(dict, []))

    def test_system_config_rejects_non_dict_component_config(self):
        with pytest.raises(ValueError, match="Normalized config config must be a dict"):
            SystemConfig({"component_config": []})

    def test_gantry_config_requires_components_mapping(self):
        with pytest.raises(ValueError, match="Config config must contain a 'components' mapping"):
            GantryConfig({}, "gantry_1")

    def test_gantry_config_requires_named_component(self):
        with pytest.raises(ValueError, match="Config config does not contain component 'gantry_1'"):
            GantryConfig({"components": {}}, "gantry_1")

    def test_gantry_config_requires_axis_mapping(self):
        component = _base_modbus_component()
        component.pop("axes")
        with pytest.raises(ValueError, match="Config component 'gantry_1' must contain an 'axes' mapping"):
            GantryConfig({"components": {"gantry_1": component}}, "gantry_1")

    def test_gantry_config_axis_order_unknown_axis_error_text(self):
        component = _base_modbus_component()
        component["axis_order"] = ["Y"]
        with pytest.raises(ValueError, match=r"Config axis_order references unknown axes: \['Y'\]"):
            GantryConfig({"components": {"gantry_1": component}}, "gantry_1")

    def test_gantry_config_concurrent_axes_unknown_axis_error_text(self):
        component = _base_modbus_component()
        component["concurrent_axes"] = ["Y"]
        with pytest.raises(ValueError, match=r"Config concurrent_axes references unknown axes: \['Y'\]"):
            GantryConfig({"components": {"gantry_1": component}}, "gantry_1")

    def test_gantry_config_modbus_axis_requires_ip_error_text(self):
        component = _base_modbus_component()
        component["axes"]["X"] = {"name": "X"}
        with pytest.raises(ValueError, match="Modbus axis 'X' must define string field 'ip'"):
            GantryConfig({"components": {"gantry_1": component}}, "gantry_1")

    @pytest.mark.parametrize("axis_type", ["linear", "rotary"])
    def test_modbus_config_accepts_supported_axis_types(self, axis_type):
        component = _base_modbus_component()
        component["axes"]["X"]["type"] = axis_type

        config = GantryConfig({"components": {"gantry_1": component}}, "gantry_1")

        assert config.axes_cfg["X"]["type"] == axis_type

    def test_modbus_config_rejects_unknown_axis_type(self):
        component = _base_modbus_component()
        component["axes"]["X"]["type"] = "circular"

        with pytest.raises(ValueError, match="Modbus axis 'X' has unsupported type 'circular'"):
            GantryConfig({"components": {"gantry_1": component}}, "gantry_1")

    def test_modbus_factory_defaults_missing_axis_type_to_linear(self, mocker):
        from applied_motion.gantry_factory import _build_modbus_from_gcfg

        config = GantryConfig(SystemConfig(_MODBUS_CONFIG)(), "gantry_1")
        linear_factory = mocker.patch("applied_motion.gantry_factory.LinearAxis", autospec=True)
        rotary_factory = mocker.patch("applied_motion.gantry_factory.RotaryAxis", autospec=True)

        _build_modbus_from_gcfg(config)

        assert linear_factory.call_count == 2
        rotary_factory.assert_not_called()

    def test_modbus_factory_dispatches_explicit_axis_types(self, mocker):
        import copy

        from applied_motion.gantry_factory import _build_modbus_from_gcfg

        config_source = copy.deepcopy(_MODBUS_CONFIG)
        axes_config = config_source["component_config"]["components"]["gantry_1"]["axes"]
        axes_config["X"]["type"] = "linear"
        axes_config["Y"]["type"] = "rotary"
        config = GantryConfig(SystemConfig(config_source)(), "gantry_1")
        linear_factory = mocker.patch("applied_motion.gantry_factory.LinearAxis", autospec=True)
        rotary_factory = mocker.patch("applied_motion.gantry_factory.RotaryAxis", autospec=True)

        construction = _build_modbus_from_gcfg(config)

        assert construction.axes["X"] is linear_factory.return_value
        assert construction.axes["Y"] is rotary_factory.return_value
        linear_factory.assert_called_once()
        rotary_factory.assert_called_once()

    @pytest.mark.parametrize("axis_type", ["linear", "rotary"])
    def test_factory_forwards_homing_override_to_selected_axis(self, mocker, axis_type):
        from applied_motion.gantry_factory import _build_modbus_from_gcfg

        component = _base_modbus_component()
        component["axes"]["X"].update(type=axis_type, run_referencing=False)
        config = GantryConfig({"components": {"gantry_1": component}}, "gantry_1")
        linear_factory = mocker.patch("applied_motion.gantry_factory.LinearAxis", autospec=True)
        rotary_factory = mocker.patch("applied_motion.gantry_factory.RotaryAxis", autospec=True)

        _build_modbus_from_gcfg(config)

        selected_factory = linear_factory if axis_type == "linear" else rotary_factory
        assert selected_factory.call_args.kwargs["run_referencing"] is False

    def test_partial_mixed_axis_construction_closes_created_linear_axis(self, mocker):
        from applied_motion.gantry_factory import _build_modbus_from_gcfg

        component = _base_modbus_component()
        component["axes"]["Y"] = {"name": "Y", "ip": "192.168.0.32", "type": "rotary"}
        component["axis_order"] = ["X", "Y"]
        linear_axis = MagicMock(spec=EdconAxis)
        mocker.patch("applied_motion.gantry_factory.LinearAxis", autospec=True, return_value=linear_axis)
        mocker.patch(
            "applied_motion.gantry_factory.RotaryAxis",
            autospec=True,
            side_effect=RuntimeError("Y connection failed"),
        )

        config = GantryConfig({"components": {"gantry_1": component}}, "gantry_1")
        with pytest.raises(RuntimeError, match="Y connection failed"):
            _build_modbus_from_gcfg(config)

        linear_axis.close.assert_called_once_with()

    def test_axis_classes_are_exported_from_public_packages(self):
        import applied_motion
        import applied_motion.backends as backends

        assert applied_motion.LinearAxis is LinearAxis
        assert applied_motion.RotaryAxis is RotaryAxis
        assert backends.LinearAxis is LinearAxis
        assert backends.RotaryAxis is RotaryAxis

class TestConstructorValidation:
    def test_constructor_rejects_config_plus_manual_axes(self):
        axis = object.__new__(EdconAxis)
        axis.name = "X"
        with pytest.raises(ValueError, match="either config or axes"):
            Gantry(axes={"X": axis}, config=_MODBUS_CONFIG)

    def test_constructor_requires_axes_when_no_config(self):
        with pytest.raises(ValueError, match="axes must be provided"):
            Gantry()


# ---------------------------------------------------------------------------
# Gantry.home — backend dispatch
# ---------------------------------------------------------------------------


class TestGantryHomeBackendDispatch:
    def test_fposbapi_home_sends_single_home_command(self, gantry_fposbapi_mock):
        """For FPosBAPI backend, one HOME command must be sent via the client,
        not one per axis.
        """
        gantry_fposbapi_mock.home()
        gantry_fposbapi_mock._stub_client.send_command.assert_called_with("HOME")

    def test_fposbapi_home_does_not_call_axis_home(self, gantry_fposbapi_mock):
        """FPosBAPI home must NOT call home() on individual axis proxies since
        the controller homes all axes together via a single command.
        """
        # Patch each proxy's home method to detect spurious calls
        for axis in gantry_fposbapi_mock._stub_axes.values():
            axis.home = MagicMock()
        gantry_fposbapi_mock.home()
        for axis in gantry_fposbapi_mock._stub_axes.values():
            axis.home.assert_not_called()

    def test_modbus_home_calls_home_on_every_axis(self, gantry_mock):
        """For Modbus backend, home must delegate to each axis."""
        gantry_mock.home()
        for axis in gantry_mock._stub_axes.values():
            axis.home.assert_called_once()
