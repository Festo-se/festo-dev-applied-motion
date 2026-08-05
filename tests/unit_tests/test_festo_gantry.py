"""Unit tests for Gantry.

Coverage areas
--------------
* ``Gantry.__init__`` — axes and concurrent_axes storage.

* ``Gantry.home()`` — delegates to every axis.
* ``Gantry.is_stopped()`` — returns True only when all axes report stopped.
* ``Gantry.is_ready_for_motion()`` — returns True only when all axes
  report ready.
* ``Gantry.get_status()`` — returns structured per-axis, summary, and
    backend/controller diagnostics.
* ``Gantry.get_location()`` — returns a dict keyed by axis name whose
  values come from each axis's ``_valid_position`` / ``current_position``.

No hardware or network connection required.  All tests use either the
``gantry_mock`` fixture (lightweight stub axes, no __init__ bypass needed
for Gantry itself) or construct their own stub axes inline.
"""

from unittest.mock import MagicMock

import pytest

from applied_motion.backends.fposbapi_axis import FPosBAxis
from applied_motion.backends.gantry_backend import FPosBAPIGantryBackend
from applied_motion.gantry import AxisNotFoundError, Gantry, MovementError
from applied_motion.backends.edcon_axis import EdconAxis


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stub_axis(name: str, *, move_return=True) -> EdconAxis:
    """Return a bare EdconAxis with every method that Gantry calls
    replaced by a ``MagicMock``.
    """
    axis = object.__new__(EdconAxis)
    axis.name = name
    axis.move = MagicMock(return_value=move_return)
    axis.home = MagicMock()
    axis.current_position = MagicMock(return_value=0)
    axis._valid_position = MagicMock(return_value=0.0)
    axis.get_current_axis_position = MagicMock(return_value=0.0)
    axis.is_homed = MagicMock(return_value=True)
    axis.stopped = MagicMock(return_value=True)
    axis.ready_for_motion = MagicMock(return_value=True)
    return axis


# ---------------------------------------------------------------------------
# Gantry.__init__
# ---------------------------------------------------------------------------


class TestGantryInit:
    def test_axes_stored_by_name(self, gantry_mock):
        assert "X" in gantry_mock.axes
        assert "Z" in gantry_mock.axes

    def test_axes_are_the_supplied_festo_axis_instances(self, gantry_mock):
        for name, axis in gantry_mock._stub_axes.items():
            assert gantry_mock.axes[name] is axis

    def test_concurrent_axes_defaults_to_none(self):
        axis = _make_stub_axis("X")
        g = Gantry(axes={"X": axis})
        assert g.concurrent_axes is None

    def test_concurrent_axes_stored_when_provided(self):
        axis_x = _make_stub_axis("X")
        axis_z = _make_stub_axis("Z")
        g = Gantry(
            axes={"X": axis_x},
            concurrent_axes={"Z": axis_z},
        )
        assert "Z" in g.concurrent_axes
        assert g.concurrent_axes["Z"] is axis_z

    def test_accepts_empty_axes_dict(self):
        """Gantry must not raise when initialised with zero axes, as
        callers may build the gantry incrementally or in a disabled state.
        """
        g = Gantry(axes={})
        assert g.axes == {}



# ---------------------------------------------------------------------------
# Gantry.home()
# ---------------------------------------------------------------------------


class TestGantryHome:
    def test_home_called_on_every_axis(self, gantry_mock):
        gantry_mock.home()
        for axis in gantry_mock._stub_axes.values():
            axis.home.assert_called_once()

    def test_home_called_on_all_axes_even_with_many(self):
        axes = {name: _make_stub_axis(name) for name in ["X", "Y", "Z", "W"]}
        g = Gantry(axes=axes)
        g.home()
        for axis in axes.values():
            axis.home.assert_called_once()


# ---------------------------------------------------------------------------
# Gantry.is_stopped() / is_ready_for_motion()
# ---------------------------------------------------------------------------


class TestGantryStatus:
    def test_is_stopped_true_when_all_axes_stopped(self, gantry_mock):
        for axis in gantry_mock._stub_axes.values():
            axis.stopped.return_value = True
        assert gantry_mock.is_stopped() is True

    def test_is_stopped_false_when_any_axis_still_moving(self, gantry_mock):
        axes = list(gantry_mock._stub_axes.values())
        axes[0].stopped.return_value = False  # first axis still moving
        axes[1].stopped.return_value = True
        assert gantry_mock.is_stopped() is False

    def test_is_stopped_false_when_all_axes_moving(self, gantry_mock):
        for axis in gantry_mock._stub_axes.values():
            axis.stopped.return_value = False
        assert gantry_mock.is_stopped() is False

    def test_is_ready_for_motion_true_when_all_axes_ready(self, gantry_mock):
        for axis in gantry_mock._stub_axes.values():
            axis.ready_for_motion.return_value = True
        assert gantry_mock.is_ready_for_motion() is True

    def test_is_ready_for_motion_false_when_any_axis_not_ready(self, gantry_mock):
        axes = list(gantry_mock._stub_axes.values())
        axes[0].ready_for_motion.return_value = False
        axes[1].ready_for_motion.return_value = True
        assert gantry_mock.is_ready_for_motion() is False

    def test_is_ready_for_motion_false_when_all_axes_not_ready(self, gantry_mock):
        for axis in gantry_mock._stub_axes.values():
            axis.ready_for_motion.return_value = False
        assert gantry_mock.is_ready_for_motion() is False

    def test_get_status_modbus_reports_axis_and_summary_fields(self, gantry_mock):
        for axis in gantry_mock._stub_axes.values():
            axis.get_current_axis_position.return_value = 12.34
            axis.stopped.return_value = True
            axis.ready_for_motion.return_value = True
            axis.is_homed = MagicMock(return_value=True)

        status = gantry_mock.get_status()

        assert status["backend"] == "ModbusGantryBackend"
        assert status["supports_teach"] is False
        assert set(status["axes"].keys()) == set(gantry_mock._stub_axes.keys())
        assert status["summary"]["axis_count"] == 2
        assert status["summary"]["all_homed"] is True
        assert status["summary"]["all_stopped"] is True
        assert status["summary"]["all_ready_for_motion"] is True
        assert status["summary"]["healthy"] is True
        assert status["summary"]["axis_errors"] == {}
        assert status["controller"]["sys_status"] is None

    def test_get_status_marks_axis_error_and_unhealthy_when_axis_query_fails(self, gantry_mock):
        for axis in gantry_mock._stub_axes.values():
            axis.is_homed = MagicMock(return_value=True)
        bad_axis = gantry_mock._stub_axes[next(iter(gantry_mock._stub_axes))]
        bad_axis.get_current_axis_position.side_effect = RuntimeError("position read failed")

        status = gantry_mock.get_status()

        assert status["summary"]["healthy"] is False
        assert len(status["summary"]["axis_errors"]) == 1
        first_axis = next(iter(status["summary"]["axis_errors"]))
        assert "RuntimeError" in status["summary"]["axis_errors"][first_axis]

    def test_get_status_fposbapi_includes_controller_diagnostics(self, gantry_fposbapi_mock):
        for axis in gantry_fposbapi_mock._stub_axes.values():
            axis.get_current_axis_position = MagicMock(return_value=10.0)
            axis.is_homed = MagicMock(return_value=True)
            axis.stopped = MagicMock(return_value=True)
            axis.ready_for_motion = MagicMock(return_value=True)

        client = gantry_fposbapi_mock._stub_client
        client.sys_status.return_value = "IDLE"
        client.is_error.return_value = False
        client.fpb_error.return_value = "0"
        client.read_err.return_value = "1, READ_ERR, 0, NULL, SUCCESS"

        status = gantry_fposbapi_mock.get_status()

        assert status["backend"] == "FPosBAPIGantryBackend"
        assert status["supports_teach"] is True
        assert status["controller"]["sys_status"] == "IDLE"
        assert status["controller"]["is_error"] is False
        assert status["controller"]["fpb_error"] == "0"
        assert status["controller"]["read_err"] == "1, READ_ERR, 0, NULL, SUCCESS"
        assert status["controller"]["error"] is None

    def test_get_status_fposbapi_handles_controller_query_error(self, gantry_fposbapi_mock):
        for axis in gantry_fposbapi_mock._stub_axes.values():
            axis.get_current_axis_position = MagicMock(return_value=10.0)
            axis.is_homed = MagicMock(return_value=True)
            axis.stopped = MagicMock(return_value=True)
            axis.ready_for_motion = MagicMock(return_value=True)

        client = gantry_fposbapi_mock._stub_client
        client.sys_status.side_effect = RuntimeError("PLC offline")

        status = gantry_fposbapi_mock.get_status()

        assert status["controller"]["error"] is not None
        assert "RuntimeError" in status["controller"]["error"]
        assert status["summary"]["healthy"] is False


# ---------------------------------------------------------------------------
# Gantry.get_location()
# ---------------------------------------------------------------------------


class TestGantryGetLocation:
    def test_returns_dict_keyed_by_axis_name(self, gantry_mock):
        location = gantry_mock.get_location()
        assert set(location.keys()) == set(gantry_mock._stub_axes.keys())

    def test_location_values_come_from_get_current_axis_position(self, gantry_mock):
        """get_location must delegate to get_current_axis_position on each
        axis so the unit conversion is always encapsulated in EdconAxis and
        not duplicated in Gantry.
        """
        for axis in gantry_mock._stub_axes.values():
            axis.get_current_axis_position.return_value = 123.456
        location = gantry_mock.get_location()
        for value in location.values():
            assert value == pytest.approx(123.456)

    def test_get_current_axis_position_called_for_every_axis(self, gantry_mock):
        """get_location must call get_current_axis_position on every axis
        rather than caching a stale value.
        """
        gantry_mock.get_location()
        for axis in gantry_mock._stub_axes.values():
            axis.get_current_axis_position.assert_called_once()

    def test_get_location_independent_values_per_axis(self):
        """Each axis must contribute its own position to the returned dict
        so mixed-position states are represented faithfully.
        """
        axis_x = _make_stub_axis("X")
        axis_z = _make_stub_axis("Z")
        axis_x.get_current_axis_position.return_value = 1.0
        axis_z.get_current_axis_position.return_value = 2.0
        g = Gantry(axes={"X": axis_x, "Z": axis_z})
        location = g.get_location()
        assert location["X"] == pytest.approx(1.0)
        assert location["Z"] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Error class hierarchy
# ---------------------------------------------------------------------------


class TestErrorHierarchy:
    """Verify that the custom exception classes satisfy the expected
    inheritance contract so downstream callers can catch them at the
    appropriate level of specificity.
    """

    def test_axis_not_found_error_is_movement_error(self):
        assert issubclass(AxisNotFoundError, MovementError)

    def test_movement_error_is_exception(self):
        assert issubclass(MovementError, Exception)

    def test_axis_not_found_error_can_be_caught_as_movement_error(self):
        with pytest.raises(MovementError):
            raise AxisNotFoundError("test axis missing")


# ---------------------------------------------------------------------------
# Gantry.__repr__
# ---------------------------------------------------------------------------


class TestGantryRepr:
    """Verify the __repr__ contract for Gantry instances."""

    def test_repr_contains_class_name(self):
        g = Gantry(axes={"X": _make_stub_axis("X")})
        assert "Gantry" in repr(g)

    def test_repr_contains_axis_names(self):
        g = Gantry(axes={"X": _make_stub_axis("X"), "Z": _make_stub_axis("Z")})
        r = repr(g)
        assert "X" in r
        assert "Z" in r


# ---------------------------------------------------------------------------
# Gantry.__eq__
# ---------------------------------------------------------------------------


class TestGantryEquality:
    """Verify the equality contract for Gantry instances."""

    def test_same_axes_are_equal(self):
        axis_x = _make_stub_axis("X")
        g1 = Gantry(axes={"X": axis_x})
        g2 = Gantry(axes={"X": axis_x})
        assert g1 == g2

    def test_different_axes_are_not_equal(self):
        axis_x = _make_stub_axis("X")
        axis_z = _make_stub_axis("Z")
        g1 = Gantry(axes={"X": axis_x})
        g2 = Gantry(axes={"Z": axis_z})
        assert g1 != g2

    def test_comparison_with_non_gantry_returns_not_implemented(self):
        """Comparing against a non-Gantry object should return
        NotImplemented, which Python translates to False/TypeError.
        """
        g = Gantry(axes={})
        result = g.__eq__("not a gantry")
        assert result is NotImplemented

    def test_same_axes_different_concurrent_axes_are_not_equal(self):
        axis_x = _make_stub_axis("X")
        axis_z = _make_stub_axis("Z")
        g1 = Gantry(axes={"X": axis_x}, concurrent_axes=None)
        g2 = Gantry(axes={"X": axis_x}, concurrent_axes={"Z": axis_z})
        assert g1 != g2

    def test_same_fposb_axes_different_controller_endpoints_are_not_equal(self):
        client_a = MagicMock()
        client_a.ip = "192.168.10.10"
        client_a.port = 1234
        client_b = MagicMock()
        client_b.ip = "192.168.10.11"
        client_b.port = 1234

        g1 = Gantry(
            axes={"X": FPosBAxis(name="X", index=1, client=client_a)},
            _backend=FPosBAPIGantryBackend(client_a, owns_client=False),
        )
        g2 = Gantry(
            axes={"X": FPosBAxis(name="X", index=1, client=client_b)},
            _backend=FPosBAPIGantryBackend(client_b, owns_client=False),
        )

        assert g1 != g2

    def test_same_fposb_axes_same_controller_endpoint_are_equal(self):
        client_a = MagicMock()
        client_a.ip = "192.168.10.10"
        client_a.port = 1234
        client_b = MagicMock()
        client_b.ip = "192.168.10.10"
        client_b.port = 1234

        g1 = Gantry(
            axes={"X": FPosBAxis(name="X", index=1, client=client_a)},
            _backend=FPosBAPIGantryBackend(client_a, owns_client=False),
        )
        g2 = Gantry(
            axes={"X": FPosBAxis(name="X", index=1, client=client_b)},
            _backend=FPosBAPIGantryBackend(client_b, owns_client=False),
        )

        assert g1 == g2


# ---------------------------------------------------------------------------
# Gantry.__hash__
# ---------------------------------------------------------------------------


class TestGantryHash:
    """Verify the __hash__ contract for Gantry instances."""

    def test_gantry_is_hashable(self):
        g = Gantry(axes={"X": _make_stub_axis("X")})
        assert isinstance(hash(g), int)

    def test_gantry_usable_as_dict_key(self):
        g = Gantry(axes={"X": _make_stub_axis("X")})
        d = {g: "value"}
        assert d[g] == "value"

    def test_gantry_usable_in_set(self):
        g = Gantry(axes={"X": _make_stub_axis("X")})
        s = {g}
        assert g in s


# ---------------------------------------------------------------------------
# Gantry collection protocol (__len__, __iter__, __contains__)
# ---------------------------------------------------------------------------


class TestGantryCollectionProtocol:
    """Verify the collection-like interface of Gantry."""

    def test_len_returns_number_of_axes(self):
        axes = {"X": _make_stub_axis("X"), "Z": _make_stub_axis("Z")}
        g = Gantry(axes=axes)
        assert len(g) == 2

    def test_len_empty_gantry_is_zero(self):
        g = Gantry(axes={})
        assert len(g) == 0

    def test_iter_yields_axis_names(self):
        axes = {"X": _make_stub_axis("X"), "Z": _make_stub_axis("Z")}
        g = Gantry(axes=axes)
        assert set(g) == {"X", "Z"}

    def test_contains_returns_true_for_registered_axis(self):
        g = Gantry(axes={"X": _make_stub_axis("X")})
        assert "X" in g

    def test_contains_returns_false_for_unknown_axis(self):
        g = Gantry(axes={"X": _make_stub_axis("X")})
        assert "MISSING" not in g

    def test_contains_false_for_empty_gantry(self):
        g = Gantry(axes={})
        assert "X" not in g


# ---------------------------------------------------------------------------
# Gantry teach/capability and lifecycle APIs
# ---------------------------------------------------------------------------


class TestGantryTeachCapability:
    """Verify backend capability API behavior exposed by Gantry."""

    def test_modbus_supports_teach_false(self):
        g = Gantry(axes={"X": _make_stub_axis("X")})
        assert g.supports_teach() is False

    def test_modbus_teach_pos_raises_not_implemented(self):
        g = Gantry(axes={"X": _make_stub_axis("X")})
        with pytest.raises(NotImplementedError):
            g.teach_pos(1)

    def test_modbus_teach_tray_raises_not_implemented(self):
        g = Gantry(axes={"X": _make_stub_axis("X")})
        with pytest.raises(NotImplementedError):
            g.teach_tray(1, 1)


class TestGantryLifecycle:
    """Verify Gantry close/context manager behavior."""

    def test_close_noop_for_modbus_backend(self):
        g = Gantry(axes={"X": _make_stub_axis("X")})
        g.close()
        g.close()

    def test_context_manager_returns_self_for_modbus(self):
        g = Gantry(axes={"X": _make_stub_axis("X")})
        with g as managed:
            assert managed is g
