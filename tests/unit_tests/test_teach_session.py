"""Unit tests for TeachSession.

Coverage areas
--------------
* ``TeachSession.jog``

  - Computes the correct absolute target for both ``"+"`` and ``"-"``
    directions.
  - Calls ``gantry.move_to`` with the expected single-axis deque.
  - Returns the updated location from ``gantry.get_location``.
  - Raises ``ValueError`` for invalid direction.
  - Raises ``ValueError`` for non-positive step.
  - Raises ``KeyError`` for an unknown axis name.

* ``TeachSession.capture``

  - Calls ``gantry.get_location`` and stores the result under the label.
  - Calls the ``on_capture`` hook with the label and position.
  - Works correctly when no hook is provided.
  - Overwrites a previous entry with the same label.

* ``TeachSession.save`` / ``TeachSession.load``

  - Round-trip: positions written by ``save`` are re-read by ``load``.
  - ``load`` merges into (not replaces) existing positions.

No hardware or network connection required.  All tests use a plain
``MagicMock`` Gantry so the test file has no dependency on
``prompt_toolkit`` or ``rich``.
"""

import json
from collections import deque
from unittest.mock import MagicMock, call

import pytest

from applied_motion.teach_in.session import TeachSession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_gantry(axis_names: list[str] = None, current_positions: dict[str, float] = None):
    """Return a MagicMock Gantry pre-configured for teach-in tests."""
    if axis_names is None:
        axis_names = ["X", "Y", "Z"]
    if current_positions is None:
        current_positions = {name: 0.0 for name in axis_names}

    gantry = MagicMock()
    gantry.axes = {}
    for name in axis_names:
        axis = MagicMock()
        axis.get_current_axis_position.return_value = current_positions.get(name, 0.0)
        gantry.axes[name] = axis

    gantry.get_location.return_value = dict(current_positions)
    gantry.move_to.return_value = None
    return gantry


# ---------------------------------------------------------------------------
# TeachSession.jog
# ---------------------------------------------------------------------------


class TestJog:
    def test_positive_direction_computes_correct_target(self):
        gantry = _make_gantry(current_positions={"X": 10.0, "Y": 0.0, "Z": 0.0})
        session = TeachSession(gantry)
        session.jog("X", "+", 5.0)
        move_call = gantry.move_to.call_args[0][0]
        assert list(move_call)[0] == {"X": {"position": 15.0, "velocity": 10.0}}

    def test_negative_direction_computes_correct_target(self):
        gantry = _make_gantry(current_positions={"X": 10.0, "Y": 0.0, "Z": 0.0})
        session = TeachSession(gantry)
        session.jog("X", "-", 3.0)
        move_call = gantry.move_to.call_args[0][0]
        assert list(move_call)[0] == {"X": {"position": 7.0, "velocity": 10.0}}

    def test_custom_velocity_is_forwarded(self):
        gantry = _make_gantry(current_positions={"Y": 0.0})
        session = TeachSession(gantry)
        session.jog("Y", "+", 1.0, velocity=25.0)
        move_call = gantry.move_to.call_args[0][0]
        assert list(move_call)[0]["Y"]["velocity"] == 25.0

    def test_returns_gantry_get_location(self):
        expected = {"X": 15.0, "Y": 0.0, "Z": 0.0}
        gantry = _make_gantry(current_positions={"X": 10.0, "Y": 0.0, "Z": 0.0})
        gantry.get_location.return_value = expected
        session = TeachSession(gantry)
        result = session.jog("X", "+", 5.0)
        assert result == expected

    def test_move_to_called_with_deque(self):
        gantry = _make_gantry(current_positions={"X": 0.0})
        session = TeachSession(gantry)
        session.jog("X", "+", 1.0)
        args, _ = gantry.move_to.call_args
        assert isinstance(args[0], deque)

    def test_default_timeout_passed_to_move_to(self):
        gantry = _make_gantry(current_positions={"X": 0.0})
        session = TeachSession(gantry)
        session.jog("X", "+", 1.0)
        _, kwargs = gantry.move_to.call_args
        assert kwargs["timeout"] == 30

    def test_custom_timeout_passed_to_move_to(self):
        gantry = _make_gantry(current_positions={"X": 0.0})
        session = TeachSession(gantry)
        session.jog("X", "+", 1.0, timeout=5)
        _, kwargs = gantry.move_to.call_args
        assert kwargs["timeout"] == 5

    def test_invalid_direction_raises_value_error(self):
        gantry = _make_gantry()
        session = TeachSession(gantry)
        with pytest.raises(ValueError, match="direction"):
            session.jog("X", ">", 1.0)

    def test_non_positive_step_raises_value_error(self):
        gantry = _make_gantry()
        session = TeachSession(gantry)
        with pytest.raises(ValueError, match="step_mm"):
            session.jog("X", "+", 0.0)

    def test_negative_step_raises_value_error(self):
        gantry = _make_gantry()
        session = TeachSession(gantry)
        with pytest.raises(ValueError, match="step_mm"):
            session.jog("X", "+", -5.0)

    def test_unknown_axis_raises_key_error(self):
        gantry = _make_gantry(axis_names=["X"])
        session = TeachSession(gantry)
        with pytest.raises(KeyError, match="BOGUS"):
            session.jog("BOGUS", "+", 1.0)


# ---------------------------------------------------------------------------
# TeachSession.capture
# ---------------------------------------------------------------------------


class TestCapture:
    def test_stores_position_under_label(self):
        expected = {"X": 1.0, "Y": 2.0, "Z": 3.0}
        gantry = _make_gantry()
        gantry.get_location.return_value = expected
        session = TeachSession(gantry)
        session.capture("home")
        assert session.positions["home"] == expected

    def test_returns_the_recorded_position(self):
        expected = {"X": 5.5}
        gantry = _make_gantry(axis_names=["X"])
        gantry.get_location.return_value = expected
        session = TeachSession(gantry)
        result = session.capture("p1")
        assert result == expected

    def test_calls_on_capture_hook_with_label_and_position(self):
        position = {"X": 10.0}
        gantry = _make_gantry(axis_names=["X"])
        gantry.get_location.return_value = position
        hook = MagicMock()
        session = TeachSession(gantry, on_capture=hook)
        session.capture("deck_a1")
        hook.assert_called_once_with("deck_a1", position)

    def test_no_hook_does_not_raise(self):
        gantry = _make_gantry()
        session = TeachSession(gantry, on_capture=None)
        session.capture("p1")  # must not raise

    def test_overwrites_previous_entry_with_same_label(self):
        gantry = _make_gantry(axis_names=["X"])
        gantry.get_location.side_effect = [{"X": 1.0}, {"X": 2.0}]
        session = TeachSession(gantry)
        session.capture("p1")
        session.capture("p1")
        assert session.positions["p1"] == {"X": 2.0}

    def test_hook_exception_propagates(self):
        gantry = _make_gantry()

        def bad_hook(label, pos):
            raise RuntimeError("PLC unreachable")

        session = TeachSession(gantry, on_capture=bad_hook)
        with pytest.raises(RuntimeError, match="PLC unreachable"):
            session.capture("p1")


# ---------------------------------------------------------------------------
# TeachSession.save / load
# ---------------------------------------------------------------------------


class TestSaveLoad:
    def test_roundtrip(self, tmp_path):
        gantry = _make_gantry()
        session = TeachSession(gantry)
        session.positions = {"a1": {"X": 1.0, "Y": 2.0}, "a2": {"X": 3.0, "Y": 4.0}}
        path = tmp_path / "deck.json"
        session.save(path)

        session2 = TeachSession(gantry)
        session2.load(path)
        assert session2.positions == session.positions

    def test_save_creates_valid_json(self, tmp_path):
        gantry = _make_gantry()
        session = TeachSession(gantry)
        session.positions = {"p": {"X": 9.9}}
        path = tmp_path / "out.json"
        session.save(path)
        raw = json.loads(path.read_text())
        assert raw == {"p": {"X": 9.9}}

    def test_load_merges_not_replaces(self, tmp_path):
        gantry = _make_gantry()
        existing = {"existing": {"X": 0.0}}
        loaded_data = {"new": {"X": 5.0}}
        path = tmp_path / "deck.json"
        path.write_text(json.dumps(loaded_data))

        session = TeachSession(gantry)
        session.positions = dict(existing)
        session.load(path)
        assert "existing" in session.positions
        assert "new" in session.positions

    def test_load_overwrites_same_label(self, tmp_path):
        gantry = _make_gantry()
        path = tmp_path / "deck.json"
        path.write_text(json.dumps({"p": {"X": 99.0}}))
        session = TeachSession(gantry)
        session.positions = {"p": {"X": 0.0}}
        session.load(path)
        assert session.positions["p"]["X"] == 99.0

    def test_load_raises_on_missing_file(self):
        gantry = _make_gantry()
        session = TeachSession(gantry)
        with pytest.raises(OSError):
            session.load("/nonexistent/path/deck.json")
