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

from typing import Iterator, cast

import json
import logging
from copy import deepcopy

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from applied_motion.backends.axis_protocol import Axis
from applied_motion.backends.fposbapi_axis import FPosBAxis
from applied_motion.backends.fposbapi_client import FPosBAPIClient
from applied_motion.backends.edcon_axis import EdconAxis


logger = logging.getLogger(__name__)

AxisMap = dict[str, Axis]


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

    axes: AxisMap
    concurrent_axes: AxisMap | None

    def __init__(
        self,
        axes: AxisMap,
        concurrent_axes: AxisMap | None = None,
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
        logger.info("Gantry: initialized with axes=%s", list(axes.keys()))
        if concurrent_axes:
            logger.debug("Gantry: concurrent axes=%s", list(concurrent_axes.keys()))

    @staticmethod
    def _load_config_source(config: dict | Path) -> dict:
        """Return a raw config dict loaded from *config*.

        Args:
            config: Parsed configuration dict or path to a JSON file.

        Returns:
            Raw configuration mapping.

        Raises:
            ValueError: If the loaded configuration is not a dict.
        """
        if isinstance(config, Path):
            with config.open() as fh:
                config = json.load(fh)
        if not isinstance(config, dict):
            raise ValueError("Gantry config must load as a dict")
        return config

    @staticmethod
    def _normalize_config(raw_config: dict) -> dict:
        """Return the normalized component config mapping.

        Args:
            raw_config: Raw configuration dict, possibly wrapped in a
                top-level ``component_config`` key.

        Returns:
            Normalized component configuration dict.

        Raises:
            ValueError: If the normalized configuration is not a dict.
        """
        parsed_config = raw_config.get("component_config", raw_config)
        if not isinstance(parsed_config, dict):
            raise ValueError("Normalized gantry config must be a dict")
        return parsed_config

    @staticmethod
    def _validate_axis_name_list(value: object, field_name: str) -> list[str]:
        """Return *value* as a validated list of axis-name strings.

        Args:
            value: Value to validate.
            field_name: Config field name used in error messages.

        Returns:
            Validated list of axis-name strings.

        Raises:
            ValueError: If *value* is not a list of strings.
        """
        if not isinstance(value, list) or not all(isinstance(axis_name, str) for axis_name in value):
            raise ValueError(f"Gantry {field_name} must be a list of axis-name strings")
        return cast(list[str], value)

    @staticmethod
    def _validate_modbus_axes(axes_cfg: dict, axis_order: list[str]) -> None:
        """Validate backend-specific Modbus axis config fields.

        Args:
            axes_cfg: Axis config mapping.
            axis_order: Ordered list of configured axis names.

        Raises:
            ValueError: If any axis config is missing required fields.
        """
        for axis_name in axis_order:
            axis_cfg = axes_cfg.get(axis_name)
            if not isinstance(axis_cfg, dict):
                raise ValueError(f"Axis config for {axis_name!r} must be a dict")
            if not isinstance(axis_cfg.get("name"), str):
                raise ValueError(f"Modbus axis {axis_name!r} must define string field 'name'")
            if not isinstance(axis_cfg.get("ip"), str):
                raise ValueError(f"Modbus axis {axis_name!r} must define string field 'ip'")

    @staticmethod
    def _validate_fposbapi_config(gantry_cfg: dict, axes_cfg: dict, axis_order: list[str]) -> None:
        """Validate backend-specific FPosBAPI interface and axis fields.

        Args:
            gantry_cfg: Gantry component config.
            axes_cfg: Axis config mapping.
            axis_order: Ordered list of configured axis names.

        Raises:
            ValueError: If required interface or axis fields are missing or invalid.
        """
        interface = gantry_cfg.get("interface")
        if not isinstance(interface, dict):
            raise ValueError("FPosBAPI gantry config must contain an 'interface' mapping")
        if not isinstance(interface.get("ip"), str):
            raise ValueError("FPosBAPI interface must define string field 'ip'")
        if "port" in interface and not isinstance(interface["port"], int):
            raise ValueError("FPosBAPI interface 'port' must be an int")
        if (
            "timeout" in interface
            and interface["timeout"] is not None
            and not isinstance(interface["timeout"], (int, float))
        ):
            raise ValueError("FPosBAPI interface 'timeout' must be numeric or null")

        for axis_name in axis_order:
            axis_cfg = axes_cfg.get(axis_name)
            if not isinstance(axis_cfg, dict):
                raise ValueError(f"Axis config for {axis_name!r} must be a dict")
            if not isinstance(axis_cfg.get("name"), str):
                raise ValueError(f"FPosBAPI axis {axis_name!r} must define string field 'name'")
            if not isinstance(axis_cfg.get("index"), int):
                raise ValueError(f"FPosBAPI axis {axis_name!r} must define int field 'index'")

    @staticmethod
    def _validate_gantry_config(parsed_config: dict, name: str) -> tuple[dict, str, dict, list[str], list[str] | None]:
        """Validate and extract one gantry component config.

        Args:
            parsed_config: Normalized component configuration mapping.
            name: Component name to extract.

        Returns:
            Tuple of ``(gantry_cfg, backend, axes_cfg, axis_order, concurrent_axes)``.

        Raises:
            ValueError: If required config structure or backend-specific fields
                are missing or invalid.
        """
        components = parsed_config.get("components")
        if not isinstance(components, dict):
            raise ValueError("Gantry config must contain a 'components' mapping")
        if name not in components:
            raise ValueError(f"Gantry config does not contain component {name!r}")

        gantry_cfg = components[name]
        if not isinstance(gantry_cfg, dict):
            raise ValueError(f"Gantry component {name!r} must be a dict")

        backend = gantry_cfg.get("backend", "modbus")
        if backend not in {"modbus", "fposbapi"}:
            raise ValueError(f'Unsupported backend: {backend!r}. Expected "modbus" or "fposbapi".')

        axes_cfg = gantry_cfg.get("axes")
        if not isinstance(axes_cfg, dict):
            raise ValueError(f"Gantry component {name!r} must contain an 'axes' mapping")

        axis_order = gantry_cfg.get("axis_order", list(axes_cfg.keys()))
        axis_order = Gantry._validate_axis_name_list(axis_order, "axis_order")
        unknown_axis_order = [axis_name for axis_name in axis_order if axis_name not in axes_cfg]
        if unknown_axis_order:
            raise ValueError(f"Gantry axis_order references unknown axes: {unknown_axis_order}")

        concurrent_raw = gantry_cfg.get("concurrent_axes")
        if not concurrent_raw:
            concurrent_axes = None
        else:
            concurrent_axes = Gantry._validate_axis_name_list(concurrent_raw, "concurrent_axes")
            unknown_concurrent = [axis_name for axis_name in concurrent_raw if axis_name not in axes_cfg]
            if unknown_concurrent:
                raise ValueError(f"Gantry concurrent_axes references unknown axes: {unknown_concurrent}")

        if backend == "modbus":
            Gantry._validate_modbus_axes(axes_cfg, axis_order)
        else:
            Gantry._validate_fposbapi_config(gantry_cfg, axes_cfg, axis_order)

        return gantry_cfg, backend, axes_cfg, axis_order, concurrent_axes

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
        config = cls._load_config_source(config)
        logger.debug("Gantry.from_config: loaded config source=%s", type(config).__name__)
        parsed_config = cls._normalize_config(config)
        logger.debug("Gantry.from_config: parsed component config")
        gantry_cfg, backend, axes_cfg, axis_order, concurrent_raw = cls._validate_gantry_config(parsed_config, name)

        if backend == "modbus":
            axes: AxisMap = {
                axis_name: EdconAxis(
                    name=axes_cfg[axis_name]["name"],
                    ip=axes_cfg[axis_name]["ip"],
                    run_referencing=axes_cfg[axis_name].get("run_referencing", False),
                )
                for axis_name in axis_order
            }
            concurrent_axes: AxisMap | None = (
                {axis_name: axes[axis_name] for axis_name in concurrent_raw if axis_name in axes}
                if concurrent_raw
                else None
            )
            logger.info("Gantry.from_config: backend=modbus axes=%s", axis_order)
            return cls(axes=axes, concurrent_axes=concurrent_axes)

        if backend == "fposbapi":
            conn = gantry_cfg["interface"]
            if "timeout" in conn:
                client = FPosBAPIClient(
                    ip=conn["ip"],
                    port=conn.get("port", 1234),
                    timeout=conn["timeout"],
                )
            else:
                client = FPosBAPIClient(ip=conn["ip"], port=conn.get("port", 1234))
            try:
                client.send_command("ENABLE")
            except Exception:
                client.close()
                raise
            fposb_axes: AxisMap = {
                axis_name: FPosBAxis(
                    name=axes_cfg[axis_name]["name"],
                    index=axes_cfg[axis_name]["index"],
                    client=client,
                )
                for axis_name in axis_order
            }
            fposb_concurrent: dict[str, Axis] | None = (
                {axis_name: fposb_axes[axis_name] for axis_name in concurrent_raw if axis_name in fposb_axes}
                if concurrent_raw
                else None
            )
            logger.info("Gantry.from_config: backend=fposbapi axes=%s", axis_order)
            return cls(axes=fposb_axes, concurrent_axes=fposb_concurrent, _client=client)

        raise ValueError(f'Unsupported backend: {backend!r}. Expected "modbus" or "fposbapi".')

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
        if concurrent:
            with ThreadPoolExecutor(max_workers=len(movements)) as executor:
                move_results = executor.map(lambda x: self._single_move(x, timeout=timeout), movements, timeout=timeout)
                executed_movements = tuple(res for res in move_results)  # TODO: Timeout result?

                return executed_movements  # TODO: Timeout result?
        else:
            placedholder = []
            while movements:
                movement = movements.popleft()

                placedholder.append(self._single_move(movement=movement, timeout=timeout))
            return tuple(placedholder)

    def _single_move(self, movement: dict[str, dict["str", int | float]], timeout: int | None = None) -> int:
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
        ((axis_name, kinematic_params),) = tuple(list(movement.items()))

        logger.debug("Gantry._single_move: axis=%s params=%s timeout=%s", axis_name, kinematic_params, timeout)
        try:
            self.axes[axis_name].move(**kinematic_params, timeout=timeout)
            success = True  # self.axes[axis_name].wait_for_position_motion_execution()
            move_result = int(not (success))
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
            timeout: Reserved for future use; not consumed by this method.

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
                ``{"X": {"position": 100.0, "velocity": 50.0, "id" : 1 ???}}``.
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
        order and calls :meth:`EdconAxis.home` on each one sequentially.
        """
        if self._client is not None:
            logger.info("Gantry.home: issuing HOME via FPosBAPI client")
            self._client.send_command("HOME", timeout=None)
        else:
            for axis in self.axes.values():
                axis.home()
        logger.info("Gantry.home: complete")

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
