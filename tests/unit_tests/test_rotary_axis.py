"""Unit tests for the explicit EDCON linear and rotary axis implementations."""

from math import nan
from unittest.mock import MagicMock

import pytest
from edcon.edrive.motion_handler import MotionHandler

from applied_motion.backends.edcon_axis import EdconAxis
from applied_motion.backends.linear_axis import LinearAxis
from applied_motion.backends.rotary_axis import RotaryAxis

_DEFAULT_PNUS = {
    832: 0,
    833: 1_000_000,
    834: -2_000_000,
    835: 2_000_000,
    11212: -50,
    11213: 50,
    11724: -6,
    11725: -3,
    12113: 0,
    12118: 0,
}


def _prepare_drive(mocker, *, pnu_values=None, current_position=0, referencing_error=None):
    values = _DEFAULT_PNUS.copy()
    if pnu_values is not None:
        values.update(pnu_values)

    com = MagicMock()
    com.read_pnu.side_effect = lambda pnu: values.get(pnu)
    mocker.patch("applied_motion.backends.edcon_axis.ComModbus", return_value=com)
    mocker.patch.object(MotionHandler, "__init__", lambda self, com, config_mode=None: setattr(self, "com", com))
    mocker.patch.object(MotionHandler, "acknowledge_faults", return_value=True)
    mocker.patch.object(MotionHandler, "configure_software_limit_switch")
    mocker.patch.object(MotionHandler, "fault_present", return_value=False)
    mocker.patch.object(MotionHandler, "fault_string", return_value="OK")
    mocker.patch.object(MotionHandler, "current_fault_code", return_value=0)
    enable = mocker.patch.object(MotionHandler, "enable_powerstage", return_value=True)
    disable = mocker.patch.object(MotionHandler, "disable_powerstage")
    position_task = mocker.patch.object(MotionHandler, "position_task", return_value=True)
    referencing_task = mocker.patch.object(
        MotionHandler, "referencing_task", side_effect=referencing_error
    )
    mocker.patch.object(MotionHandler, "current_position", return_value=current_position)
    return com, enable, disable, position_task, referencing_task


def _create_axis(
    mocker,
    axis_type: type[EdconAxis] = RotaryAxis,
    *,
    pnu_values=None,
    current_position=0,
    **kwargs,
):
    com, enable, disable, position_task, referencing_task = _prepare_drive(
        mocker,
        pnu_values=pnu_values,
        current_position=current_position,
    )
    axis = axis_type(name="R", ip="192.168.0.10", **kwargs)
    return axis, com, enable, disable, position_task, referencing_task


def test_rotary_degrees_and_degrees_per_second_convert_through_revolutions(mocker):
    axis, *_ = _create_axis(mocker)

    assert axis._valid_position(360, axis.input_pos_unit) == pytest.approx(1_000_000)
    assert axis._valid_position(250_000, axis.input_pos_unit, invert=True) == pytest.approx(90)
    assert axis._valid_velocity(90, axis.input_vel_unit) == pytest.approx(250)
    assert axis._valid_velocity(250, axis.input_vel_unit, invert=True) == pytest.approx(90)


def test_edcon_and_linear_axes_preserve_millimetre_scaling(mocker):
    legacy, *_ = _create_axis(mocker, EdconAxis)
    linear, *_ = _create_axis(mocker, LinearAxis)

    assert legacy._valid_position(10, legacy.input_pos_unit) == pytest.approx(10_000)
    assert linear._valid_position(10, linear.input_pos_unit) == pytest.approx(10_000)
    assert legacy.min_position == pytest.approx(-1_999)
    assert legacy.max_position == pytest.approx(1_999)
    assert legacy == linear


def test_modulo_disabled_axis_preserves_signed_unwrapped_targets(mocker):
    axis, _, _, _, position_task, _ = _create_axis(mocker)

    axis.move(position=-450, velocity=90)

    position_task.assert_called_once_with(-1_250_000, 250, absolute=True, nonblocking=False)


def test_modulo_enabled_axis_uses_commissioned_boundary_without_linear_inset(mocker):
    axis, _, _, _, position_task, _ = _create_axis(
        mocker,
        pnu_values={12113: 1, 12118: 1},
    )

    assert axis.min_position == pytest.approx(0)
    assert axis.max_position == pytest.approx(360)
    assert axis._modulo_period_drive_units == 1_000_000

    axis.move(position=400, velocity=90)

    position_task.assert_called_once_with(1_000_000, 250, absolute=True, nonblocking=False)


def test_modulo_enabled_relative_move_is_clamped_at_verified_upper_boundary(mocker):
    axis, _, _, _, position_task, _ = _create_axis(
        mocker,
        pnu_values={12113: 1, 12118: 1},
        current_position=972_222,
    )

    axis.move(position=20, velocity=90, position_type="relative")

    position_task.assert_called_once_with(27_778, 250, absolute=False, nonblocking=False)


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [
        (nan, 10),
        (10, 0),
    ],
)
def test_invalid_or_empty_limits_fail_before_power_enable(mocker, minimum, maximum):
    com, enable, disable, _, _ = _prepare_drive(mocker)

    with pytest.raises(ValueError):
        RotaryAxis(
            name="R",
            ip="192.168.0.10",
            min_position=minimum,
            max_position=maximum,
        )

    enable.assert_not_called()
    disable.assert_not_called()
    com.stop_io.assert_called_once_with()


def test_equal_limits_allow_only_a_current_position_noop(mocker):
    axis, _, _, _, position_task, _ = _create_axis(
        mocker,
        min_position=0,
        max_position=0,
    )

    assert axis.move(position=0, velocity=90) is True
    assert axis.move(position=1, velocity=90) is False
    position_task.assert_not_called()


@pytest.mark.parametrize(
    "pnu_values",
    [
        {12113: 1, 12118: 0},
        {12113: 0, 12118: 1},
        {12113: 2, 12118: 2},
        {12113: 0, 12118: 0, 832: 1_000_000, 833: 0},
        {12113: 0, 12118: None},
        {12113: 0, 12118: 0, 832: None},
    ],
)
def test_invalid_modulo_pnu_state_fails_before_power_enable(mocker, pnu_values):
    com, enable, disable, _, _ = _prepare_drive(mocker, pnu_values=pnu_values)

    with pytest.raises((TypeError, ValueError)):
        RotaryAxis(name="R", ip="192.168.0.10")

    enable.assert_not_called()
    disable.assert_not_called()
    com.stop_io.assert_called_once_with()


def test_failed_homing_cleans_up_power_stage_and_io(mocker):
    com, enable, disable, _, _ = _prepare_drive(
        mocker,
        referencing_error=RuntimeError("homing failed"),
    )

    with pytest.raises(RuntimeError, match="homing failed"):
        RotaryAxis(name="R", ip="192.168.0.10", run_referencing=True)

    assert enable.call_count == 2
    disable.assert_called_once_with()
    com.stop_io.assert_called_once_with()


def test_linear_and_rotary_axes_with_same_endpoint_are_not_equal():
    linear = object.__new__(LinearAxis)
    rotary = object.__new__(RotaryAxis)
    linear.name = rotary.name = "R"
    linear.ip = rotary.ip = "192.168.0.10"

    assert linear != rotary
    assert len({linear, rotary}) == 2