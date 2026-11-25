# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG

"""Gantry commissioning and motion session — position recording and jogging.

This module is backend-agnostic.  It does not depend on ``prompt_toolkit``
or ``rich`` and can be imported and tested without the ``teach`` extra
installed, as long as [`applied_motion.cli`][applied_motion.cli] (the package guard)
is not triggered first.

The [`MotionSession`][applied_motion.cli.session.MotionSession] class deliberately avoids calling any private
attributes of [`Gantry`][applied_motion.applied_motion.Gantry].
PLC-specific actions (``TEACH_POS``, ``TEACH_TRAY``) are delegated to the
caller via the ``on_capture`` hook, keeping this class backend-agnostic and
independently unit-testable.
"""

import json
import logging
import math
import warnings
from pathlib import Path
from typing import Callable

from applied_motion.applied_motion import Gantry

logger = logging.getLogger(__name__)

_LINEAR_JOG_LIMIT_MARGIN: float = 0.02
_LEGACY_POSITION_UNIT = "mm"
_LEGACY_VELOCITY_UNIT = "mm/s"

OnCaptureHook = Callable[[str, dict[str, float]], None]
"""Type alias for the optional capture hook.

Signature: ``on_capture(label: str, position: dict[str, float]) -> None``

Called after every successful [`MotionSession.capture`][applied_motion.cli.session.MotionSession.capture] with the
position label and the recorded position dict.  Typical uses:

* Send ``TEACH_POS`` / ``TEACH_TRAY`` to the CECC-X PLC via
  [`FPosBAPIClient`][applied_motion.backends.fposbapi_client.FPosBAPIClient].
* Emit an event to a higher-level orchestration layer.
* Write a running log to a remote database.
"""


def _validate_positions(value: object) -> dict[str, dict[str, float]]:
    """Validate the numeric position mapping used by capture files.

    Args:
        value: Candidate label-to-axis position mapping.

    Returns:
        Validated mapping with numeric position values.

    Raises:
        ValueError: If the mapping is malformed or contains non-finite values.
    """
    if not isinstance(value, dict):
        raise ValueError("positions must be a mapping")

    positions: dict[str, dict[str, float]] = {}
    for label, coordinates in value.items():
        if not isinstance(label, str) or not isinstance(coordinates, dict):
            raise ValueError("each position must map a string label to an axis mapping")
        validated_coordinates: dict[str, float] = {}
        for axis_name, position in coordinates.items():
            if not isinstance(axis_name, str):
                raise ValueError("axis names in positions must be strings")
            if isinstance(position, bool) or not isinstance(position, (int, float)) or not math.isfinite(position):
                raise ValueError("position values must be finite JSON numbers")
            validated_coordinates[axis_name] = float(position)
        positions[label] = validated_coordinates
    return positions


def _resolve_jog_step(step: float | None, step_mm: float | None, position_unit: str) -> float:
    """Resolve the generic step and deprecated linear alias.

    Args:
        step: Generic step in native units, if supplied.
        step_mm: Deprecated linear step alias, if supplied.
        position_unit: Position unit of the target axis.

    Returns:
        The requested step in native units.

    Raises:
        ValueError: If no step is supplied, both forms are supplied, or the
            deprecated alias is used for a non-linear axis.
    """
    if step is not None and step_mm is not None:
        raise ValueError("pass either step or deprecated step_mm, not both")
    if step is not None:
        return step
    if step_mm is None:
        raise ValueError("step is required")
    warnings.warn("step_mm is deprecated; use step", DeprecationWarning, stacklevel=3)
    if position_unit != _LEGACY_POSITION_UNIT:
        raise ValueError("step_mm is a deprecated alias for linear axes only")
    return step_mm


class MotionSession:
    """Records gantry positions interactively or programmatically.

    Provides step-jog and position capture on top of a connected
    [`Gantry`][applied_motion.applied_motion.Gantry].  Captured positions are kept
    in ``positions`` and can be persisted to and from JSON via
    [`save`][applied_motion.cli.session.MotionSession.save] / [`load`][applied_motion.cli.session.MotionSession.load].

    This class has no knowledge of the gantry backend (Modbus vs FPosBAPI)
    and no dependency on ``prompt_toolkit`` or ``rich``.  Backend-specific
    post-capture actions (e.g. sending ``TEACH_POS`` to the PLC) are
    performed by the caller through the *on_capture* hook.

    Args:
        gantry: Connected and homed [`Gantry`][applied_motion.applied_motion.Gantry]
            instance.
        on_capture: Optional hook called immediately after each successful
            [`capture`][applied_motion.cli.session.MotionSession.capture].  Receives the position label and position dict.
            Exceptions raised by the hook propagate to the caller.

    Attributes:
        positions: Ordered dict mapping label → ``{axis_name: position_mm}``.
            Populated by [`capture`][applied_motion.cli.session.MotionSession.capture] and [`load`][applied_motion.cli.session.MotionSession.load].

    Example::

        # FPosBAPI: wire a hook that commits positions to the PLC
        def plc_hook(label, pos):
            gantry.teach_pos(pos_id=label_to_id[label])


        session = MotionSession(gantry, on_capture=plc_hook)
        session.jog("X", "+", 5.0)
        session.capture("deck_a1")
        session.save("deck_layout.json")
    """

    def __init__(  # noqa
        self,
        gantry: Gantry,
        on_capture: OnCaptureHook | None = None,
    ) -> None:
        self.gantry = gantry
        self.on_capture = on_capture
        self.positions: dict[str, dict[str, float]] = {}
        logger.debug(
            "MotionSession: created for gantry=%r hook=%s",
            gantry,
            getattr(on_capture, "__name__", repr(on_capture)) if on_capture is not None else "None",
        )

    # ------------------------------------------------------------------
    # Motion
    # ------------------------------------------------------------------

    def jog(
        self,
        axis_name: str,
        direction: str,
        step: float | None = None,
        velocity: float = 10.0,
        timeout: int = 15,
        *,
        step_mm: float | None = None,
    ) -> dict[str, float]:
        """Step-move one axis by *step* in *direction* using native units.

        Reads the current axis position, computes an absolute target, and
        dispatches a single-axis move via
        [`Gantry.move_to`][applied_motion.applied_motion.Gantry.move_to].

        Args:
            axis_name: Name of the axis to jog (must be a key in
                ``axes``).
            direction: ``"+"`` to move in the positive direction, ``"-"``
                to move in the negative direction.
            step: Distance to step in the selected axis's position unit.
                Must be positive. Positional calls remain supported.
            velocity: Jog speed in the axis's velocity unit. Defaults to ``10.0``.
            timeout: Maximum seconds to wait for the move to complete.
                When exceeded the axis backend issues a stop command and
                the move is abandoned.  Defaults to ``30`` seconds —
                long enough for any normal jog step, but ensures the
                axis will always halt rather than run indefinitely.
            step_mm: Deprecated alias for *step*, accepted only for linear axes.

        Returns:
            Full gantry location dict after the move completes.

        Raises:
            ValueError: If *direction* is not ``"+"`` or ``"-"``, if the
                step is not positive, or if *step_mm* is used for a rotary axis.
            KeyError: If *axis_name* is not registered with the gantry.
        """
        if direction not in ("+", "-"):
            raise ValueError(f"direction must be '+' or '-', got {direction!r}")
        if axis_name not in self.gantry.axes:
            raise KeyError(f"Axis {axis_name!r} is not registered with this gantry")

        axis = self.gantry.axes[axis_name]
        position_unit = getattr(axis, "position_unit", _LEGACY_POSITION_UNIT)
        velocity_unit = getattr(axis, "velocity_unit", _LEGACY_VELOCITY_UNIT)
        step = _resolve_jog_step(step, step_mm, position_unit)
        if step <= 0:
            raise ValueError(f"step_mm/step must be positive, got {step!r}")

        current = axis.get_current_axis_position()
        raw_delta = step if direction == "+" else -step
        margin = _LINEAR_JOG_LIMIT_MARGIN if position_unit == _LEGACY_POSITION_UNIT else 0.0
        if raw_delta > 0:
            available = max(0.0, axis.max_position - current - margin)
            delta = min(raw_delta, available)
        else:
            available = max(0.0, current - axis.min_position - margin)
            delta = max(raw_delta, -available)

        logger.info(
            "MotionSession.jog: axis=%s limits=[%.3f, %.3f] %s current=%.3f delta=%.3f %s vel=%.1f %s timeout=%ss",
            axis_name,
            axis.min_position,
            axis.max_position,
            position_unit,
            current,
            delta,
            position_unit,
            velocity,
            velocity_unit,
            timeout,
        )
        if delta != raw_delta:
            logger.warning(
                "MotionSession.jog: axis=%s delta clamped %.3f\u2192%.3f %s (%s %s margin from SW limit)",
                axis_name,
                raw_delta,
                delta,
                position_unit,
                margin,
                position_unit,
            )
        if abs(delta) < 1e-3:
            logger.info("MotionSession.jog: axis=%s at limit, skipping move", axis_name)
            return self.gantry.get_location()

        axis.move(delta, velocity, position_type="relative", timeout=timeout)
        location = self.gantry.get_location()
        logger.debug("MotionSession.jog: post-move location=%s", location)
        return location

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def capture(self, label: str) -> dict[str, float]:
        """Record the current gantry position under *label*.

        Calls [`Gantry.get_location`][applied_motion.applied_motion.Gantry.get_location], stores
        the result in ``positions``, then invokes *on_capture* if one
        was provided.

        Args:
            label: Unique name for this position (e.g. ``"deck_a1"``).
                Overwrites any previous entry with the same label.

        Returns:
            The recorded position dict ``{axis_name: position_mm}``.

        Raises:
            Any exception raised by *on_capture* propagates unchanged.
        """
        position = self.gantry.get_location()
        self.positions[label] = position
        logger.info("MotionSession.capture: label=%r position=%s", label, position)
        if self.on_capture is not None:
            self.on_capture(label, position)
        return position

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _axis_unit_metadata(self, axis_name: str) -> dict[str, str]:
        """Return public units for one active axis, with legacy defaults.

        Args:
            axis_name: Axis whose units are requested.

        Returns:
            Mapping containing position and velocity unit names.

        Raises:
            ValueError: If *axis_name* is not active on the gantry.
        """
        axis = self.gantry.axes.get(axis_name)
        if axis is None:
            raise ValueError(f"Unknown axis {axis_name!r}")
        return {
            "position_unit": getattr(axis, "position_unit", _LEGACY_POSITION_UNIT),
            "velocity_unit": getattr(axis, "velocity_unit", _LEGACY_VELOCITY_UNIT),
        }

    def save(self, path: Path | str) -> None:
        """Write captured positions and referenced axis units as schema v2.

        Args:
            path: Destination file path. Parent directories must exist.

        Raises:
            ValueError: If captured positions are malformed, non-finite, or
                reference an inactive axis.
        """
        positions = _validate_positions(self.positions)
        axis_names = sorted({axis_name for coordinates in positions.values() for axis_name in coordinates})
        axis_units = {axis_name: self._axis_unit_metadata(axis_name) for axis_name in axis_names}
        document = {"schema_version": 2, "axis_units": axis_units, "positions": positions}

        path = Path(path)
        with path.open("w") as fh:
            json.dump(document, fh, indent=2, allow_nan=False)
        logger.info("MotionSession.save: wrote %d position(s) to %s", len(self.positions), path)

    def _load_legacy_positions(self, loaded: dict) -> dict[str, dict[str, float]]:
        """Validate legacy numeric-only v1 positions for linear active axes."""
        positions = _validate_positions(loaded)
        axis_names = {axis_name for coordinates in positions.values() for axis_name in coordinates}
        expected_units = {"position_unit": _LEGACY_POSITION_UNIT, "velocity_unit": _LEGACY_VELOCITY_UNIT}
        for axis_name in axis_names:
            if self._axis_unit_metadata(axis_name) != expected_units:
                raise ValueError("legacy v1 positions require active axes using mm and mm/s")
        return positions

    def _load_v2_positions(self, loaded: dict) -> dict[str, dict[str, float]]:
        """Validate schema-v2 structure, units, axis references, and positions."""
        if set(loaded) != {"schema_version", "axis_units", "positions"}:
            raise ValueError("schema v2 capture must contain schema_version, axis_units, and positions only")

        positions = _validate_positions(loaded["positions"])
        axis_names = {axis_name for coordinates in positions.values() for axis_name in coordinates}
        axis_units = loaded["axis_units"]
        if not isinstance(axis_units, dict) or set(axis_units) != axis_names:
            raise ValueError("schema v2 axis_units must cover exactly the axes referenced by positions")

        for axis_name in axis_names:
            saved_units = axis_units[axis_name]
            if (
                not isinstance(saved_units, dict)
                or set(saved_units) != {"position_unit", "velocity_unit"}
                or not all(isinstance(unit, str) for unit in saved_units.values())
            ):
                raise ValueError(f"schema v2 unit metadata is malformed for axis {axis_name!r}")
            if saved_units != self._axis_unit_metadata(axis_name):
                raise ValueError(f"schema v2 unit mismatch for axis {axis_name!r}")
        return positions

    def load(self, path: Path | str) -> None:
        """Merge positions from a JSON file into ``positions``.

        Existing labels are overwritten by entries from *path*; labels
        absent in *path* are preserved.

        Args:
            path: Source JSON file previously written by [`save`][applied_motion.cli.session.MotionSession.save].

        Raises:
            OSError: If *path* cannot be opened.
            ValueError: If the file contains invalid JSON.
        """
        path = Path(path)
        with path.open() as fh:
            loaded = json.load(fh)
        if not isinstance(loaded, dict):
            raise ValueError("capture file must contain a JSON object")

        if "schema_version" not in loaded:
            positions = self._load_legacy_positions(loaded)
        elif loaded.get("schema_version") == 2:
            positions = self._load_v2_positions(loaded)
        else:
            raise ValueError(f"unsupported capture schema_version {loaded.get('schema_version')!r}")

        self.positions.update(positions)
        logger.info("MotionSession.load: read %d position(s) from %s", len(positions), path)
