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
from unittest.mock import MagicMock

import pytest

from applied_motion.applied_motion import Gantry
from applied_motion.gantry_factory import GantryConstruction
from applied_motion.backends.edcon_axis import EdconAxis
from applied_motion.backends.fposbapi_axis import FPosBAxis
from applied_motion.backends.fposbapi_client import FPosBAPIClient


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

_MODBUS_CONFIG = {
    "spec_version": "3.0",
    "system_config": {"metadata": {}},
    "component_config": {
        "metadata": {},
        "components": {
            "gantry_1": {
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
    "spec_version": "3.0",
    "system_config": {
        "metadata": {}
    },
    "component_config": {
        "metadata": {},
        "components": 
            {"gantry_1": {
                "_comment": "FPosBAPI backend test fixture for Gantry. Targets a CECC-X PLC running the FPosBAPI CoDeSys server.",
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


@pytest.fixture()
def patched_festo_axis(mocker):
    """Patch EdconAxis so its __init__ does not open a Modbus connection."""
    mock_cls = mocker.patch("applied_motion.gantry_factory.EdconAxis", autospec=True)
    mock_cls.side_effect = lambda name, ip, run_referencing=False: MagicMock(
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

    def test_axis_order_respected(self, patched_festo_axis):
        g = Gantry.from_config(_MODBUS_CONFIG)
        assert list(g.axes.keys()) == ["X", "Y"]

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
            "spec_version": "3.0",
            "system_config": {"metadata": {}},
            "component_config": {
                "metadata": {},
                "components": {
                    "gantry_1": {
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

    def test_axis_order_respected(self, patched_fposbapi_client):
        _, _ = patched_fposbapi_client
        g = Gantry.from_config(_FPOSBAPI_CONFIG)
        assert list(g.axes.keys()) == ["X", "Y", "Z"]

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
