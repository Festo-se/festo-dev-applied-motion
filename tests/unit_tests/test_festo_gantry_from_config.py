"""Unit tests for Gantry.from_config.

Coverage areas
--------------
* ``from_config`` with a modbus backend dict — creates EdconAxis instances
  with the correct name, ip, and honours axis_order.
* ``from_config`` with a Path to a JSON file — loads the file and constructs
  correctly.
* ``from_config`` with no ``backend`` key — defaults to ``"modbus"`` for
  backward compatibility with spec version 1.0 configs.
* ``from_config`` with a fposapi backend dict — creates FPosAPIClient with
  the correct ip/port, creates FPosAxis instances with correct name and
  index, stores the client on the gantry.
* ``from_config`` with concurrent_axes specified — populates
  ``gantry.concurrent_axes``.
* ``from_config`` with an unsupported backend — raises ``ValueError``.
* ``Gantry.home`` for FPosAPI backend — sends single HOME command via
  client rather than calling axis.home() on each proxy.
* ``Gantry.home`` for Modbus backend — delegates to each axis.

No hardware or network connection required.  EdconAxis and FPosAPIClient
constructors are patched so no TCP or Modbus connections are attempted.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from festo_dev_applied_motion.backends.edcon_axis import EdconAxis
from festo_dev_applied_motion.gantry import Gantry
from festo_dev_applied_motion.backends.fposapi_axis import FPosAxis
from festo_dev_applied_motion.backends.fposapi_client import FPosAPIClient


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

_MODBUS_CONFIG = {
    "spec_version": "2.0",
    "backend": "modbus",
    "axes": {
        "X": {"name": "X", "ip": "192.168.0.193"},
        "Z": {"name": "Z", "ip": "192.168.0.32"},
    },
    "gantry": {
        "axis_order": ["X", "Z"],
        "concurrent_axes": None,
    },
}

_FPOSAPI_CONFIG = {
    "spec_version": "2.0",
    "backend": "fposapi",
    "connection": {"ip": "192.168.10.10", "port": 1234},
    "axes": {
        "X": {"name": "X", "index": 1},
        "Y": {"name": "Y", "index": 2},
        "Z": {"name": "Z", "index": 3},
    },
    "gantry": {
        "axis_order": ["X", "Y", "Z"],
        "concurrent_axes": None,
    },
}


@pytest.fixture()
def patched_festo_axis(mocker):
    """Patch EdconAxis so its __init__ does not open a Modbus connection."""
    mock_cls = mocker.patch("festo_dev_applied_motion.gantry.EdconAxis", autospec=True)
    mock_cls.side_effect = lambda name, ip, run_referencing=False: MagicMock(
        spec=EdconAxis, name=name, ip=ip
    )
    return mock_cls


@pytest.fixture()
def patched_fposapi_client(mocker):
    """Patch FPosAPIClient so from_config does not open a TCP socket."""
    mock_cls = mocker.patch("festo_dev_applied_motion.gantry.FPosAPIClient", autospec=True)
    mock_instance = MagicMock(spec=FPosAPIClient)
    mock_instance.ip = "192.168.10.10"
    mock_instance.port = 1234
    mock_instance.send_command.return_value = "1, HOME, 0, NULL, SUCCESS"
    mock_cls.return_value = mock_instance
    return mock_cls, mock_instance


# ---------------------------------------------------------------------------
# Modbus backend
# ---------------------------------------------------------------------------


class TestFromConfigModbus:
    def test_creates_festo_axis_for_each_entry(self, patched_festo_axis):
        g = Gantry.from_config(_MODBUS_CONFIG)
        assert "X" in g.axes
        assert "Z" in g.axes

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
        assert list(g.axes.keys()) == ["X", "Z"]

    def test_client_is_none_for_modbus_backend(self, patched_festo_axis):
        g = Gantry.from_config(_MODBUS_CONFIG)
        assert g._client is None

    def test_concurrent_axes_none_when_not_specified(self, patched_festo_axis):
        g = Gantry.from_config(_MODBUS_CONFIG)
        assert g.concurrent_axes is None

    def test_concurrent_axes_populated_when_specified(self, patched_festo_axis):
        config = {**_MODBUS_CONFIG, "gantry": {"axis_order": ["X", "Z"], "concurrent_axes": ["X"]}}
        g = Gantry.from_config(config)
        assert "X" in g.concurrent_axes
        assert "Z" not in g.concurrent_axes

    def test_no_backend_key_defaults_to_modbus(self, patched_festo_axis):
        """Spec version 1.0 configs without a backend key must still work."""
        config = {
            "spec_version": "1.0",
            "axes": {"X": {"name": "X", "ip": "192.168.0.193"}},
            "gantry": {"axis_order": ["X"], "concurrent_axes": None},
        }
        g = Gantry.from_config(config)
        assert "X" in g.axes
        assert g._client is None


# ---------------------------------------------------------------------------
# FPosAPI backend
# ---------------------------------------------------------------------------


class TestFromConfigFPosAPI:
    def test_creates_fposapi_client_with_correct_ip(self, patched_fposapi_client):
        mock_cls, _ = patched_fposapi_client
        Gantry.from_config(_FPOSAPI_CONFIG)
        mock_cls.assert_called_once_with(ip="192.168.10.10", port=1234)

    def test_creates_fposaxis_proxy_for_each_entry(self, patched_fposapi_client):
        _, mock_client = patched_fposapi_client
        g = Gantry.from_config(_FPOSAPI_CONFIG)
        assert "X" in g.axes
        assert "Y" in g.axes
        assert "Z" in g.axes

    def test_proxy_types_are_fposaxis_proxy(self, patched_fposapi_client):
        _, mock_client = patched_fposapi_client
        g = Gantry.from_config(_FPOSAPI_CONFIG)
        for axis in g.axes.values():
            assert isinstance(axis, FPosAxis)

    def test_proxy_indices_match_config(self, patched_fposapi_client):
        _, mock_client = patched_fposapi_client
        g = Gantry.from_config(_FPOSAPI_CONFIG)
        assert g.axes["X"].index == 1
        assert g.axes["Y"].index == 2
        assert g.axes["Z"].index == 3

    def test_proxy_names_match_config(self, patched_fposapi_client):
        _, mock_client = patched_fposapi_client
        g = Gantry.from_config(_FPOSAPI_CONFIG)
        assert g.axes["X"].name == "X"
        assert g.axes["Y"].name == "Y"
        assert g.axes["Z"].name == "Z"

    def test_gantry_client_is_shared_instance(self, patched_fposapi_client):
        """The gantry's _client and all proxy _client refs must be the same object."""
        _, mock_client = patched_fposapi_client
        g = Gantry.from_config(_FPOSAPI_CONFIG)
        assert g._client is mock_client
        for axis in g.axes.values():
            assert axis._client is mock_client

    def test_axis_order_respected(self, patched_fposapi_client):
        _, _ = patched_fposapi_client
        g = Gantry.from_config(_FPOSAPI_CONFIG)
        assert list(g.axes.keys()) == ["X", "Y", "Z"]


# ---------------------------------------------------------------------------
# from_config with Path
# ---------------------------------------------------------------------------


class TestFromConfigPath:
    def test_loads_modbus_json_file(self, tmp_path, patched_festo_axis):
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(_MODBUS_CONFIG))
        g = Gantry.from_config(spec_file)
        assert "X" in g.axes
        assert "Z" in g.axes

    def test_loads_fposapi_json_file(self, tmp_path, patched_fposapi_client):
        _, mock_client = patched_fposapi_client
        spec_file = tmp_path / "fposapi-spec.json"
        spec_file.write_text(json.dumps(_FPOSAPI_CONFIG))
        g = Gantry.from_config(spec_file)
        assert len(g.axes) == 3

    def test_loads_canonical_modbus_fixture(self, patched_festo_axis):
        """The checked-in test-gantry-spec.json must parse without error."""
        fixture = Path(__file__).parent.parent / "fixtures" / "test-gantry-spec.json"
        g = Gantry.from_config(fixture)
        assert len(g.axes) == 2

    def test_loads_canonical_fposapi_fixture(self, patched_fposapi_client):
        """The checked-in test-gantry-spec-fposapi.json must parse without error."""
        _, _ = patched_fposapi_client
        fixture = Path(__file__).parent.parent / "fixtures" / "test-gantry-spec-fposapi.json"
        g = Gantry.from_config(fixture)
        assert len(g.axes) == 3


# ---------------------------------------------------------------------------
# Invalid backend
# ---------------------------------------------------------------------------


class TestFromConfigInvalidBackend:
    def test_unsupported_backend_raises_value_error(self):
        config = {**_MODBUS_CONFIG, "backend": "can_bus"}
        with pytest.raises(ValueError, match="Unsupported backend"):
            Gantry.from_config(config)

    def test_error_message_contains_backend_name(self):
        config = {**_MODBUS_CONFIG, "backend": "opc_ua"}
        with pytest.raises(ValueError, match="opc_ua"):
            Gantry.from_config(config)


# ---------------------------------------------------------------------------
# Gantry.home — backend dispatch
# ---------------------------------------------------------------------------


class TestGantryHomeBackendDispatch:
    def test_fposapi_home_sends_single_home_command(self, gantry_fposapi_mock):
        """For FPosAPI backend, one HOME command must be sent via the client,
        not one per axis."""
        gantry_fposapi_mock.home()
        gantry_fposapi_mock._client.send_command.assert_called_once_with("HOME")

    def test_fposapi_home_does_not_call_axis_home(self, gantry_fposapi_mock):
        """FPosAPI home must NOT call home() on individual axis proxies since
        the controller homes all axes together via a single command."""
        # Patch each proxy's home method to detect spurious calls
        for axis in gantry_fposapi_mock._stub_axes.values():
            axis.home = MagicMock()
        gantry_fposapi_mock.home()
        for axis in gantry_fposapi_mock._stub_axes.values():
            axis.home.assert_not_called()

    def test_modbus_home_calls_home_on_every_axis(self, gantry_mock):
        """For Modbus backend (_client is None), home must delegate to each axis."""
        gantry_mock.home()
        for axis in gantry_mock._stub_axes.values():
            axis.home.assert_called_once()
