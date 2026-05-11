# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG


"""Festo gantry axis and multi-axis gantry abstractions.

This module provides :class:`Gantry`, which coordinates one or more axes
for sequential or concurrent positioning, and re-exports the axis backends for
convenience.

Two axis backends are available:

* :class:`~applied_motion.backends.edcon_axis.EdconAxis` — direct Modbus TCP
  connection to an individual CMMT/CMMT-ST drive via ``festo-edcon``.
* :class:`~applied_motion.backends.fposbapi_axis.FPosBAxis` — TCP socket
  connection to a CECC-X PLC running the FPosBAPI CoDeSys server.

Use :meth:`Gantry.from_config` to instantiate the correct backend
automatically from a JSON configuration dict or file.
"""

import json
import logging
import time

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Thread

from applied_motion.backends.axis_protocol import Axis
from applied_motion.backends.fposbapi_axis import FPosBAxis
from applied_motion.backends.fposbapi_client import FPosBAPIClient
from applied_motion.backends.edcon_axis import EdconAxis


logger = logging.getLogger(__name__)


class MovementError(Exception):
    """Raised when a gantry or axis movement fails."""


class AxisNotFoundError(MovementError):
    """Raised when an axis name referenced in a movement command does not exist."""


class Gantry:
    """Coordinate multiple :class:`EdconAxis` objects as a single gantry.

    Dispatches sequential or concurrent move commands to the individual
    axes and provides convenience methods for homing, status queries, and
    position readback.

    Attributes:
        axes: Mapping of axis name → :class:`EdconAxis` for all registered axes.
        concurrent_axes: Optional mapping of axes that may move simultaneously.
            When ``None``, no concurrent grouping is applied.
    """

    axes: dict[str, Axis]
    concurrent_axes: dict[str, Axis] | None

    def __init__(
        self,
        axes: dict[str, Axis],
        concurrent_axes: dict[str, Axis] | None = None,
        *,
        _client: FPosBAPIClient | None = None,
    ) -> None:
        """Initialise the gantry with the provided axis mapping.

        Prefer :meth:`from_config` for production use; it selects the correct
        backend (Modbus or FPosBAPI) and creates axes automatically from a JSON
        configuration dict or file.

        Args:
            axes: Dict mapping axis names to axis instances.  Accepts both
                :class:`EdconAxis` (Modbus backend) and
                :class:`~applied_motion.backends.fposbapi_axis.FPosBAxis`
                (FPosBAPI backend) — any object satisfying
                :class:`~applied_motion.backends.axis_protocol.Axis`.
            concurrent_axes: Optional dict of axes that are allowed to move
                simultaneously.  Pass ``None`` (default) to disable concurrent
                grouping.
            _client: Internal.  The shared
                :class:`~applied_motion.backends.fposbapi_client.FPosBAPIClient`
                instance when using the FPosBAPI backend.  Set by
                :meth:`from_config`; do not pass directly.
        """
        self.axes = axes
        self.concurrent_axes = concurrent_axes
        self._client: FPosBAPIClient | None = _client
        logger.info("Gantry initialized with axes: %s", list(axes.keys()))
        if concurrent_axes:
            logger.debug("Concurrent axes: %s", list(concurrent_axes.keys()))
        self.gantry = self  # Experimental

    def __repr__(self) -> str:
        """Return an unambiguous string representation of the gantry."""
        axis_names = list(self.axes.keys())
        return f"Gantry(axes={axis_names!r})"

    def __eq__(self, other: object) -> bool:
        """Return True when *other* has the same axes and concurrent-axis configuration.

        Args:
            other: Object to compare against.

        Returns:
            ``True`` if *other* is a :class:`Gantry` with equal ``axes``
            and ``concurrent_axes`` mappings; ``False`` otherwise.
        """
        if not isinstance(other, Gantry):
            return NotImplemented
        return self.axes == other.axes and self.concurrent_axes == other.concurrent_axes

    def __hash__(self) -> int:
        """Return a hash based on object identity.

        :class:`Gantry` is mutable (axes are a mutable dict), so
        hashing by value is not safe.  ``id(self)`` is used as a stable
        fallback so instances remain usable in sets and as dict keys.
        """
        return id(self)

    def __len__(self) -> int:
        """Return the number of axes registered with this gantry."""
        return len(self.axes)

    def __iter__(self):
        """Iterate over the axis names registered with this gantry."""
        return iter(self.axes)

    def __contains__(self, item: object) -> bool:
        """Return True if *item* is the name of a registered axis.

        Args:
            item: Axis name to look up.

        Returns:
            ``True`` if *item* is a key in :attr:`axes`.
        """
        return item in self.axes

    def _execute_single_movement(
        self, axis_name: str, kinematic_params: dict, timeout: int | None = None
    ) -> tuple[str, bool, Exception | None]:
        """Execute a single axis movement and return a structured result tuple.

        Args:
            axis_name: Key into :attr:`axes` identifying the target axis.
            kinematic_params: Dict of keyword arguments forwarded to
                :meth:`EdconAxis.move` (e.g. ``{"position": 100.0,
                "velocity": 50.0}``).
            timeout: Optional per-move time limit in seconds forwarded to
                :meth:`EdconAxis.move`.

        Returns:
            A three-tuple ``(axis_name, success, exception)`` where
            *success* is ``True`` on a clean move, ``False`` on any failure,
            and *exception* is the caught exception or ``None`` on success.
        """
        logger.debug("_execute_single_movement: axis='%s' params=%s timeout=%s", axis_name, kinematic_params, timeout)
        try:
            self.axes[axis_name].move(**kinematic_params, timeout=timeout)
            logger.debug("_execute_single_movement: axis='%s' succeeded", axis_name)
            return (axis_name, True, None)
        except KeyError:
            logger.error("_execute_single_movement: axis '%s' not found in gantry axes", axis_name)
            return (axis_name, False, AxisNotFoundError(f"Axis {axis_name} not found"))
        except Exception as e:
            logger.error("_execute_single_movement: axis '%s' raised %s", axis_name, e)
            return (axis_name, False, e)

    def _execute_concurrent_movements(self, movements_batch: list[dict], timeout: int | None = None) -> int:
        """Execute multiple movements concurrently using threads.

        Args:
            movements_batch: List of movement dicts, each like {"axis_name": {kinematic_params}}
            timeout: Optional timeout in seconds for each movement

        Returns:
            0 if all succeeded, 1 if any failed
        """
        logger.info("Executing %d concurrent movement(s)", len(movements_batch))
        threads = []
        results = []
        # if len(movements_batch) > os.MAX_THREADS:
        #     raise RuntimeError("Request thread count greater than threads available")

        for movement in movements_batch:
            axis_name, kinematic_params = list(movement.items())[0]
            thread = Thread(
                target=lambda a=axis_name, k=kinematic_params: results.append(
                    self._execute_single_movement(a, k, timeout=timeout)
                )
            )
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # Check results
        for axis_name, success, exception in results:
            if not success:
                logger.error("Movement failed for axis '%s': %s", axis_name, exception)
                if isinstance(exception, AxisNotFoundError):
                    raise exception
                return 1

        logger.info("All concurrent movements completed successfully")
        return 0

    def _move_dispatch(self, movements: deque, concurrent: bool, timeout: int | None = None):
        """Dispatch a batch of movements either concurrently or sequentially.

        Args:
            movements: :class:`~collections.deque` of movement dicts to execute.
            concurrent: When ``True``, all movements are executed in parallel
                via :class:`~concurrent.futures.ThreadPoolExecutor`.
                When ``False``, movements are executed one at a time.
            timeout: Optional per-move time limit in seconds forwarded to each
                :meth:`EdconAxis.move` call.

        Returns:
            Tuple of integer result codes, one per movement dispatched.
        """
        if concurrent:
            with ThreadPoolExecutor(max_workers=len(movements)) as executor:
                move_results = executor.map(self._single_move, movements, timeout=timeout)
                return tuple(res for res in move_results)  # TODO: Timeout result?
        else:
            while movements:
                movement = movements.popleft()
                return (self._single_move(movement=movement, timeout=timeout),)

    def _single_move(self, movement: dict, timeout: int | None = None) -> int:
        """Execute one movement dict and return an integer result code.

        Pops the sole ``{axis_name: kinematic_params}`` entry from *movement*
        and delegates to :meth:`EdconAxis.move`.

        Args:
            movement: Single-item dict mapping an axis name to its kinematic
                parameter dict.  The dict is mutated (item is popped).
            timeout: Optional per-move time limit in seconds forwarded to
                :meth:`EdconAxis.move`.

        Returns:
            ``0`` on success, ``1`` on failure.

        Raises:
            AxisNotFoundError: If the axis name is not found in :attr:`axes`.
        """
        axis_name, kinematic_params = movement.popitem()
        logger.debug("_single_move: axis='%s' params=%s timeout=%s", axis_name, kinematic_params, timeout)
        try:
            self.axes[axis_name].move(**kinematic_params, timeout=timeout)
            success = True  # self.axes[axis_name].wait_for_position_motion_execution()
            move_result = int(not (success))
            logger.debug("_single_move: axis='%s' result=%s", axis_name, move_result)
        except Exception as e:
            logger.error("_single_move: axis '%s' not found or raised error: %s", axis_name, e)
            move_result = 1
            raise MovementError(f"Axis {axis_name} move failed: {e}") from e
        return move_result

    def _get_next_moves(self, movements: deque, concurrent_axes: dict[str, Axis], timeout: int | None) -> deque:
        """Pull the next group of movements that may run concurrently.

        Consumes entries from the front of *movements* as long as they
        belong to axes listed in *concurrent_axes*.  Returns a deque
        containing that concurrent batch (may be a single-item deque if
        the first movement's axis is not in *concurrent_axes*).

        Args:
            movements: Queue of pending movement dicts.  Entries are popped
                from the left as they are consumed.
            concurrent_axes: Dict of axes that are permitted to move at the
                same time.  Acts as a filter — only axes present here are
                batched together.
            timeout: Reserved for future use; not consumed by this method.

        Returns:
            A :class:`~collections.deque` containing the next batch of
            movements to dispatch concurrently.
        """
        next = deque()
        concurrent_working_reference = concurrent_axes.copy()
        #
        # Filter rule is:
        # Deep copy a temporary reference to concurrent_axes for comparison and accounting
        # Grab Move
        # if move axis is in temp ref,
        #   remove axis from temp ref
        #   while next move is (still) in (temp ref) concurrent_axes
        #       Grab that move
        #       Remove that axis from temporary reference dict/set of concurrent_axes (previously, deep copy?)
        # else:
        #   Execute grabbed moves
        #       while move container is not empty
        #           Assign each grabbed move to own thread
        #       Launch threads
        #
        movement = movements.popleft()
        next.append(movement)
        while concurrent_working_reference:
            (axis_name, kinematic_params) = movement.items()
            if axis_name not in concurrent_working_reference:
                return next
            else:
                del concurrent_working_reference[axis_name]
                next.append(movement)
            movement = movements.popleft()

        return next

    # movements =  {"axis_name": {"position": pos, "velocity": speed}, "axis_name": {"position": pos, "velocity": speed} }
    # ( ""{"name": axis_name, "id" : axis_id , "position": coord, "velocity": speed})
    def move_to(self, movements: deque, timeout: int | None = None, concurrent: bool = False) -> None:
        """Dispatch a queue of movements to the gantry axes.

        Processes each movement dict in *movements*, dispatching them either
        sequentially or concurrently according to *concurrent* and the
        gantry's ``concurrent_axes`` configuration.

        Args:
            movements: A :class:`~collections.deque` of movement dicts.  Each
                dict maps a single axis name to its kinematic parameters, e.g.
                ``{"X": {"position": 100.0, "velocity": 50.0}}``.
            timeout: Optional per-move time limit in seconds.  Passed through
                to each :meth:`EdconAxis.move` call.
            concurrent: When ``True``, all movements in the current batch are
                dispatched in parallel threads.  When ``False`` (default),
                movements are grouped using ``concurrent_axes`` and dispatched
                sequentially.
        """
        # TODO: Use queue.Queue instead? deque?
        # Queue: put, get methods
        # deque: append, popleft methods

        logger.info("move_to: %d movement(s) queued, concurrent=%s, timeout=%s", len(movements), concurrent, timeout)
        # initiate move

        # Assign axis moves to own thread each OR filter by queue simultaneous moves.

        # if concurrent:
        #   launch all as concurrent
        while movements:
            if concurrent:
                # self._move_dispatch(movements, concurrent=concurrent)
                self._move_dispatch(movements, concurrent, timeout=timeout)
            else:
                concurrent_axes = self.concurrent_axes or {}
                next_moves = self._get_next_moves(movements, concurrent_axes, timeout)
                self._move_dispatch(next_moves, concurrent=True, timeout=timeout)

        # else:
        #   start processing movements
        #       at least, the next movement will be dispatched

    def home(self) -> None:
        """Home all registered axes.

        For the **FPosBAPI backend**, sends a single ``HOME`` command to the
        CECC-X PLC which homes all axes in a coordinated sequence.

        For the **Modbus backend**, iterates over every axis in insertion
        order and calls :meth:`EdconAxis.home` on each one sequentially.
        """
        if self._client is not None:
            logger.info("Gantry.home: sending HOME via FPosBAPI client")
            self._client.send_command("HOME")
        else:
            for axis in self.axes:
                self.axes[axis].home()
        logger.info("All axes homed")

    @classmethod
    def from_config(cls, config: dict | Path, name: str = "gantry_1") -> "Gantry":
        """Instantiate a :class:`Gantry` from a configuration dict or JSON file.

        Reads the ``backend`` key (defaults to ``"modbus"`` when absent for
        backward compatibility with spec version 1.0 configs) and creates the
        appropriate axis instances:

        * ``"modbus"`` — creates :class:`EdconAxis` instances, one per axis
          entry, using the ``ip`` field from each axis config.
        * ``"fposbapi"`` — creates one shared
          :class:`~applied_motion.backends.fposbapi_client.FPosBAPIClient` from the
          top-level ``connection`` block, then creates
          :class:`~applied_motion.backends.fposbapi_axis.FPosBAxis` instances
          using the ``index`` field from each axis config.

        Config schema (JSON):

        .. code-block:: json

            {
                "backend": "modbus",
                "axes": {
                    "X": {"name": "X", "ip": "192.168.0.193"}
                },
                "gantry": {
                    "axis_order": ["X"],
                    "concurrent_axes": null
                }
            }

        FPosBAPI variant:

        .. code-block:: json

            {
                "backend": "fposbapi",
                "interface": {"type":"tcp/ip","ip": "192.168.10.10", "port": 1234},
                "axes": {
                    "X": {"name": "X", "index": 1},
                    "Y": {"name": "Y", "index": 2},
                    "Z": {"name": "Z", "index": 3}
                },
                "gantry": {
                    "axis_order": ["X", "Y", "Z"],
                    "concurrent_axes": null
                }
            }

        Args:
            config: Either a :class:`dict` containing the parsed configuration,
                or a :class:`~pathlib.Path` to a JSON file on disk.
            name: Unique key/name of gantry in config file. Used to select intended gantry.

        Returns:
            A fully initialised :class:`Gantry` with axes created for the
            specified backend.

        Raises:
            ValueError: If ``backend`` is set to an unrecognised value.
            KeyError: If required config fields are missing.
            OSError: (FPosBAPI only) If the TCP connection to the CECC-X cannot
                be established.
        """
        if isinstance(config, Path):
            with config.open() as fh:
                config = json.load(fh)
        logger.debug("config import: ", config)
        # TODO: Festo config validation and config spec alignment
        parsed_config = {}
        if "component_config" in config:
            parsed_config = config["component_config"]
            import pprint

            pprint.pprint(parsed_config)
            logger.debug("parsed config: ", parsed_config)

        logger.debug("parsed config scoped: ", parsed_config)
        gantry_cfg = parsed_config["components"][name]
        backend: str = gantry_cfg.get("backend", "modbus")
        axes_cfg: dict = gantry_cfg["axes"]
        axis_order: list[str] = gantry_cfg.get("axis_order", list(axes_cfg.keys()))
        concurrent_raw: list[str] | None = gantry_cfg.get("concurrent_axes")

        if backend == "modbus":
            axes: dict[str, Axis] = {
                name: EdconAxis(
                    name=axes_cfg[name]["name"],
                    ip=axes_cfg[name]["ip"],
                    run_referencing=axes_cfg[name].get("run_referencing", False),
                )
                for name in axis_order
            }
            concurrent_axes: dict[str, Axis] | None = (
                {name: axes[name] for name in concurrent_raw if name in axes} if concurrent_raw else None
            )
            logger.info("Gantry.from_config: modbus backend, axes=%s", axis_order)
            return cls(axes=axes, concurrent_axes=concurrent_axes)

        if backend == "fposbapi":
            conn = gantry_cfg["interface"]
            client = FPosBAPIClient(ip=conn["ip"], port=conn.get("port", 1234))
            try:
                client.send_command("ENABLE")
            except Exception:
                client.close()
                raise
            fposb_axes: dict[str, Axis] = {
                name: FPosBAxis(
                    name=axes_cfg[name]["name"],
                    index=axes_cfg[name]["index"],
                    client=client,
                )
                for name in axis_order
            }
            fposb_concurrent: dict[str, Axis] | None = (
                {name: fposb_axes[name] for name in concurrent_raw if name in fposb_axes} if concurrent_raw else None
            )
            logger.info("Gantry.from_config: fposbapi backend, axes=%s", axis_order)
            return cls(axes=fposb_axes, concurrent_axes=fposb_concurrent, _client=client)

        raise ValueError(f'Unsupported backend: {backend!r}. Expected "modbus" or "fposbapi".')

    def get_status(self) -> None:
        """Return the current status of the gantry.

        .. note::
            Not yet implemented.  Returns ``None`` until the status model
            is defined.
        """

    def get_location(self) -> dict[str, float]:
        """Return the current position of every axis in millimetres.

        Calls :meth:`EdconAxis.get_current_axis_position` for each registered
        axis and returns the results as a dict keyed by axis name.

        Returns:
            Mapping of axis name → current position in mm.
        """
        coordinates = {axis: self.axes[axis].get_current_axis_position() for axis in self.axes}
        logger.debug("get_location: %s", coordinates)
        return coordinates

    def is_stopped(self) -> bool:
        """Return True when every registered axis has stopped moving.

        Returns:
            ``True`` if :meth:`~edcon.edrive.motion_handler.MotionHandler.stopped`
            returns ``True`` for all axes; ``False`` if any axis is still in motion.
        """
        return all(axis.stopped() for axis in self.axes.values())

    def is_ready_for_motion(self) -> bool:
        """Return True when every registered axis is ready to accept a move command.

        Returns:
            ``True`` if
            :meth:`~edcon.edrive.motion_handler.MotionHandler.ready_for_motion`
            returns ``True`` for all axes; ``False`` otherwise.
        """
        return all(axis.ready_for_motion() for axis in self.axes.values())


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    x_axis = EdconAxis(name="X", ip="192.168.0.100", run_referencing=True)
    y_axis = EdconAxis(name="Y", ip="192.168.0.101")
    zg_axis = EdconAxis(name="ZG", ip="192.168.0.102")
    zp_axis = EdconAxis(name="ZP", ip="192.168.0.103")

    try:
        gantry = Gantry(axes={"X": x_axis, "Y": y_axis, "ZG": zg_axis, "ZP": zp_axis})

        moves = deque(
            [
                {
                    "X": {
                        "position": 192.23012889999998,
                        "velocity": 100.0,
                        "position_type": "absolute",
                    }
                },
                {
                    "Y": {
                        "position": 147.08970470000003,
                        "velocity": 100.0,
                        "position_type": "absolute",
                    }
                },
                {
                    "ZP": {
                        "position": 0.0,
                        "velocity": 100.0,
                        "position_type": "absolute",
                    }
                },
                {
                    "ZG": {
                        "position": 64.84794609999999,
                        "velocity": 100.0,
                        "position_type": "absolute",
                    }
                },
            ]
        )

        gantry.move_to(moves)

        params = {"position": 20000, "velocity": 50}
        move_result = x_axis.move(params["position"], params["velocity"])
        logger.info("Move result: %s", move_result)

        params["position"] = 10000
        move_result = x_axis.move(params["position"], params["velocity"])

        time.sleep(0.5)
        if x_axis.fault_present():
            logger.warning("Fault present!")
        logger.info(x_axis.fault_string())

        # demo
        moves = deque()

        while True:
            params = {"position": 20000, "velocity": 10}
            move_result = x_axis.move(**params)
            logger.info("Move result: %s", move_result)
            logger.debug("xist_a=%s", x_axis.telegram.xist_a)

            params["position"] = 10000
            move_result = x_axis.move(**params)
            logger.debug("xist_a=%s", x_axis.telegram.xist_a)

    except KeyboardInterrupt:
        logger.info("Exiting...")
        try:
            logger.debug("xist_a=%s", x_axis.telegram.xist_a)
            x_axis.stop_motion_task()
        except Exception as e:
            logger.debug("xist_a=%s", x_axis.telegram.xist_a)
            logger.error("Error stopping motion task: %s", e)
        logger.info("Current position: %s", x_axis.position_info_string())
        logger.debug("xist_a=%s", x_axis.telegram.xist_a)
        x_axis.shutdown()
