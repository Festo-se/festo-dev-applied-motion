# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG


"""Festo gantry axis and multi-axis gantry abstractions.

This module provides [`Gantry`][applied_motion.gantry.Gantry], which coordinates one or more axes
for sequential or concurrent positioning.

Pass ``config=...`` to [`Gantry`][applied_motion.gantry.Gantry] to instantiate the correct backend
automatically from a JSON configuration dict or file.
[`Gantry.from_config`][applied_motion.gantry.Gantry.from_config] is a convenience wrapper around that constructor path.
"""

from typing import Iterator, TypedDict, TypeAlias

import logging

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from applied_motion.backends.axis_protocol import Axis
from applied_motion.backends.gantry_backend import ControllerDiagnostics, GantryBackend
from applied_motion.gantry_factory import build_gantry_from_config


logger = logging.getLogger(__name__)

AxisMap: TypeAlias = dict[str, Axis]


class _OptionalKinematicParams(TypedDict, total=False):
    """Optional kinematic parameters accepted by [`Axis.move`][applied_motion.backends.axis_protocol.Axis.move]."""

    position_type: str


class KinematicParams(_OptionalKinematicParams):
    """Kinematic parameters accepted by [`Axis.move`][applied_motion.backends.axis_protocol.Axis.move]."""

    position: float
    velocity: float


Movement: TypeAlias = dict[str, KinematicParams]
MovementBatch: TypeAlias = deque[Movement]


class AxisStatus(TypedDict):
    """Per-axis status payload returned by [`Gantry.get_status`][applied_motion.gantry.Gantry.get_status]."""

    position_mm: float | None
    is_homed: bool | None
    is_stopped: bool | None
    ready_for_motion: bool | None
    error: str | None


class ControllerStatus(TypedDict):
    """Controller diagnostics payload returned by [`Gantry.get_status`][applied_motion.gantry.Gantry.get_status]."""

    sys_status: str | None
    is_error: bool | None
    fpb_error: str | None
    read_err: str | None
    error: str | None


class GantryStatusSummary(TypedDict):
    """Aggregate gantry health values returned by [`Gantry.get_status`][applied_motion.gantry.Gantry.get_status]."""

    axis_count: int
    all_homed: bool
    all_stopped: bool
    all_ready_for_motion: bool
    healthy: bool
    axis_errors: dict[str, str]


class GantryStatus(TypedDict):
    """Top-level status payload returned by [`Gantry.get_status`][applied_motion.gantry.Gantry.get_status]."""

    backend: str
    supports_teach: bool
    axes: dict[str, AxisStatus]
    summary: GantryStatusSummary
    controller: ControllerStatus


class MovementError(Exception):
    """Raised when a gantry or axis movement fails."""


class AxisNotFoundError(MovementError):
    """Raised when an axis name referenced in a movement command does not exist."""


class Gantry:
    """Coordinate multiple axis objects as a single gantry.

    Dispatches sequential or concurrent move commands to the individual
    axes and provides convenience methods for homing, status queries, and
    position readback.

    Attributes:
        axes: Mapping of axis name → axis instance for all registered axes.
        concurrent_axes: Optional mapping of axes that may move simultaneously.
            When ``None``, no concurrent grouping is applied.
    """

    def __init__(
        self,
        axes: AxisMap | None = None,
        concurrent_axes: AxisMap | None = None,
        *,
        config: dict | str | Path | None = None,
        name: str = "gantry_1",
        _backend: GantryBackend | None = None,
    ) -> None:
        """Initialise the gantry with the provided axis mapping.

        When ``config`` is provided, this constructor selects the correct
        backend (Modbus or FPosBAPI) and creates axes automatically from a JSON
        configuration dict or file.

        Args:
            axes: Dict mapping axis names to axis instances.  Accepts both
                [`EdconAxis`][applied_motion.backends.edcon_axis.EdconAxis] (Modbus backend) and
                [`FPosBAxis`][applied_motion.backends.fposbapi_axis.FPosBAxis]
                (FPosBAPI backend) — any object satisfying
                [`Axis`][applied_motion.backends.axis_protocol.Axis].
            concurrent_axes: Optional dict of axes that are allowed to move
                simultaneously.  Pass ``None`` (default) to disable concurrent
                grouping.
            config: Optional configuration dict or JSON file path. When
                provided, the gantry builds axes and backend from config and
                ignores ``axes`` / ``concurrent_axes``.
            name: Gantry component name to load from ``config``.
            _backend: Internal backend strategy object that owns backend-
                specific gantry behavior.
        """
        if config is not None:
            if axes is not None or concurrent_axes is not None or _backend is not None:
                raise ValueError("Pass either config or axes/concurrent_axes, not both")
            build = build_gantry_from_config(config, name)
            axes = build.axes
            concurrent_axes = build.concurrent_axes
            _backend = build.backend

        if axes is None:
            raise ValueError("axes must be provided when config is not supplied")

        self.axes = axes
        self.concurrent_axes = concurrent_axes
        self._backend = _backend
        logger.info("Gantry: initialized with axes=%s", list(axes.keys()))
        if concurrent_axes:
            logger.debug("Gantry: concurrent axes=%s", list(concurrent_axes.keys()))

    @classmethod
    def from_config(cls, config: dict | str | Path, name: str = "gantry_1") -> "Gantry":
        """Instantiate a [`Gantry`][applied_motion.gantry.Gantry] from a configuration dict or JSON file.

        Args:
            config: Parsed configuration mapping or JSON file path.
            name: Component name to load from config.

        Returns:
            Initialised [`Gantry`][applied_motion.gantry.Gantry] instance.
        """
        return cls(config=config, name=name)

    def __repr__(self) -> str:
        """Return an unambiguous string representation of the gantry."""
        axis_names = list(self.axes.keys())
        return f"Gantry({axis_names!r})"

    def __eq__(self, other: object) -> bool:
        """Return True when *other* represents the same gantry identity.

        Args:
            other: Object to compare against.

        Returns:
            ``True`` if *other* is a [`Gantry`][applied_motion.gantry.Gantry] with equal ``axes``
            and ``concurrent_axes`` mappings *and* the same backend/controller
            identity; ``False`` otherwise.
        """
        if not isinstance(other, Gantry):
            return NotImplemented

        return (
            self.axes == other.axes
            and self.concurrent_axes == other.concurrent_axes
            and self._backend_identity() == other._backend_identity()
        )

    def __hash__(self) -> int:
        """Return a hash derived from gantry identity fields."""
        axis_identity = tuple(self.axes.keys())
        concurrent_identity = tuple(self.concurrent_axes.keys()) if self.concurrent_axes is not None else None
        return hash((axis_identity, concurrent_identity, self._backend_identity()))

    def _backend_identity(self) -> tuple[type[object], tuple[str, int] | None]:
        """Return stable backend identity fields used by [`__eq__`][applied_motion.gantry.Gantry.__eq__].

        Returns:
            Tuple of backend type and optional ``(ip, port)`` endpoint.
        """
        if self._backend is None:
            return type(None), None

        return self._backend.backend_identity()

    def __len__(self) -> int:
        """Return the number of axes registered with this gantry."""
        return len(self.axes)

    def __iter__(self) -> Iterator[str]:
        """Iterate over the axis names registered with this gantry."""
        return iter(self.axes)

    def __contains__(self, item: object) -> bool:
        """Return True if *item* is the name of a registered axis.

        Args:
            item: Axis name to look up.

        Returns:
            ``True`` if *item* is a key in ``axes``.
        """
        return item in self.axes

    def _move_dispatch(
        self,
        movements: MovementBatch,
        concurrent: bool,
        timeout: int | None = None,
    ) -> tuple[int, ...]:
        """Dispatch a batch of movements either concurrently or sequentially.

        Args:
            movements: `deque` of movement dicts to execute.
            concurrent: When ``True``, all movements are executed in parallel
                via `ThreadPoolExecutor`.
                When ``False``, movements are executed one at a time.
            timeout: Optional per-move time limit in seconds forwarded to each
                [`EdconAxis.move`][applied_motion.backends.edcon_axis.EdconAxis.move] call.

        Returns:
            Tuple of integer result codes, one per movement dispatched.
        """
        logger.debug("Gantry._move_dispatch: batch=%d concurrent=%s timeout=%s", len(movements), concurrent, timeout)
        if concurrent:
            with ThreadPoolExecutor(max_workers=len(movements)) as executor:
                move_results = executor.map(lambda x: self._single_move(x, timeout=timeout), movements, timeout=timeout)
                executed_movements = tuple(res for res in move_results)  # TODO: Timeout result?
                logger.debug("Gantry._move_dispatch: concurrent results=%s", executed_movements)

                return executed_movements  # TODO: Timeout result?
        else:
            placedholder = []
            while movements:
                movement = movements.popleft()

                placedholder.append(self._single_move(movement=movement, timeout=timeout))
            logger.debug("Gantry._move_dispatch: sequential results=%s", tuple(placedholder))
            return tuple(placedholder)

    def _log_move_results(self, axis_names: tuple[str, ...], results: tuple[int, ...], concurrent: bool) -> None:
        """Log the outcome of one dispatched movement batch.

        Args:
            axis_names: Axis names in dispatch order for the batch.
            results: Integer result codes returned by [`_move_dispatch`][applied_motion.gantry.Gantry._move_dispatch].
            concurrent: Whether batch ran via concurrent dispatch.

        Returns:
            ``None``.
        """
        batch_mode = "concurrent" if concurrent else "sequential"
        if len(axis_names) != len(results):
            logger.error(
                "Gantry.move_to: %s batch axis/result mismatch axes=%s results=%s",
                batch_mode,
                axis_names,
                results,
            )
            return

        failed_axes = tuple(axis for axis, result in zip(axis_names, results, strict=True) if result != 0)
        if failed_axes:
            logger.warning(
                "Gantry.move_to: %s batch completed with failures axes=%s results=%s",
                batch_mode,
                failed_axes,
                results,
            )
        else:
            logger.info(
                "Gantry.move_to: %s batch completed successfully axes=%s",
                batch_mode,
                axis_names,
            )

    def _single_move(self, movement: Movement, timeout: int | None = None) -> int:
        """Execute one movement dict and return an integer result code.

        Extracts the sole ``{axis_name: kinematic_params}`` entry from
        *movement* and delegates to [`Axis.move`][applied_motion.backends.axis_protocol.Axis.move].

        Args:
            movement: Single-item dict mapping an axis name to its kinematic
                parameter dict.
            timeout: Optional per-move time limit in seconds forwarded to
                [`Axis.move`][applied_motion.backends.axis_protocol.Axis.move].

        Returns:
            ``0`` on success, ``1`` on failure.

        Raises:
            AxisNotFoundError: If the axis name is not found in ``axes``.
        """
        ((axis_name, kinematic_params),) = movement.items()

        logger.debug("Gantry._single_move: axis=%s params=%s timeout=%s", axis_name, kinematic_params, timeout)
        try:
            success = self.axes[axis_name].move(**kinematic_params, timeout=timeout)
            move_result = int(not success)
            logger.debug("Gantry._single_move: axis=%s result=%s", axis_name, move_result)
        except KeyError as e:
            logger.error("Gantry._single_move: axis=%s not found in gantry axes", axis_name)
            move_result = 1
            raise AxisNotFoundError(f"Axis {axis_name} not found") from e
        except Exception as e:
            logger.exception("Gantry._single_move: axis=%s move failed", axis_name)
            move_result = 1
            raise MovementError(f"Axis {axis_name} move failed: {e}") from e

        return move_result

    def _get_next_moves(
        self,
        movements: MovementBatch,
        concurrent_axes: AxisMap,
    ) -> MovementBatch:
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

        Returns:
            A `deque` containing the next batch of
            movements to dispatch concurrently.
        """
        next_batch: MovementBatch = deque()
        concurrent_working_reference = set(concurrent_axes.keys())

        movement = movements.popleft()
        next_batch.append(movement)
        ((axis_name, kinematic_params),) = tuple(movement.items())
        if axis_name not in concurrent_working_reference:
            return next_batch
        concurrent_working_reference.remove(axis_name)

        while concurrent_working_reference:
            movement = movements.popleft()
            ((axis_name, (kinematic_params)),) = tuple(movement.items())

            if axis_name not in concurrent_working_reference:
                return next_batch
            else:
                concurrent_working_reference.remove(axis_name)

            next_batch.append(movement)

        return next_batch

    def _movement_axis_name(self, movement: object, index: int) -> str | None:
        """Extract and validate axis name from one movement payload.

        Args:
            movement: Runtime movement payload expected to be a single-item dict.
            index: Zero-based position of the movement in the queued batch.

        Returns:
            Axis name when payload is structurally valid, else ``None``.
        """
        if not isinstance(movement, dict):
            logger.error(
                "Gantry.move_to: malformed movement at index=%d; expected dict got=%s",
                index,
                type(movement).__name__,
            )
            return None

        if len(movement) != 1:
            logger.error(
                "Gantry.move_to: malformed movement at index=%d; expected single axis entry got keys=%s",
                index,
                tuple(movement.keys()),
            )
            return None

        axis_name = next(iter(movement))
        if not isinstance(axis_name, str) or not axis_name:
            logger.error(
                "Gantry.move_to: malformed movement at index=%d; axis name must be non-empty string got=%r",
                index,
                axis_name,
            )
            return None

        return axis_name

    def _collect_valid_movements(self, movements: MovementBatch) -> tuple[MovementBatch, tuple[str, ...]]:
        """Filter queued movements to entries with valid, known axis references.

        Args:
            movements: Full queued movement batch to validate.

        Returns:
            Tuple ``(valid_movements, axis_names)`` containing only entries
            with well-formed axis specifications that reference known axes.
        """
        valid_movements: MovementBatch = deque()
        axis_names: list[str] = []
        for index, movement in enumerate(movements):
            axis_name = self._movement_axis_name(movement, index)
            if axis_name is None:
                logger.warning("Gantry.move_to: skipping malformed movement at index=%d", index)
                continue

            if axis_name not in self.axes:
                logger.warning(
                    "Gantry.move_to: unknown axis at index=%d axis=%s known_axes=%s; skipping movement",
                    index,
                    axis_name,
                    tuple(self.axes.keys()),
                )
                continue

            valid_movements.append(movement)
            axis_names.append(axis_name)

        return valid_movements, tuple(axis_names)

    def move_to(
        self,
        movements: MovementBatch,
        timeout: int | None = None,
        concurrent: bool = False,
    ) -> None:
        """Dispatch a queue of movements to the gantry axes.

        Processes each movement dict in *movements*, dispatching them either
        sequentially or concurrently according to *concurrent* and the
        gantry's ``concurrent_axes`` configuration.

        Args:
            movements: A `deque` of movement dicts.  Each
                dict maps a single axis name to its kinematic parameters, e.g.
                ``{"X": {"position": 100.0, "velocity": 50.0}}``.
            timeout: Optional per-move time limit in seconds.  Passed through
                to each [`EdconAxis.move`][applied_motion.backends.edcon_axis.EdconAxis.move] call.
            concurrent: When ``True``, all movements in the current batch are
                dispatched in parallel threads.  When ``False`` (default),
                movements are grouped using ``concurrent_axes`` and dispatched
                sequentially.

        Notes:
            Axis references are validated before dispatch. Malformed movement
            entries or entries for unknown axes are logged and skipped.
        """
        logger.info("Gantry.move_to: queued=%d concurrent=%s timeout=%s", len(movements), concurrent, timeout)

        valid_movements, validated_axis_names = self._collect_valid_movements(movements)
        if not valid_movements:
            logger.warning("Gantry.move_to: no valid movement entries to dispatch")
            return

        skipped_count = len(movements) - len(valid_movements)
        if skipped_count > 0:
            logger.warning("Gantry.move_to: skipped %d invalid movement entrie(s)", skipped_count)

        if concurrent:
            results = self._move_dispatch(valid_movements, concurrent=concurrent, timeout=timeout)
            self._log_move_results(validated_axis_names, results, concurrent=True)
            return
        else:
            concurrent_axes = self.concurrent_axes or {}
            while valid_movements:
                next_moves = self._get_next_moves(valid_movements, concurrent_axes)
                axis_names = tuple(next(iter(movement)) for movement in next_moves)
                results = self._move_dispatch(next_moves, concurrent=True, timeout=timeout)
                self._log_move_results(axis_names, results, concurrent=False)

    def home(self) -> None:
        """Home all registered axes.

        For the **FPosBAPI backend**, sends a single ``HOME`` command to the
        CECC-X PLC which homes all axes in a coordinated sequence.

        For the **Modbus backend**, iterates over every axis in insertion
        order and calls [`Axis.home`][applied_motion.backends.axis_protocol.Axis.home] on each one sequentially.
        """
        if self._backend is None:
            for axis in self.axes.values():
                axis.home()
        else:
            self._backend.home(self.axes)
        logger.info("Gantry.home: complete")

    def close(self) -> None:
        """Close backend-owned resources.

        Safe to call multiple times.
        """
        if self._backend is not None:
            self._backend.close()

    def __enter__(self) -> "Gantry":
        """Return self for context-manager support."""
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        """Ensure backend resources are released when leaving context."""
        self.close()

    def supports_teach(self) -> bool:
        """Return whether PLC teaching commands are supported by this backend."""
        if self._backend is None:
            return False
        return self._backend.supports_teach()

    def teach_pos(self, pos_id: int) -> None:
        """Teach current location into PLC position slot.

        Args:
            pos_id: PLC position slot ID.

        Raises:
            NotImplementedError: If backend does not support PLC teaching.
        """
        if self._backend is None:
            raise NotImplementedError("teach_pos is only available for FPosBAPI backend")
        self._backend.teach_pos(pos_id)

    def teach_tray(self, tray_id: int, tray_pos: int) -> None:
        """Teach current location into PLC tray slot.

        Args:
            tray_id: Tray ID.
            tray_pos: Position index within tray.

        Raises:
            NotImplementedError: If backend does not support PLC teaching.
        """
        if self._backend is None:
            raise NotImplementedError("teach_tray is only available for FPosBAPI backend")
        self._backend.teach_tray(tray_id, tray_pos)

    def list_commands(self) -> list[str]:
        """Return backend command list when available."""
        if self._backend is None:
            return []
        return self._backend.list_commands()

    def _collect_axis_status(self, axis_name: str, axis: Axis) -> AxisStatus:
        """Return status details for one axis.

        Args:
            axis_name: Logical axis label.
            axis: Axis instance to query.

        Returns:
            Mapping with position, homing/motion flags, and optional error.
        """
        axis_state: AxisStatus = {
            "position_mm": None,
            "is_homed": None,
            "is_stopped": None,
            "ready_for_motion": None,
            "error": None,
        }
        try:
            axis_state["position_mm"] = axis.get_current_axis_position()
            axis_state["is_homed"] = axis.is_homed()
            axis_state["is_stopped"] = axis.stopped()
            axis_state["ready_for_motion"] = axis.ready_for_motion()
        except Exception as exc:
            axis_state["error"] = f"{type(exc).__name__}: {exc}"
            logger.exception("Gantry.get_status: failed to query axis '%s'", axis_name)
        return axis_state

    def _collect_controller_status(self) -> ControllerStatus:
        """Return backend controller diagnostics when available.

        Returns:
            Mapping with PLC diagnostics fields. For backends without a
            shared controller client, all diagnostic fields remain ``None``.
        """
        controller_status: ControllerStatus = {
            "sys_status": None,
            "is_error": None,
            "fpb_error": None,
            "read_err": None,
            "error": None,
        }
        if self._backend is None:
            return controller_status

        backend_diagnostics: ControllerDiagnostics | None = self._backend.controller_diagnostics()
        if backend_diagnostics is None:
            return controller_status

        controller_status["sys_status"] = backend_diagnostics["sys_status"]
        controller_status["is_error"] = backend_diagnostics["is_error"]
        controller_status["fpb_error"] = backend_diagnostics["fpb_error"]
        controller_status["read_err"] = backend_diagnostics["read_err"]
        controller_status["error"] = backend_diagnostics["error"]
        if controller_status["error"] is not None:
            logger.error("Gantry.get_status: backend controller diagnostics failed: %s", controller_status["error"])
        return controller_status

    def _build_status_summary(
        self,
        axis_statuses: dict[str, AxisStatus],
        controller_status: ControllerStatus,
    ) -> GantryStatusSummary:
        """Build aggregate gantry health values from axis/controller status.

        Args:
            axis_statuses: Per-axis status mapping from [`get_status`][applied_motion.gantry.Gantry.get_status].
            controller_status: Controller diagnostics mapping.

        Returns:
            Summary mapping with aggregate booleans and axis error details.
        """
        all_homed = all(state["is_homed"] is True for state in axis_statuses.values())
        all_stopped = all(state["is_stopped"] is True for state in axis_statuses.values())
        all_ready = all(state["ready_for_motion"] is True for state in axis_statuses.values())
        axis_errors = {name: state["error"] for name, state in axis_statuses.items() if state["error"] is not None}

        healthy = all_homed and all_stopped and all_ready and not axis_errors
        if controller_status["error"] is not None:
            healthy = False
        if controller_status["is_error"] is True:
            healthy = False

        return {
            "axis_count": len(self.axes),
            "all_homed": all_homed,
            "all_stopped": all_stopped,
            "all_ready_for_motion": all_ready,
            "healthy": healthy,
            "axis_errors": axis_errors,
        }

    def get_status(self) -> GantryStatus:
        """Return a comprehensive status snapshot for the gantry.

        The returned mapping combines per-axis health, aggregate summary
        booleans, and (when available) controller-level diagnostics exposed
        by the backend's shared client.

        Returns:
            A dict with keys:

            - ``backend``: Backend class name.
            - ``supports_teach``: Whether backend supports TEACH commands.
            - ``axes``: Mapping of axis name to status details.
            - ``summary``: Aggregate booleans and counts.
            - ``controller``: PLC/controller diagnostics (for FPosBAPI).
        """
        axis_statuses = {axis_name: self._collect_axis_status(axis_name, axis) for axis_name, axis in self.axes.items()}
        controller_status = self._collect_controller_status()
        summary = self._build_status_summary(axis_statuses, controller_status)

        status: GantryStatus = {
            "backend": "ModbusGantryBackend" if self._backend is None else type(self._backend).__name__,
            "supports_teach": self.supports_teach(),
            "axes": axis_statuses,
            "summary": summary,
            "controller": controller_status,
        }
        logger.debug("Gantry.get_status: %s", status)
        return status

    def get_location(self) -> dict[str, float]:
        """Return the current position of every axis in millimetres.

        Calls [`EdconAxis.get_current_axis_position`][applied_motion.backends.edcon_axis.EdconAxis.get_current_axis_position] for each registered
        axis and returns the results as a dict keyed by axis name.

        Returns:
            Mapping of axis name → current position in mm.
        """
        coordinates = {axis: self.axes[axis].get_current_axis_position() for axis in self.axes}
        logger.debug("Gantry.get_location: %s", coordinates)
        return coordinates

    def is_stopped(self) -> bool:
        """Return True when every registered axis has stopped moving.

        Returns:
            ``True`` if `stopped`
                returns ``True`` for all axes; ``False`` if any axis is still in motion.
        """
        return all(axis.stopped() for axis in self.axes.values())

    def is_ready_for_motion(self) -> bool:
        """Return True when every registered axis is ready to accept a move command.

        Returns:
            ``True`` if `ready_for_motion` returns ``True`` for all axes; ``False`` otherwise.
        """
        return all(axis.ready_for_motion() for axis in self.axes.values())
