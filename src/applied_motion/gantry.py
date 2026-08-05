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

from typing import Iterator, TypedDict, cast

import logging
from copy import deepcopy

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from applied_motion.backends.axis_protocol import Axis
from applied_motion.config import GantryConfig, SystemConfig
from applied_motion.backends.fposbapi_axis import FPosBAxis
from applied_motion.backends.fposbapi_client import FPosBAPIClient
from applied_motion.backends.gantry_backend import FPosBAPIGantryBackend, GantryBackend, ModbusGantryBackend
from applied_motion.backends.edcon_axis import EdconAxis


logger = logging.getLogger(__name__)

AxisMap = dict[str, Axis]


class AxisStatus(TypedDict):
    """Per-axis status payload returned by :meth:`Gantry.get_status`."""

    position_mm: float | None
    is_homed: bool | None
    is_stopped: bool | None
    ready_for_motion: bool | None
    error: str | None


class ControllerStatus(TypedDict):
    """Controller diagnostics payload returned by :meth:`Gantry.get_status`."""

    sys_status: str | None
    is_error: bool | None
    fpb_error: str | None
    read_err: str | None
    error: str | None


class GantryStatusSummary(TypedDict):
    """Aggregate gantry health values returned by :meth:`Gantry.get_status`."""

    axis_count: int
    all_homed: bool
    all_stopped: bool
    all_ready_for_motion: bool
    healthy: bool
    axis_errors: dict[str, str]


class GantryStatus(TypedDict):
    """Top-level status payload returned by :meth:`Gantry.get_status`."""

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
        axes: AxisMap,
        concurrent_axes: AxisMap | None = None,
        *,
        _backend: GantryBackend | None = None,
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
            _backend: Internal backend strategy object that owns backend-
                specific gantry behavior.
        """
        self.axes = axes
        self.concurrent_axes = concurrent_axes
        self._backend = _backend or ModbusGantryBackend()
        logger.info("Gantry: initialized with axes=%s", list(axes.keys()))
        if concurrent_axes:
            logger.debug("Gantry: concurrent axes=%s", list(concurrent_axes.keys()))

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
                    component ``interface`` block, then creates
          :class:`~applied_motion.backends.fposbapi_axis.FPosBAxis` instances
          using the ``index`` field from each axis config.

        Config schema (JSON):

        .. code-block:: json

            {
                "components": {
                    "gantry_1": {
                        "backend": "modbus",
                        "axes": {
                            "X": {"name": "X", "ip": "192.168.0.193"}
                        },
                        "axis_order": ["X"],
                        "concurrent_axes": null
                    }
                }
            }

        FPosBAPI variant:

        .. code-block:: json

            {
                "components": {
                    "gantry_1": {
                        "backend": "fposbapi",
                        "interface": {"type":"tcp/ip","ip": "192.168.10.10", "port": 1234},
                        "axes": {
                            "X": {"name": "X", "index": 1},
                            "Y": {"name": "Y", "index": 2},
                            "Z": {"name": "Z", "index": 3}
                        },
                        "axis_order": ["X", "Y", "Z"],
                        "concurrent_axes": null
                    }
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
            OSError: (FPosBAPI only) If the TCP connection to the CECC-X cannot
                be established.
        """
        gcfg = GantryConfig(SystemConfig(config)(), name)

        if gcfg.backend == "modbus":
            axes: AxisMap = {
                axis_name: EdconAxis(
                    name=gcfg.axes_cfg[axis_name]["name"],
                    ip=gcfg.axes_cfg[axis_name]["ip"],
                    run_referencing=gcfg.axes_cfg[axis_name].get("run_referencing", False),
                )
                for axis_name in gcfg.axis_order
            }
            concurrent_axes: AxisMap | None = (
                {axis_name: axes[axis_name] for axis_name in gcfg.concurrent_raw if axis_name in axes}
                if gcfg.concurrent_raw
                else None
            )
            logger.info("Gantry.from_config: backend=modbus axes=%s", gcfg.axis_order)
            return cls(axes=axes, concurrent_axes=concurrent_axes, _backend=ModbusGantryBackend())

        # fposbapi
        conn = cast(dict, gcfg.interface)  # non-None guaranteed by GantryConfig._validate_fposbapi_config
        client_kwargs = {"timeout": conn["timeout"]} if "timeout" in conn else {}
        client = FPosBAPIClient(ip=conn["ip"], port=conn.get("port", 1234), **client_kwargs)
        backend_handler = FPosBAPIGantryBackend(client)
        try:
            client.send_command("ENABLE")
            fposb_axes: AxisMap = {
                axis_name: FPosBAxis(
                    name=gcfg.axes_cfg[axis_name]["name"],
                    index=gcfg.axes_cfg[axis_name]["index"],
                    client=client,
                )
                for axis_name in gcfg.axis_order
            }
            fposb_concurrent: AxisMap | None = (
                {axis_name: fposb_axes[axis_name] for axis_name in gcfg.concurrent_raw if axis_name in fposb_axes}
                if gcfg.concurrent_raw
                else None
            )
            logger.info("Gantry.from_config: backend=fposbapi axes=%s", gcfg.axis_order)
            return cls(axes=fposb_axes, concurrent_axes=fposb_concurrent, _backend=backend_handler)
        except Exception:
            backend_handler.close()
            raise

    def __repr__(self) -> str:
        """Return an unambiguous string representation of the gantry."""
        axis_names = list(self.axes.keys())
        return f"Gantry({axis_names!r})"

    def __eq__(self, other: object) -> bool:
        """Return True when *other* represents the same gantry identity.

        Args:
            other: Object to compare against.

        Returns:
            ``True`` if *other* is a :class:`Gantry` with equal ``axes``
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

    def _backend_identity(self) -> tuple[type[GantryBackend], tuple[str, int] | None]:
        """Return stable backend identity fields used by :meth:`__eq__`.

        For Modbus backends, identity is simply the backend type.
        For FPosBAPI backends, identity also includes the controller endpoint
        ``(ip, port)`` so gantries targeting different PLCs are not considered
        equal even when axis mappings match.

        Returns:
            Tuple of backend type and optional ``(ip, port)`` endpoint.
        """
        client = self._backend.client
        endpoint: tuple[str, int] | None = None
        if client is not None:
            endpoint = (client.ip, client.port)
        return type(self._backend), endpoint

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
            ``True`` if *item* is a key in :attr:`axes`.
        """
        return item in self.axes

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

    def _single_move(self, movement: dict, timeout: int | None = None) -> int:
        """Execute one movement dict and return an integer result code.

        Extracts the sole ``{axis_name: kinematic_params}`` entry from
        *movement* and delegates to :meth:`Axis.move`.

        Args:
            movement: Single-item dict mapping an axis name to its kinematic
                parameter dict.
            timeout: Optional per-move time limit in seconds forwarded to
                :meth:`Axis.move`.

        Returns:
            ``0`` on success, ``1`` on failure.

        Raises:
            AxisNotFoundError: If the axis name is not found in :attr:`axes`.
        """
        ((axis_name, kinematic_params),) = tuple(list(movement.items()))

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
        movements: deque,
        concurrent_axes: AxisMap,
    ) -> deque:
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
            A :class:`~collections.deque` containing the next batch of
            movements to dispatch concurrently.
        """
        next_batch = deque()
        concurrent_working_reference = deepcopy(concurrent_axes)

        movement = movements.popleft()
        next_batch.append(movement)
        ((axis_name, kinematic_params),) = tuple(movement.items())
        if axis_name not in concurrent_working_reference:
            return next_batch
        del concurrent_working_reference[axis_name]

        while concurrent_working_reference:
            movement = movements.popleft()
            ((axis_name, (kinematic_params)),) = tuple(movement.items())

            if axis_name not in concurrent_working_reference:
                return next_batch
            else:
                del concurrent_working_reference[axis_name]

            next_batch.append(movement)

        return next_batch

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
        logger.info("Gantry.move_to: queued=%d concurrent=%s timeout=%s", len(movements), concurrent, timeout)

        if concurrent:
            self._move_dispatch(movements, concurrent=concurrent, timeout=timeout)
            return
        else:
            while movements:
                concurrent_axes = self.concurrent_axes or {}
                next_moves = self._get_next_moves(movements, concurrent_axes)
                self._move_dispatch(next_moves, concurrent=True, timeout=timeout)

    def home(self) -> None:
        """Home all registered axes.

        For the **FPosBAPI backend**, sends a single ``HOME`` command to the
        CECC-X PLC which homes all axes in a coordinated sequence.

        For the **Modbus backend**, iterates over every axis in insertion
        order and calls :meth:`Axis.home` on each one sequentially.
        """
        self._backend.home(self.axes)
        logger.info("Gantry.home: complete")

    def close(self) -> None:
        """Close backend-owned resources.

        Safe to call multiple times.
        """
        self._backend.close()

    def __enter__(self) -> "Gantry":
        """Return self for context-manager support."""
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        """Ensure backend resources are released when leaving context."""
        self.close()

    def supports_teach(self) -> bool:
        """Return whether PLC teaching commands are supported by this backend."""
        return self._backend.supports_teach()

    def teach_pos(self, pos_id: int) -> None:
        """Teach current location into PLC position slot.

        Args:
            pos_id: PLC position slot ID.

        Raises:
            NotImplementedError: If backend does not support PLC teaching.
        """
        self._backend.teach_pos(pos_id)

    def teach_tray(self, tray_id: int, tray_pos: int) -> None:
        """Teach current location into PLC tray slot.

        Args:
            tray_id: Tray ID.
            tray_pos: Position index within tray.

        Raises:
            NotImplementedError: If backend does not support PLC teaching.
        """
        self._backend.teach_tray(tray_id, tray_pos)

    def list_commands(self) -> list[str]:
        """Return backend command list when available."""
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
        client = self._backend.client
        if client is None:
            return controller_status

        try:
            controller_status["sys_status"] = client.sys_status()
            controller_status["is_error"] = client.is_error()
            controller_status["fpb_error"] = client.fpb_error()
            controller_status["read_err"] = client.read_err()
        except Exception as exc:
            controller_status["error"] = f"{type(exc).__name__}: {exc}"
            logger.exception("Gantry.get_status: failed to query controller diagnostics")
        return controller_status

    def _build_status_summary(
        self,
        axis_statuses: dict[str, AxisStatus],
        controller_status: ControllerStatus,
    ) -> GantryStatusSummary:
        """Build aggregate gantry health values from axis/controller status.

        Args:
            axis_statuses: Per-axis status mapping from :meth:`get_status`.
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
            "backend": type(self._backend).__name__,
            "supports_teach": self.supports_teach(),
            "axes": axis_statuses,
            "summary": summary,
            "controller": controller_status,
        }
        logger.debug("Gantry.get_status: %s", status)
        return status

    def get_location(self) -> dict[str, float]:
        """Return the current position of every axis in millimetres.

        Calls :meth:`EdconAxis.get_current_axis_position` for each registered
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
