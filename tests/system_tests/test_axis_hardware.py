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

_SAFE_POSITION_MM = 10.0   # mm — adjust if the bench has a narrower range
_SAFE_VELOCITY_MM_S = 20.0  # mm/s
_POSITION_TOLERANCE_MM = 1.0  # mm


@pytest.mark.hardware
def test_axis_move_returns_truthy(axis_a):
    """move() should return a truthy result on success."""
    result = axis_a.move(_SAFE_POSITION_MM, _SAFE_VELOCITY_MM_S)
    assert result


@pytest.mark.hardware
def test_axis_move_reaches_target(axis_a):
    """Axis should reach the requested position within tolerance."""
    axis_a.move(_SAFE_POSITION_MM, _SAFE_VELOCITY_MM_S)
    input_pos_unit = {"distance": {"unit": "m", "power": 1, "power_of_ten": -3}}
    actual_mm = axis_a._valid_position(axis_a.current_position(), input_pos_unit, invert=True)
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
def test_axis_move_clamps_at_sw_limit(axis_a):
    """Requesting a position far beyond the positive SW limit should not raise
    and should leave the axis at or below the positive limit."""
    far_beyond = axis_a._pos_sw_limit * 10
    axis_a.move(far_beyond, _SAFE_VELOCITY_MM_S)
    assert axis_a.current_position() <= axis_a._pos_sw_limit
