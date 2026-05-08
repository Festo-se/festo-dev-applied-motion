"""Unit tests for EdconAxis._check_overshoot.

No hardware or network connection required.  ``EdconAxis`` instances are
created via ``object.__new__`` to bypass the hardware-dependent
``__init__``, and only the attributes touched by ``_check_overshoot``
are set.
"""

import pytest

from applied_motion.backends.edcon_axis import EdconAxis


def _make_axis(neg_limit: int, pos_limit: int, current_pos: int = 0) -> EdconAxis:
    """Return a bare EdconAxis with only the attributes _check_overshoot needs."""
    axis = object.__new__(EdconAxis)
    axis.name = "TEST"
    axis._neg_sw_limit = neg_limit
    axis._pos_sw_limit = pos_limit
    # Patch current_position as a plain lambda so relative moves work.
    axis.current_position = lambda: current_pos
    return axis


# ---------------------------------------------------------------------------
# Absolute moves
# ---------------------------------------------------------------------------


def test_absolute_within_limits_unchanged():
    axis = _make_axis(neg_limit=0, pos_limit=10_000)
    assert axis._check_overshoot(5_000, absolute=True) == 5_000


def test_absolute_on_positive_limit_unchanged():
    axis = _make_axis(neg_limit=0, pos_limit=10_000)
    assert axis._check_overshoot(10_000, absolute=True) == 10_000


def test_absolute_on_negative_limit_unchanged():
    axis = _make_axis(neg_limit=0, pos_limit=10_000)
    assert axis._check_overshoot(0, absolute=True) == 0


def test_absolute_above_positive_limit_clamped():
    axis = _make_axis(neg_limit=0, pos_limit=10_000)
    assert axis._check_overshoot(12_000, absolute=True) == 10_000


def test_absolute_below_negative_limit_clamped():
    axis = _make_axis(neg_limit=0, pos_limit=10_000)
    assert axis._check_overshoot(-500, absolute=True) == 0


# ---------------------------------------------------------------------------
# Relative moves
# ---------------------------------------------------------------------------


def test_relative_within_limits_unchanged():
    axis = _make_axis(neg_limit=0, pos_limit=10_000, current_pos=5_000)
    # delta of +2000 → target 7000, within limits → delta unchanged
    assert axis._check_overshoot(2_000, absolute=False) == 2_000


def test_relative_would_exceed_positive_limit_clamped():
    axis = _make_axis(neg_limit=0, pos_limit=10_000, current_pos=8_000)
    # delta of +5000 → target 13000, clamped to 10000 → returned delta = 10000 - 8000 = 2000
    assert axis._check_overshoot(5_000, absolute=False) == 2_000


def test_relative_would_exceed_negative_limit_clamped():
    axis = _make_axis(neg_limit=0, pos_limit=10_000, current_pos=3_000)
    # delta of -5000 → target -2000, clamped to 0 → returned delta = 0 - 3000 = -3000
    assert axis._check_overshoot(-5_000, absolute=False) == -3_000


def test_relative_zero_delta_unchanged():
    axis = _make_axis(neg_limit=0, pos_limit=10_000, current_pos=5_000)
    assert axis._check_overshoot(0, absolute=False) == 0
