"""Unit tests for MotionSession.

Coverage areas
--------------
* ``MotionSession.jog``

  - Computes the correct absolute target for both ``"+"`` and ``"-"``
    directions.
  - Calls ``gantry.move_to`` with the expected single-axis deque.
  - Returns the updated location from ``gantry.get_location``.
  - Raises ``ValueError`` for invalid direction.
  - Raises ``ValueError`` for non-positive step.
  - Raises ``KeyError`` for an unknown axis name.

* ``MotionSession.capture``

  - Calls ``gantry.get_location`` and stores the result under the label.
  - Calls the ``on_capture`` hook with the label and position.
  - Works correctly when no hook is provided.
  - Overwrites a previous entry with the same label.

* ``MotionSession.save`` / ``MotionSession.load``

  - Round-trip: positions written by ``save`` are re-read by ``load``.
  - ``load`` merges into (not replaces) existing positions.

No hardware or network connection required.  All tests use a plain
``MagicMock`` Gantry so the test file has no dependency on
``prompt_toolkit`` or ``rich``.
"""

import json
from math import inf, nan
from unittest.mock import MagicMock, call

import pytest

from applied_motion.cli.session import MotionSession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_gantry(
    axis_names: list[str] | None = None,
    current_positions: dict[str, float] | None = None,
):
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
        axis.min_position = -inf
        axis.max_position = inf
        axis.position_unit = "mm"
        axis.velocity_unit = "mm/s"
        gantry.axes[name] = axis

    gantry.get_location.return_value = dict(current_positions)
    gantry.move_to.return_value = None
    return gantry


# ---------------------------------------------------------------------------
# MotionSession.jog
# ---------------------------------------------------------------------------


class TestJog:
    def test_positive_direction_calls_axis_move_with_relative_delta(self):
        gantry = _make_gantry(current_positions={"X": 10.0, "Y": 0.0, "Z": 0.0})
        session = MotionSession(gantry)
        session.jog("X", "+", 5.0)
        args, kwargs = gantry.axes["X"].move.call_args
        assert args[0] == pytest.approx(5.0)
        assert kwargs.get("position_type") == "relative"

    def test_negative_direction_calls_axis_move_with_negative_delta(self):
        gantry = _make_gantry(current_positions={"X": 10.0, "Y": 0.0, "Z": 0.0})
        session = MotionSession(gantry)
        session.jog("X", "-", 3.0)
        args, kwargs = gantry.axes["X"].move.call_args
        assert args[0] == pytest.approx(-3.0)
        assert kwargs.get("position_type") == "relative"

    def test_custom_velocity_is_forwarded(self):
        gantry = _make_gantry(current_positions={"Y": 0.0})
        session = MotionSession(gantry)
        session.jog("Y", "+", 1.0, velocity=25.0)
        args, _ = gantry.axes["Y"].move.call_args
        assert args[1] == 25.0

    def test_generic_step_keyword_is_accepted(self):
        gantry = _make_gantry(axis_names=["X"])
        session = MotionSession(gantry)

        session.jog("X", "+", step=2.0)

        assert gantry.axes["X"].move.call_args.args[0] == pytest.approx(2.0)

    def test_step_mm_alias_remains_supported_for_linear_axis(self):
        gantry = _make_gantry(axis_names=["X"])
        session = MotionSession(gantry)

        with pytest.warns(DeprecationWarning, match="step_mm"):
            session.jog("X", "+", step_mm=2.0)

        assert gantry.axes["X"].move.call_args.args[0] == pytest.approx(2.0)

    def test_step_mm_alias_is_rejected_for_rotary_axis(self):
        gantry = _make_gantry(axis_names=["R"])
        gantry.axes["R"].position_unit = "degrees"
        session = MotionSession(gantry)

        with pytest.warns(DeprecationWarning, match="step_mm"):
            with pytest.raises(ValueError, match="linear"):
                session.jog("R", "+", step_mm=2.0)

    def test_linear_jog_retains_two_hundredths_mm_limit_margin(self):
        gantry = _make_gantry(axis_names=["X"], current_positions={"X": 9.9})
        gantry.axes["X"].min_position = 0.0
        gantry.axes["X"].max_position = 10.0
        session = MotionSession(gantry)

        session.jog("X", "+", 1.0)

        assert gantry.axes["X"].move.call_args.args[0] == pytest.approx(0.08)

    def test_rotary_jog_uses_zero_degree_limit_margin(self):
        gantry = _make_gantry(axis_names=["R"], current_positions={"R": 359.5})
        gantry.axes["R"].position_unit = "degrees"
        gantry.axes["R"].min_position = 0.0
        gantry.axes["R"].max_position = 360.0
        session = MotionSession(gantry)

        session.jog("R", "+", 1.0)

        assert gantry.axes["R"].move.call_args.args[0] == pytest.approx(0.5)

    def test_returns_gantry_get_location(self):
        expected = {"X": 15.0, "Y": 0.0, "Z": 0.0}
        gantry = _make_gantry(current_positions={"X": 10.0, "Y": 0.0, "Z": 0.0})
        gantry.get_location.return_value = expected
        session = MotionSession(gantry)
        result = session.jog("X", "+", 5.0)
        assert result == expected

    def test_axis_move_called_directly(self):
        gantry = _make_gantry(current_positions={"X": 0.0})
        session = MotionSession(gantry)
        session.jog("X", "+", 1.0)
        gantry.axes["X"].move.assert_called_once()
        gantry.move_to.assert_not_called()

    def test_default_timeout_passed_to_axis_move(self):
        gantry = _make_gantry(current_positions={"X": 0.0})
        session = MotionSession(gantry)
        session.jog("X", "+", 1.0)
        _, kwargs = gantry.axes["X"].move.call_args
        assert kwargs["timeout"] == 15

    def test_custom_timeout_passed_to_axis_move(self):
        gantry = _make_gantry(current_positions={"X": 0.0})
        session = MotionSession(gantry)
        session.jog("X", "+", 1.0, timeout=5)
        _, kwargs = gantry.axes["X"].move.call_args
        assert kwargs["timeout"] == 5

    def test_invalid_direction_raises_value_error(self):
        gantry = _make_gantry()
        session = MotionSession(gantry)
        with pytest.raises(ValueError, match="direction"):
            session.jog("X", ">", 1.0)

    def test_non_positive_step_raises_value_error(self):
        gantry = _make_gantry()
        session = MotionSession(gantry)
        with pytest.raises(ValueError, match="step_mm"):
            session.jog("X", "+", 0.0)

    def test_negative_step_raises_value_error(self):
        gantry = _make_gantry()
        session = MotionSession(gantry)
        with pytest.raises(ValueError, match="step_mm"):
            session.jog("X", "+", -5.0)

    def test_unknown_axis_raises_key_error(self):
        gantry = _make_gantry(axis_names=["X"])
        session = MotionSession(gantry)
        with pytest.raises(KeyError, match="BOGUS"):
            session.jog("BOGUS", "+", 1.0)


# ---------------------------------------------------------------------------
# MotionSession.capture
# ---------------------------------------------------------------------------


class TestCapture:
    def test_stores_position_under_label(self):
        expected = {"X": 1.0, "Y": 2.0, "Z": 3.0}
        gantry = _make_gantry()
        gantry.get_location.return_value = expected
        session = MotionSession(gantry)
        session.capture("home")
        assert session.positions["home"] == expected

    def test_returns_the_recorded_position(self):
        expected = {"X": 5.5}
        gantry = _make_gantry(axis_names=["X"])
        gantry.get_location.return_value = expected
        session = MotionSession(gantry)
        result = session.capture("p1")
        assert result == expected

    def test_calls_on_capture_hook_with_label_and_position(self):
        position = {"X": 10.0}
        gantry = _make_gantry(axis_names=["X"])
        gantry.get_location.return_value = position
        hook = MagicMock()
        session = MotionSession(gantry, on_capture=hook)
        session.capture("deck_a1")
        hook.assert_called_once_with("deck_a1", position)

    def test_no_hook_does_not_raise(self):
        gantry = _make_gantry()
        session = MotionSession(gantry, on_capture=None)
        session.capture("p1")  # must not raise

    def test_overwrites_previous_entry_with_same_label(self):
        gantry = _make_gantry(axis_names=["X"])
        gantry.get_location.side_effect = [{"X": 1.0}, {"X": 2.0}]
        session = MotionSession(gantry)
        session.capture("p1")
        session.capture("p1")
        assert session.positions["p1"] == {"X": 2.0}

    def test_hook_exception_propagates(self):
        gantry = _make_gantry()

        def bad_hook(label, pos):
            raise RuntimeError("PLC unreachable")

        session = MotionSession(gantry, on_capture=bad_hook)
        with pytest.raises(RuntimeError, match="PLC unreachable"):
            session.capture("p1")


# ---------------------------------------------------------------------------
# MotionSession.save / load
# ---------------------------------------------------------------------------


class TestSaveLoad:
    def test_roundtrip(self, tmp_path):
        gantry = _make_gantry()
        session = MotionSession(gantry)
        session.positions = {"a1": {"X": 1.0, "Y": 2.0}, "a2": {"X": 3.0, "Y": 4.0}}
        path = tmp_path / "deck.json"
        session.save(path)

        session2 = MotionSession(gantry)
        session2.load(path)
        assert session2.positions == session.positions

    def test_save_creates_valid_json(self, tmp_path):
        gantry = _make_gantry()
        session = MotionSession(gantry)
        session.positions = {"p": {"X": 9.9}}
        path = tmp_path / "out.json"
        session.save(path)
        raw = json.loads(path.read_text())
        assert raw == {
            "schema_version": 2,
            "axis_units": {"X": {"position_unit": "mm", "velocity_unit": "mm/s"}},
            "positions": {"p": {"X": 9.9}},
        }

    def test_save_records_native_units_for_referenced_axes_only(self, tmp_path):
        gantry = _make_gantry(axis_names=["X", "R"])
        gantry.axes["R"].position_unit = "degrees"
        gantry.axes["R"].velocity_unit = "degrees/s"
        session = MotionSession(gantry)
        session.positions = {"home": {"X": 1.0, "R": 90.0}}
        path = tmp_path / "mixed.json"

        session.save(path)

        raw = json.loads(path.read_text())
        assert raw["schema_version"] == 2
        assert raw["axis_units"] == {
            "X": {"position_unit": "mm", "velocity_unit": "mm/s"},
            "R": {"position_unit": "degrees", "velocity_unit": "degrees/s"},
        }
        assert raw["positions"] == session.positions

    @pytest.mark.parametrize("value", [True, nan, inf, -inf])
    def test_save_rejects_non_numeric_or_non_finite_positions(self, tmp_path, value):
        session = MotionSession(_make_gantry(axis_names=["X"]))
        session.positions = {"p": {"X": value}}

        with pytest.raises(ValueError):
            session.save(tmp_path / "invalid.json")

    def test_load_merges_not_replaces(self, tmp_path):
        gantry = _make_gantry()
        existing = {"existing": {"X": 0.0}}
        loaded_data = {"new": {"X": 5.0}}
        path = tmp_path / "deck.json"
        path.write_text(json.dumps(loaded_data))

        session = MotionSession(gantry)
        session.positions = dict(existing)
        session.load(path)
        assert "existing" in session.positions
        assert "new" in session.positions

    def test_load_overwrites_same_label(self, tmp_path):
        gantry = _make_gantry()
        path = tmp_path / "deck.json"
        path.write_text(json.dumps({"p": {"X": 99.0}}))
        session = MotionSession(gantry)
        session.positions = {"p": {"X": 0.0}}
        session.load(path)
        assert session.positions["p"]["X"] == 99.0

    def test_load_legacy_v1_for_linear_axes(self, tmp_path):
        path = tmp_path / "legacy.json"
        path.write_text(json.dumps({"home": {"X": 10.0}}))
        session = MotionSession(_make_gantry(axis_names=["X"]))

        session.load(path)

        assert session.positions == {"home": {"X": 10.0}}

    def test_load_legacy_v1_rejects_rotary_axis(self, tmp_path):
        path = tmp_path / "legacy.json"
        path.write_text(json.dumps({"home": {"R": 90.0}}))
        gantry = _make_gantry(axis_names=["R"])
        gantry.axes["R"].position_unit = "degrees"
        gantry.axes["R"].velocity_unit = "degrees/s"
        session = MotionSession(gantry)

        with pytest.raises(ValueError, match="legacy v1.*mm"):
            session.load(path)

        assert session.positions == {}

    @pytest.mark.parametrize("value", [True, nan, inf, -inf])
    def test_load_legacy_v1_rejects_non_numeric_or_non_finite_positions(self, tmp_path, value):
        path = tmp_path / "invalid-legacy.json"
        path.write_text(json.dumps({"home": {"X": value}}))
        session = MotionSession(_make_gantry(axis_names=["X"]))

        with pytest.raises(ValueError):
            session.load(path)

        assert session.positions == {}

    @pytest.mark.parametrize("schema_version", [1, 2])
    def test_load_rejects_unknown_axis(self, tmp_path, schema_version):
        path = tmp_path / "unknown-axis.json"
        if schema_version == 1:
            payload = {"home": {"BOGUS": 1.0}}
        else:
            payload = {
                "schema_version": 2,
                "axis_units": {"BOGUS": {"position_unit": "mm", "velocity_unit": "mm/s"}},
                "positions": {"home": {"BOGUS": 1.0}},
            }
        path.write_text(json.dumps(payload))
        session = MotionSession(_make_gantry(axis_names=["X"]))

        with pytest.raises(ValueError, match="Unknown axis"):
            session.load(path)

    def test_load_v2_requires_exact_unit_metadata_for_referenced_axes(self, tmp_path):
        path = tmp_path / "incomplete-units.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "axis_units": {},
                    "positions": {"home": {"X": 1.0}},
                }
            )
        )
        session = MotionSession(_make_gantry(axis_names=["X"]))

        with pytest.raises(ValueError, match="axis_units"):
            session.load(path)

    def test_load_v2_rejects_unreferenced_axis_metadata(self, tmp_path):
        path = tmp_path / "extra-units.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "axis_units": {
                        "X": {"position_unit": "mm", "velocity_unit": "mm/s"},
                        "Y": {"position_unit": "mm", "velocity_unit": "mm/s"},
                    },
                    "positions": {"home": {"X": 1.0}},
                }
            )
        )
        session = MotionSession(_make_gantry(axis_names=["X", "Y"]))

        with pytest.raises(ValueError, match="axis_units"):
            session.load(path)

    def test_load_v2_rejects_unit_mismatch(self, tmp_path):
        path = tmp_path / "mismatched-units.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "axis_units": {"R": {"position_unit": "mm", "velocity_unit": "mm/s"}},
                    "positions": {"home": {"R": 90.0}},
                }
            )
        )
        gantry = _make_gantry(axis_names=["R"])
        gantry.axes["R"].position_unit = "degrees"
        gantry.axes["R"].velocity_unit = "degrees/s"
        session = MotionSession(gantry)

        with pytest.raises(ValueError, match="unit mismatch"):
            session.load(path)

    def test_load_rejects_unknown_schema_version(self, tmp_path):
        path = tmp_path / "unknown-version.json"
        path.write_text(json.dumps({"schema_version": 3, "axis_units": {}, "positions": {}}))
        session = MotionSession(_make_gantry())

        with pytest.raises(ValueError, match="schema_version"):
            session.load(path)

    @pytest.mark.parametrize("value", [True, nan, inf, -inf])
    def test_load_rejects_non_numeric_or_non_finite_v2_positions(self, tmp_path, value):
        path = tmp_path / "invalid-position.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "axis_units": {"X": {"position_unit": "mm", "velocity_unit": "mm/s"}},
                    "positions": {"home": {"X": value}},
                }
            )
        )
        session = MotionSession(_make_gantry(axis_names=["X"]))

        with pytest.raises(ValueError):
            session.load(path)

        assert session.positions == {}

    def test_load_raises_on_missing_file(self):
        gantry = _make_gantry()
        session = MotionSession(gantry)
        with pytest.raises(OSError):
            session.load("/nonexistent/path/deck.json")
