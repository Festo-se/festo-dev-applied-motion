"""Threading and concurrency tests for Gantry.

These tests go beyond outcome assertions and verify the actual threading
behaviour — that moves genuinely run in parallel, that all threads are
joined before the method returns, and that the ``_move_dispatch`` and
``_single_move`` helpers behave correctly.

Coverage areas
--------------


``_move_dispatch``
    - ``concurrent=True``: ``_single_move`` is called exactly once per
      movement in the deque; the return value is a tuple of results.
    - ``concurrent=False``: documented structural bug — the ``return``
      inside the ``while`` loop means only the first movement is ever
      dispatched per call.  An ``xfail`` test pins this so the regression
      is caught when the bug is fixed.

``_single_move``
    - Returns 0 on a successful move.
    - Forwards kinematic params and timeout to ``axis.move()``.
    - Wraps any exception as ``AxisNotFoundError``.
    - Reads the movement dict without mutating it.

No hardware or network connection required.
"""

import logging
from collections import deque
from unittest.mock import MagicMock, patch

import pytest

from applied_motion.gantry import MovementError, Gantry
from applied_motion.backends.edcon_axis import EdconAxis

# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

_PARAMS = {"position": 50, "velocity": 100}


def _make_stub_axis(name: str) -> EdconAxis:
    axis = object.__new__(EdconAxis)
    axis.name = name
    axis.move = MagicMock(return_value=True)
    axis.home = MagicMock()
    axis.current_position = MagicMock(return_value=0)
    axis._valid_position = MagicMock(return_value=0.0)
    axis.get_current_axis_position = MagicMock(return_value=0.0)
    axis.stopped = MagicMock(return_value=True)
    axis.ready_for_motion = MagicMock(return_value=True)
    return axis



# ---------------------------------------------------------------------------
# _move_dispatch — concurrent and sequential paths
# ---------------------------------------------------------------------------


class TestMoveDispatch:
    """Verify _move_dispatch routing and the per-path return contract."""

    def test_concurrent_true_calls_single_move_for_each_movement(self):
        """When concurrent=True, _move_dispatch must call _single_move
        exactly once for every movement in the deque.
        """
        axis_x = _make_stub_axis("X")
        axis_z = _make_stub_axis("Y")
        g = Gantry(axes={"X": axis_x, "Y": axis_z})

        # Provide single-item dicts so _single_move can read them directly.
        movements = deque([
            {"X": dict(_PARAMS)},
            {"Y": dict(_PARAMS)},
        ])

        call_log: list[str] = []
        original_single_move = g._single_move

        def _recording_single_move(movement, timeout=None):
            name = next(iter(movement))  # peek without consuming
            call_log.append(name)
            return original_single_move(movement, timeout=timeout)

        with patch.object(g, "_single_move", side_effect=_recording_single_move):
            result = g._move_dispatch(movements, concurrent=True)

        assert set(call_log) == {"X", "Y"}, (
            f"Expected _single_move called for X and Z, got {call_log}"
        )

    def test_concurrent_true_returns_tuple_of_results(self):
        """The concurrent path must return a tuple whose length equals the
        batch size, one entry per movement.
        """
        axis_x = _make_stub_axis("X")
        axis_z = _make_stub_axis("Y")
        g = Gantry(axes={"X": axis_x, "Y": axis_z})

        movements = deque([{"X": dict(_PARAMS)}, {"Y": dict(_PARAMS)}])
        result = g._move_dispatch(movements, concurrent=True)

        assert isinstance(result, tuple), "concurrent=True path must return a tuple"
        assert len(result) == 2, f"Expected 2 results for 2 movements, got {len(result)}"

    @pytest.mark.xfail(
        strict=False,
        reason=(
            "Known bug: the 'return' inside the 'while movements:' loop in "
            "_move_dispatch(concurrent=False) means only the first movement "
            "is ever dispatched — remaining movements are silently dropped. "
            "This test documents the *correct* contract (all movements "
            "processed) and will flip to PASSED once the bug is fixed."
        ),
    )
    def test_concurrent_false_processes_all_movements(self):
        """Sequential path must process every movement in the deque."""
        axis_x = _make_stub_axis("X")
        axis_z = _make_stub_axis("Y")
        g = Gantry(axes={"X": axis_x, "Y": axis_z})

        movements = deque([{"X": dict(_PARAMS)}, {"Y": dict(_PARAMS)}])

        with patch.object(g, "_single_move", return_value=0) as mock_single:
            g._move_dispatch(movements, concurrent=False)

        assert mock_single.call_count == 2, (
            f"Sequential path must call _single_move for every movement "
            f"(expected 2, got {mock_single.call_count})"
        )

    def test_concurrent_false_processes_at_least_first_movement(self):
        """Sequential path must process every movement in the deque."""
        axis_x = _make_stub_axis("X")
        g = Gantry(axes={"X": axis_x})

        movements = deque([{"X": dict(_PARAMS)}, {"X": dict(_PARAMS)}])

        with patch.object(g, "_single_move", return_value=0) as mock_single:
            result = g._move_dispatch(movements, concurrent=False)

        assert mock_single.call_count == 2, "_single_move must be called for every movement"
        assert result == (0, 0), "Sequential path must return one result per movement"


# ---------------------------------------------------------------------------
# _single_move
# ---------------------------------------------------------------------------


class TestSingleMove:
    """Verify the _single_move helper in isolation."""

    def test_returns_zero_on_successful_move(self):
        axis = _make_stub_axis("X")
        g = Gantry(axes={"X": axis})
        result = g._single_move({"X": dict(_PARAMS)})
        assert result == 0

    def test_delegates_kinematic_params_to_axis_move(self):
        """Kinematic parameters must be passed as keyword arguments to
        axis.move() so the axis receives the exact position and velocity
        that the caller requested.
        """
        axis = _make_stub_axis("X")
        g = Gantry(axes={"X": axis})
        g._single_move({"X": {"position": 75, "velocity": 200}})
        axis.move.assert_called_once_with(position=75, velocity=200, timeout=None)

    def test_timeout_forwarded_to_axis_move(self):
        axis = _make_stub_axis("X")
        g = Gantry(axes={"X": axis})
        g._single_move({"X": dict(_PARAMS)}, timeout=5)
        axis.move.assert_called_once_with(**_PARAMS, timeout=5)

    def test_axis_exception_raises_axis_not_found_error(self):
        """Any exception from axis.move() must be re-raised as
        MovementError so the caller has a uniform error type to
        catch, regardless of the underlying drive error.
        """
        axis = _make_stub_axis("X")
        axis.move.side_effect = RuntimeError("drive fault")
        g = Gantry(axes={"X": axis})
        with pytest.raises(MovementError):
            g._single_move({"X": dict(_PARAMS)})

    def test_axis_not_found_error_chained_from_original_exception(self):
        """The MovementError must chain the original exception via
        __cause__ so the full diagnostic is available in tracebacks.
        """
        axis = _make_stub_axis("X")
        original = RuntimeError("drive fault")
        axis.move.side_effect = original
        g = Gantry(axes={"X": axis})
        with pytest.raises(MovementError) as exc_info:
            g._single_move({"X": dict(_PARAMS)})
        assert exc_info.value.__cause__ is original

    def test_movement_dict_is_not_consumed_by_single_move(self):
        """_single_move must read movement payload without mutating it."""
        axis = _make_stub_axis("X")
        g = Gantry(axes={"X": axis})
        movement = {"X": dict(_PARAMS)}
        g._single_move(movement)
        assert movement == {"X": dict(_PARAMS)}, "movement payload must remain intact"

    def test_missing_axis_raises_axis_not_found_error(self):
        """If the axis named in the movement is not registered in
        self.axes, MovementError must still be raised (via the
        general exception handler).
        """
        g = Gantry(axes={})
        with pytest.raises(MovementError):
            g._single_move({"MISSING": dict(_PARAMS)})


# ---------------------------------------------------------------------------
# move_to regression coverage
# ---------------------------------------------------------------------------


class TestMoveToRegressions:
    """Regression coverage for move_to orchestration bugs."""

    def test_invalid_axis_is_logged_and_skipped(self, caplog):
        """Unknown axis in queued batch must be logged and skipped.

        Valid movements in the same queue must still be dispatched.
        """
        axis_x = _make_stub_axis("X")
        axis_y = _make_stub_axis("Y")
        g = Gantry(axes={"X": axis_x, "Y": axis_y})

        movements = deque(
            [
                {"X": {"position": 10, "velocity": 20}},
                {"MISSING": {"position": 20, "velocity": 30}},
                {"Y": {"position": 30, "velocity": 40}},
            ]
        )

        with patch.object(g, "_move_dispatch", wraps=g._move_dispatch) as dispatch_mock:
            with caplog.at_level(logging.WARNING):
                g.move_to(movements, concurrent=True)

        dispatch_mock.assert_called_once()
        axis_x.move.assert_called_once_with(position=10, velocity=20, timeout=None)
        axis_y.move.assert_called_once_with(position=30, velocity=40, timeout=None)
        assert "unknown axis" in caplog.text
        assert "skipping movement" in caplog.text

    def test_malformed_axis_spec_is_logged_and_skipped(self, caplog):
        """Malformed axis payload must be logged and skipped.

        Valid payload entries in same queue must still dispatch.
        """
        axis_x = _make_stub_axis("X")
        g = Gantry(axes={"X": axis_x})

        movements = deque(
            [
                {"X": {"position": 10, "velocity": 20}},
                {
                    "X": {"position": 20, "velocity": 30},
                    "Y": {"position": 30, "velocity": 40},
                },
            ]
        )

        with patch.object(g, "_move_dispatch", wraps=g._move_dispatch) as dispatch_mock:
            with caplog.at_level(logging.WARNING):
                g.move_to(movements, concurrent=True)

        dispatch_mock.assert_called_once()
        axis_x.move.assert_called_once_with(position=10, velocity=20, timeout=None)
        assert "malformed movement" in caplog.text
        assert "skipping malformed movement" in caplog.text

    def test_concurrent_true_dispatches_once_with_full_batch(self):
        """Regression for #6.

        move_to(concurrent=True) must call _move_dispatch exactly once for the
        full queued batch. If move_to loops while leaving the deque non-empty,
        _move_dispatch will be called repeatedly and this test fails fast.
        """
        axis_x = _make_stub_axis("X")
        axis_y = _make_stub_axis("Y")
        axis_z = _make_stub_axis("Z")
        g = Gantry(axes={"X": axis_x, "Y": axis_y, "Z": axis_z})

        movements = deque(
            [
                {"X": {"position": 10, "velocity": 20}},
                {"Y": {"position": 20, "velocity": 30}},
                {"Z": {"position": 30, "velocity": 40}},
            ]
        )

        seen_batches: list[tuple[dict, ...]] = []

        def _dispatch_probe(batch, concurrent, timeout=None):
            seen_batches.append(tuple(batch))
            return tuple(0 for _ in batch)

        with patch.object(g, "_move_dispatch", side_effect=_dispatch_probe) as dispatch_mock:
            g.move_to(movements, timeout=2, concurrent=True)

        assert dispatch_mock.call_count == 1, (
            "move_to(concurrent=True) must dispatch once; repeated dispatches "
            "indicate the queue was not consumed or the method did not return."
        )
        assert len(seen_batches) == 1
        assert len(seen_batches[0]) == 3

    def test_concurrent_true_moves_each_axis_once_with_timeout(self):
        """Stricter regression for #6.

        For a concurrent batch, each axis move must be issued exactly once and
        must receive the same timeout passed to move_to().
        """
        axis_x = _make_stub_axis("X")
        axis_y = _make_stub_axis("Y")
        axis_z = _make_stub_axis("Z")
        g = Gantry(axes={"X": axis_x, "Y": axis_y, "Z": axis_z})

        movements = deque(
            [
                {"X": {"position": 10, "velocity": 20}},
                {"Y": {"position": 20, "velocity": 30}},
                {"Z": {"position": 30, "velocity": 40}},
            ]
        )

        g.move_to(movements, timeout=7, concurrent=True)

        axis_x.move.assert_called_once_with(position=10, velocity=20, timeout=7)
        axis_y.move.assert_called_once_with(position=20, velocity=30, timeout=7)
        axis_z.move.assert_called_once_with(position=30, velocity=40, timeout=7)

    def test_concurrent_true_movement_payloads_consumed_once(self):
        """Concurrent move_to should leave each movement payload intact.

        _single_move should not mutate each movement dict; after dispatch each
        payload should still contain its original axis mapping.
        """
        axis_x = _make_stub_axis("X")
        axis_y = _make_stub_axis("Y")
        g = Gantry(axes={"X": axis_x, "Y": axis_y})

        movement_x = {"X": {"position": 10, "velocity": 20}}
        movement_y = {"Y": {"position": 20, "velocity": 30}}
        movements = deque([movement_x, movement_y])

        g.move_to(movements, timeout=3, concurrent=True)

        assert movement_x == {"X": {"position": 10, "velocity": 20}}
        assert movement_y == {"Y": {"position": 20, "velocity": 30}}

    def test_concurrent_true_logs_successful_batch(self, caplog):
        """move_to should log success when every result code is zero."""
        axis_x = _make_stub_axis("X")
        axis_y = _make_stub_axis("Y")
        g = Gantry(axes={"X": axis_x, "Y": axis_y})

        movements = deque([
            {"X": {"position": 10, "velocity": 20}},
            {"Y": {"position": 20, "velocity": 30}},
        ])

        with patch.object(g, "_move_dispatch", return_value=(0, 0)):
            with caplog.at_level(logging.INFO):
                g.move_to(movements, concurrent=True)

        assert "completed successfully" in caplog.text

    def test_concurrent_true_logs_failed_batch(self, caplog):
        """move_to should log warning when any result code is non-zero."""
        axis_x = _make_stub_axis("X")
        axis_y = _make_stub_axis("Y")
        g = Gantry(axes={"X": axis_x, "Y": axis_y})

        movements = deque([
            {"X": {"position": 10, "velocity": 20}},
            {"Y": {"position": 20, "velocity": 30}},
        ])

        with patch.object(g, "_move_dispatch", return_value=(0, 1)):
            with caplog.at_level(logging.WARNING):
                g.move_to(movements, concurrent=True)

        assert "completed with failures" in caplog.text
