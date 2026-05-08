"""System tests for Gantry — require two hardware drives.

Run with::

    uv run pytest tests/system_tests/ -m hardware -v

The gantry under test is configured via environment variables (see
``tests/conftest.py``).
"""

from collections import deque

import pytest

from applied_motion.backends.fposbapi_client import FPosBAPIClientError


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
    from applied_motion.gantry import AxisNotFoundError

    movements = deque([{"__nonexistent__": {"position": _SAFE_POSITION_MM, "velocity": _SAFE_VELOCITY_MM_S}}])
    with pytest.raises(AxisNotFoundError):
        gantry.move_to(movements)


# ---------------------------------------------------------------------------
# FPosBAPI backend — connection validation
# ---------------------------------------------------------------------------


@pytest.mark.hardware
def test_fposbapi_gantry_connects_and_has_correct_axes(gantry_fposbapi):
    """from_config with the FPosBAPI spec should yield a Gantry with X, Y, Z axes."""
    assert set(gantry_fposbapi.axes.keys()) == {"X", "Y", "Z"}


@pytest.mark.hardware
def test_fposbapi_gantry_get_location_returns_all_axes(gantry_fposbapi):
    """get_location() over a live FPosBAPI connection should return X, Y, Z coordinates."""
    location = gantry_fposbapi.get_location()
    assert set(location.keys()) == {"X", "Y", "Z"}


@pytest.mark.hardware
def test_fposbapi_gantry_get_location_values_are_numeric(gantry_fposbapi):
    """Every coordinate returned by get_location() over FPosBAPI should be numeric."""
    for value in gantry_fposbapi.get_location().values():
        assert isinstance(value, (int, float))


@pytest.mark.hardware
def test_fposbapi_gantry_is_stopped_after_connect(gantry_fposbapi):
    """Gantry should report all axes stopped immediately after connecting via FPosBAPI."""
    assert gantry_fposbapi.is_stopped()


@pytest.mark.hardware
def test_fposbapi_gantry_is_ready_for_motion_after_connect(gantry_fposbapi):
    """All axes should be ready for motion immediately after connecting via FPosBAPI."""
    assert gantry_fposbapi.is_ready_for_motion()


# ---------------------------------------------------------------------------
# FPosBAPI backend — diagnostic queries
# ---------------------------------------------------------------------------


@pytest.mark.hardware
def test_fposbapi_sys_status_responds(gantry_fposbapi):
    """SYS_STATUS should return a SUCCESS response."""
    response = gantry_fposbapi._client.send_command("SYS_STATUS")
    assert "SUCCESS" in response[-1], f"Unexpected SYS_STATUS response: {response!r}"


@pytest.mark.hardware
def test_fposbapi_is_homed_responds(gantry_fposbapi):
    """IS_HOME should return a SUCCESS response and expose the homed state."""
    response = gantry_fposbapi._client.send_command("IS_HOME")
    assert "SUCCESS" in response[-1], f"Unexpected IS_HOME response: {response!r}"


@pytest.mark.hardware
def test_fposbapi_fpb_error_is_clear(gantry_fposbapi):
    """FPB_ERROR should return SUCCESS with no active fieldbus errors."""
    response = gantry_fposbapi._client.send_command("FPB_ERROR")
    assert "SUCCESS" in response[-1], f"Unexpected FPB_ERROR response: {response!r}"


# ---------------------------------------------------------------------------
# FPosBAPI backend — parameter queries (GET_PAR)
# ---------------------------------------------------------------------------

# Soft limit parameter IDs per API Rev 5 (values returned by GET_PAR are in mm)
_SOFT_LIMIT_PARAMS = {
    "X": (107, 108),
    "Y": (109, 110),
    "Z": (111, 112),
}

_FPOSBAPI_MOTION_VELOCITY_MM_S = 20.0


def _get_par_value(client, par_id: int) -> float:
    """Read one parameter from the PLC via GET_PAR and return its first value."""
    values = client.get_par(par_id)
    if not values:
        raise RuntimeError(f"GET_PAR {par_id} returned no values")
    return values[0]


def _soft_limits_mm(client, axis_name: str) -> tuple[float, float]:
    """Return (min_mm, max_mm) soft limits for *axis_name* (GET_PAR returns values in mm)."""
    min_par, max_par = _SOFT_LIMIT_PARAMS[axis_name]
    return _get_par_value(client, min_par), _get_par_value(client, max_par)


def _safe_target_mm(current_mm: float, min_mm: float, max_mm: float, delta_mm: float = 5.0) -> float:
    """Return a safe absolute target within soft limits.

    Tries ``current + delta``, then ``current - delta``, then the midpoint.
    Keeps a 2 mm margin away from each limit.
    """
    margin = 2.0
    lo, hi = min_mm + margin, max_mm - margin
    if hi - lo < 1.0:
        return (lo + hi) / 2.0
    for candidate in (current_mm + delta_mm, current_mm - delta_mm, (lo + hi) / 2.0):
        if lo <= candidate <= hi:
            return candidate
    return (lo + hi) / 2.0


@pytest.mark.hardware
def test_fposbapi_get_par_x_soft_limits_valid(gantry_fposbapi):
    """GET_PAR should return valid (min < max) soft limits for X axis."""
    min_mm, max_mm = _soft_limits_mm(gantry_fposbapi._client, "X")
    assert max_mm > min_mm, f"X soft limits invalid: min={min_mm:.3f} mm, max={max_mm:.3f} mm"


@pytest.mark.hardware
def test_fposbapi_get_par_y_soft_limits_valid(gantry_fposbapi):
    """GET_PAR should return valid (min < max) soft limits for Y axis."""
    min_mm, max_mm = _soft_limits_mm(gantry_fposbapi._client, "Y")
    assert max_mm > min_mm, f"Y soft limits invalid: min={min_mm:.3f} mm, max={max_mm:.3f} mm"


@pytest.mark.hardware
def test_fposbapi_get_par_z_soft_limits_valid(gantry_fposbapi):
    """GET_PAR should return valid (min < max) soft limits for Z axis."""
    min_mm, max_mm = _soft_limits_mm(gantry_fposbapi._client, "Z")
    assert max_mm > min_mm, f"Z soft limits invalid: min={min_mm:.3f} mm, max={max_mm:.3f} mm"


@pytest.mark.hardware
def test_fposbapi_get_par_gantry_speed_is_positive(gantry_fposbapi):
    """GET_PAR 103 (gantry speed) should return a positive value in mm/s."""
    speed = _get_par_value(gantry_fposbapi._client, 103)
    assert speed > 0, f"Gantry speed (param 103) is not positive: {speed}"


@pytest.mark.hardware
def test_fposbapi_is_enbl_responds(gantry_fposbapi):
    """IS_ENBL should return a SUCCESS response confirming drive enable state."""
    response = gantry_fposbapi._client.send_command("IS_ENBL")
    assert "SUCCESS" in response[-1], f"Unexpected IS_ENBL response: {response!r}"


# ---------------------------------------------------------------------------
# FPosBAPI backend — single-axis motion via gantry.move_to()
# ---------------------------------------------------------------------------


@pytest.mark.hardware
def test_fposbapi_move_to_x_axis_completes(gantry_fposbapi):
    """gantry.move_to() on X axis should complete without raising."""
    client = gantry_fposbapi._client
    min_mm, max_mm = _soft_limits_mm(client, "X")
    current = gantry_fposbapi.axes["X"].get_current_axis_position()
    target = _safe_target_mm(current, min_mm, max_mm)
    movements = deque([{"X": {"position": target, "velocity": _FPOSBAPI_MOTION_VELOCITY_MM_S}}])
    gantry_fposbapi.move_to(movements)


@pytest.mark.hardware
def test_fposbapi_move_to_x_axis_reaches_target(gantry_fposbapi):
    """After gantry.move_to(), X axis position should be within 1 mm of the commanded target."""
    client = gantry_fposbapi._client
    min_mm, max_mm = _soft_limits_mm(client, "X")
    target = (min_mm + max_mm) / 2.0
    movements = deque([{"X": {"position": target, "velocity": _FPOSBAPI_MOTION_VELOCITY_MM_S}}])
    gantry_fposbapi.move_to(movements)
    actual = gantry_fposbapi.axes["X"].get_current_axis_position()
    assert abs(actual - target) < 1.0, f"X axis missed target: commanded={target:.3f} mm, got={actual:.3f} mm"


@pytest.mark.hardware
def test_fposbapi_move_to_z_axis_completes(gantry_fposbapi):
    """gantry.move_to() on Z axis should complete without raising."""
    client = gantry_fposbapi._client
    min_mm, max_mm = _soft_limits_mm(client, "Z")
    current = gantry_fposbapi.axes["Z"].get_current_axis_position()
    target = _safe_target_mm(current, min_mm, max_mm)
    movements = deque([{"Z": {"position": target, "velocity": _FPOSBAPI_MOTION_VELOCITY_MM_S}}])
    gantry_fposbapi.move_to(movements)


@pytest.mark.hardware
def test_fposbapi_move_to_z_axis_reaches_target(gantry_fposbapi):
    """After gantry.move_to(), Z axis position should be within 1 mm of the commanded target."""
    client = gantry_fposbapi._client
    min_mm, max_mm = _soft_limits_mm(client, "Z")
    target = (min_mm + max_mm) / 2.0
    movements = deque([{"Z": {"position": target, "velocity": _FPOSBAPI_MOTION_VELOCITY_MM_S}}])
    gantry_fposbapi.move_to(movements)
    actual = gantry_fposbapi.axes["Z"].get_current_axis_position()
    assert abs(actual - target) < 1.0, f"Z axis missed target: commanded={target:.3f} mm, got={actual:.3f} mm"


@pytest.mark.hardware
def test_fposbapi_is_stopped_after_move_to(gantry_fposbapi):
    """is_stopped() should return True after a completed gantry.move_to()."""
    client = gantry_fposbapi._client
    min_mm, max_mm = _soft_limits_mm(client, "X")
    current = gantry_fposbapi.axes["X"].get_current_axis_position()
    target = _safe_target_mm(current, min_mm, max_mm)
    movements = deque([{"X": {"position": target, "velocity": _FPOSBAPI_MOTION_VELOCITY_MM_S}}])
    gantry_fposbapi.move_to(movements)
    assert gantry_fposbapi.is_stopped()


@pytest.mark.hardware
def test_fposbapi_move_to_relative_x(gantry_fposbapi):
    """A relative move via gantry.move_to() should displace X by the commanded delta."""
    client = gantry_fposbapi._client
    min_mm, max_mm = _soft_limits_mm(client, "X")
    current = gantry_fposbapi.axes["X"].get_current_axis_position()
    margin = 2.0
    delta = 5.0
    if current + delta > max_mm - margin:
        delta = -5.0
    if current + delta < min_mm + margin:
        pytest.skip(f"Not enough room for a {delta:+.0f} mm relative X move within limits [{min_mm:.1f}, {max_mm:.1f}]")
    expected = current + delta
    movements = deque([{"X": {"position": delta, "velocity": _FPOSBAPI_MOTION_VELOCITY_MM_S, "position_type": "relative"}}])
    gantry_fposbapi.move_to(movements)
    actual = gantry_fposbapi.axes["X"].get_current_axis_position()
    assert abs(actual - expected) < 1.0, f"Relative move error: expected={expected:.3f} mm, got={actual:.3f} mm"


# ---------------------------------------------------------------------------
# FPosBAPI backend — direct MOVE_AXIS command (per API Rev 5 spec)
# ---------------------------------------------------------------------------


@pytest.mark.hardware
def test_fposbapi_move_axis_command_absolute(gantry_fposbapi):
    """Direct MOVE_AXIS command (absolute, MOVE_TYP=0) should return SUCCESS."""
    client = gantry_fposbapi._client
    min_mm, max_mm = _soft_limits_mm(client, "X")
    current = gantry_fposbapi.axes["X"].get_current_axis_position()
    target = _safe_target_mm(current, min_mm, max_mm)
    client.send_command("SET_PAR", 103, _FPOSBAPI_MOTION_VELOCITY_MM_S)
    # MOVE_AXIS, AXIS_ID=1 (X), MOVE_TYP=0 (absolute), VALUE=target
    response = client.send_command("MOVE_AXIS", 1, 0, target)
    assert "SUCCESS" in response[-1], f"MOVE_AXIS response: {response!r}"


@pytest.mark.hardware
def test_fposbapi_move_axis_absolute_position_echoed_in_response(gantry_fposbapi):
    """MOVE_AXIS response should echo ABS_POS matching the commanded target within 1 mm."""
    client = gantry_fposbapi._client
    min_mm, max_mm = _soft_limits_mm(client, "X")
    target = (min_mm + max_mm) / 2.0
    client.send_command("SET_PAR", 103, _FPOSBAPI_MOTION_VELOCITY_MM_S)
    response = client.send_command("MOVE_AXIS", 1, 0, target)
    # Terminal line: MSG_ID, MOVE_AXIS, AXIS_ID, MOVE_TYP, VALUE, ABS_POS, 0, NULL, SUCCESS
    fields = [f.strip() for f in response[-1].split(",")]
    abs_pos = float(fields[5])
    assert abs(abs_pos - target) < 1.0, f"MOVE_AXIS ABS_POS={abs_pos:.3f} mm, expected ~{target:.3f} mm"


@pytest.mark.hardware
def test_fposbapi_move_axis_command_relative(gantry_fposbapi):
    """Direct MOVE_AXIS command (relative, MOVE_TYP=1) should return SUCCESS."""
    client = gantry_fposbapi._client
    min_mm, max_mm = _soft_limits_mm(client, "X")
    current = gantry_fposbapi.axes["X"].get_current_axis_position()
    margin = 2.0
    delta = 3.0
    if current + delta > max_mm - margin:
        delta = -3.0
    if current + delta < min_mm + margin:
        pytest.skip(f"Not enough room for a {delta:+.0f} mm relative X move")
    client.send_command("SET_PAR", 103, _FPOSBAPI_MOTION_VELOCITY_MM_S)
    # MOVE_AXIS, AXIS_ID=1 (X), MOVE_TYP=1 (relative), VALUE=delta
    response = client.send_command("MOVE_AXIS", 1, 1, delta)
    assert "SUCCESS" in response[-1], f"Relative MOVE_AXIS response: {response!r}"


# ---------------------------------------------------------------------------
# FPosBAPI backend — coordinated multi-axis motion (MOVE_LOC)
# ---------------------------------------------------------------------------


@pytest.mark.hardware
def test_fposbapi_move_loc_all_axes_completes(gantry_fposbapi):
    """MOVE_LOC should move all three axes to explicit coordinates and return SUCCESS."""
    client = gantry_fposbapi._client
    x_min, x_max = _soft_limits_mm(client, "X")
    y_min, y_max = _soft_limits_mm(client, "Y")
    z_min, z_max = _soft_limits_mm(client, "Z")
    # Quarter-range positions — well within limits
    x_t = x_min + (x_max - x_min) * 0.25
    y_t = y_min + (y_max - y_min) * 0.25
    z_t = z_min + (z_max - z_min) * 0.25
    client.send_command("SET_PAR", 103, _FPOSBAPI_MOTION_VELOCITY_MM_S)
    # MOVE_LOC: A1, A2, A3, TOOL_ID=0, MOVE_TYP=0 (absolute), RETRACT_Z=0, SLOW_APP=0
    response = client.send_command("MOVE_LOC", x_t, y_t, z_t, 0, 0, 0, 0)
    assert "SUCCESS" in response[-1], f"MOVE_LOC response: {response!r}"


@pytest.mark.hardware
def test_fposbapi_move_loc_all_axes_position_verified(gantry_fposbapi):
    """After MOVE_LOC, ROB_POS should show all three axes within 1 mm of commanded targets."""
    client = gantry_fposbapi._client
    x_min, x_max = _soft_limits_mm(client, "X")
    y_min, y_max = _soft_limits_mm(client, "Y")
    z_min, z_max = _soft_limits_mm(client, "Z")
    x_t = x_min + (x_max - x_min) * 0.75
    y_t = y_min + (y_max - y_min) * 0.75
    z_t = z_min + (z_max - z_min) * 0.75
    client.send_command("SET_PAR", 103, _FPOSBAPI_MOTION_VELOCITY_MM_S)
    client.send_command("MOVE_LOC", x_t, y_t, z_t, 0, 0, 0, 0)
    location = gantry_fposbapi.get_location()
    for axis, target in (("X", x_t), ("Y", y_t), ("Z", z_t)):
        assert abs(location[axis] - target) < 1.0, (
            f"{axis} missed target after MOVE_LOC: commanded={target:.3f} mm, got={location[axis]:.3f} mm"
        )


# ---------------------------------------------------------------------------
# FPosBAPI backend — Y-axis motion via gantry.move_to()
# ---------------------------------------------------------------------------


@pytest.mark.hardware
def test_fposbapi_move_to_y_axis_completes(gantry_fposbapi):
    """gantry.move_to() on Y axis should complete without raising."""
    client = gantry_fposbapi._client
    min_mm, max_mm = _soft_limits_mm(client, "Y")
    current = gantry_fposbapi.axes["Y"].get_current_axis_position()
    target = _safe_target_mm(current, min_mm, max_mm)
    movements = deque([{"Y": {"position": target, "velocity": _FPOSBAPI_MOTION_VELOCITY_MM_S}}])
    gantry_fposbapi.move_to(movements)


@pytest.mark.hardware
def test_fposbapi_move_to_y_axis_reaches_target(gantry_fposbapi):
    """After gantry.move_to(), Y axis position should be within 1 mm of the commanded target."""
    client = gantry_fposbapi._client
    min_mm, max_mm = _soft_limits_mm(client, "Y")
    target = (min_mm + max_mm) / 2.0
    movements = deque([{"Y": {"position": target, "velocity": _FPOSBAPI_MOTION_VELOCITY_MM_S}}])
    gantry_fposbapi.move_to(movements)
    actual = gantry_fposbapi.axes["Y"].get_current_axis_position()
    assert abs(actual - target) < 1.0, f"Y axis missed target: commanded={target:.3f} mm, got={actual:.3f} mm"


# ---------------------------------------------------------------------------
# FPosBAPI backend — all-axis sequential via gantry.move_to()
# ---------------------------------------------------------------------------


@pytest.mark.hardware
def test_fposbapi_move_to_all_axes_sequential_completes(gantry_fposbapi):
    """Queuing X, Y, Z as separate deque entries through gantry.move_to() should complete."""
    client = gantry_fposbapi._client
    targets = {}
    for axis in ("X", "Y", "Z"):
        min_mm, max_mm = _soft_limits_mm(client, axis)
        current = gantry_fposbapi.axes[axis].get_current_axis_position()
        targets[axis] = _safe_target_mm(current, min_mm, max_mm)
    movements = deque(
        [{axis: {"position": targets[axis], "velocity": _FPOSBAPI_MOTION_VELOCITY_MM_S}}
         for axis in ("X", "Y", "Z")]
    )
    gantry_fposbapi.move_to(movements)


@pytest.mark.hardware
def test_fposbapi_move_to_all_axes_sequential_positions_verified(gantry_fposbapi):
    """After sequential X → Y → Z move_to(), all axes should be within 1 mm of their targets."""
    client = gantry_fposbapi._client
    targets = {}
    for axis in ("X", "Y", "Z"):
        min_mm, max_mm = _soft_limits_mm(client, axis)
        targets[axis] = (min_mm + max_mm) / 2.0
    movements = deque(
        [{axis: {"position": targets[axis], "velocity": _FPOSBAPI_MOTION_VELOCITY_MM_S}}
         for axis in ("X", "Y", "Z")]
    )
    gantry_fposbapi.move_to(movements)
    location = gantry_fposbapi.get_location()
    for axis, target in targets.items():
        assert abs(location[axis] - target) < 1.0, (
            f"{axis} missed target after sequential move_to: commanded={target:.3f} mm, got={location[axis]:.3f} mm"
        )


# ---------------------------------------------------------------------------
# FPosBAPI backend — Python method wrappers exercised on real hardware
# ---------------------------------------------------------------------------


@pytest.mark.hardware
def test_fposbapi_axis_is_homed_returns_bool(gantry_fposbapi):
    """FPosBAxis.is_homed() should return a bool without raising against the real PLC."""
    result = gantry_fposbapi.axes["X"].is_homed()
    assert isinstance(result, bool)


@pytest.mark.hardware
def test_fposbapi_axis_is_homed_true_after_home(gantry_fposbapi):
    """FPosBAxis.is_homed() should return True after the gantry has been homed."""
    gantry_fposbapi._client.send_command("HOME")
    assert gantry_fposbapi.axes["X"].is_homed(), (
        "FPosBAxis.is_homed() returned False immediately after issuing HOME"
    )


@pytest.mark.hardware
def test_fposbapi_axis_ready_for_motion_returns_bool(gantry_fposbapi):
    """FPosBAxis.ready_for_motion() should return a bool without raising against the real PLC."""
    result = gantry_fposbapi.axes["X"].ready_for_motion()
    assert isinstance(result, bool)


@pytest.mark.hardware
def test_fposbapi_axis_ready_for_motion_true_when_enabled(gantry_fposbapi):
    """FPosBAxis.ready_for_motion() should return True when drives are enabled."""
    assert gantry_fposbapi.axes["X"].ready_for_motion(), (
        "FPosBAxis.ready_for_motion() returned False — drives may not be enabled"
    )


@pytest.mark.hardware
def test_fposbapi_all_axes_ready_for_motion(gantry_fposbapi):
    """Every axis proxy's ready_for_motion() should agree with gantry.is_ready_for_motion()."""
    per_axis = {name: axis.ready_for_motion() for name, axis in gantry_fposbapi.axes.items()}
    gantry_level = gantry_fposbapi.is_ready_for_motion()
    assert all(per_axis.values()) == gantry_level, (
        f"Per-axis ready_for_motion: {per_axis}, gantry.is_ready_for_motion(): {gantry_level}"
    )


# ---------------------------------------------------------------------------
# FPosBAPI backend — additional parameter queries
# ---------------------------------------------------------------------------


@pytest.mark.hardware
def test_fposbapi_get_par_gantry_accel_is_nonzero(gantry_fposbapi):
    """GET_PAR 102 (gantry acceleration) should return a non-zero value in mm/s²."""
    accel = _get_par_value(gantry_fposbapi._client, 102)
    assert accel != 0, f"Gantry acceleration (param 102) is zero — drive may not be configured"


@pytest.mark.hardware
def test_fposbapi_get_par_gantry_decel_is_nonzero(gantry_fposbapi):
    """GET_PAR 104 (gantry deceleration) should return a non-zero value in mm/s²."""
    decel = _get_par_value(gantry_fposbapi._client, 104)
    assert decel != 0, f"Gantry deceleration (param 104) is zero — drive may not be configured"


# ---------------------------------------------------------------------------
# FPosBAPI backend — position repeatability
# ---------------------------------------------------------------------------

_REPEATABILITY_TOLERANCE_MM = 0.5


@pytest.mark.hardware
def test_fposbapi_x_axis_position_repeatability(gantry_fposbapi):
    """Moving X to the same absolute target twice should land within 0.5 mm both times."""
    client = gantry_fposbapi._client
    min_mm, max_mm = _soft_limits_mm(client, "X")
    target = (min_mm + max_mm) / 2.0
    away = _safe_target_mm(target, min_mm, max_mm, delta_mm=10.0)

    # First approach
    movements = deque([{"X": {"position": target, "velocity": _FPOSBAPI_MOTION_VELOCITY_MM_S}}])
    gantry_fposbapi.move_to(movements)
    pos1 = gantry_fposbapi.axes["X"].get_current_axis_position()

    # Move away
    movements = deque([{"X": {"position": away, "velocity": _FPOSBAPI_MOTION_VELOCITY_MM_S}}])
    gantry_fposbapi.move_to(movements)

    # Second approach
    movements = deque([{"X": {"position": target, "velocity": _FPOSBAPI_MOTION_VELOCITY_MM_S}}])
    gantry_fposbapi.move_to(movements)
    pos2 = gantry_fposbapi.axes["X"].get_current_axis_position()

    assert abs(pos1 - pos2) <= _REPEATABILITY_TOLERANCE_MM, (
        f"X axis repeatability failure: first approach={pos1:.3f} mm, "
        f"second approach={pos2:.3f} mm, delta={abs(pos1-pos2):.3f} mm "
        f"(tolerance={_REPEATABILITY_TOLERANCE_MM} mm)"
    )


@pytest.mark.hardware
def test_fposbapi_y_axis_position_repeatability(gantry_fposbapi):
    """Moving Y to the same absolute target twice should land within 0.5 mm both times."""
    client = gantry_fposbapi._client
    min_mm, max_mm = _soft_limits_mm(client, "Y")
    target = (min_mm + max_mm) / 2.0
    away = _safe_target_mm(target, min_mm, max_mm, delta_mm=10.0)

    movements = deque([{"Y": {"position": target, "velocity": _FPOSBAPI_MOTION_VELOCITY_MM_S}}])
    gantry_fposbapi.move_to(movements)
    pos1 = gantry_fposbapi.axes["Y"].get_current_axis_position()

    movements = deque([{"Y": {"position": away, "velocity": _FPOSBAPI_MOTION_VELOCITY_MM_S}}])
    gantry_fposbapi.move_to(movements)

    movements = deque([{"Y": {"position": target, "velocity": _FPOSBAPI_MOTION_VELOCITY_MM_S}}])
    gantry_fposbapi.move_to(movements)
    pos2 = gantry_fposbapi.axes["Y"].get_current_axis_position()

    assert abs(pos1 - pos2) <= _REPEATABILITY_TOLERANCE_MM, (
        f"Y axis repeatability failure: first approach={pos1:.3f} mm, "
        f"second approach={pos2:.3f} mm, delta={abs(pos1-pos2):.3f} mm"
    )


# ---------------------------------------------------------------------------
# FPosBAPI backend — full XYZ forward/back cycle
# ---------------------------------------------------------------------------


@pytest.mark.hardware
def test_fposbapi_xyz_forward_and_back_cycle(gantry_fposbapi):
    """Move all three axes through a forward/back cycle, verifying positions at both ends.

    Per-axis travel fractions keep Z motion small and X/Y clearly offset from home:
        X: forward=70 %, backward=30 %
        Y: forward=75 %, backward=25 %
        Z: forward=25 %, backward=10 %  (Z travel intentionally limited)
    """
    # (forward_fraction, backward_fraction) per axis
    _CYCLE_FRACTIONS: dict[str, tuple[float, float]] = {
        "X": (0.70, 0.30),
        "Y": (0.75, 0.25),
        "Z": (0.25, 0.10),
    }

    client = gantry_fposbapi._client
    targets: dict[str, tuple[float, float]] = {}
    for axis in ("X", "Y", "Z"):
        min_mm, max_mm = _soft_limits_mm(client, axis)
        fwd_frac, bwd_frac = _CYCLE_FRACTIONS[axis]
        forward = min_mm + (max_mm - min_mm) * fwd_frac
        backward = min_mm + (max_mm - min_mm) * bwd_frac
        targets[axis] = (forward, backward)

    # --- forward leg: X → Y → Z ---
    for axis in ("X", "Y", "Z"):
        forward_target, _ = targets[axis]
        movements = deque([{axis: {"position": forward_target, "velocity": _FPOSBAPI_MOTION_VELOCITY_MM_S}}])
        gantry_fposbapi.move_to(movements)

    forward_positions = {axis: gantry_fposbapi.axes[axis].get_current_axis_position() for axis in ("X", "Y", "Z")}
    for axis in ("X", "Y", "Z"):
        forward_target, _ = targets[axis]
        assert abs(forward_positions[axis] - forward_target) < 1.0, (
            f"{axis} missed forward target: commanded={forward_target:.3f} mm, "
            f"got={forward_positions[axis]:.3f} mm"
        )

    # --- backward leg: X → Y → Z ---
    for axis in ("X", "Y", "Z"):
        _, backward_target = targets[axis]
        movements = deque([{axis: {"position": backward_target, "velocity": _FPOSBAPI_MOTION_VELOCITY_MM_S}}])
        gantry_fposbapi.move_to(movements)

    backward_positions = {axis: gantry_fposbapi.axes[axis].get_current_axis_position() for axis in ("X", "Y", "Z")}
    for axis in ("X", "Y", "Z"):
        _, backward_target = targets[axis]
        assert abs(backward_positions[axis] - backward_target) < 1.0, (
            f"{axis} missed backward target: commanded={backward_target:.3f} mm, "
            f"got={backward_positions[axis]:.3f} mm"
        )


# ---------------------------------------------------------------------------
# FPosBAPI backend — CMD_LIST / list_commands()
# NOTE: these tests send commands that may not be supported by all firmware
# versions.  On unsupported firmware the PLC enters a brief busy state, so
# these tests are placed last to avoid contaminating earlier tests.
# ---------------------------------------------------------------------------

# Commands that must be present regardless of firmware version
_REQUIRED_COMMANDS = {"HOME", "MOVE_AXIS", "ROB_POS", "IS_HOME", "IS_ENBL", "IS_ERROR"}


@pytest.mark.hardware
def test_fposbapi_list_commands_returns_nonempty_list(gantry_fposbapi):
    """list_commands() should return a non-empty list of command name strings."""
    try:
        commands = gantry_fposbapi._client.list_commands()
    except FPosBAPIClientError as exc:
        pytest.skip(f"CMD_LIST not supported by this firmware: {exc}")
    assert isinstance(commands, list)
    assert len(commands) > 0, "CMD_LIST returned an empty list — unexpected for any firmware version"


@pytest.mark.hardware
def test_fposbapi_list_commands_contains_core_commands(gantry_fposbapi):
    """list_commands() should include the core commands that every firmware version supports."""
    try:
        commands = set(gantry_fposbapi._client.list_commands())
    except FPosBAPIClientError as exc:
        pytest.skip(f"CMD_LIST not supported by this firmware: {exc}")
    missing = _REQUIRED_COMMANDS - commands
    assert not missing, (
        f"CMD_LIST is missing expected core commands: {missing}. "
        f"Full list returned: {sorted(commands)}"
    )


@pytest.mark.hardware
def test_fposbapi_list_commands_all_strings(gantry_fposbapi):
    """Every entry returned by list_commands() should be a non-empty string."""
    try:
        commands = gantry_fposbapi._client.list_commands()
    except FPosBAPIClientError as exc:
        pytest.skip(f"CMD_LIST not supported by this firmware: {exc}")
    for cmd in commands:
        assert isinstance(cmd, str) and cmd, f"Non-string or empty entry in CMD_LIST: {cmd!r}"


# ---------------------------------------------------------------------------
# FPosBAPI backend — error state queries
# NOTE: placed last for the same reason as CMD_LIST tests above.
# ---------------------------------------------------------------------------


@pytest.mark.hardware
def test_fposbapi_is_error_clear_at_session_start(gantry_fposbapi):
    """IS_ERROR should report no active error at the start of a healthy session."""
    try:
        response = gantry_fposbapi._client.send_command("IS_ERROR")
    except FPosBAPIClientError as exc:
        pytest.skip(f"IS_ERROR not supported by this firmware: {exc}")
    fields = [f.strip() for f in response[-1].split(",")]
    # Expected layout: MSG_ID, IS_ERROR, <error_flag>, ..., 0, NULL, SUCCESS
    assert fields[2] == "0", (
        f"IS_ERROR reports an active error — gantry may need RESET_ERR before testing. "
        f"IS_ERROR response: {response!r}"
    )


@pytest.mark.hardware
def test_fposbapi_read_err_responds(gantry_fposbapi):
    """READ_ERR should return a SUCCESS response."""
    try:
        response = gantry_fposbapi._client.send_command("READ_ERR")
    except FPosBAPIClientError as exc:
        pytest.skip(f"READ_ERR not supported by this firmware: {exc}")
    assert "SUCCESS" in response[-1], f"Unexpected READ_ERR response: {response!r}"
