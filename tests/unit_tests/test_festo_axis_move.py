"""Unit tests for EdconAxis.move(), EdconAxis.home(), and internal
unit-conversion helpers.

No hardware or network connection required.  ``EdconAxis`` instances are
constructed via ``object.__new__`` to bypass the hardware-dependent
``__init__``, and only the attributes actually read by each method under
test are populated.

Coverage areas
--------------
* ``EdconAxis.move()``

  - Normal path: position and velocity are validated, overshoot is
    clamped, and the result of ``position_task`` is returned.
  - Fallback values when ``_valid_position`` or ``_valid_velocity`` raise.
  - Absolute vs relative positioning controlled by the
    ``position_type`` kwarg.
  - Timeout path: a worker thread is launched, joined with the supplied
    timeout, and ``stop_motion_task`` is called only if the thread is still
    alive after the join.  The thread's return value is captured via a
    result box and returned correctly.

* ``EdconAxis.home()``

  - ``acknowledge_faults``, ``enable_powerstage``, and
    ``referencing_task`` are all called in the correct order.
"""

from threading import Event
from unittest.mock import MagicMock, call, patch

import pytest

from applied_motion.backends.edcon_axis import EdconAxis


# ---------------------------------------------------------------------------
# Helper — bare EdconAxis configured for move() tests
# ---------------------------------------------------------------------------


def _make_move_axis(
    neg_limit: int = -300_000,
    pos_limit: int = 300_000,
    current_pos: int = 0,
    system_pos_power: int = -6,
    system_vel_power: int = -3,
) -> EdconAxis:
    """Return a bare EdconAxis pre-loaded with only the state that
    ``move()`` and ``home()`` need.

    Defaults mirror the conftest fixture:
    - position scale -6 (1 µm/unit): ±300 mm stroke = ±300,000 drive units
    - velocity scale -3 (1 mm/s per drive unit): the drive moves in mm/s

    ``position_task``, ``stop_motion_task``, ``fault_string``, and
    ``current_fault_code`` are all ``MagicMock`` objects.
    ``current_position`` is a ``MagicMock`` that returns ``current_pos``.
    ``com.read_pnu`` returns the scale exponents used by the unit
    converters.
    """
    axis = object.__new__(EdconAxis)
    axis.name = "TEST"
    axis._neg_sw_limit = neg_limit
    axis._pos_sw_limit = pos_limit
    axis.input_pos_unit = {"distance": {"unit": "m", "power": 1, "power_of_ten": -3}}
    axis.input_vel_unit = {
        "distance": {"unit": "m", "power": 1, "power_of_ten": -3},
        "time": {"unit": "s", "power": -1, "power_of_ten": 1},
    }
    # Convert raw drive limits to mm using the same scale relation as _valid_position(..., invert=True).
    _scale = 10 ** (axis.input_pos_unit["distance"]["power_of_ten"] - system_pos_power)
    axis.min_position = neg_limit / _scale
    axis.max_position = pos_limit / _scale
    axis.current_position = MagicMock(return_value=current_pos)
    axis.update_inputs = MagicMock()
    axis.telegram = MagicMock()
    axis.telegram.xist_a.value = current_pos
    axis.com = MagicMock()
    axis.com.read_pnu.side_effect = lambda pnu: {
        11724: system_pos_power,
        11725: system_vel_power,
    }.get(pnu, 0)
    axis.position_task = MagicMock(return_value=True)
    axis.stop_motion_task = MagicMock()
    axis.fault_string = MagicMock(return_value="OK")
    axis.current_fault_code = MagicMock(return_value=0)
    axis.acknowledge_faults = MagicMock(return_value=True)
    axis.enable_powerstage = MagicMock(return_value=True)
    return axis


# ---------------------------------------------------------------------------
# EdconAxis.move() — normal (no-timeout) path
# ---------------------------------------------------------------------------


class TestEdconAxisMoveNoTimeout:
    """Tests for the non-timeout code path in ``EdconAxis.move()``."""

    def test_returns_position_task_result(self):
        axis = _make_move_axis()
        axis.position_task.return_value = True
        result = axis.move(position=50, velocity=100)
        assert result is True

    def test_delegates_to_position_task(self):
        """position_task must be called exactly once per move() call on the
        no-timeout path so the drive actually executes the motion."""
        axis = _make_move_axis()
        axis.move(position=50, velocity=100)
        axis.position_task.assert_called_once()

    def test_relative_position_is_default_when_position_type_omitted(self):
        """When position_type is not supplied the expression
        ``kwargs.get('position_type', 'absolute') == 'absolute'`` evaluates to
        ``True``, so the default positioning mode is **absolute** (absolute=True)."""
        axis = _make_move_axis()
        axis.move(position=50, velocity=100)  # 50 mm → 50,000 µm, within ±300,000
        _, kwargs = axis.position_task.call_args
        assert kwargs.get("absolute") is True

    def test_relative_positioning_set_by_kwarg(self):
        """Passing ``position_type='relative'`` must result in
        ``position_task`` being called with ``absolute=False``."""
        axis = _make_move_axis()
        axis.move(position=50, velocity=100, position_type="relative")  # 50 mm within stroke
        _, kwargs = axis.position_task.call_args
        assert kwargs.get("absolute") is False

    def test_non_blocking_always_false_on_no_timeout_path(self):
        """``nonblocking`` must be ``False`` on the no-timeout path so the
        caller blocks until the motion command completes before returning."""
        axis = _make_move_axis()
        axis.move(position=50, velocity=100)  # 50 mm within stroke
        _, kwargs = axis.position_task.call_args
        assert kwargs.get("nonblocking") is False

    def test_position_is_passed_through_unit_converter(self):
        """Input position (mm, power_of_ten=-3) must be scaled to the drive's
        internal unit (µm, power_of_ten=-6) before reaching position_task.

        scale = 10 ** (input_power - system_power)
              = 10 ** (-3 - -6) = 10 ** 3 = 1000

        So 50 mm × 1000 = 50,000 drive units (µm).
        """
        axis = _make_move_axis()  # default system_pos_power=-6
        axis.move(position=50, velocity=100)
        pos_arg = axis.position_task.call_args[0][0]
        assert pos_arg == 50_000

    def test_overshoot_is_clamped_before_position_task(self):
        """A position beyond the positive SW limit must be clamped by
        ``_check_overshoot`` before reaching ``position_task`` so the
        drive never receives an out-of-range target.

        Axis stroke: 0 to 10,000 µm (0 to 10 mm).  Target: 20 mm
        → 20,000 µm drive units > 10,000 µm limit → clamped to 10,000.
        """
        axis = _make_move_axis(neg_limit=0, pos_limit=10_000)  # 10 mm stroke in µm
        axis.move(position=20, velocity=100)  # 20 mm → 20,000 µm > 10,000 µm limit
        pos_arg = axis.position_task.call_args[0][0]
        assert pos_arg == 10_000

    def test_overshoot_below_neg_limit_clamped(self):
        """A position below the negative SW limit must be clamped to that limit.

        Axis stroke: 0 to 10,000 µm.  Target: -5 mm → -5,000 µm < 0 → clamped to 0.
        """
        axis = _make_move_axis(neg_limit=0, pos_limit=10_000)
        axis.move(position=-5, velocity=100)  # -5 mm → -5,000 µm < 0
        pos_arg = axis.position_task.call_args[0][0]
        assert pos_arg == 0

    def test_position_validation_failure_uses_fallback_neg_five(self):
        """When ``_valid_position`` raises, ``move()`` must fall back to
        ``-5`` (drive units) and continue executing rather than
        propagating the exception, so a single bad call does not crash the
        motion controller."""
        axis = _make_move_axis()
        read_count = [0]

        # Force only the first position-scale read to fail (the one used by
        # move() position validation); later reads must succeed so
        # _check_overshoot can still run.
        def _raise_once_for_position_scale(pnu):
            if pnu == 11724 and read_count[0] == 0:
                read_count[0] += 1
                raise RuntimeError("simulated PNU read failure")
            if pnu == 11724:
                return -6
            if pnu == 11725:
                return -3
            return 0

        axis.com.read_pnu.side_effect = _raise_once_for_position_scale
        axis.move(position=5_000, velocity=100)
        pos_arg = axis.position_task.call_args[0][0]
        assert pos_arg == -5

    def test_velocity_validation_failure_uses_fallback_five(self):
        """When ``_valid_velocity`` raises, ``move()`` must fall back to
        ``5`` (drive units) and continue rather than propagating the
        exception."""
        axis = _make_move_axis()
        read_count = [0]

        def _raise_on_velocity_pnu(pnu):
            read_count[0] += 1
            # PNU 11724 is position scale (must succeed); 11725 is velocity
            if pnu == 11725:
                raise RuntimeError("simulated PNU read failure")
            return -6  # position scale: 1 µm/unit (velocity scale is -3, mm/s, but not reached here)

        axis.com.read_pnu.side_effect = _raise_on_velocity_pnu
        axis.move(position=5_000, velocity=100)
        vel_arg = axis.position_task.call_args[0][1]
        assert vel_arg == 5

    def test_stop_motion_task_not_called_on_no_timeout_path(self):
        """stop_motion_task must NOT be called when no timeout is specified
        so the drive is not halted prematurely."""
        axis = _make_move_axis()
        axis.move(position=5_000, velocity=100)
        axis.stop_motion_task.assert_not_called()


# ---------------------------------------------------------------------------
# EdconAxis.move() — timeout path
# ---------------------------------------------------------------------------


class TestEdconAxisMoveWithTimeout:
    """Tests for the timeout code path in ``EdconAxis.move()``."""

    def test_stop_motion_task_called_after_timeout(self):
        """When a timeout is provided and the move thread does not finish
        in time, ``stop_motion_task`` must be called to halt the drive and
        prevent an axis runaway.
        """
        axis = _make_move_axis()

        # Make position_task block long enough for the join timeout to expire
        started = Event()
        blocked = Event()

        def _blocking_position_task(*args, **kwargs):
            started.set()
            blocked.wait(timeout=5)

        axis.position_task.side_effect = _blocking_position_task

        axis.move(position=5_000, velocity=100, timeout=0.01)

        blocked.set()  # unblock the background thread
        axis.stop_motion_task.assert_called_once()

    def test_stop_motion_task_not_called_when_move_completes_in_time(self):
        """When the move finishes before the timeout, ``stop_motion_task``
        must NOT be called — the axis completed normally.
        """
        axis = _make_move_axis()
        axis.position_task.return_value = True
        axis.move(position=5_000, velocity=100, timeout=5)
        axis.stop_motion_task.assert_not_called()

    def test_timeout_path_returns_result_from_thread(self):
        """When the thread finishes before the timeout, ``move()`` must
        return the boolean result from ``position_task``.
        """
        axis = _make_move_axis()
        axis.position_task.return_value = True
        result = axis.move(position=5_000, velocity=100, timeout=5)
        assert result is True

    def test_timeout_path_returns_false_when_timed_out(self):
        """When the move thread is still alive after the timeout, ``move()``
        stops the axis and returns ``False``.
        """
        axis = _make_move_axis()
        started = Event()
        blocked = Event()

        def _blocking_position_task(*args, **kwargs):
            started.set()
            blocked.wait(timeout=5)

        axis.position_task.side_effect = _blocking_position_task

        result = axis.move(position=5_000, velocity=100, timeout=0.01)
        blocked.set()
        assert result is False


# ---------------------------------------------------------------------------
# EdconAxis.home()
# ---------------------------------------------------------------------------


class TestEdconAxisHome:
    """Verify the homing sequence calls the correct MotionHandler methods
    in the required order."""

    def _make_home_axis(self) -> EdconAxis:
        axis = object.__new__(EdconAxis)
        axis.name = "TEST"
        axis.acknowledge_faults = MagicMock()
        axis.enable_powerstage = MagicMock()
        axis.referencing_task = MagicMock()
        return axis

    def test_acknowledge_faults_called(self):
        axis = self._make_home_axis()
        axis.home()
        axis.acknowledge_faults.assert_called_once()

    def test_enable_powerstage_called(self):
        axis = self._make_home_axis()
        axis.home()
        axis.enable_powerstage.assert_called_once()

    def test_referencing_task_called_blocking(self):
        """referencing_task must be called with ``nonblocking=False`` so
        the homing sequence completes before the caller continues."""
        axis = self._make_home_axis()
        axis.home()
        axis.referencing_task.assert_called_once_with(nonblocking=False)

    def test_home_call_order_is_faults_then_power_then_reference(self):
        """The homing sequence must follow the order:
        1. acknowledge_faults — clear any latched errors
        2. enable_powerstage — energise the motor
        3. referencing_task — execute the reference run

        Deviating from this order risks energising a drive that still has
        active faults, or starting a reference run on a de-energised motor.
        """
        axis = self._make_home_axis()
        call_log: list[str] = []
        axis.acknowledge_faults.side_effect = lambda: call_log.append("acknowledge_faults")
        axis.enable_powerstage.side_effect = lambda: call_log.append("enable_powerstage")
        axis.referencing_task.side_effect = lambda **kw: call_log.append("referencing_task")
        axis.home()
        assert call_log == ["acknowledge_faults", "enable_powerstage", "referencing_task"]
