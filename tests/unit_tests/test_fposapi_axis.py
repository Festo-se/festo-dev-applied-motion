"""Unit tests for FPosAxis.

Coverage areas
--------------
* ``FPosAxis.__init__`` — attribute storage, client stored.
* ``FPosAxis.move`` — SET_PAR then MOV_AXIS command order, absolute vs
  relative flag, returns True on success.
* ``FPosAxis.home`` — sends HOME command.
* ``FPosAxis.get_current_axis_position`` — parses ROB_POS response by
  axis index, raises RuntimeError on malformed response.
* ``FPosAxis.stopped`` — always True.
* ``FPosAxis.ready_for_motion`` — always True.
* ``FPosAxis.__repr__``, ``__str__``, ``__eq__``, ``__hash__``.

No hardware or network connection required.  All tests use the
``fposapi_axis_mock`` fixture (from conftest.py) or construct proxies
directly with a MagicMock client.
"""

from unittest.mock import MagicMock, call

import pytest

from applied_motion.backends.fposapi_axis import FPosAxis
from applied_motion.backends.fposapi_client import FPosAPIClient, FPosAPIClientError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_proxy(name: str = "X", index: int = 1) -> tuple[FPosAxis, MagicMock]:
    """Return a proxy and its mock client as a tuple."""
    client = MagicMock(spec=FPosAPIClient)
    client.send_command.return_value = ["1, CMD, 0, NULL, SUCCESS"]
    return FPosAxis(name=name, index=index, client=client), client


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestFPosAxisInit:
    def test_name_stored(self):
        proxy, _ = _make_proxy(name="Y")
        assert proxy.name == "Y"

    def test_index_stored(self):
        proxy, _ = _make_proxy(index=2)
        assert proxy.index == 2

    def test_client_stored(self):
        proxy, client = _make_proxy()
        assert proxy._client is client


# ---------------------------------------------------------------------------
# move
# ---------------------------------------------------------------------------


class TestFPosAxisMove:
    def test_move_sends_set_par_before_move_axis(self, fposapi_axis_mock):
        """SET_PAR 103 must be sent before MOVE_AXIS so speed is applied."""
        fposapi_axis_mock.move(position=100.0, velocity=50.0)
        calls = fposapi_axis_mock._client.send_command.call_args_list
        commands = [c[0][0] for c in calls]
        set_par_idx = commands.index("SET_PAR")
        move_axis_idx = commands.index("MOVE_AXIS")
        assert set_par_idx < move_axis_idx

    def test_move_set_par_uses_velocity(self, fposapi_axis_mock):
        fposapi_axis_mock.move(position=100.0, velocity=75.0)
        set_par_call = fposapi_axis_mock._client.send_command.call_args_list[0]
        assert set_par_call == call("SET_PAR", 103, 75.0)

    def test_move_absolute_uses_rel_flag_zero(self, fposapi_axis_mock):
        fposapi_axis_mock.move(position=100.0, velocity=50.0, position_type="absolute")
        move_axis_call = fposapi_axis_mock._client.send_command.call_args_list[1]
        # args: ("MOVE_AXIS", axis_index, rel_flag, position)
        assert move_axis_call[0][2] == 0

    def test_move_default_is_absolute(self, fposapi_axis_mock):
        fposapi_axis_mock.move(position=100.0, velocity=50.0)
        move_axis_call = fposapi_axis_mock._client.send_command.call_args_list[1]
        assert move_axis_call[0][2] == 0

    def test_move_relative_uses_rel_flag_one(self, fposapi_axis_mock):
        fposapi_axis_mock.move(position=25.0, velocity=50.0, position_type="relative")
        move_axis_call = fposapi_axis_mock._client.send_command.call_args_list[1]
        assert move_axis_call[0][2] == 1

    def test_move_sends_correct_axis_index(self):
        proxy, client = _make_proxy(name="Y", index=2)
        proxy.move(position=50.0, velocity=30.0)
        move_axis_call = client.send_command.call_args_list[1]
        assert move_axis_call[0][1] == 2

    def test_move_sends_correct_position(self, fposapi_axis_mock):
        fposapi_axis_mock.move(position=123.45, velocity=50.0)
        move_axis_call = fposapi_axis_mock._client.send_command.call_args_list[1]
        assert move_axis_call[0][3] == 123.45

    def test_move_returns_true_on_success(self, fposapi_axis_mock):
        result = fposapi_axis_mock.move(position=100.0, velocity=50.0)
        assert result is True

    def test_move_propagates_client_error(self, fposapi_axis_mock):
        fposapi_axis_mock._client.send_command.side_effect = FPosAPIClientError("drive fault")
        with pytest.raises(FPosAPIClientError):
            fposapi_axis_mock.move(position=100.0, velocity=50.0)


# ---------------------------------------------------------------------------
# home
# ---------------------------------------------------------------------------


class TestFPosAxisHome:
    def test_home_sends_home_command(self, fposapi_axis_mock):
        fposapi_axis_mock.home()
        fposapi_axis_mock._client.send_command.assert_called_once_with("HOME")

    def test_home_propagates_client_error(self, fposapi_axis_mock):
        fposapi_axis_mock._client.send_command.side_effect = FPosAPIClientError("homing failed")
        with pytest.raises(FPosAPIClientError):
            fposapi_axis_mock.home()


# ---------------------------------------------------------------------------
# get_current_axis_position
# ---------------------------------------------------------------------------


class TestFPosAxisGetCurrentAxisPosition:
    def test_x_axis_returns_field_2(self):
        """Axis index 1 (X) must parse fields[2] from the ROB_POS response."""
        proxy, client = _make_proxy(name="X", index=1)
        # msg_id, ROB_POS, x, y, z, 0, NULL, SUCCESS
        client.send_command.return_value = ["1, ROB_POS, 123.4, 50.0, 10.0, 0, NULL, SUCCESS"]
        result = proxy.get_current_axis_position()
        assert result == pytest.approx(123.4)

    def test_y_axis_returns_field_3(self):
        """Axis index 2 (Y) must parse fields[3]."""
        proxy, client = _make_proxy(name="Y", index=2)
        client.send_command.return_value = ["1, ROB_POS, 123.4, 50.0, 10.0, 0, NULL, SUCCESS"]
        result = proxy.get_current_axis_position()
        assert result == pytest.approx(50.0)

    def test_z_axis_returns_field_4(self):
        """Axis index 3 (Z) must parse fields[4]."""
        proxy, client = _make_proxy(name="Z", index=3)
        client.send_command.return_value = ["1, ROB_POS, 123.4, 50.0, 10.0, 0, NULL, SUCCESS"]
        result = proxy.get_current_axis_position()
        assert result == pytest.approx(10.0)

    def test_sends_rob_pos_command(self, fposapi_axis_mock):
        fposapi_axis_mock._client.send_command.return_value = [
            "1, ROB_POS, 0.0, 0.0, 0.0, 0, NULL, SUCCESS"
        ]
        fposapi_axis_mock.get_current_axis_position()
        fposapi_axis_mock._client.send_command.assert_called_once_with("ROB_POS")

    def test_malformed_response_raises_runtime_error(self, fposapi_axis_mock):
        """A response too short to contain a position field for this axis index
        must raise RuntimeError rather than silently returning a wrong value."""
        # Only 2 fields — field index 2 (X axis) doesn't exist
        fposapi_axis_mock._client.send_command.return_value = ["1, ROB_POS"]
        with pytest.raises(RuntimeError, match="Failed to parse ROB_POS"):
            fposapi_axis_mock.get_current_axis_position()

    def test_non_numeric_position_raises_runtime_error(self, fposapi_axis_mock):
        fposapi_axis_mock._client.send_command.return_value = [
            "1, ROB_POS, INVALID, 0.0, 0.0, 0, NULL, SUCCESS"
        ]
        with pytest.raises(RuntimeError):
            fposapi_axis_mock.get_current_axis_position()


# ---------------------------------------------------------------------------
# stopped / ready_for_motion
# ---------------------------------------------------------------------------


class TestFPosAxisStatus:
    def test_stopped_always_true(self, fposapi_axis_mock):
        assert fposapi_axis_mock.stopped() is True

    def test_ready_for_motion_true_when_enabled(self, fposapi_axis_mock):
        fposapi_axis_mock._client.send_command.return_value = [
            "1, IS_ENBL, 1, 0, NULL, SUCCESS"
        ]
        assert fposapi_axis_mock.ready_for_motion() is True

    def test_ready_for_motion_false_when_disabled(self, fposapi_axis_mock):
        fposapi_axis_mock._client.send_command.return_value = [
            "1, IS_ENBL, 0, 0, NULL, SUCCESS"
        ]
        assert fposapi_axis_mock.ready_for_motion() is False

    def test_ready_for_motion_sends_is_enbl(self, fposapi_axis_mock):
        fposapi_axis_mock._client.send_command.return_value = [
            "1, IS_ENBL, 1, 0, NULL, SUCCESS"
        ]
        fposapi_axis_mock.ready_for_motion()
        fposapi_axis_mock._client.send_command.assert_called_with("IS_ENBL")

    def test_is_homed_true_when_homed(self, fposapi_axis_mock):
        fposapi_axis_mock._client.send_command.return_value = [
            "1, IS_HOME, 1, 0, NULL, SUCCESS"
        ]
        assert fposapi_axis_mock.is_homed() is True

    def test_is_homed_false_when_not_homed(self, fposapi_axis_mock):
        fposapi_axis_mock._client.send_command.return_value = [
            "1, IS_HOME, 0, 0, NULL, SUCCESS"
        ]
        assert fposapi_axis_mock.is_homed() is False

    def test_is_homed_sends_is_home(self, fposapi_axis_mock):
        fposapi_axis_mock._client.send_command.return_value = [
            "1, IS_HOME, 1, 0, NULL, SUCCESS"
        ]
        fposapi_axis_mock.is_homed()
        fposapi_axis_mock._client.send_command.assert_called_with("IS_HOME")


# ---------------------------------------------------------------------------
# __repr__, __str__, __eq__, __hash__
# ---------------------------------------------------------------------------


class TestFPosAxisIdentity:
    def test_repr_contains_name_and_index(self):
        proxy, _ = _make_proxy(name="Z", index=3)
        r = repr(proxy)
        assert "Z" in r
        assert "3" in r

    def test_str_contains_name(self):
        proxy, _ = _make_proxy(name="Y", index=2)
        assert "Y" in str(proxy)

    def test_eq_same_name_and_index(self):
        client = MagicMock(spec=FPosAPIClient)
        a = FPosAxis(name="X", index=1, client=client)
        b = FPosAxis(name="X", index=1, client=client)
        assert a == b

    def test_eq_different_name(self):
        client = MagicMock(spec=FPosAPIClient)
        a = FPosAxis(name="X", index=1, client=client)
        b = FPosAxis(name="Y", index=1, client=client)
        assert a != b

    def test_eq_different_index(self):
        client = MagicMock(spec=FPosAPIClient)
        a = FPosAxis(name="X", index=1, client=client)
        b = FPosAxis(name="X", index=2, client=client)
        assert a != b

    def test_eq_non_proxy_returns_not_implemented(self):
        proxy, _ = _make_proxy()
        assert proxy.__eq__("not-a-proxy") is NotImplemented

    def test_hash_equal_proxies_match(self):
        client = MagicMock(spec=FPosAPIClient)
        a = FPosAxis(name="X", index=1, client=client)
        b = FPosAxis(name="X", index=1, client=client)
        assert hash(a) == hash(b)

    def test_hash_usable_in_set(self):
        proxy, _ = _make_proxy()
        s = {proxy}
        assert proxy in s
