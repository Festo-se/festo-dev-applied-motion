"""Shared pytest fixtures for the festo-dev-applied-motion test suite.

Fixtures
--------
axis_mock
    A ``EdconAxis`` instance where ``ComModbus`` and all ``MotionHandler``
    methods touched during ``__init__`` are replaced by ``MagicMock``
    objects.  No hardware or network connection required.  Use this in
    all unit tests.  The underlying mock com object is accessible as
    ``axis_mock._mock_com`` so individual tests can inspect calls or
    override return values.

gantry_mock
    A ``Gantry`` built from two lightweight stub ``EdconAxis``
    objects whose ``move``, ``home``, ``current_position``,
    ``_valid_position``, ``stopped``, and ``ready_for_motion`` methods
    are all ``MagicMock`` instances.  No hardware or network connection
    required.  The stub axes are accessible as ``gantry_mock._stub_axes``.

axis_a
    A ``EdconAxis`` connected to the first configured drive.  The IP and
    axis label are read from ``GANTRY_A_IP`` and ``GANTRY_A_NAME``
    environment variables, falling back to the defaults found in the
    slas-8 pipettor config.  Mark any test that uses this fixture with
    ``@pytest.mark.hardware``.

axis_b
    A ``EdconAxis`` connected to the second configured drive.  Uses
    ``GANTRY_B_IP`` and ``GANTRY_B_NAME``, with matching defaults.

gantry
    A ``Gantry`` built from ``axis_a`` and ``axis_b``.  Mark any
    test that uses this fixture with ``@pytest.mark.hardware``.

Run all hardware tests with::

    uv run pytest -m hardware

Skip hardware tests (CI default)::

    uv run pytest -m "not hardware"

Override connection details at runtime::

    GANTRY_A_IP=10.0.0.5 GANTRY_A_NAME=X GANTRY_B_IP=10.0.0.6 GANTRY_B_NAME=Z uv run pytest -m hardware
"""

import json
import socket
from os import getenv
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from edcon.edrive.motion_handler import MotionHandler

from applied_motion.gantry import Gantry
from applied_motion.backends.edcon_axis import EdconAxis
from applied_motion.backends.fposbapi_axis import FPosBAxis
from applied_motion.backends.fposbapi_client import FPosBAPIClient, FPosBAPIClientError

# ---------------------------------------------------------------------------
# Hardware reachability probe
# ---------------------------------------------------------------------------

_MODBUS_PORT = 502
_CONNECT_TIMEOUT_S = 2.0


def _is_reachable(ip: str, port: int = _MODBUS_PORT, timeout: float = _CONNECT_TIMEOUT_S) -> bool:
    """Return True if *ip:port* accepts a TCP connection within *timeout* seconds."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((ip, port)) == 0


# ---------------------------------------------------------------------------
# Defaults taken from festo-dev-fluid-control/slas-8-config.json TODO
# ---------------------------------------------------------------------------
_DEFAULT_A_IP = "192.168.0.193"
_DEFAULT_A_NAME = "X"
_DEFAULT_B_IP = "192.168.0.32"
_DEFAULT_B_NAME = "Z"
_DEFAULT_FPOSBAPI_IP = "192.168.10.25"
_DEFAULT_FPOSBAPI_PORT = 1234

# PNU addresses used by EdconAxis during construction and unit-conversion
_PNU_NEG_SW_LIMIT = 11584
_PNU_POS_SW_LIMIT = 11585
_PNU_POS_UNIT_SCALE = 11724
_PNU_VEL_UNIT_SCALE = 11725

# Representative SW-limit values in drive units used by the mock com.
# Position unit scale is -6 (1 µm/unit), so a 300 mm stroke maps to ±300,000 drive units.
_MOCK_NEG_SW_LIMIT = -300_000
_MOCK_POS_SW_LIMIT = 300_000
# Velocity bounds that MotionHandler would normally supply.
# Velocity unit scale is -3 (1 mm/s per drive unit); ±500 mm/s is a
# representative gantry top speed.
_MOCK_MIN_VELOCITY = -500.0
_MOCK_MAX_VELOCITY = 500.0


# ---------------------------------------------------------------------------
# Mock fixtures — no hardware required
# ---------------------------------------------------------------------------


@pytest.fixture()
def axis_mock(mocker):
    """Return a EdconAxis with ComModbus and MotionHandler fully mocked.

    The mock ``com`` object is pre-configured so every ``read_pnu`` call
    used during ``EdconAxis.__init__`` returns a sensible default.
    ``MotionHandler.__init__`` is replaced with a stub that sets only the
    attributes that ``EdconAxis.__init__`` reads (``min_velocity`` and
    ``max_velocity``).  All MotionHandler methods called during
    ``EdconAxis.__init__`` (``acknowledge_faults``,
    ``configure_software_limit_switch``, ``fault_present``,
    ``fault_string``, ``current_fault_code``) are replaced with
    ``MagicMock`` objects.

    The mock com object is accessible as ``axis_mock._mock_com`` so
    individual tests can inspect calls or override return values.
    """
    mock_com = MagicMock()
    mock_com.read_pnu.side_effect = lambda pnu: {
        _PNU_NEG_SW_LIMIT: _MOCK_NEG_SW_LIMIT,
        _PNU_POS_SW_LIMIT: _MOCK_POS_SW_LIMIT,
        _PNU_POS_UNIT_SCALE: -6,
        _PNU_VEL_UNIT_SCALE: -3,
    }.get(pnu, 0)

    mocker.patch("applied_motion.backends.edcon_axis.ComModbus", return_value=mock_com)

    def _fake_mh_init(self, com):
        # Provide the velocity-bound attributes that EdconAxis.__init__
        # reads immediately after super().__init__() returns.
        self.min_velocity = _MOCK_MIN_VELOCITY
        self.max_velocity = _MOCK_MAX_VELOCITY

    mocker.patch.object(MotionHandler, "__init__", _fake_mh_init)
    mocker.patch.object(MotionHandler, "acknowledge_faults")
    mocker.patch.object(MotionHandler, "configure_software_limit_switch")
    mocker.patch.object(MotionHandler, "fault_present", return_value=False)
    mocker.patch.object(MotionHandler, "fault_string", return_value="OK")
    mocker.patch.object(MotionHandler, "current_fault_code", return_value=0)

    axis = EdconAxis(name=_DEFAULT_A_NAME, ip=_DEFAULT_A_IP)
    # Expose the mock so individual tests can inspect calls or change return values.
    axis._mock_com = mock_com
    return axis


@pytest.fixture()
def gantry_mock():
    """Return a Gantry with lightweight stub axes; no hardware required.

    Both stub axes expose ``MagicMock`` replacements for every method
    that ``Gantry`` delegates to them.  The stubs are accessible as
    ``gantry_mock._stub_axes`` so individual tests can assert call counts
    or change return values.
    """

    def _make_stub_axis(name: str) -> EdconAxis:
        axis = object.__new__(EdconAxis)
        axis.name = name
        axis.move = MagicMock(return_value=True)
        axis.home = MagicMock()
        axis.current_position = MagicMock(return_value=0)
        axis._valid_position = MagicMock(return_value=0.0)
        axis.get_current_axis_position = MagicMock(return_value=0.0)
        axis.stopped = MagicMock(return_value=True)
        axis.ready_for_motion = MagicMock(return_value=True)
        return axis

    axis_x = _make_stub_axis(_DEFAULT_A_NAME)
    axis_z = _make_stub_axis(_DEFAULT_B_NAME)
    g = Gantry(axes={axis_x.name: axis_x, axis_z.name: axis_z})
    g._stub_axes = {axis_x.name: axis_x, axis_z.name: axis_z}
    return g


@pytest.fixture()
def fposbapi_client_mock(mocker):
    """Return a mock :class:`FPosBAPIClient` with ``send_command`` stubbed.

    ``send_command`` returns a generic SUCCESS response line by default.
    Individual tests can override ``send_command.return_value`` or
    ``send_command.side_effect`` as needed.
    """
    client = MagicMock(spec=FPosBAPIClient)
    client.ip = "192.168.10.10"
    client.port = 1234
    client.send_command.return_value = ["1, CMD, 0, NULL, SUCCESS"]
    return client


@pytest.fixture()
def fposbapi_axis_mock(fposbapi_client_mock):
    """Return a :class:`FPosBAxis` backed by ``fposbapi_client_mock``.

    Uses axis name ``"X"`` and index ``1`` — the canonical X-axis in the
    CECC-X convention.  The underlying mock client is accessible as
    ``fposbapi_axis_mock._client``.
    """
    return FPosBAxis(name="X", index=1, client=fposbapi_client_mock)


@pytest.fixture()
def gantry_fposbapi_mock(fposbapi_client_mock):
    """Return a :class:`Gantry` with three :class:`FPosBAxis` stubs.

    Axes X (index 1), Y (index 2), Z (index 3) are backed by
    ``fposbapi_client_mock``.  The proxy instances are accessible as
    ``gantry_fposbapi_mock._stub_axes`` and the shared client as
    ``gantry_fposbapi_mock._client``.
    """
    axis_x = FPosBAxis(name="X", index=1, client=fposbapi_client_mock)
    axis_y = FPosBAxis(name="Y", index=2, client=fposbapi_client_mock)
    axis_z = FPosBAxis(name="Z", index=3, client=fposbapi_client_mock)
    axes = {"X": axis_x, "Y": axis_y, "Z": axis_z}
    g = Gantry(axes=axes, _client=fposbapi_client_mock)
    g._stub_axes = axes
    return g


# ---------------------------------------------------------------------------
# Hardware fixtures — require connected drives
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def axis_a():
    """Return a EdconAxis connected to the first configured drive.

    Skips immediately (rather than hanging) when the drive is not reachable.
    """
    ip = getenv("GANTRY_A_IP", _DEFAULT_A_IP)
    name = getenv("GANTRY_A_NAME", _DEFAULT_A_NAME)
    if not _is_reachable(ip):
        pytest.skip(f"Hardware axis A not reachable at {ip}:{_MODBUS_PORT}")
    return EdconAxis(name=name, ip=ip)


@pytest.fixture(scope="module")
def axis_b():
    """Return a EdconAxis connected to the second configured drive.

    Skips immediately (rather than hanging) when the drive is not reachable.
    """
    ip = getenv("GANTRY_B_IP", _DEFAULT_B_IP)
    name = getenv("GANTRY_B_NAME", _DEFAULT_B_NAME)
    if not _is_reachable(ip):
        pytest.skip(f"Hardware axis B not reachable at {ip}:{_MODBUS_PORT}")
    return EdconAxis(name=name, ip=ip)


@pytest.fixture(scope="module")
def gantry(axis_a, axis_b):
    """Return a Gantry built from the two configured hardware fixtures."""
    return Gantry(axes={axis_a.name: axis_a, axis_b.name: axis_b})


@pytest.fixture(scope="module")
def gantry_fposbapi():
    """Return a :class:`Gantry` built from the FPosBAPI JSON fixture spec.

    Loads ``tests/fixtures/test-gantry-spec-fposbapi.json`` and overrides the
    connection block from ``FPOSBAPI_IP`` / ``FPOSBAPI_PORT`` environment
    variables so the target PLC can be changed at runtime without editing the
    fixture file.  Skips immediately when the CECC-X PLC is not reachable.

    Override connection details at runtime::

        FPOSBAPI_IP=10.0.0.1 FPOSBAPI_PORT=1234 uv run pytest -m hardware
    """
    ip = getenv("FPOSBAPI_IP", _DEFAULT_FPOSBAPI_IP)
    port = int(getenv("FPOSBAPI_PORT", str(_DEFAULT_FPOSBAPI_PORT)))
    fixture_path = Path(__file__).parent / "fixtures" / "test-gantry-spec-fposbapi.json"
    with fixture_path.open() as fh:
        cfg = json.load(fh)
    # cfg["interface"]["type"]="tcp/ip"
    cfg["interface"]["ip"] = ip
    cfg["interface"]["port"] = port

    if not _is_reachable(ip, port):
        pytest.skip(f"FPosBAPI PLC not reachable at {ip}:{port}")

    gantry = Gantry.from_config(cfg)
    yield gantry
    if gantry._client is not None:
        gantry._client.close()
