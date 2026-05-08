# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG

"""Gantry teach-in session — position recording and jogging.

This module is backend-agnostic.  It does not depend on ``prompt_toolkit``
or ``rich`` and can be imported and tested without the ``teach`` extra
installed, as long as :func:`applied_motion.teach_in` (the package guard)
is not triggered first.

The :class:`TeachSession` class deliberately avoids calling any private
attributes of :class:`~applied_motion.gantry.Gantry` (such as ``_client``).
PLC-specific actions (``TEACH_POS``, ``TEACH_TRAY``) are delegated to the
caller via the ``on_capture`` hook, keeping this class backend-agnostic and
independently unit-testable.
"""

import json
import logging
from collections import deque
from pathlib import Path
from typing import Callable

from applied_motion.gantry import Gantry

logger = logging.getLogger(__name__)

OnCaptureHook = Callable[[str, dict[str, float]], None]
"""Type alias for the optional capture hook.

Signature: ``on_capture(label: str, position: dict[str, float]) -> None``

Called after every successful :meth:`TeachSession.capture` with the
position label and the recorded position dict.  Typical uses:

* Send ``TEACH_POS`` / ``TEACH_TRAY`` to the CECC-X PLC via
  :class:`~applied_motion.backends.fposapi_client.FPosAPIClient`.
* Emit an event to a higher-level orchestration layer.
* Write a running log to a remote database.
"""


class TeachSession:
    """Records gantry positions interactively or programmatically.

    Provides step-jog and position capture on top of a connected
    :class:`~applied_motion.gantry.Gantry`.  Captured positions are kept
    in :attr:`positions` and can be persisted to and from JSON via
    :meth:`save` / :meth:`load`.

    This class has no knowledge of the gantry backend (Modbus vs FPosAPI)
    and no dependency on ``prompt_toolkit`` or ``rich``.  Backend-specific
    post-capture actions (e.g. sending ``TEACH_POS`` to the PLC) are
    performed by the caller through the *on_capture* hook.

    Args:
        gantry: Connected and homed :class:`~applied_motion.gantry.Gantry`
            instance.
        on_capture: Optional hook called immediately after each successful
            :meth:`capture`.  Receives the position label and position dict.
            Exceptions raised by the hook propagate to the caller.

    Attributes:
        positions: Ordered dict mapping label → ``{axis_name: position_mm}``.
            Populated by :meth:`capture` and :meth:`load`.

    Example::

        # FPosAPI: wire a hook that commits positions to the PLC
        def plc_hook(label, pos):
            gantry._client.teach_pos(pos_id=label_to_id[label])

        session = TeachSession(gantry, on_capture=plc_hook)
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
            "TeachSession created for gantry %r, hook=%s",
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
        step_mm: float,
        velocity: float = 10.0,
        timeout: int = 30,
    ) -> dict[str, float]:
        """Step-move one axis by *step_mm* in *direction*.

        Reads the current axis position, computes an absolute target, and
        dispatches a single-axis move via
        :meth:`~applied_motion.gantry.Gantry.move_to`.

        Args:
            axis_name: Name of the axis to jog (must be a key in
                :attr:`~applied_motion.gantry.Gantry.axes`).
            direction: ``"+"`` to move in the positive direction, ``"-"``
                to move in the negative direction.
            step_mm: Distance to step in millimetres.  Must be positive.
            velocity: Jog speed in mm/s.  Defaults to ``10.0``.
            timeout: Maximum seconds to wait for the move to complete.
                When exceeded the axis backend issues a stop command and
                the move is abandoned.  Defaults to ``30`` seconds —
                long enough for any normal jog step, but ensures the
                axis will always halt rather than run indefinitely.

        Returns:
            Full gantry location dict after the move completes.

        Raises:
            ValueError: If *direction* is not ``"+"`` or ``"-"``, or if
                *step_mm* is not positive.
            KeyError: If *axis_name* is not registered with the gantry.
        """
        if direction not in ("+", "-"):
            raise ValueError(f"direction must be '+' or '-', got {direction!r}")
        if step_mm <= 0:
            raise ValueError(f"step_mm must be positive, got {step_mm!r}")
        if axis_name not in self.gantry.axes:
            raise KeyError(f"Axis {axis_name!r} is not registered with this gantry")

        current = self.gantry.axes[axis_name].get_current_axis_position()
        delta = step_mm if direction == "+" else -step_mm
        target = current + delta

        logger.info(
            "jog: axis=%s %s%.3fmm  %.3f → %.3f mm  vel=%.1f mm/s  timeout=%ss",
            axis_name,
            direction,
            step_mm,
            current,
            target,
            velocity,
            timeout,
        )
        self.gantry.move_to(
            deque([{axis_name: {"position": target, "velocity": velocity}}]),
            timeout=timeout,
        )
        location = self.gantry.get_location()
        logger.debug("jog: post-move location=%s", location)
        return location

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def capture(self, label: str) -> dict[str, float]:
        """Record the current gantry position under *label*.

        Calls :meth:`~applied_motion.gantry.Gantry.get_location`, stores
        the result in :attr:`positions`, then invokes *on_capture* if one
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
        logger.info("capture: %r → %s", label, position)
        if self.on_capture is not None:
            self.on_capture(label, position)
        return position

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: Path | str) -> None:
        """Write all captured positions to a JSON file.

        The output format is a plain JSON object mapping label →
        ``{axis_name: position_mm}``.

        Args:
            path: Destination file path.  Parent directories must exist.
        """
        path = Path(path)
        with path.open("w") as fh:
            json.dump(self.positions, fh, indent=2)
        logger.info("save: %d position(s) written to %s", len(self.positions), path)

    def load(self, path: Path | str) -> None:
        """Merge positions from a JSON file into :attr:`positions`.

        Existing labels are overwritten by entries from *path*; labels
        absent in *path* are preserved.

        Args:
            path: Source JSON file previously written by :meth:`save`.

        Raises:
            OSError: If *path* cannot be opened.
            ValueError: If the file contains invalid JSON.
        """
        path = Path(path)
        with path.open() as fh:
            loaded: dict[str, dict[str, float]] = json.load(fh)
        self.positions.update(loaded)
        logger.info("load: %d position(s) read from %s", len(loaded), path)
