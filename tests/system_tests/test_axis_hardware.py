"""System tests for EdconAxis — require a hardware drive.

Run with::

    uv run pytest tests/system_tests/ -m hardware -v

The axis under test is configured via environment variables (see
``tests/conftest.py``).  Both ``axis_a`` and ``axis_b`` fixtures are
available; tests that only need one axis use ``axis_a``.
"""

from collections import deque

import pytest


# ---------------------------------------------------------------------------
# Connectivity / initialisation
# ---------------------------------------------------------------------------


@pytest.mark.hardware
def test_axis_connects_without_fault(axis_a):
    """Axis should initialise and report no fault."""
    assert not axis_a.fault_present()


@pytest.mark.hardware
def test_axis_current_position_is_numeric(axis_a):
    """current_position() should return a numeric value after init."""
    pos = axis_a.current_position()
    assert isinstance(pos, (int, float))


@pytest.mark.hardware
def test_axis_sw_limits_are_ordered(axis_a):
    """Negative SW limit must be strictly less than positive SW limit."""
    assert axis_a._neg_sw_limit < axis_a._pos_sw_limit


# ---------------------------------------------------------------------------
# Motion — single axis
# ---------------------------------------------------------------------------

_SAFE_POSITION_MM = 50.0   # mm — adjust if the bench has a narrower range
_SAFE_VELOCITY_MM_S = 50.0  # mm/s
_POSITION_TOLERANCE_MM = 0.1  # mm
_NEG_STOP_TOLERANCE_MM = 1.0  # mm tolerance for home/negative-stop proximity checks


def _axis_position_mm(axis_a) -> float:
    """Return current axis position in mm using the backend conversion helper."""
    input_pos_unit = {"distance": {"unit": "m", "power": 1, "power_of_ten": -3}}
    return axis_a._valid_position(axis_a.current_position(), input_pos_unit, invert=True)

@pytest.mark.hardware
def test_axis_homes(axis_a):
    """move() should return a truthy result on success."""
    result = axis_a.home()
    assert result

@pytest.mark.hardware
def test_axis_move_returns_truthy(axis_a):
    """move() should return a truthy result on success."""
    result = axis_a.move(_SAFE_POSITION_MM, _SAFE_VELOCITY_MM_S)
    assert result


@pytest.mark.hardware
def test_axis_move_reaches_target(axis_a):
    """Axis should reach the requested position within tolerance."""
    axis_a.move(_SAFE_POSITION_MM, _SAFE_VELOCITY_MM_S)
    actual_mm = _axis_position_mm(axis_a)
    assert abs(actual_mm - _SAFE_POSITION_MM) <= _POSITION_TOLERANCE_MM


@pytest.mark.hardware
def test_axis_is_stopped_after_move(axis_a):
    """Axis should report stopped after a blocking move completes."""
    axis_a.move(_SAFE_POSITION_MM, _SAFE_VELOCITY_MM_S)
    assert axis_a.stopped()


@pytest.mark.hardware
def test_axis_is_ready_for_motion_after_move(axis_a):
    """Axis should be ready for motion after a completed move."""
    axis_a.move(_SAFE_POSITION_MM, _SAFE_VELOCITY_MM_S)
    assert axis_a.ready_for_motion()


@pytest.mark.hardware
def test_axis_home_is_near_negative_stop(axis_a):
    """After homing, axis position should be at/near configured min_position.

    This verifies the practical "negative stop" registration used by the
    backend: homing plus configured software minimum.
    """
    axis_a.home()
    actual_mm = _axis_position_mm(axis_a)
    assert abs(actual_mm - axis_a.min_position) <= _NEG_STOP_TOLERANCE_MM, (
        f"Home did not land near negative stop: position={actual_mm:.3f} mm, "
        f"min_position={axis_a.min_position:.3f} mm"
    )


@pytest.mark.hardware
def test_axis_move_below_negative_stop_clamps_to_min(axis_a):
    """Requesting a target far below min_position should clamp at/above min_position."""
    axis_a.home()
    far_below = axis_a.min_position - 100.0
    axis_a.move(far_below, _SAFE_VELOCITY_MM_S)
    actual_mm = _axis_position_mm(axis_a)
    assert actual_mm >= axis_a.min_position - _NEG_STOP_TOLERANCE_MM, (
        f"Axis moved below configured negative stop: position={actual_mm:.3f} mm, "
        f"min_position={axis_a.min_position:.3f} mm"
    )
