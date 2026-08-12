# SPDX-FileCopyrightText: 2026 Festo SE & Co. KG


"""Structural protocol that all axis backend implementations must satisfy."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class Axis(Protocol):
    """Structural interface for a single controllable axis.

    Both [`EdconAxis`][applied_motion.backends.edcon_axis.EdconAxis] (Modbus/festo-edcon backend) and
    [`FPosBAxis`][applied_motion.backends.fposbapi_axis.FPosBAxis] (FPosBAPI backend)
    satisfy this protocol structurally — no explicit inheritance required.

    All position values are in **millimetres (mm)**.
    All velocity values are in **mm/s**.
    """

    name: str
    min_position: float
    max_position: float

    def move(self, position: float, velocity: float, **kwargs) -> bool:
        """Move the axis to *position* at *velocity*.

        Args:
            position: Target position in mm (absolute or relative depending on
                ``position_type`` kwarg).
            velocity: Move speed in mm/s.
            **kwargs: Backend-specific options (e.g. ``position_type``,
                ``timeout``).

        Returns:
            ``True`` if the move completed successfully.
        """
        ...

    def home(self) -> bool:
        """Run the axis homing/referencing sequence."""
        ...

    def get_current_axis_position(self) -> float:
        """Return the current axis position in mm.

        Returns:
            Current position in mm.
        """
        ...

    def is_homed(self) -> bool:
        """Return whether this axis reports a valid homed/reference state.

        Returns:
            ``True`` if this axis is homed/referenced; ``False`` otherwise.
        """
        ...

    def stopped(self) -> bool:
        """Return ``True`` when the axis is not currently in motion.

        Returns:
            ``True`` if the axis has stopped; ``False`` if motion is ongoing.
        """
        ...

    def acknowledge_faults(self):
        """Clear errors."""
        # TODO: This shouldn't be exposed to end-users. This is done automatically for almost every motion task to proceed and is part of an internal interface.
        ...

    def enable_powerstage(self):
        """Turn on torque to motor controlling this axis."""
        # TODO: This shouldn't be exposed to end-users. This is done automatically for almost every motion task to proceed and is part of an internal interface.
        ...

    def disable_powerstage(self):
        """Turn off torque to motor controlling this axis."""
        ...

    def current_position(self):
        """Return current position."""
        ...

    def current_velocity(self):
        """Return current velocity."""
        ...

    def jog_task(
        self,
        jog_positive: bool = True,
        jog_negative: bool = False,
        incremental: bool = False,
        duration: float = 0.0,
    ) -> bool:
        """Jog the axis according to backend-specific jog semantics.

        Args:
            jog_positive: When ``True``, request jog in the positive direction.
            jog_negative: When ``True``, request jog in the negative direction.
            incremental: Backend-specific incremental jog mode flag.
            duration: Optional jog duration in seconds. A value of ``0``
                starts the jog and returns immediately.

        Returns:
            ``True`` if the jog command succeeds; ``False`` otherwise.
        """
        # TODO: Implement this functionality using corresponding FPosBAPI command

        ...

    def ready_for_motion(self) -> bool:
        """Return ``True`` when the axis is ready to accept a move command.

        Returns:
            ``True`` if the axis is enabled, homed, and fault-free.
        """
        ...
