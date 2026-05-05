"""Unit tests for EdconAxis._valid_position and EdconAxis._valid_velocity.

No hardware or network connection required.  ``EdconAxis`` instances are
created via ``object.__new__``, and ``axis.com`` is replaced with a
``MagicMock`` whose ``read_pnu`` return value controls the system unit
scale reported by the drive.

Scale formula (from _valid_position / _valid_velocity):
    scale = 10 ** (input_power_of_ten - system_power_of_ten)
    result = value * scale          (forward, invert=False)
    result = value / scale          (inverse, invert=True)
"""

from unittest.mock import MagicMock

import pytest

from festo_dev_applied_motion.backends.edcon_axis import EdconAxis

# PNU numbers used by _valid_position and _valid_velocity respectively
_PNU_POSITION_UNIT = 11724
_PNU_VELOCITY_UNIT = 11725


def _make_axis(system_power_of_ten: int) -> EdconAxis:
    """Return a bare EdconAxis whose com.read_pnu returns system_power_of_ten."""
    axis = object.__new__(EdconAxis)
    axis.name = "TEST"
    axis.com = MagicMock()
    axis.com.read_pnu.return_value = system_power_of_ten
    return axis


# ---------------------------------------------------------------------------
# _valid_position
# ---------------------------------------------------------------------------

_MM_INPUT = {"distance": {"unit": "m", "power": 1, "power_of_ten": -3}}


def test_position_identity_when_units_match():
    # System also uses mm (power_of_ten = -3) → scale = 1 → value unchanged
    axis = _make_axis(system_power_of_ten=-3)
    assert axis._valid_position(100, _MM_INPUT) == pytest.approx(100)


def test_position_scales_mm_to_finer_unit():
    # System uses 0.01 mm (power_of_ten = -5), input is mm (-3)
    # scale = 10**(-3 - -5) = 100 → 50 mm * 100 = 5000 drive units
    axis = _make_axis(system_power_of_ten=-5)
    assert axis._valid_position(50, _MM_INPUT) == pytest.approx(5_000)


def test_position_scales_mm_to_coarser_unit():
    # System uses m (power_of_ten = 0), input is mm (-3)
    # scale = 10**(-3 - 0) = 0.001 → 500 mm * 0.001 = 0.5 m
    axis = _make_axis(system_power_of_ten=0)
    assert axis._valid_position(500, _MM_INPUT) == pytest.approx(0.5)


def test_position_invert_converts_drive_units_to_mm():
    # Inverse of the finer-unit case: 5000 drive units / 100 = 50 mm
    axis = _make_axis(system_power_of_ten=-5)
    assert axis._valid_position(5_000, _MM_INPUT, invert=True) == pytest.approx(50)


# ---------------------------------------------------------------------------
# _valid_velocity
# ---------------------------------------------------------------------------

_MM_PER_S_INPUT = {
    "distance": {"unit": "m", "power": 1, "power_of_ten": -3},
    "time": {"unit": "s", "power": -1, "power_of_ten": 1},
}


def test_velocity_identity_when_units_match():
    axis = _make_axis(system_power_of_ten=-3)
    # _valid_velocity only reads the distance part of system_unit from read_pnu
    assert axis._valid_velocity(200, _MM_PER_S_INPUT) == pytest.approx(200)


def test_velocity_scales_mm_s_to_finer_unit():
    # Same distance scaling as position: system at -5, input at -3
    axis = _make_axis(system_power_of_ten=-5)
    assert axis._valid_velocity(10, _MM_PER_S_INPUT) == pytest.approx(1_000)


def test_velocity_invert():
    axis = _make_axis(system_power_of_ten=-5)
    assert axis._valid_velocity(1_000, _MM_PER_S_INPUT, invert=True) == pytest.approx(10)


# ---------------------------------------------------------------------------
# get_current_axis_position
# ---------------------------------------------------------------------------


def _make_axis_with_super_position(system_power_of_ten: int, raw_position: int) -> EdconAxis:
    """Return a bare EdconAxis whose super().current_position() returns
    ``raw_position`` in drive units and whose com reports
    ``system_power_of_ten`` for PNU 11724."""
    axis = object.__new__(EdconAxis)
    axis.name = "TEST"
    axis.com = MagicMock()
    axis.com.read_pnu.return_value = system_power_of_ten
    # Patch the method resolution order so that super().current_position() returns the raw value
    # without touching real hardware.
    with pytest.MonkeyPatch().context() as mp:
        pass  # used below via direct attribute override
    # The simplest way: patch the MotionHandler method on the class just for
    # this instance via __class__ is fragile; instead we call _valid_position
    # directly in the test assertions (see tests below).
    axis._raw_position = raw_position
    return axis


def test_get_current_axis_position_converts_drive_units_to_mm(mocker):
    """get_current_axis_position must call super().current_position() and
    convert the result through _valid_position(invert=True) so the return
    value is in mm."""
    from edcon.edrive.motion_handler import MotionHandler

    axis = object.__new__(EdconAxis)
    axis.name = "TEST"
    axis.com = MagicMock()
    # Drive configured in 0.001 mm steps (power_of_ten = -6)
    axis.com.read_pnu.return_value = -6

    mocker.patch.object(MotionHandler, "current_position", return_value=5_000)

    result = axis.get_current_axis_position()
    # 5000 drive units @ 0.001 mm/unit → 5 mm
    assert result == pytest.approx(5.0)


def test_get_current_axis_position_identity_when_drive_uses_mm(mocker):
    """When the drive's internal unit is already mm (power_of_ten = -3),
    the raw position value must pass through unchanged."""
    from edcon.edrive.motion_handler import MotionHandler

    axis = object.__new__(EdconAxis)
    axis.name = "TEST"
    axis.com = MagicMock()
    axis.com.read_pnu.return_value = -3  # drive unit = mm

    mocker.patch.object(MotionHandler, "current_position", return_value=1_234)

    result = axis.get_current_axis_position()
    assert result == pytest.approx(1_234.0)


def test_get_current_axis_position_zero(mocker):
    """A raw position of zero must return 0.0 regardless of the drive scale."""
    from edcon.edrive.motion_handler import MotionHandler

    axis = object.__new__(EdconAxis)
    axis.name = "TEST"
    axis.com = MagicMock()
    axis.com.read_pnu.return_value = -5

    mocker.patch.object(MotionHandler, "current_position", return_value=0)

    assert axis.get_current_axis_position() == pytest.approx(0.0)


def test_get_current_axis_position_negative(mocker):
    """Negative raw positions must be converted correctly (negative mm)."""
    from edcon.edrive.motion_handler import MotionHandler

    axis = object.__new__(EdconAxis)
    axis.name = "TEST"
    axis.com = MagicMock()
    axis.com.read_pnu.return_value = -5  # 0.01 mm resolution

    mocker.patch.object(MotionHandler, "current_position", return_value=-3_000)

    # -3000 * 0.01 mm = -30 mm
    assert axis.get_current_axis_position() == pytest.approx(-30.0)


# ---------------------------------------------------------------------------
# current_position (warning wrapper)
# ---------------------------------------------------------------------------


def test_current_position_returns_raw_drive_value(mocker):
    """current_position() must return exactly what MotionHandler.current_position
    returns, unmodified, so it remains a transparent pass-through."""
    from edcon.edrive.motion_handler import MotionHandler

    axis = object.__new__(EdconAxis)
    axis.name = "TEST"
    axis.com = MagicMock()

    mocker.patch.object(MotionHandler, "current_position", return_value=7_500)

    assert axis.current_position() == 7_500


def test_current_position_emits_warning(mocker, caplog):
    """current_position() must emit a WARNING-level log message that
    mentions the drive-unit caveat, so callers are immediately informed
    they are receiving a raw, scale-dependent value."""
    import logging
    from edcon.edrive.motion_handler import MotionHandler

    axis = object.__new__(EdconAxis)
    axis.name = "X"
    axis.com = MagicMock()

    mocker.patch.object(MotionHandler, "current_position", return_value=0)

    with caplog.at_level(logging.WARNING, logger="festo_dev_applied_motion.gantry"):
        axis.current_position()

    assert any(
        "drive-unit" in record.message or "drive unit" in record.message.lower()
        for record in caplog.records
        if record.levelno == logging.WARNING
    ), "Expected a WARNING mentioning drive units"
