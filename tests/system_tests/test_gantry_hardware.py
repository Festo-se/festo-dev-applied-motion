"""System tests for Gantry — require two hardware drives.

Run with::

    uv run pytest tests/system_tests/ -m hardware -v

The gantry under test is configured via environment variables (see
``tests/conftest.py``).
"""

from collections import deque

import pytest


# ---------------------------------------------------------------------------
# State queries
# ---------------------------------------------------------------------------


@pytest.mark.hardware
def test_gantry_get_location_returns_all_axes(gantry):
    """get_location() should return a coordinate for every configured axis."""
    location = gantry.get_location()
    assert set(location.keys()) == set(gantry.axes.keys())


@pytest.mark.hardware
def test_gantry_get_location_values_are_numeric(gantry):
    """Every coordinate returned by get_location() should be numeric."""
    for value in gantry.get_location().values():
        assert isinstance(value, (int, float))


@pytest.mark.hardware
def test_gantry_is_stopped_after_init(gantry):
    """Gantry should report all axes stopped when no motion is queued."""
    assert gantry.is_stopped()


@pytest.mark.hardware
def test_gantry_is_ready_for_motion_after_init(gantry):
    """All axes should be ready for motion after initialisation."""
    assert gantry.is_ready_for_motion()


# ---------------------------------------------------------------------------
# Sequential move_to
# ---------------------------------------------------------------------------

_SAFE_POSITION_MM = 10.0
_SAFE_VELOCITY_MM_S = 20.0


@pytest.mark.hardware
def test_gantry_move_to_single_axis(gantry):
    """move_to with one axis movement should complete without raising."""
    axis_name = next(iter(gantry.axes))
    movements = deque([{axis_name: {"position": _SAFE_POSITION_MM, "velocity": _SAFE_VELOCITY_MM_S}}])
    gantry.move_to(movements)


@pytest.mark.hardware
def test_gantry_move_to_all_axes_sequential(gantry):
    """move_to with one movement per axis (sequential) should complete without raising."""
    movements = deque(
        [{name: {"position": _SAFE_POSITION_MM, "velocity": _SAFE_VELOCITY_MM_S}} for name in gantry.axes]
    )
    gantry.move_to(movements)


@pytest.mark.hardware
def test_gantry_is_stopped_after_move_to(gantry):
    """All axes should report stopped after a completed move_to."""
    axis_name = next(iter(gantry.axes))
    movements = deque([{axis_name: {"position": _SAFE_POSITION_MM, "velocity": _SAFE_VELOCITY_MM_S}}])
    gantry.move_to(movements)
    assert gantry.is_stopped()


# ---------------------------------------------------------------------------
# Unknown axis error handling
# ---------------------------------------------------------------------------


@pytest.mark.hardware
def test_gantry_move_to_unknown_axis_raises(gantry):
    """move_to with an axis name not in the gantry should raise AxisNotFoundError."""
    from festo_dev_applied_motion.gantry import AxisNotFoundError

    movements = deque([{"__nonexistent__": {"position": _SAFE_POSITION_MM, "velocity": _SAFE_VELOCITY_MM_S}}])
    with pytest.raises(AxisNotFoundError):
        gantry.move_to(movements)
