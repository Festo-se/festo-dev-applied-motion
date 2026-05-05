"""Threading and concurrency tests for Gantry.

These tests go beyond outcome assertions and verify the actual threading
behaviour — that moves genuinely run in parallel, that all threads are
joined before the method returns, and that the ``_move_dispatch`` and
``_single_move`` helpers behave correctly.

Coverage areas
--------------
``_execute_concurrent_movements`` — true parallelism
    A ``threading.Barrier`` is used as the canonical proof of concurrency.
    If the implementation serialises the moves, only one thread ever reaches
    the barrier; the barrier times out and raises ``BrokenBarrierError``,
    which the test detects.  A peak-concurrency counter provides an
    independent measurement: it must equal the batch size.

``_execute_concurrent_movements`` — completion contract
    All axis.move() calls must finish before the method returns.  A shared
    ``set`` is updated under a lock inside each move stub; after the call
    returns the set must contain every axis name.

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
    - Consumes the movement dict via ``popitem()`` (documents mutation).

No hardware or network connection required.
"""

import threading
import time
from collections import deque
from unittest.mock import MagicMock, patch

import pytest

from festo_dev_applied_motion.gantry import AxisNotFoundError, Gantry
from festo_dev_applied_motion.backends.edcon_axis import EdconAxis

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
# _execute_concurrent_movements — true parallelism
# ---------------------------------------------------------------------------


class TestExecuteConcurrentMovementsParallelism:
    """Prove that _execute_concurrent_movements runs axis.move() calls in
    genuinely overlapping threads, not sequentially."""

    def test_both_axes_move_truly_concurrently(self):
        """Barrier proof: both axis.move() calls must overlap in time.

        A ``threading.Barrier(2)`` requires exactly two threads to call
        ``barrier.wait()`` before either is released.  If the
        implementation is sequential, the first thread waits alone until
        the barrier times out (BrokenBarrierError), ``barrier.broken``
        is set to True, and the assertion fails with a clear message.
        """
        barrier = threading.Barrier(2, timeout=2.0)

        def _rendezvous(**kwargs):
            barrier.wait()

        axis_x = _make_stub_axis("X")
        axis_z = _make_stub_axis("Z")
        axis_x.move.side_effect = _rendezvous
        axis_z.move.side_effect = _rendezvous

        g = Gantry(axes={"X": axis_x, "Z": axis_z})
        batch = [{"X": dict(_PARAMS)}, {"Z": dict(_PARAMS)}]

        g._execute_concurrent_movements(batch)

        assert not barrier.broken, (
            "_execute_concurrent_movements ran moves sequentially. "
            "Both axis.move() calls must be in-flight at the same time."
        )

    def test_peak_concurrent_move_count_equals_batch_size(self):
        """A peak-concurrency counter must reach N for a batch of N
        movements.  This is an independent proof that N threads are
        genuinely in-flight simultaneously — complementary to the barrier
        test above."""
        lock = threading.Lock()
        active = [0]
        peak = [0]
        barrier = threading.Barrier(2, timeout=2.0)

        def _counting_move(**kwargs):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            barrier.wait()  # hold both threads until both have incremented
            with lock:
                active[0] -= 1

        axis_x = _make_stub_axis("X")
        axis_z = _make_stub_axis("Z")
        axis_x.move.side_effect = _counting_move
        axis_z.move.side_effect = _counting_move

        g = Gantry(axes={"X": axis_x, "Z": axis_z})
        batch = [{"X": dict(_PARAMS)}, {"Z": dict(_PARAMS)}]
        g._execute_concurrent_movements(batch)

        assert peak[0] == 2, (
            f"Expected peak concurrency of 2 (one thread per axis), got {peak[0]}. "
            "Moves are not running in parallel."
        )

    def test_three_axes_all_concurrent(self):
        """Scaling test: three axes must all be in-flight simultaneously.
        The Barrier(3) requires all three threads to arrive before any
        is released."""
        barrier = threading.Barrier(3, timeout=2.0)

        def _rendezvous(**kwargs):
            barrier.wait()

        axes = {name: _make_stub_axis(name) for name in ["X", "Y", "Z"]}
        for axis in axes.values():
            axis.move.side_effect = _rendezvous

        g = Gantry(axes=axes)
        batch = [{name: dict(_PARAMS)} for name in ["X", "Y", "Z"]]
        g._execute_concurrent_movements(batch)

        assert not barrier.broken, (
            "Not all three axis.move() calls were in-flight simultaneously."
        )


# ---------------------------------------------------------------------------
# _execute_concurrent_movements — completion contract
# ---------------------------------------------------------------------------


class TestExecuteConcurrentMovementsCompletion:
    """Verify that all moves complete before the method returns — no
    fire-and-forget threads should be running after the call."""

    def test_all_moves_complete_before_return(self):
        """A shared completion set must contain every axis name as soon as
        _execute_concurrent_movements returns, proving that all threads were
        joined and not merely started."""
        completed: set[str] = set()
        lock = threading.Lock()

        def _slow_x(**kwargs):
            time.sleep(0.02)  # slightly slower than Z
            with lock:
                completed.add("X")

        def _fast_z(**kwargs):
            time.sleep(0.005)
            with lock:
                completed.add("Z")

        axis_x = _make_stub_axis("X")
        axis_z = _make_stub_axis("Z")
        axis_x.move.side_effect = _slow_x
        axis_z.move.side_effect = _fast_z

        g = Gantry(axes={"X": axis_x, "Z": axis_z})
        batch = [{"X": dict(_PARAMS)}, {"Z": dict(_PARAMS)}]
        g._execute_concurrent_movements(batch)

        assert completed == {"X", "Z"}, (
            f"Moves not complete at return time: completed={completed}. "
            "All worker threads must be joined before the method returns."
        )

    def test_slower_axis_does_not_block_return(self):
        """The method must not return before the slowest axis finishes.
        This verifies thread.join() is called for every thread, including
        those that complete last."""
        finish_times: dict[str, float] = {}
        lock = threading.Lock()

        def _slow_move(**kwargs):
            time.sleep(0.05)
            with lock:
                finish_times["slow"] = time.monotonic()

        def _fast_move(**kwargs):
            with lock:
                finish_times["fast"] = time.monotonic()

        axis_x = _make_stub_axis("X")
        axis_z = _make_stub_axis("Z")
        axis_x.move.side_effect = _slow_move
        axis_z.move.side_effect = _fast_move

        g = Gantry(axes={"X": axis_x, "Z": axis_z})
        batch = [{"X": dict(_PARAMS)}, {"Z": dict(_PARAMS)}]

        return_time = None
        g._execute_concurrent_movements(batch)
        return_time = time.monotonic()

        assert "slow" in finish_times, "Slow axis move never ran"
        assert return_time >= finish_times["slow"], (
            "Method returned before the slow axis finished — "
            "the slow thread was not properly joined."
        )


# ---------------------------------------------------------------------------
# _move_dispatch — concurrent and sequential paths
# ---------------------------------------------------------------------------


class TestMoveDispatch:
    """Verify _move_dispatch routing and the per-path return contract."""

    def test_concurrent_true_calls_single_move_for_each_movement(self):
        """When concurrent=True, _move_dispatch must call _single_move
        exactly once for every movement in the deque."""
        axis_x = _make_stub_axis("X")
        axis_z = _make_stub_axis("Z")
        g = Gantry(axes={"X": axis_x, "Z": axis_z})

        # Provide two-element dicts so _single_move's popitem() won't fail.
        movements = deque([
            {"X": dict(_PARAMS)},
            {"Z": dict(_PARAMS)},
        ])

        call_log: list[str] = []
        original_single_move = g._single_move

        def _recording_single_move(movement, timeout=None):
            name = next(iter(movement))  # peek without consuming
            call_log.append(name)
            return original_single_move(movement, timeout=timeout)

        with patch.object(g, "_single_move", side_effect=_recording_single_move):
            result = g._move_dispatch(movements, concurrent=True)

        assert set(call_log) == {"X", "Z"}, (
            f"Expected _single_move called for X and Z, got {call_log}"
        )

    def test_concurrent_true_returns_tuple_of_results(self):
        """The concurrent path must return a tuple whose length equals the
        batch size, one entry per movement."""
        axis_x = _make_stub_axis("X")
        axis_z = _make_stub_axis("Z")
        g = Gantry(axes={"X": axis_x, "Z": axis_z})

        movements = deque([{"X": dict(_PARAMS)}, {"Z": dict(_PARAMS)}])
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
        axis_z = _make_stub_axis("Z")
        g = Gantry(axes={"X": axis_x, "Z": axis_z})

        movements = deque([{"X": dict(_PARAMS)}, {"Z": dict(_PARAMS)}])

        with patch.object(g, "_single_move", return_value=0) as mock_single:
            g._move_dispatch(movements, concurrent=False)

        assert mock_single.call_count == 2, (
            f"Sequential path must call _single_move for every movement "
            f"(expected 2, got {mock_single.call_count})"
        )

    def test_concurrent_false_processes_at_least_first_movement(self):
        """Document the current behaviour of the sequential path: it
        dispatches the first movement and returns immediately, leaving
        any remaining movements in the deque."""
        axis_x = _make_stub_axis("X")
        g = Gantry(axes={"X": axis_x})

        movements = deque([{"X": dict(_PARAMS)}, {"X": dict(_PARAMS)}])

        with patch.object(g, "_single_move", return_value=0) as mock_single:
            result = g._move_dispatch(movements, concurrent=False)

        assert mock_single.call_count >= 1, "_single_move must be called at least once"
        assert result == (0,), "Sequential path must return a 1-tuple of the first result"


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
        that the caller requested."""
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
        AxisNotFoundError so the caller has a uniform error type to
        catch, regardless of the underlying drive error."""
        axis = _make_stub_axis("X")
        axis.move.side_effect = RuntimeError("drive fault")
        g = Gantry(axes={"X": axis})
        with pytest.raises(AxisNotFoundError):
            g._single_move({"X": dict(_PARAMS)})

    def test_axis_not_found_error_chained_from_original_exception(self):
        """The AxisNotFoundError must chain the original exception via
        __cause__ so the full diagnostic is available in tracebacks."""
        axis = _make_stub_axis("X")
        original = RuntimeError("drive fault")
        axis.move.side_effect = original
        g = Gantry(axes={"X": axis})
        with pytest.raises(AxisNotFoundError) as exc_info:
            g._single_move({"X": dict(_PARAMS)})
        assert exc_info.value.__cause__ is original

    def test_movement_dict_is_consumed_by_popitem(self):
        """_single_move calls dict.popitem() which removes the entry.
        Callers must not reuse the dict after the call — this test
        documents and pins that mutation so it is not accidentally removed."""
        axis = _make_stub_axis("X")
        g = Gantry(axes={"X": axis})
        movement = {"X": dict(_PARAMS)}
        g._single_move(movement)
        assert len(movement) == 0, (
            "popitem() must have consumed the movement entry. "
            "If this fails, the internal API contract has changed."
        )

    def test_missing_axis_raises_axis_not_found_error(self):
        """If the axis named in the movement is not registered in
        self.axes, AxisNotFoundError must still be raised (via the
        general exception handler)."""
        g = Gantry(axes={})
        with pytest.raises(AxisNotFoundError):
            g._single_move({"MISSING": dict(_PARAMS)})
